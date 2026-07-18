"""插件系统模块 - 提供插件加载、卸载、启用/禁用等功能

此模块是插件系统的核心，包含：
- PluginBase: 插件基类，所有插件必须继承此类
- PluginManager: 插件管理器，负责插件的扫描、加载、启用/禁用等
- getPluginManager(): 获取插件管理器单例的入口函数

使用方式：
    from src.plugin import getPluginManager
    pm = getPluginManager()
    plugins = pm.scanPlugins()"""

import sys
import shutil
import importlib
import inspect
from abc import ABC
from pathlib import Path
from typing import Dict, List, Optional, Type

from src.util import plugin_dir, logger, Singleton


PLUGIN_EXTENSION = {".py", ".pyd", ".so"}

class PluginBase(ABC):
    """
    插件基类，所有插件必须继承此类

    属性:
        version: 插件版本
        description: 插件描述
        author: 作者
        file: 插件写入的文件列表

    规范（所有插件必须遵循）:
        __init__():    只声明属性（=None/=[]/=""），不调用任何函数，不创建重资源，不读写配置

        initialize():  负责初始化创建资源，达到懒加载效果，首次交互时由 action handler 调 self.initialize() 触发。
        子类必须调用并 if not super().initialize(): return。
        每个 QAction 在执行前都要 self.initialize()

        getAction():   返回 QMenu/QAction，不要调 initialize()。只取 __init__ 和 loadConfig() 中已就绪的属性。

        cleanup():     清理资源，开头守卫: if not self._initialized: return

        loadConfig():  加载配置，启动时由 PluginManager 调用

    执行顺序:
        启动: __init__() → loadConfig() → getAction()（菜单构建）
        交互: action handler → self.initialize() → 业务逻辑
        禁用: if _initialized → cleanup()
    """
    
    version: str = "1.0.0"
    description: str = ""
    author: str = ""
    file: list = []

    def __init__(self, main=None):
        self.name = self.__module__.rsplit(".", 1)[-1]
        self.main = main
        self.enabled = False
        self.settings = {}
        self._initialized = False
        self._cleanup_hooks: list = []

    def getAction(self):
        """获取插件动作菜单，返回 QMenu、QAction 或 None"""
        return None

    def initialize(self) -> bool:
        """初始化插件。返回 True 表示首次初始化。子类必须重写此方法，开头:
            if not super().initialize():
                return
        """
        if self._initialized:
            return False
        self._initialized = True
        return True

    def cleanup(self):
        """清理插件资源。子类重写时开头守卫:
            if not self._initialized:
                return
        基类会自动执行注册的清理钩子。"""
        for fn in self._cleanup_hooks:
            try:
                fn()
            except Exception:
                logger.exception("清理钩子执行异常")
        self._cleanup_hooks.clear()

    def loadConfig(self):
        """加载插件配置。子类覆盖时开头必须调 super().loadConfig()，
        用 setdefault 补充默认值:
            super().loadConfig()
            self.settings.setdefault("key", "default")
        """
        if self.main and hasattr(self.main, 'config'):
            saved = self.main.config.get("Plugin", {}).get(self.name, {})
            if saved:
                self.settings.update(saved)
    
    def saveConfig(self) -> dict:
        if self.main and hasattr(self.main, 'config'):
            plugin_config = self.main.config.get("Plugin", {})
            existing = plugin_config.get(self.name, {})
            enabled = existing.get("enabled", True)
            existing.update(self.settings)
            existing["enabled"] = enabled
            plugin_config[self.name] = existing
            self.main.config.save()
        return self.settings
    
    def getSelect(self, callback):
        """异步获取当前选中文本，完成后回调 callback(text)"""
        from src.core.input import copyWait
        copyWait(callback)

    def onFileOpen(self, file_path: str):
        """文件打开时的回调"""
        pass
    
    def onFileSave(self, file_path: str):
        """文件保存时的回调"""
        pass

class PluginManager(Singleton):
    """插件管理器 - 负责插件的扫描、加载、启用/禁用等

    使用单例模式，通过 getPluginManager() 获取实例。"""
    
    def _init(self, main=None):
        self.main = main
        self.plugins: Dict[str, PluginBase] = {}
        self._scan_cache: Dict[str, tuple] = {}
        self.enabled_plugins: Dict[str, bool] = {}
        self._file_handlers: List[tuple] = []
        self.extra_plugin_dir = None
        self.scanPlugins()
    
    def _scanDir(self, scan_dir: Path):
        if not scan_dir.exists():
            return
        for item in scan_dir.iterdir():
            if item.name.startswith('_'):
                continue
            if item.is_file() and item.suffix in PLUGIN_EXTENSION:
                self._scan_cache[item.stem] = (f"plugin.{item.stem}", item, None)
            elif item.is_dir():
                for f in item.iterdir():
                    if f.is_file() and f.suffix in PLUGIN_EXTENSION:
                        dotted = f"plugin.{item.name}.{f.stem}"
                        if '__init__' not in dotted:
                            self._scan_cache[f.stem] = (dotted, f, None)

    def scanPlugins(self) -> List[str]:
        self._scan_cache.clear()
        self._scanDir(plugin_dir)
        if self.extra_plugin_dir:
            self._scanDir(Path(self.extra_plugin_dir))
        return list(self._scan_cache.keys())

    def importPluginModule(self, module_key: str):
        entry = self._scan_cache.get(module_key)
        if entry is None:
            return None
        dotted_path, file_path, _ = entry
        try:
            spec = importlib.util.spec_from_file_location(dotted_path, file_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            for name, obj in inspect.getmembers(module, inspect.isclass):
                if (inspect.isclass(obj) and 
                    issubclass(obj, PluginBase) and 
                    obj is not PluginBase and
                    hasattr(obj, '__bases__')):
                    return module, obj
        except Exception:
            logger.exception(f"加载插件模块 {dotted_path} 失败")
        return None

    def loadPluginClass(self, module_key: str) -> Optional[Type['PluginBase']]:
        entry = self._scan_cache.get(module_key)
        if entry is None:
            return None
        _, _, cached = entry
        if cached is not None:
            return cached
        
        result = self.importPluginModule(module_key)
        if result is None:
            return None
        
        module, obj = result
        self._scan_cache[module_key] = (entry[0], entry[1], obj)
        return obj
    
    def pluginClass(self, plugin_name: str) -> Optional[Type[PluginBase]]:
        entry = self._scan_cache.get(plugin_name)
        if entry is None:
            return None
        _, _, obj = entry
        if obj is not None:
            return obj
        return self.loadPluginClass(plugin_name)

    def reloadPlugin(self, plugin_name: str) -> bool:
        if plugin_name in self.plugins:
            self.disablePlugin(plugin_name)
        
        if plugin_name not in self._scan_cache:
            return False
        
        dotted_path, file_path, _ = self._scan_cache[plugin_name]
        
        if dotted_path in sys.modules:
            del sys.modules[dotted_path]
        
        try:
            result = self.importPluginModule(plugin_name)
            if result is None:
                return False
            
            module, obj = result
            sys.modules[dotted_path] = module
            
            self._scan_cache[plugin_name] = (dotted_path, file_path, obj)
            return self.enablePlugin(plugin_name)
        except Exception:
            logger.exception(f"重新加载插件 {plugin_name} 失败")
        
        return False
    
    def reloadPlugins(self) -> list:
        """重新加载所有插件，返回失败的插件名列表"""
        enabled = list(self.plugins.keys())
        
        for name in enabled:
            self.disablePlugin(name)
        
        self._scan_cache.clear()
        self.scanPlugins()
        
        failed = []
        for name in enabled:
            if not self.enablePlugin(name):
                failed.append(name)
        if failed:
            logger.warning(f"重新加载插件失败: {failed}")
        return failed
    
    def enablePlugin(self, plugin_name: str) -> bool:
        """启用插件（首次启用时懒加载模块）"""
        if plugin_name in self.plugins:
            return True
        
        if plugin_name not in self._scan_cache:
            return False
        
        _, _, obj = self._scan_cache[plugin_name]
        if obj is None:
            obj = self.loadPluginClass(plugin_name)
            if obj is None:
                return False
        
        try:
            plugin = obj(main=self.main)
            
            plugin.loadConfig()
            plugin.enabled = True
            
            self.plugins[plugin_name] = plugin
            self.enabled_plugins[plugin_name] = True
            return True
        except Exception:
            logger.exception(f"启用插件 {plugin_name} 失败")
            return False
    
    def disablePlugin(self, plugin_name: str):
        """禁用插件"""
        if plugin_name not in self.plugins:
            return
        
        plugin = self.plugins[plugin_name]
        
        if plugin._initialized:
            try:
                plugin.cleanup()
            except Exception:
                logger.exception(f"插件 {plugin_name} 清理失败")
        
        plugin.enabled = False
        self.enabled_plugins[plugin_name] = False
        del self.plugins[plugin_name]
    
    def isPluginEnabled(self, plugin_name: str) -> bool:
        """检查插件是否已启用"""
        return self.enabled_plugins.get(plugin_name, False)
    
    def registerFileHandler(self, can_handle: callable, open_file: callable):
        """注册文件处理器。can_handle(file_path)->bool, open_file(file_path, main_window)"""
        self._file_handlers.append((can_handle, open_file))
    
    @property
    def fileHandlers(self) -> list:
        return self._file_handlers
    
    def initConfig(self, config):
        """从 config["Plugin"] 读取启用状态，扫描插件，加载已启用的插件"""
        plugin_config = config.get("Plugin", {})
        
        self.enabled_plugins.clear()
        for plugin_name, plugin_data in plugin_config.items():
            if isinstance(plugin_data, dict):
                self.enabled_plugins[plugin_name] = plugin_data.get("enabled", True)
        
        self.extra_plugin_dir = config.get("extra_plugin", "")
        available = self.scanPlugins()
        for p in available:
            if p not in self.enabled_plugins:
                self.enabled_plugins[p] = True
        
        for plugin_name, enabled in self.enabled_plugins.items():
            if enabled:
                self.enablePlugin(plugin_name)
    
    def saveConfig(self, config):
        """将当前插件启用状态和 settings 写回 config["Plugin"]"""
        plugin_config = config.get("Plugin")
        if plugin_config is None:
            plugin_config = {}
            config.set("Plugin", plugin_config)
        for plugin_name in self._scan_cache:
            is_enabled = self.enabled_plugins.get(plugin_name, False)
            if plugin_name not in plugin_config:
                plugin_config[plugin_name] = {}
            plugin = self.plugins.get(plugin_name)
            if plugin and plugin.settings:
                plugin_config[plugin_name].update(plugin.settings)
            plugin_config[plugin_name]["enabled"] = is_enabled
        config.save()
    
    def deletePlugin(self, plugin_name: str) -> list:
        """删除插件文件及关联数据，返回错误列表"""
        errors = []

        entry = self._scan_cache.get(plugin_name)
        if entry:
            _, plugin_file, cached_class = entry
        else:
            plugin_file = plugin_dir / f"{plugin_name}.py"
            cached_class = None
        if plugin_file and plugin_file.exists():
            try:
                plugin_file.unlink()
            except Exception as e:
                errors.append(f"插件文件: {e}")

        if cached_class:
            for f in cached_class.file:
                p = Path(f)
                try:
                    if p.is_dir():
                        shutil.rmtree(p)
                    elif p.exists():
                        p.unlink()
                except Exception as e:
                    errors.append(f"清理文件 {p}: {e}")

        if plugin_name in self.plugins:
            self.disablePlugin(plugin_name)

        if plugin_name in self.enabled_plugins:
            del self.enabled_plugins[plugin_name]

        return errors

def pluginActionMenu(plugin_manager):
    """遍历所有已启用插件，yield (display_name, getAction(), plugin_instance)。

    Yields:
        (description, action_or_menu, plugin) 三元组"""
    available = plugin_manager.scanPlugins()
    for plugin_name in available:
        if not plugin_manager.isPluginEnabled(plugin_name):
            continue
        plugin = plugin_manager.plugins.get(plugin_name)
        if not plugin:
            continue
        action = plugin.getAction()
        if action is not None:
            yield plugin.description, action, plugin


def getPluginManager(main=None) -> PluginManager:
    """获取插件管理器单例，是插件系统的主要入口点。
    
    Args:
        main: Launcher 窗口实例（可选，首次调用时设置）
    
    Returns:
        PluginManager 单例实例
    """
    if PluginManager._instance is None:
        PluginManager(main=main)
    return PluginManager._instance
