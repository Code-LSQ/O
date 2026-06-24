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


PLUGIN_EXTENSION = {'.py', '.pyd', '.so'}

class PluginBase(ABC):
    """
    插件基类，所有插件必须继承此类
    
    属性:
        version: 插件版本
        description: 插件描述
        author: 作者
        file: 插件写入的文件列表
    """
    
    version: str = "1.0.0"
    description: str = ""
    author: str = ""
    file: list = []
    
    def __init__(self, main_window=None):
        self.name = self.__module__.rsplit(".", 1)[-1]
        self.main_window = main_window
        self.enabled = False
        self.settings = {}
    
    def initialize(self):
        """初始化插件"""
        pass
    
    def getAction(self):
        """获取插件动作菜单，返回 QMenu、QAction 或 None"""
        return None
    
    def loadConfig(self):
        if self.main_window and hasattr(self.main_window, 'config'):
            saved = self.main_window.config.get("Plugin", {}).get(self.name, {})
            if saved:
                self.settings.update(saved)
    
    def saveConfig(self) -> dict:
        if self.main_window and hasattr(self.main_window, 'config'):
            plugin_config = self.main_window.config.get("Plugin", {})
            existing = plugin_config.get(self.name, {})
            existing.update(self.settings)
            plugin_config[self.name] = existing
            self.main_window.config.set("Plugin", plugin_config)
            self.main_window.config.save()
        return self.settings
    
    def onFileOpen(self, file_path: str):
        """文件打开时的回调"""
        pass
    
    def onFileSave(self, file_path: str):
        """文件保存时的回调"""
        pass
    
    def activate(self):
        """插件激活时的回调"""
        pass
    
    def deactivate(self):
        """插件停用时的回调"""
        pass
    
    def cleanup(self):
        """清理插件资源"""
        pass


class PluginManager(Singleton):
    """插件管理器 - 负责插件的扫描、加载、启用/禁用等

    使用单例模式，通过 getPluginManager() 获取实例。"""
    
    def _init_impl(self, main_window=None):
        self.main_window = main_window
        self.plugins: Dict[str, PluginBase] = {}
        self._scan_cache: Dict[str, tuple] = {}
        self.enabled_plugins: Dict[str, bool] = {}
        self._file_handlers: List[tuple] = []
        self.scanPlugins()
    
    def scanPlugins(self) -> List[str]:
        self._scan_cache.clear()
        if not plugin_dir.exists():
            return []
        
        for item in plugin_dir.iterdir():
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
    
    def getPluginClass(self, plugin_name: str) -> Optional[Type[PluginBase]]:
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
    
    def reloadPlugins(self) -> None:
        """重新加载所有插件"""
        enabled = list(self.plugins.keys())
        
        for name in enabled:
            self.disablePlugin(name)
        
        self._scan_cache.clear()
        self.scanPlugins()
        
        for name in enabled:
            self.enablePlugin(name)
    
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
            plugin = obj(self.main_window)
            
            plugin.loadConfig()
            plugin.initialize()
            plugin.enabled = True
            
            if hasattr(plugin, 'activate'):
                plugin.activate()
            
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
        
        if hasattr(plugin, 'deactivate'):
            try:
                plugin.deactivate()
            except Exception:
                logger.exception(f"插件 {plugin_name} 停用回调失败")
        
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
    
    def getAllPlugin(self) -> Dict[str, PluginBase]:
        """获取所有已加载的插件"""
        return self.plugins.copy()
    
    def registerFileHandler(self, can_handle: callable, open_file: callable):
        """注册文件处理器。can_handle(file_path)->bool, open_file(file_path, main_window)"""
        self._file_handlers.append((can_handle, open_file))
    
    @property
    def file_handlers(self) -> list:
        return self._file_handlers
    
    def initConfig(self, config):
        """从 config["Plugin"] 读取启用状态，扫描插件，加载已启用的插件"""
        plugin_config = config.get("Plugin", {})
        
        self.enabled_plugins.clear()
        for plugin_name, plugin_data in plugin_config.items():
            if isinstance(plugin_data, dict):
                self.enabled_plugins[plugin_name] = plugin_data.get("enabled", True)
        
        available = self.scanPlugins()
        for p in available:
            if p not in self.enabled_plugins:
                self.enabled_plugins[p] = True
        
        for plugin_name, enabled in self.enabled_plugins.items():
            if enabled:
                self.enablePlugin(plugin_name)
    
    def saveConfig(self, config):
        """将当前插件启用状态和 settings 写回 config["Plugin"]"""
        plugin_data = {}
        for plugin_name in self._scan_cache:
            is_enabled = self.enabled_plugins.get(plugin_name, False)
            entry = {"enabled": is_enabled}
            plugin = self.plugins.get(plugin_name)
            if plugin and plugin.settings:
                entry.update(plugin.settings)
            plugin_data[plugin_name] = entry
        config.set("Plugin", plugin_data)
        config.save()
    
    def deletePlugin(self, plugin_name: str) -> list:
        """删除插件文件及关联数据，返回错误列表"""
        errors = []

        plugin_file = plugin_dir / f"{plugin_name}.py"
        if plugin_file.exists():
            try:
                plugin_file.unlink()
            except Exception as e:
                errors.append(f"插件文件: {e}")

        entry = self._scan_cache.get(plugin_name)
        if entry:
            _, _, cached_class = entry
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
            try:
                self.disablePlugin(plugin_name)
            except Exception as e:
                errors.append(f"禁用插件: {e}")

        if plugin_name in self.enabled_plugins:
            del self.enabled_plugins[plugin_name]

        return errors

    def setMainWindow(self, main_window):
        """设置主窗口实例"""
        self.main_window = main_window
        for plugin in self.plugins.values():
            plugin.main_window = main_window


def pluginActionMenu(plugin_manager, main_window=None):
    """遍历所有已启用插件，yield (display_name, getAction(), plugin_instance)。

    Args:
        plugin_manager: PluginManager 实例
        main_window: 可选，设置到 plugin.main_window

    Yields:
        (description, action_or_menu, plugin) 三元组"""
    available = plugin_manager.scanPlugins()
    for plugin_name in available:
        if not plugin_manager.isPluginEnabled(plugin_name):
            continue
        plugin = plugin_manager.plugins.get(plugin_name)
        if not plugin:
            continue
        if main_window is not None:
            plugin.main_window = main_window
        action = plugin.getAction()
        if action is not None:
            yield plugin.description, action, plugin


def getPluginManager(main_window=None) -> PluginManager:
    """获取插件管理器单例，是插件系统的主要入口点。
    
    Args:
        main_window: 主窗口实例（可选，首次调用时设置）
    
    Returns:
        PluginManager 单例实例
    """
    if PluginManager._instance is None:
        PluginManager(main_window)
    elif main_window is not None:
        PluginManager._instance.main_window = main_window
        if PluginManager._instance.plugins:
            for plugin in PluginManager._instance.plugins.values():
                plugin.main_window = main_window
    return PluginManager._instance
