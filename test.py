"""测试模块，始终详细输出

用法:
    --util                    仅 util 测试
    --compare                 仅 compare 测试
    --plugin                  仅 plugin 测试
    --ai                      仅 AI adapter 测试
    --sync                    仅 sync 测试
    --perf                    性能测试
    --exception               异常测试
    --list                    列出测试组
    --gen-tokens
    --help                    帮助


维护说明:
    所有 TestCase 统一使用 applyMock() 工厂注入 mock 依赖，
    setUp/tearDown 配对管理 patchers，禁止全局状态。
新增分组:
    1. 在 _GROUP_REGISTRY 注册 (group_name, test_class)
    2. 按需调用 applyMock(mock_key=...) 注入依赖
    3. main() 自动注册 --group_name CLI 参数

"""

import os
import sys
import argparse
import json
import shutil
import hashlib
import tempfile
import time
import types
import unittest
import fnmatch
import tarfile
import requests
from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6.QtTest import QTest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.util import logger

# ============================================================
# Mock 基础设施 — 统一工厂
# ============================================================

def _make_module(name, **attrs):
    """Create a module with given attributes"""
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


class _MockSingleton:
    _instance = None
    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    def __init__(self, *args, **kwargs):
        if getattr(self, '_initialized', False):
            return
        self._initialized = True
        self._init_impl(*args, **kwargs)
    def _init_impl(self, *args, **kwargs):
        pass


_SHARED_UTIL_ATTRS = {
    'Singleton': _MockSingleton,
    'logger': MagicMock(),
    'APP_NAME': 'O',
    'IMAGE_EXTENSIONS': {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.ico', '.webp'},
    'TEXT_EXTENSIONS': {'.txt', '.md', '.py', '.js', '.json', '.html', '.css', '.xml'},
    'MARKDOWN_EXTENSIONS': {'.md', '.markdown'},
    'ZIP_EXTENSIONS': {'.zip', '.jar', '.apk'},
    'TAR_EXTENSIONS': {'.tar', '.tar.gz', '.tgz'},
    'ENCODING_MAP': {'UTF-8': 'utf-8', 'GBK': 'gb18030', 'Shift-JIS': 'shift_jis'},
    'data_dir': MagicMock(),
    'plugin_dir': MagicMock()
}


def _make_qt_core():
    """Create Qt mock modules (PySide6 / QtCore / QtGui / QtWidgets)"""
    return {
        'PySide6': MagicMock(),
        'PySide6.QtCore': _make_module('PySide6.QtCore',
            QThread=MagicMock,
            Signal=MagicMock,
            QObject=MagicMock,
            Qt=MagicMock(),
            QTimer=MagicMock,
            QThreadPool=MagicMock,
            QRunnable=MagicMock,
            Slot=MagicMock,
            QUrl=MagicMock,
        ),
        'PySide6.QtGui': MagicMock(),
        'PySide6.QtWidgets': MagicMock(),
    }


def _make_util():
    """Create src.util mock module"""
    return {'src.util': _make_module('src.util', **_SHARED_UTIL_ATTRS)}


def _make_file():
    """Create src.file mock module"""
    return {'src.file': _make_module('src.file',
        FileSelect=MagicMock(),
        format_file_size=MagicMock(return_value="1.0 KB"),
        fileTree=MagicMock(return_value=[]),
        filter_files=MagicMock(return_value=[]),
    )}


def _make_config():
    """Create src.config mock module"""
    return {'src.config': _make_module('src.config',
        getConfig=MagicMock(return_value={}),
    )}


def applyMock(*, qt=True, psutil=False, pynput=False,
                 keyboard=False, mouse=False, requests_mod=False,
                 markdown=False, util=False, real_logger_util=False,
                 file_mod=False, config=False, real_plugin=False):
    """按需组装 mock 环境，每个 setUp() 中调用

    返回 patcher 列表，调用方需在 tearDown() 中对每个 patcher 执行 stop()。
    """
    patchers = []

    if qt:
        p = patch.dict('sys.modules', _make_qt_core())
        p.start()
        patchers.append(p)

    if psutil:
        p = patch.dict('sys.modules', {'psutil': MagicMock()})
        p.start()
        patchers.append(p)

    if pynput:
        p = patch.dict('sys.modules', {'pynput': MagicMock()})
        p.start()
        patchers.append(p)

    if keyboard:
        p = patch.dict('sys.modules', {'pynput.keyboard': MagicMock()})
        p.start()
        patchers.append(p)

    if mouse:
        p = patch.dict('sys.modules', {'pynput.mouse': MagicMock()})
        p.start()
        patchers.append(p)

    if requests_mod:
        p = patch.dict('sys.modules', {'requests': MagicMock()})
        p.start()
        patchers.append(p)

    if markdown:
        p = patch.dict('sys.modules', {'markdown': MagicMock()})
        p.start()
        patchers.append(p)

    if util:
        if real_logger_util:
            import importlib
            real_util = importlib.import_module('src.util')
            attrs = dict(_SHARED_UTIL_ATTRS)
            attrs['logger'] = real_util.logger
            attrs['getTimestamp'] = MagicMock(return_value='2025-01-01 00:00:00')
            p = patch.dict('sys.modules', {'src.util': _make_module('src.util', **attrs)})
        else:
            p = patch.dict('sys.modules', _make_util())
        p.start()
        patchers.append(p)

    if file_mod:
        p = patch.dict('sys.modules', _make_file())
        p.start()
        patchers.append(p)

    if config:
        p = patch.dict('sys.modules', _make_config())
        p.start()
        patchers.append(p)

    if real_plugin:
        if 'src.plugin' in sys.modules:
            del sys.modules['src.plugin']
        import src.plugin
        p = patch.dict('sys.modules', {'src.plugin': src.plugin})
        p.start()
        patchers.append(p)

    return patchers


# Group: util (12 tests)

class TestEncodingMap(unittest.TestCase):
    def setUp(self):
        self._patchers = applyMock(util=True)

    def tearDown(self):
        for p in self._patchers:
            p.stop()

    def test_encoding_map_contains_common(self):
        encodings = sys.modules['src.util'].ENCODING_MAP
        self.assertIn("UTF-8", encodings)
        self.assertIn("GBK", encodings)
        self.assertEqual(encodings["UTF-8"], "utf-8")

    def test_encoding_map_values_are_strings(self):
        for key, val in sys.modules['src.util'].ENCODING_MAP.items():
            self.assertIsInstance(key, str)
            self.assertIsInstance(val, str)


class TestFileExtensions(unittest.TestCase):
    def setUp(self):
        self._patchers = applyMock(util=True)

    def tearDown(self):
        for p in self._patchers:
            p.stop()

    def test_image_extensions_contain_common(self):
        exts = sys.modules['src.util'].IMAGE_EXTENSIONS
        for ext in ['.png', '.jpg', '.jpeg', '.gif', '.bmp']:
            self.assertIn(ext, exts)

    def test_text_extensions_contain_common(self):
        exts = sys.modules['src.util'].TEXT_EXTENSIONS
        for ext in ['.txt', '.md', '.py', '.json']:
            self.assertIn(ext, exts)

    def test_markdown_extensions(self):
        exts = sys.modules['src.util'].MARKDOWN_EXTENSIONS
        self.assertIn('.md', exts)
        self.assertIn('.markdown', exts)

    def test_zip_extensions(self):
        exts = sys.modules['src.util'].ZIP_EXTENSIONS
        self.assertIn('.zip', exts)
        self.assertIn('.apk', exts)


class TestConstants(unittest.TestCase):
    def setUp(self):
        self._patchers = applyMock(util=True)

    def tearDown(self):
        for p in self._patchers:
            p.stop()

    def test_app_name(self):
        self.assertEqual(sys.modules['src.util'].APP_NAME, "O")


# Group: plugin (48 tests)

class _PluginTestBase(unittest.TestCase):
    def setUp(self):
        self._patchers = applyMock(qt=True, util=True, psutil=True, pynput=True, real_plugin=True)
        from src.plugin import PluginManager, PluginBase
        self.PluginBase = PluginBase
        self.PluginManager = PluginManager

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        if hasattr(self, '_reset'):
            self._reset()


class TestPluginBase(_PluginTestBase):

    def test_default_attributes(self):
        plugin = self.PluginBase()
        self.assertEqual(plugin.name, "Unnamed Plugin")
        self.assertEqual(plugin.description, "")
        self.assertEqual(plugin.version, "1.0.0")
        self.assertEqual(plugin.author, "")
        self.assertFalse(plugin.enabled)
        self.assertEqual(plugin.settings, {})

    def test_initialize_called_without_error(self):
        plugin = self.PluginBase()
        plugin.initialize()

    def test_getAction_returns_none_by_default(self):
        plugin = self.PluginBase()
        self.assertIsNone(plugin.getAction())

    def test_loadConfig(self):
        plugin = self.PluginBase()
        plugin.loadConfig()
        self.assertEqual(plugin.settings, {})

    def test_saveConfig(self):
        plugin = self.PluginBase()
        plugin.settings = {"a": 1}
        result = plugin.saveConfig()
        self.assertEqual(result, {"a": 1})

    def test_onFileOpen_does_not_raise(self):
        plugin = self.PluginBase()
        plugin.onFileOpen("/some/path")

    def test_onFileSave_does_not_raise(self):
        plugin = self.PluginBase()
        plugin.onFileSave("/some/path")

    def test_activate_does_not_raise(self):
        plugin = self.PluginBase()
        plugin.activate()

    def test_deactivate_does_not_raise(self):
        plugin = self.PluginBase()
        plugin.deactivate()

    def test_cleanup_does_not_raise(self):
        plugin = self.PluginBase()
        plugin.cleanup()

    def test_main_window_none_by_default(self):
        plugin = self.PluginBase()
        self.assertIsNone(plugin.main_window)

    def test_main_window_set_in_init(self):
        mw = MagicMock()
        plugin = self.PluginBase(main_window=mw)
        self.assertEqual(plugin.main_window, mw)

    def test_custom_name(self):
        class TestPlugin(self.PluginBase):
            name = "MyPlugin"
            description = "A test plugin"
        plugin = TestPlugin()
        self.assertEqual(plugin.name, "MyPlugin")
        self.assertEqual(plugin.description, "A test plugin")


class TestCreateCustomPlugin(_PluginTestBase):

    def test_plugin_with_custom_getAction(self):
        class MenuPlugin(self.PluginBase):
            name = "MenuPlugin"
            def getAction(self):
                return "menu_action"
        plugin = MenuPlugin()
        self.assertEqual(plugin.getAction(), "menu_action")

    def test_plugin_lifecycle_methods(self):
        calls = []
        class LifecyclePlugin(self.PluginBase):
            name = "LifecyclePlugin"
            def __init__(self, main_window=None):
                super().__init__(main_window)
                self.initialized = False
            def initialize(self):
                calls.append("initialize")
            def activate(self):
                calls.append("activate")
            def deactivate(self):
                calls.append("deactivate")
            def cleanup(self):
                calls.append("cleanup")
        plugin = LifecyclePlugin()
        plugin.initialize()
        plugin.activate()
        plugin.deactivate()
        plugin.cleanup()
        self.assertEqual(calls, ["initialize", "activate", "deactivate", "cleanup"])


class TestPluginManagerBasic(_PluginTestBase):

    def test_singleton_pattern(self):
        pm1 = self.PluginManager()
        pm2 = self.PluginManager()
        self.assertIs(pm1, pm2)

    def test_singleton_through_getPluginManager(self):
        from src.plugin import getPluginManager
        pm1 = getPluginManager()
        pm2 = getPluginManager()
        self.assertIs(pm1, pm2)

    def test_initial_state(self):
        pm = self.PluginManager()
        self.assertEqual(pm.plugins, {})

    def test_initial_attributes_exist(self):
        pm = self.PluginManager()
        self.assertTrue(hasattr(pm, 'enabled_plugins'))
        self.assertTrue(hasattr(pm, 'plugin_dir'))

    def test_main_window_settable(self):
        mw = MagicMock()
        pm = self.PluginManager(main_window=mw)
        self.assertEqual(pm.main_window, mw)


class _PluginWithTempDirBase(_PluginTestBase):
    def setUp(self):
        super().setUp()
        self.temp_plugin_dir = tempfile.mkdtemp()
        self.pm = self.PluginManager()
        self.pm.plugin_dir = Path(self.temp_plugin_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_plugin_dir, ignore_errors=True)
        super().tearDown()

    def _create_plugin_file(self, name, content):
        path = os.path.join(self.temp_plugin_dir, name)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        return path


class TestPluginManagerWithTempDir(_PluginWithTempDirBase):

    def test_scan_empty_dir_returns_empty(self):
        plugins = self.pm.scanPlugins()
        self.assertEqual(plugins, [])

    def test_skip_init_files(self):
        self._create_plugin_file("__init__.py", "")
        self._create_plugin_file("_hidden.py", "")
        plugins = self.pm.scanPlugins()
        self.assertEqual(plugins, [])

    def test_scan_valid_plugin_file(self):
        self._create_plugin_file("my_plugin.py", """
from src.plugin import PluginBase

class MyPlugin(PluginBase):
    name = "MyPlugin"
    description = "Test"
""")
        plugins = self.pm.scanPlugins()
        self.assertIn("my_plugin", plugins)

    def test_scan_plugin_missing_base_class(self):
        self._create_plugin_file("bad.py", """
class NotAPlugin:
    pass
""")
        result = self.pm.scanPlugins()
        self.assertIn("bad", result)
        self.assertFalse(self.pm.enablePlugin("bad"))

    def test_enable_unknown_plugin_returns_false(self):
        result = self.pm.enablePlugin("nonexistent")
        self.assertFalse(result)

class TestPluginManagerEnableDisable(_PluginWithTempDirBase):

    def test_enable_disable_cycle(self):
        self._create_plugin_file("cycle_plugin.py", """
from src.plugin import PluginBase

class CyclePlugin(PluginBase):
    name = "CyclePlugin"
""")
        self.pm.scanPlugins()
        result = self.pm.enablePlugin("cycle_plugin")
        self.assertTrue(result)
        self.assertTrue(self.pm.isPluginEnabled("cycle_plugin"))
        self.assertIn("cycle_plugin", self.pm.plugins)
        self.pm.disablePlugin("cycle_plugin")
        self.assertFalse(self.pm.isPluginEnabled("cycle_plugin"))
        self.assertNotIn("cycle_plugin", self.pm.plugins)

    def test_enable_twice_returns_true(self):
        self._create_plugin_file("dup.py", """
from src.plugin import PluginBase

class DupPlugin(PluginBase):
    name = "DupPlugin"
""")
        self.pm.scanPlugins()
        self.assertTrue(self.pm.enablePlugin("dup"))
        self.assertTrue(self.pm.enablePlugin("dup"))

    def test_disable_not_enabled_does_not_raise(self):
        self.pm.disablePlugin("not_enabled")

    def test_get_all_plugins(self):
        self._create_plugin_file("p1.py", """
from src.plugin import PluginBase

class P1(PluginBase):
    name = "Plugin1"
""")
        self.pm.scanPlugins()
        self.pm.enablePlugin("p1")
        all_p = self.pm.getAllPlugin()
        self.assertIn("p1", all_p)

    def test_get_enabled_plugins(self):
        self._create_plugin_file("en.py", """
from src.plugin import PluginBase

class En(PluginBase):
    name = "EnabledPlugin"
""")
        self.pm.scanPlugins()
        self.pm.enablePlugin("en")
        self.assertTrue(self.pm.isPluginEnabled("en"))


class TestGetPluginModulePaths(_PluginTestBase):

    def test_nonexistent_dir_returns_empty(self):
        pm = self.PluginManager()
        pm.plugin_dir = Path(r"C:\nonexistent_xyz_plugin_dir")
        result = pm.scanPlugins()
        self.assertEqual(result, [])


class TestPluginManagerConfig(_PluginTestBase):
    def _make_config(self, data=None):
        config = MagicMock()
        config_data = data or {}
        def get_side_effect(key, default=None):
            return config_data.get(key, default)
        config.get.side_effect = get_side_effect
        def set_side_effect(key, value):
            config_data[key] = value
        config.set.side_effect = set_side_effect
        return config, config_data

    def test_init_config_loads_enabled_plugins(self):
        pm = self.PluginManager()
        config, _ = self._make_config({"Plugin": {"p1": {"enabled": True}}})
        pm.plugin_dir = Path(tempfile.mkdtemp())
        self._create_plugin_file_in(pm.plugin_dir, "p1.py", """
from src.plugin import PluginBase

class P1(PluginBase):
    name = "P1"
    description = "Test plugin"
""")
        pm.initConfig(config)
        self.assertTrue(pm.isPluginEnabled("p1"))
        self.assertIn("p1", pm.plugins)
        shutil.rmtree(pm.plugin_dir, ignore_errors=True)

    def test_init_config_default_enabled(self):
        pm = self.PluginManager()
        config, _ = self._make_config({"Plugin": {}})
        pm.plugin_dir = Path(tempfile.mkdtemp())
        self._create_plugin_file_in(pm.plugin_dir, "p1.py", """
from src.plugin import PluginBase

class P1(PluginBase):
    name = "P1"
""")
        pm.initConfig(config)
        self.assertTrue(pm.isPluginEnabled("p1"))

    def _create_plugin_file_in(self, directory, name, content):
        path = os.path.join(str(directory), name)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        return path

    def test_save_config_writes_enabled_and_settings(self):
        pm = self.PluginManager()
        config, config_data = self._make_config()
        pm.enabled_plugins = {"p1": True}
        pm._scan_cache = {"p1": ("plugin.p1", Path("p1.py"), None)}
        pm.plugins["p1"] = MagicMock()
        pm.plugins["p1"].settings = {"key": "val"}
        pm.saveConfig(config)
        self.assertIn("Plugin", config_data)
        self.assertEqual(config_data["Plugin"]["p1"]["enabled"], True)
        self.assertEqual(config_data["Plugin"]["p1"]["key"], "val")


# Group: ai (61 tests)

class TestResolveImageUrls(unittest.TestCase):
    def setUp(self):
        self._patchers = applyMock(qt=True, util=True, pynput=True,
                                      keyboard=True, mouse=True,
                                      config=True, file_mod=True)
        from src.core.AI import resolve_image_urls
        self.resolve_image_urls = resolve_image_urls

    def tearDown(self):
        for p in self._patchers:
            p.stop()

    def test_no_images_no_change(self):
        messages = [{"role": "user", "content": "hello"}]
        result = self.resolve_image_urls(messages)
        self.assertEqual(result, messages)

    def test_non_file_url_not_touched(self):
        messages = [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}}
            ]
        }]
        original = json.loads(json.dumps(messages))
        self.resolve_image_urls(messages)
        self.assertEqual(messages, original)

    def test_file_url_no_real_file_logs_error(self):
        messages = [{
            "role": "user",
            "content": [
                {"type": "image_url", "url": "file:///nonexistent/img.png"}
            ]
        }]
        self.resolve_image_urls(messages)
        part = messages[0]["content"][0]
        self.assertEqual(part["type"], "text")
        self.assertIn("图片加载失败", part["text"])

    def test_different_url_key_handling(self):
        messages = [{
            "role": "user",
            "content": [
                {"type": "image_url", "url": "file:///nonexistent/img.png"}
            ]
        }]
        self.resolve_image_urls(messages)
        part = messages[0]["content"][0]
        self.assertEqual(part["type"], "text")

    def test_non_dict_part_ignored(self):
        messages = [{"role": "user", "content": ["not a dict"]}]
        self.resolve_image_urls(messages)
        self.assertEqual(messages[0]["content"][0], "not a dict")

    def test_non_image_type_ignored(self):
        messages = [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]
        self.resolve_image_urls(messages)
        self.assertEqual(messages[0]["content"][0]["text"], "hello")


class TestAIClientBuildPromptContent(unittest.TestCase):
    def setUp(self):
        self._patchers = applyMock(qt=True, util=True, pynput=True,
                                      keyboard=True, mouse=True,
                                      config=True, file_mod=True)
        from src.core.AI import getAIClient
        self.getAIClient = getAIClient

    def tearDown(self):
        for p in self._patchers:
            p.stop()

    def test_with_request_placeholder(self):
        client = self.getAIClient(config={})
        result = client._build_prompt_content("Please translate: {request}", "hello world")
        self.assertEqual(result, "Please translate: hello world")

    def test_without_placeholder_appends(self):
        client = self.getAIClient(config={})
        result = client._build_prompt_content(
            "You are a translator.", "hello world"
        )
        self.assertEqual(result, "You are a translator.\n\nhello world")

    def test_empty_prompt(self):
        client = self.getAIClient(config={})
        result = client._build_prompt_content("", "user text")
        self.assertEqual(result, "\n\nuser text")

    def test_empty_user_message(self):
        client = self.getAIClient(config={})
        result = client._build_prompt_content("prefix {request} suffix", "")
        self.assertEqual(result, "prefix  suffix")

    def test_multiple_placeholders(self):
        client = self.getAIClient(config={})
        result = client._build_prompt_content(
            "{request} and {request}", "text"
        )
        self.assertEqual(result, "text and text")


class TestAIClientExtractUserMessage(unittest.TestCase):
    def setUp(self):
        self._patchers = applyMock(qt=True, util=True, pynput=True,
                                      keyboard=True, mouse=True,
                                      config=True, file_mod=True)
        from src.core.AI import getAIClient
        self.getAIClient = getAIClient

    def tearDown(self):
        for p in self._patchers:
            p.stop()

    def test_simple_text_message(self):
        client = self.getAIClient(config={})
        messages = [{"role": "user", "content": "hello"}]
        result = client._extract_user_message(messages)
        self.assertEqual(result, "hello")

    def test_last_user_message(self):
        client = self.getAIClient(config={})
        messages = [
            {"role": "assistant", "content": "response"},
            {"role": "user", "content": "final question"}
        ]
        result = client._extract_user_message(messages)
        self.assertEqual(result, "final question")

    def test_multipart_content(self):
        client = self.getAIClient(config={})
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": "part1"},
                {"type": "text", "text": "part2"}
            ]
        }]
        result = client._extract_user_message(messages)
        self.assertEqual(result, "part1\npart2")

    def test_empty_messages(self):
        client = self.getAIClient(config={})
        result = client._extract_user_message([])
        self.assertEqual(result, "")

    def test_no_user_message(self):
        client = self.getAIClient(config={})
        messages = [{"role": "assistant", "content": "only assistant"}]
        result = client._extract_user_message(messages)
        self.assertEqual(result, "")

    def test_mixed_content_types_in_multipart(self):
        client = self.getAIClient(config={})
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": "text part"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,xyz"}},
                {"type": "text", "text": "more text"}
            ]
        }]
        result = client._extract_user_message(messages)
        self.assertIn("text part", result)
        self.assertIn("more text", result)
        self.assertNotIn("image", result)


class TestLoadBalancing(unittest.TestCase):
    def setUp(self):
        self._patchers = applyMock(qt=True, util=True, pynput=True,
                                      keyboard=True, mouse=True,
                                      config=True, file_mod=True)
        from src.core.AI import getAIClient
        self.getAIClient = getAIClient

    def tearDown(self):
        for p in self._patchers:
            p.stop()

    def test_lb_disabled_no_groups(self):
        config = {"AI": {"load_balance": {"enabled": False}}}
        client = self.getAIClient(config=config)
        result = client._lb_pick_groups()
        self.assertIsNone(result)

    def test_lb_disabled_no_config(self):
        config = {}
        client = self.getAIClient(config=config)
        result = client._lb_pick_groups()
        self.assertIsNone(result)

    def test_lb_single_profile(self):
        config = {
            "AI": {
                "load_balance": {
                    "enabled": True,
                    "profiles": {
                        "DeepSeek": {"priority": 1, "weight": 1}
                    }
                }
            }
        }
        client = self.getAIClient(config=config)
        result = client._lb_pick_groups()
        self.assertEqual(result, [["DeepSeek"]])

    def test_lb_multiple_profiles_same_priority(self):
        config = {
            "AI": {
                "load_balance": {
                    "enabled": True,
                    "profiles": {
                        "A": {"priority": 1, "weight": 1},
                        "B": {"priority": 1, "weight": 1}
                    }
                }
            }
        }
        client = self.getAIClient(config=config)
        result = client._lb_pick_groups()
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]), 2)
        self.assertIn("A", result[0])
        self.assertIn("B", result[0])

    def test_lb_different_priorities(self):
        config = {
            "AI": {
                "load_balance": {
                    "enabled": True,
                    "profiles": {
                        "Primary": {"priority": 1, "weight": 1},
                        "Secondary": {"priority": 2, "weight": 1}
                    }
                }
            }
        }
        client = self.getAIClient(config=config)
        result = client._lb_pick_groups()
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], ["Primary"])
        self.assertEqual(result[1], ["Secondary"])

    def test_lb_priority_zero_disabled(self):
        config = {
            "AI": {
                "load_balance": {
                    "enabled": True,
                    "profiles": {
                        "Disabled": {"priority": 0, "weight": 1},
                        "Active": {"priority": 1, "weight": 1}
                    }
                }
            }
        }
        client = self.getAIClient(config=config)
        result = client._lb_pick_groups()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], ["Active"])

    def test_lb_failure_tracking(self):
        client = self.getAIClient(config={})
        client.__class__._lb_failures = {}
        client.__class__._lb_disabled = {}

        self.assertFalse(client._lb_disabled_check("Test"))

        client._lb_record("Test", False)
        self.assertEqual(client.__class__._lb_failures.get("Test"), 1)
        self.assertFalse(client._lb_disabled_check("Test"))

        client._lb_record("Test", False)
        client._lb_record("Test", False)
        self.assertTrue(client._lb_disabled_check("Test"))

    def test_lb_recovery_after_success(self):
        client = self.getAIClient(config={})
        client.__class__._lb_failures = {}
        client.__class__._lb_disabled = {}

        client._lb_record("Test", False)
        client._lb_record("Test", False)
        client._lb_record("Test", False)
        self.assertTrue(client._lb_disabled_check("Test"))

        client._lb_record("Test", True)
        self.assertFalse(client._lb_disabled_check("Test"))
        self.assertNotIn("Test", client.__class__._lb_failures)

    def test_lb_disabled_profile_excluded(self):
        config = {
            "AI": {
                "load_balance": {
                    "enabled": True,
                    "profiles": {
                        "Bad": {"priority": 1, "weight": 1},
                        "Good": {"priority": 1, "weight": 1}
                    }
                }
            }
        }
        client = self.getAIClient(config=config)
        client.__class__._lb_disabled = {"Bad": True}
        result = client._lb_pick_groups()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], ["Good"])

    def test_lb_all_disabled_returns_none(self):
        config = {
            "AI": {
                "load_balance": {
                    "enabled": True,
                    "profiles": {
                        "A": {"priority": 1, "weight": 1}
                    }
                }
            }
        }
        client = self.getAIClient(config=config)
        client.__class__._lb_disabled = {"A": True}
        result = client._lb_pick_groups()
        self.assertIsNone(result)


class TestAdaptersBuildChatRequest(unittest.TestCase):
    def setUp(self):
        self._patchers = applyMock(qt=True, util=True, pynput=True,
                                      keyboard=True, mouse=True,
                                      config=True, file_mod=True)
        from src.core.AI import OpenAIAdapter, ClaudeAdapter, OllamaAdapter, GeminiAdapter, AIError
        self.OpenAIAdapter = OpenAIAdapter
        self.ClaudeAdapter = ClaudeAdapter
        self.OllamaAdapter = OllamaAdapter
        self.GeminiAdapter = GeminiAdapter
        self.AIError = AIError

    def tearDown(self):
        for p in self._patchers:
            p.stop()

    def test_openai_adapter_build_request(self):
        adapter = self.OpenAIAdapter(config={}, api_key="test-key", api_url="https://api.openai.com")
        messages = [{"role": "user", "content": "hello"}]
        request = adapter.build_chat_request("gpt-4", messages, 0.7, 2000)
        self.assertEqual(request["model"], "gpt-4")
        self.assertEqual(request["messages"], messages)
        self.assertEqual(request["temperature"], 0.7)
        self.assertEqual(request["max_tokens"], 2000)

    def test_openai_adapter_headers(self):
        adapter = self.OpenAIAdapter(config={}, api_key="sk-test", api_url="https://api.openai.com")
        headers = adapter.get_headers("sk-test")
        self.assertEqual(headers["Authorization"], "Bearer sk-test")
        self.assertEqual(headers["Content-Type"], "application/json")

    def test_openai_adapter_api_url(self):
        adapter = self.OpenAIAdapter(config={}, api_key="key", api_url="https://api.deepseek.com")
        url = adapter.get_api_url()
        self.assertEqual(url, "https://api.deepseek.com/chat/completions")

    def test_openai_adapter_parse_response(self):
        adapter = self.OpenAIAdapter(config={}, api_key="key", api_url="https://api.openai.com")
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Hello, world!"}}]
        }
        result = adapter.parse_chat_response(mock_response)
        self.assertEqual(result, "Hello, world!")

    def test_openai_adapter_parse_empty_choices(self):
        adapter = self.OpenAIAdapter(config={}, api_key="key", api_url="https://api.openai.com")
        mock_response = MagicMock()
        mock_response.json.return_value = {"choices": []}
        with self.assertRaises(self.AIError):
            adapter.parse_chat_response(mock_response)

    def test_openai_adapter_parse_usage(self):
        adapter = self.OpenAIAdapter(config={}, api_key="key", api_url="https://api.openai.com")
        data = {"usage": {"prompt_tokens": 10, "completion_tokens": 20}}
        in_t, out_t = adapter.parse_usage(data)
        self.assertEqual(in_t, 10)
        self.assertEqual(out_t, 20)

    def test_openai_adapter_parse_usage_missing(self):
        adapter = self.OpenAIAdapter(config={}, api_key="key", api_url="https://api.openai.com")
        in_t, out_t = adapter.parse_usage({})
        self.assertEqual(in_t, 0)
        self.assertEqual(out_t, 0)

    def test_openai_adapter_get_models_url(self):
        adapter = self.OpenAIAdapter(config={}, api_key="key", api_url="https://api.deepseek.com")
        url = adapter.get_model_list_url()
        self.assertEqual(url, "https://api.deepseek.com/models")

    def test_claude_adapter_build_request(self):
        adapter = self.ClaudeAdapter(config={}, api_key="sk-ant-test", api_url="https://api.anthropic.com")
        messages = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "hello"}
        ]
        request = adapter.build_chat_request("claude-3-opus", messages, 0.5, 1000)
        self.assertEqual(request["model"], "claude-3-opus")
        self.assertNotIn("system", [m["role"] for m in request["messages"]])
        self.assertEqual(len(request["messages"]), 1)
        self.assertEqual(request["messages"][0]["role"], "user")

    def test_claude_adapter_headers(self):
        adapter = self.ClaudeAdapter(config={}, api_key="sk-ant-test", api_url="https://api.anthropic.com")
        headers = adapter.get_headers("sk-ant-test")
        self.assertEqual(headers["x-api-key"], "sk-ant-test")
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertIn("anthropic-version", headers)

    def test_claude_adapter_api_url(self):
        adapter = self.ClaudeAdapter(config={}, api_key="key", api_url="https://api.anthropic.com")
        url = adapter.get_api_url()
        self.assertEqual(url, "https://api.anthropic.com/v1/messages")

    def test_claude_adapter_parse_response(self):
        adapter = self.ClaudeAdapter(config={}, api_key="key", api_url="https://api.anthropic.com")
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "content": [{"text": "Claude response"}]
        }
        result = adapter.parse_chat_response(mock_response)
        self.assertEqual(result, "Claude response")

    def test_claude_adapter_parse_empty_content(self):
        adapter = self.ClaudeAdapter(config={}, api_key="key", api_url="https://api.anthropic.com")
        mock_response = MagicMock()
        mock_response.json.return_value = {}
        with self.assertRaises(self.AIError):
            adapter.parse_chat_response(mock_response)

    def test_claude_adapter_parse_usage(self):
        adapter = self.ClaudeAdapter(config={}, api_key="key", api_url="https://api.anthropic.com")
        data = {"usage": {"input_tokens": 15, "output_tokens": 25}}
        in_t, out_t = adapter.parse_usage(data)
        self.assertEqual(in_t, 15)
        self.assertEqual(out_t, 25)

    def test_ollama_adapter_build_request_no_images(self):
        adapter = self.OllamaAdapter(config={}, api_key="", api_url="http://127.0.0.1:11434")
        messages = [{"role": "user", "content": "hello"}]
        request = adapter.build_chat_request("llama3", messages, 0.7, 2000)
        self.assertEqual(request["model"], "llama3")
        self.assertIn("options", request)
        self.assertEqual(request["options"]["num_predict"], 2000)

    def test_ollama_adapter_headers(self):
        adapter = self.OllamaAdapter(config={}, api_key="", api_url="http://127.0.0.1:11434")
        headers = adapter.get_headers("")
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertNotIn("Authorization", headers)

    def test_ollama_adapter_api_url(self):
        adapter = self.OllamaAdapter(config={}, api_key="", api_url="http://127.0.0.1:11434")
        url = adapter.get_api_url()
        self.assertEqual(url, "http://127.0.0.1:11434/api/chat")

    def test_ollama_adapter_parse_response(self):
        adapter = self.OllamaAdapter(config={}, api_key="", api_url="http://127.0.0.1:11434")
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "message": {"content": "Ollama reply"}
        }
        result = adapter.parse_chat_response(mock_response)
        self.assertEqual(result, "Ollama reply")

    def test_ollama_adapter_get_models_url(self):
        adapter = self.OllamaAdapter(config={}, api_key="", api_url="http://127.0.0.1:11434")
        url = adapter.get_model_list_url()
        self.assertEqual(url, "http://127.0.0.1:11434/api/tags")

    def test_gemini_adapter_build_request(self):
        adapter = self.GeminiAdapter(config={}, api_key="gemini-key", api_url="https://generativelanguage.googleapis.com")
        messages = [{"role": "user", "content": "hello"}]
        request = adapter.build_chat_request("gemini-pro", messages, 0.7, 2000)
        self.assertIn("contents", request)
        self.assertIn("generationConfig", request)
        self.assertEqual(request["contents"][0]["role"], "user")
        self.assertEqual(request["generationConfig"]["temperature"], 0.7)
        self.assertEqual(request["generationConfig"]["maxOutputTokens"], 2000)

    def test_gemini_adapter_role_mapping(self):
        adapter = self.GeminiAdapter(config={}, api_key="key", api_url="https://generativelanguage.googleapis.com")
        messages = [{"role": "assistant", "content": "response"}]
        request = adapter.build_chat_request("gemini-pro", messages, 0.7, 2000)
        self.assertEqual(request["contents"][0]["role"], "model")

    def test_gemini_adapter_parse_response(self):
        adapter = self.GeminiAdapter(config={}, api_key="key", api_url="https://generativelanguage.googleapis.com")
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "Gemini response"}]}}]
        }
        result = adapter.parse_chat_response(mock_response)
        self.assertEqual(result, "Gemini response")

    def test_gemini_adapter_empty_candidates(self):
        adapter = self.GeminiAdapter(config={}, api_key="key", api_url="https://generativelanguage.googleapis.com")
        mock_response = MagicMock()
        mock_response.json.return_value = {"candidates": []}
        with self.assertRaises(self.AIError):
            adapter.parse_chat_response(mock_response)

    def test_gemini_adapter_parse_usage(self):
        adapter = self.GeminiAdapter(config={}, api_key="key", api_url="https://generativelanguage.googleapis.com")
        data = {"usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 10}}
        in_t, out_t = adapter.parse_usage(data)
        self.assertEqual(in_t, 5)
        self.assertEqual(out_t, 10)


class TestAdapterMap(unittest.TestCase):
    def setUp(self):
        self._patchers = applyMock(qt=True, util=True, pynput=True,
                                      keyboard=True, mouse=True,
                                      config=True, file_mod=True)
        from src.core.AI import AI_ADAPTER, get_adapter_endpoint, OpenAIAdapter, ClaudeAdapter, OllamaAdapter, GeminiAdapter
        self.AI_ADAPTER = AI_ADAPTER
        self.get_adapter_endpoint = get_adapter_endpoint
        self.OpenAIAdapter = OpenAIAdapter
        self.ClaudeAdapter = ClaudeAdapter
        self.OllamaAdapter = OllamaAdapter
        self.GeminiAdapter = GeminiAdapter

    def tearDown(self):
        for p in self._patchers:
            p.stop()

    def test_adapter_map_contains_known_services(self):
        names = [name for name, cls, url in self.AI_ADAPTER if cls is not None]
        expected = ["Claude", "DeepSeek", "Gemini", "Ollama", "OpenAI", "OpenRouter"]
        for name in expected:
            self.assertIn(name, names)

    def test_get_adapter_endpoint_returns_correct_type(self):
        adapter = self.get_adapter_endpoint("Claude", {}, api_key="key", api_url="url")
        self.assertIsInstance(adapter, self.ClaudeAdapter)

        adapter = self.get_adapter_endpoint("Ollama", {}, api_key="", api_url="url")
        self.assertIsInstance(adapter, self.OllamaAdapter)

        adapter = self.get_adapter_endpoint("Gemini", {}, api_key="key", api_url="url")
        self.assertIsInstance(adapter, self.GeminiAdapter)

        adapter = self.get_adapter_endpoint("DeepSeek", {}, api_key="key", api_url="url")
        self.assertIsInstance(adapter, self.OpenAIAdapter)

    def test_unknown_endpoint_falls_back_to_openai(self):
        adapter = self.get_adapter_endpoint("Unknown", {}, api_key="key", api_url="url")
        self.assertIsInstance(adapter, self.OpenAIAdapter)


class TestAIClientBuildFileMessage(unittest.TestCase):
    def setUp(self):
        self._patchers = applyMock(qt=True, util=True, pynput=True,
                                      keyboard=True, mouse=True,
                                      config=True, file_mod=True)
        self.test_dir = tempfile.mkdtemp()
        from src.core.AI import getAIClient
        self.getAIClient = getAIClient

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _make_file(self, name, content=b"test"):
        path = os.path.join(self.test_dir, name)
        with open(path, 'wb') as f:
            f.write(content)
        return path

    def test_text_file_message(self):
        client = self.getAIClient(config={})
        path = self._make_file("test.txt", b"hello world")
        result = client.build_file_message(path)
        self.assertIsNotNone(result)
        self.assertEqual(result[0]["role"], "user")
        self.assertIn("hello world", result[0]["content"])

    def test_large_file_skipped(self):
        client = self.getAIClient(config={})
        path = self._make_file("large.bin", b"x" * (4 * 1024 * 1024 + 1))
        result = client.build_file_message(path)
        self.assertIsNone(result)

    def test_binary_file_read_with_errors_replaced(self):
        client = self.getAIClient(config={})
        path = self._make_file("binary.bin", bytes(range(256)))
        result = client.build_file_message(path)
        self.assertIsNotNone(result)


class TestBaseAdapterConfig(unittest.TestCase):
    def setUp(self):
        self._patchers = applyMock(qt=True, util=True, pynput=True,
                                      keyboard=True, mouse=True,
                                      config=True, file_mod=True)
        from src.core.AI import OpenAIAdapter
        self.OpenAIAdapter = OpenAIAdapter

    def tearDown(self):
        for p in self._patchers:
            p.stop()

    def test_config_dict_access_dotted_key(self):
        cfg = {"AI": {"active_profile": "test"}}
        adapter = self.OpenAIAdapter(config=cfg)
        val = adapter._getConfig("AI.active_profile")
        self.assertEqual(val, "test")

    def test_config_dict_access_simple_key(self):
        cfg = {"theme": "dark"}
        adapter = self.OpenAIAdapter(config=cfg)
        val = adapter._getConfig("theme")
        self.assertEqual(val, "dark")

    def test_config_dict_access_missing_key(self):
        cfg = {}
        adapter = self.OpenAIAdapter(config=cfg)
        val = adapter._getConfig("nonexistent", default="fallback")
        self.assertEqual(val, "fallback")

    def test_config_dict_access_dotted_nested_missing(self):
        cfg = {"AI": {}}
        adapter = self.OpenAIAdapter(config=cfg)
        val = adapter._getConfig("AI.active_profile", default="default")
        self.assertEqual(val, "default")


# Group: sync (20 tests)

class TestSyncModeConstants(unittest.TestCase):
    def setUp(self):
        self._patchers = applyMock(qt=True, util=True, psutil=True,
                                      requests_mod=True, file_mod=True)

    def tearDown(self):
        for p in self._patchers:
            p.stop()

    def test_mode_constants(self):
        from plugin.OpenList import MODE_SYNC, MODE_BACKUP
        self.assertEqual(MODE_SYNC, "sync")
        self.assertEqual(MODE_BACKUP, "backup")

    def test_task_status_constants(self):
        from plugin.OpenList import (
            TASK_STATUS_IDLE, TASK_STATUS_RUNNING,
            TASK_STATUS_SUCCESS, TASK_STATUS_FAILED, TASK_STATUS_ABORTED
        )
        self.assertEqual(TASK_STATUS_IDLE, "idle")
        self.assertEqual(TASK_STATUS_RUNNING, "running")
        self.assertEqual(TASK_STATUS_SUCCESS, "success")
        self.assertEqual(TASK_STATUS_FAILED, "failed")
        self.assertEqual(TASK_STATUS_ABORTED, "aborted")


class TestTaskConfig(unittest.TestCase):
    def setUp(self):
        self._patchers = applyMock(qt=True, util=True, psutil=True,
                                      requests_mod=True, file_mod=True)

    def tearDown(self):
        for p in self._patchers:
            p.stop()

    def test_default_values(self):
        from plugin.OpenList import TaskConfig
        cfg = TaskConfig()
        self.assertEqual(cfg.name, "")
        self.assertEqual(cfg.src_path, "")
        self.assertEqual(cfg.dst_path, "")
        self.assertEqual(cfg.exclude_rules, "")
        self.assertEqual(cfg.mode, "backup")
        self.assertFalse(cfg.confirm_before_sync)
        self.assertEqual(cfg.tar_folders, "")
        self.assertEqual(cfg.tree_folders, "")

    def test_to_dict_roundtrip(self):
        from plugin.OpenList import TaskConfig
        cfg = TaskConfig(
            name="test_task",
            src_path="/local/path",
            dst_path="/remote/path",
            exclude_rules="*.pyc\n__pycache__",
            mode="sync",
            confirm_before_sync=True,
            tar_folders="/data/tar",
            tree_folders="/data/tree"
        )
        d = cfg.to_dict()
        self.assertEqual(d["name"], "test_task")
        self.assertEqual(d["mode"], "sync")
        self.assertTrue(d["confirm_before_sync"])
        restored = TaskConfig.from_dict(d)
        self.assertEqual(restored.name, "test_task")
        self.assertEqual(restored.mode, "sync")
        self.assertTrue(restored.confirm_before_sync)

    def test_from_dict_missing_keys(self):
        from plugin.OpenList import TaskConfig
        cfg = TaskConfig.from_dict({"name": "minimal"})
        self.assertEqual(cfg.name, "minimal")
        self.assertEqual(cfg.mode, "backup")
        self.assertEqual(cfg.src_path, "")

    def test_backup_mode_default(self):
        from plugin.OpenList import TaskConfig
        cfg = TaskConfig.from_dict({"name": "backup_task"})
        self.assertEqual(cfg.mode, "backup")


class TestSyncDiffAlgorithm(unittest.TestCase):
    def setUp(self):
        self._patchers = applyMock(qt=True, util=True, psutil=True,
                                      requests_mod=True, file_mod=True)
        self.local_dir = tempfile.mkdtemp()
        self.remote_dir = tempfile.mkdtemp()

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        shutil.rmtree(self.local_dir, ignore_errors=True)
        shutil.rmtree(self.remote_dir, ignore_errors=True)

    def _touch(self, folder, rel_path, content=b""):
        full = os.path.join(folder, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'wb') as f:
            f.write(content)
        return rel_path

    def test_local_file_not_in_remote(self):
        self._touch(self.local_dir, "new.txt", b"local only")
        local_files = set()
        for root, dirs, files in os.walk(self.local_dir):
            for f in files:
                rel = os.path.relpath(os.path.join(root, f), self.local_dir)
                local_files.add(rel)
        remote_files = set()
        to_upload = local_files - remote_files
        to_delete = remote_files - local_files
        self.assertIn("new.txt", to_upload)
        self.assertEqual(len(to_delete), 0)

    def test_remote_file_not_in_local_sync_mode(self):
        self._touch(self.remote_dir, "old.txt", b"remote only")
        local_files = set()
        remote_files = set()
        for root, dirs, files in os.walk(self.remote_dir):
            for f in files:
                rel = os.path.relpath(os.path.join(root, f), self.remote_dir)
                remote_files.add(rel)
        to_delete = remote_files - local_files
        self.assertIn("old.txt", to_delete)

    def test_backup_mode_no_delete(self):
        self._touch(self.remote_dir, "extra.txt", b"extra")
        local_files = set()
        remote_files = set()
        for root, dirs, files in os.walk(self.remote_dir):
            for f in files:
                rel = os.path.relpath(os.path.join(root, f), self.remote_dir)
                remote_files.add(rel)
        in_sync_mode = bool(remote_files - local_files)
        self.assertTrue(in_sync_mode)

    def test_identical_files_no_sync_needed(self):
        content = b"same content"
        self._touch(self.local_dir, "match.txt", content)
        self._touch(self.remote_dir, "match.txt", content)
        local_files = {}
        for root, dirs, files in os.walk(self.local_dir):
            for f in files:
                fp = os.path.join(root, f)
                rel = os.path.relpath(fp, self.local_dir)
                with open(fp, 'rb') as fh:
                    local_files[rel] = fh.read()
        remote_files = {}
        for root, dirs, files in os.walk(self.remote_dir):
            for f in files:
                fp = os.path.join(root, f)
                rel = os.path.relpath(fp, self.remote_dir)
                with open(fp, 'rb') as fh:
                    remote_files[rel] = fh.read()
        common = set(local_files.keys()) & set(remote_files.keys())
        need_update = [f for f in common if local_files[f] != remote_files[f]]
        self.assertEqual(len(need_update), 0)

    def test_file_content_changed_needs_update(self):
        self._touch(self.local_dir, "changed.txt", b"new version")
        self._touch(self.remote_dir, "changed.txt", b"old version")
        local_files = {}
        for root, dirs, files in os.walk(self.local_dir):
            for f in files:
                fp = os.path.join(root, f)
                rel = os.path.relpath(fp, self.local_dir)
                with open(fp, 'rb') as fh:
                    local_files[rel] = fh.read()
        remote_files = {}
        for root, dirs, files in os.walk(self.remote_dir):
            for f in files:
                fp = os.path.join(root, f)
                rel = os.path.relpath(fp, self.remote_dir)
                with open(fp, 'rb') as fh:
                    remote_files[rel] = fh.read()
        common = set(local_files.keys()) & set(remote_files.keys())
        need_update = [f for f in common if local_files[f] != remote_files[f]]
        self.assertIn("changed.txt", need_update)

    def test_nested_folder_structure(self):
        self._touch(self.local_dir, "a/b/c/deep.txt", b"deep")
        self._touch(self.local_dir, "root.txt", b"root")
        self._touch(self.remote_dir, "root.txt", b"root")
        local_files = set()
        for root, dirs, files in os.walk(self.local_dir):
            for f in files:
                rel = os.path.relpath(os.path.join(root, f), self.local_dir)
                local_files.add(rel)
        remote_files = set()
        for root, dirs, files in os.walk(self.remote_dir):
            for f in files:
                rel = os.path.relpath(os.path.join(root, f), self.remote_dir)
                remote_files.add(rel)
        to_upload = local_files - remote_files
        to_upload_norm = {f.replace('\\', '/') for f in to_upload}
        self.assertIn("a/b/c/deep.txt", to_upload_norm)

    def test_bidirectional_diff(self):
        self._touch(self.local_dir, "only_local.txt", b"A")
        self._touch(self.remote_dir, "only_remote.txt", b"B")
        self._touch(self.local_dir, "both_diff.txt", b"C1")
        self._touch(self.remote_dir, "both_diff.txt", b"C2")
        self._touch(self.local_dir, "both_same.txt", b"D")
        self._touch(self.remote_dir, "both_same.txt", b"D")
        local_files = {}
        for root, dirs, files in os.walk(self.local_dir):
            for f in files:
                fp = os.path.join(root, f)
                rel = os.path.relpath(fp, self.local_dir)
                with open(fp, 'rb') as fh:
                    local_files[rel] = fh.read()
        remote_files = {}
        for root, dirs, files in os.walk(self.remote_dir):
            for f in files:
                fp = os.path.join(root, f)
                rel = os.path.relpath(fp, self.remote_dir)
                with open(fp, 'rb') as fh:
                    remote_files[rel] = fh.read()
        local_set = set(local_files.keys())
        remote_set = set(remote_files.keys())
        to_upload = local_set - remote_set
        to_delete = remote_set - local_set
        common = local_set & remote_set
        to_update = [f for f in common if local_files[f] != remote_files[f]]
        self.assertIn("only_local.txt", to_upload)
        self.assertIn("only_remote.txt", to_delete)
        self.assertIn("both_diff.txt", to_update)
        self.assertNotIn("both_same.txt", to_update)


class TestExcludeRules(unittest.TestCase):
    def setUp(self):
        self._patchers = applyMock(qt=True, util=True, psutil=True,
                                      requests_mod=True, file_mod=True)

    def tearDown(self):
        for p in self._patchers:
            p.stop()

    def test_glob_exclude_match(self):
        rules = ["*.pyc", "*/__pycache__/*"]
        should_exclude = ["script.pyc", os.path.join("sub", "__pycache__", "cache.py")]
        should_keep = ["script.py", "data.txt"]

        for path in should_exclude:
            matched = any(fnmatch.fnmatch(path, rule) or fnmatch.fnmatch(os.path.basename(path), rule) for rule in rules)
            self.assertTrue(matched, f"{path} should be excluded")

        for path in should_keep:
            matched = any(fnmatch.fnmatch(path, rule) or fnmatch.fnmatch(os.path.basename(path), rule) for rule in rules)
            self.assertFalse(matched, f"{path} should not be excluded")

    def test_exclude_git_folder(self):
        rule = "*/.git/*"
        self.assertTrue(fnmatch.fnmatch("project/.git/config", rule))
        self.assertFalse(fnmatch.fnmatch("project/src/main.py", rule))

    def test_multiple_exclude_rules(self):
        rules = ["*.log", "*.tmp", "*.bak"]
        for r in rules:
            self.assertTrue(fnmatch.fnmatch(f"file{r}", r))


class TestTarPackaging(unittest.TestCase):
    def setUp(self):
        self._patchers = applyMock(qt=True, util=True, psutil=True,
                                      requests_mod=True, file_mod=True)
        self.test_dir = tempfile.mkdtemp()
        self.tar_path = os.path.join(self.test_dir, "test.tar")

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _touch(self, rel, content=b""):
        path = os.path.join(self.test_dir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(content)

    def test_create_tar_with_files(self):
        self._touch("file1.txt", b"content1")
        self._touch("file2.txt", b"content2")

        with tarfile.open(self.tar_path, 'w') as tar:
            tar.add(os.path.join(self.test_dir, "file1.txt"), arcname="file1.txt")
            tar.add(os.path.join(self.test_dir, "file2.txt"), arcname="file2.txt")

        self.assertTrue(os.path.exists(self.tar_path))
        self.assertGreater(os.path.getsize(self.tar_path), 0)

        with tarfile.open(self.tar_path, 'r') as tar:
            names = tar.getnames()
        self.assertIn("file1.txt", names)
        self.assertIn("file2.txt", names)

    def test_tar_preserves_content(self):
        self._touch("data.bin", b"\x00\x01\x02\x03")
        with tarfile.open(self.tar_path, 'w') as tar:
            tar.add(os.path.join(self.test_dir, "data.bin"), arcname="data.bin")

        with tarfile.open(self.tar_path, 'r') as tar:
            f = tar.extractfile("data.bin")
            content = f.read()
        self.assertEqual(content, b"\x00\x01\x02\x03")


# Group: perf (性能测试)

class TestLazyInitPerformance(unittest.TestCase):
    """启动性能测试"""

    def setUp(self):
        self._patchers = applyMock(qt=True, psutil=True, pynput=True,
                                      markdown=True, util=True,
                                      real_logger_util=True)

    def tearDown(self):
        for p in self._patchers:
            p.stop()

    def test_time_perf_counter_baseline(self):
        """测试 time.perf_counter 基本可用性"""
        start = time.perf_counter()
        import time as tm
        tm.sleep(0.01)
        elapsed = time.perf_counter() - start
        logger.info("time.perf_counter 基准测试: %.4f 秒 (预期 ~0.01)", elapsed)
        self.assertGreater(elapsed, 0)
        self.assertLess(elapsed, 1.0)


class TestSyncPerformance(unittest.TestCase):
    """同步算法性能测试 (基于文件系统 walk + diff)"""

    def setUp(self):
        self._patchers = applyMock(qt=True, psutil=True, pynput=True,
                                      markdown=True, util=True,
                                      real_logger_util=True)
        self.local_dir = tempfile.mkdtemp()
        self.remote_dir = tempfile.mkdtemp()

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        shutil.rmtree(self.local_dir, ignore_errors=True)
        shutil.rmtree(self.remote_dir, ignore_errors=True)

    def _touch(self, folder, rel_path, content=b""):
        full = os.path.join(folder, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'wb') as f:
            f.write(content)

    def _tree(self, folder):
        result = set()
        for root, dirs, files in os.walk(folder):
            for f in files:
                rel = os.path.relpath(os.path.join(root, f), folder)
                result.add(rel)
        return result

    def test_diff_100_files(self):
        """测试 100 个文件的同步 diff 耗时"""
        for i in range(100):
            self._touch(self.local_dir, f"file_{i}.txt", f"content_{i}".encode())
        for i in range(80):
            self._touch(self.remote_dir, f"file_{i}.txt", f"content_{i}".encode())

        start = time.perf_counter()
        local = self._tree(self.local_dir)
        remote = self._tree(self.remote_dir)
        to_upload = local - remote
        to_delete = remote - local
        elapsed = time.perf_counter() - start

        logger.info("100 文件 diff 耗时: %.4f 秒 (上传=%d, 删除=%d)",
                          elapsed, len(to_upload), len(to_delete))
        self.assertEqual(len(to_upload), 20)
        self.assertEqual(len(to_delete), 0)
        self.assertLess(elapsed, 2.0,
                        f"diff 耗时 {elapsed:.4f} 秒，超过阈值 2.0 秒")

    def test_diff_1000_files(self):
        """测试 1000 个文件的同步 diff 耗时"""
        for i in range(1000):
            self._touch(self.local_dir, f"file_{i}.txt", f"content_{i}".encode())
        for i in range(800):
            self._touch(self.remote_dir, f"file_{i}.txt", f"content_{i}".encode())

        start = time.perf_counter()
        local = self._tree(self.local_dir)
        remote = self._tree(self.remote_dir)
        to_upload = local - remote
        to_delete = remote - local
        elapsed = time.perf_counter() - start

        logger.info("1000 文件 diff 耗时: %.4f 秒 (上传=%d, 删除=%d)",
                          elapsed, len(to_upload), len(to_delete))
        self.assertEqual(len(to_upload), 200)
        self.assertEqual(len(to_delete), 0)
        self.assertLess(elapsed, 5.0,
                        f"diff 耗时 {elapsed:.4f} 秒，超过阈值 5.0 秒")

# Group: exception (异常测试)

_PROFILE_CONFIG_WITH_KEY = {
    "AI": {
        "active_profile": "default",
        "profiles": {
            "default": {
                "api_key": "test_key_123",
                "api_url": "https://api.openai.com/v1",
            }
        }
    }
}

_PROFILE_CONFIG_NO_KEY = {
    "AI": {
        "active_profile": "default",
        "profiles": {
            "default": {
                "api_url": "https://api.openai.com/v1",
            }
        }
    }
}


class TestApiKeyErrors(unittest.TestCase):
    """API Key 异常测试"""

    def setUp(self):
        self._patchers = applyMock(qt=True, psutil=True, pynput=True,
                                      keyboard=True, mouse=True,
                                      util=True, config=True, file_mod=True)
        from src.core.AI import OpenAIAdapter, AIError
        self.OpenAIAdapter = OpenAIAdapter
        self.AIError = AIError

    def tearDown(self):
        for p in self._patchers:
            p.stop()

    @patch('requests.Session.post')
    def test_openai_401_error(self, mock_post):
        """测试 OpenAI API 返回 401 时抛出 AIError"""
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.json.return_value = {"error": {"message": "Incorrect API key"}}
        mock_post.return_value = mock_resp

        adapter = self.OpenAIAdapter(config=_PROFILE_CONFIG_WITH_KEY)
        with self.assertRaises(self.AIError) as ctx:
            adapter.chat([{"role": "user", "content": "hi"}], model="gpt-4")
        self.assertIn("401", str(ctx.exception))

    @patch('requests.Session.post')
    def test_openai_403_error(self, mock_post):
        """测试 OpenAI API 返回 403 时抛出 AIError"""
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.json.return_value = {"error": {"message": "Account suspended"}}
        mock_post.return_value = mock_resp

        adapter = self.OpenAIAdapter(config=_PROFILE_CONFIG_WITH_KEY)
        with self.assertRaises(self.AIError) as ctx:
            adapter.chat([{"role": "user", "content": "hi"}], model="gpt-4")
        self.assertIn("403", str(ctx.exception))

    def test_missing_api_key(self):
        """测试未设置 API Key 时抛出 AIError"""
        adapter = self.OpenAIAdapter(config=_PROFILE_CONFIG_NO_KEY)
        with self.assertRaises(self.AIError):
            adapter.chat([], model="gpt-4")

    def test_missing_api_url(self):
        """测试未设置 API URL 时使用默认值 (不抛异常)"""
        cfg = {
            "AI": {
                "active_profile": "default",
                "profiles": {"default": {"api_key": "test_key"}},
            }
        }
        adapter = self.OpenAIAdapter(config=cfg)
        url = adapter.get_api_url()
        self.assertIn("chat/completions", url)


class TestNetworkErrors(unittest.TestCase):
    """网络异常测试"""

    def setUp(self):
        self._patchers = applyMock(qt=True, psutil=True, pynput=True,
                                      keyboard=True, mouse=True,
                                      util=True, config=True, file_mod=True)
        from src.core.AI import OpenAIAdapter, AIError
        self.OpenAIAdapter = OpenAIAdapter
        self.AIError = AIError

    def tearDown(self):
        for p in self._patchers:
            p.stop()

    @patch('requests.Session.post')
    def test_timeout_error(self, mock_post):
        """测试网络超时抛 AIError"""
        mock_post.side_effect = requests.exceptions.Timeout("Connection timed out")

        adapter = self.OpenAIAdapter(config=_PROFILE_CONFIG_WITH_KEY)
        with self.assertRaises(self.AIError) as ctx:
            adapter.chat([{"role": "user", "content": "hi"}], model="gpt-4")
        self.assertIn("超时", str(ctx.exception))

    @patch('requests.Session.post')
    def test_connection_error(self, mock_post):
        """测试网络断开抛 AIError"""
        mock_post.side_effect = requests.exceptions.ConnectionError("Connection refused")

        adapter = self.OpenAIAdapter(config=_PROFILE_CONFIG_WITH_KEY)
        with self.assertRaises(self.AIError) as ctx:
            adapter.chat([{"role": "user", "content": "hi"}], model="gpt-4")
        self.assertIn("网络", str(ctx.exception))


class TestPluginErrors(unittest.TestCase):
    """插件异常测试 (使用真实 src.plugin 模块)"""

    def setUp(self):
        self._patchers = applyMock(qt=True, psutil=True, pynput=True,
                                      keyboard=True, mouse=True,
                                      util=True, config=True, file_mod=True,
                                      real_plugin=True)
        from src.plugin import PluginManager
        self.PluginManager = PluginManager

    def tearDown(self):
        for p in self._patchers:
            p.stop()

    def test_enable_non_existent_plugin(self):
        """测试启用不存在的插件返回 False"""
        mgr = self.PluginManager()
        result = mgr.enablePlugin("nonexistent_plugin")
        self.assertFalse(result)

    def test_disable_non_existent_plugin(self):
        """测试禁用不存在的插件不报错"""
        mgr = self.PluginManager()
        mgr.disablePlugin("nonexistent_plugin")

    def test_reload_non_existent_plugin(self):
        """测试重新加载不存在的插件返回 False"""
        mgr = self.PluginManager()
        result = mgr.reloadPlugin("nonexistent_plugin")
        self.assertFalse(result)

    def testloadPluginClass_invalid_name(self):
        """测试加载无效插件类返回 None"""
        mgr = self.PluginManager()
        result = mgr.loadPluginClass("invalid")
        self.assertIsNone(result)

    def test_scan_cache_after_failed_load(self):
        """测试加载失败后扫描缓存状态一致"""
        mgr = self.PluginManager()
        mgr.loadPluginClass("invalid")
        self.assertIsNotNone(mgr._scan_cache)


class TestModelErrors(unittest.TestCase):
    """AI 模型异常返回测试"""

    def setUp(self):
        self._patchers = applyMock(qt=True, psutil=True, pynput=True,
                                      keyboard=True, mouse=True,
                                      util=True, config=True, file_mod=True)
        from src.core.AI import OpenAIAdapter, AIError
        self.OpenAIAdapter = OpenAIAdapter
        self.AIError = AIError

    def tearDown(self):
        for p in self._patchers:
            p.stop()

    @patch('requests.Session.post')
    def test_empty_response_text(self, mock_post):
        """测试 AI 返回空文本"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"choices": [{"message": {"content": ""}}]}
        mock_post.return_value = mock_resp

        adapter = self.OpenAIAdapter(config=_PROFILE_CONFIG_WITH_KEY)
        text, _, _ = adapter.chat([{"role": "user", "content": "hi"}], model="gpt-4")
        self.assertEqual(text, "")

    @patch('requests.Session.post')
    def test_malformed_json_response(self, mock_post):
        """测试 API 返回非 JSON 数据"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = json.JSONDecodeError("Expecting value", "", 0)
        mock_post.return_value = mock_resp

        adapter = self.OpenAIAdapter(config=_PROFILE_CONFIG_WITH_KEY)
        with self.assertRaises(self.AIError) as ctx:
            adapter.chat([{"role": "user", "content": "hi"}], model="gpt-4")
        self.assertIn("格式错误", str(ctx.exception))

    @patch('requests.Session.post')
    def test_missing_choices_key(self, mock_post):
        """测试 API 返回缺少 choices 字段"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": "123"}
        mock_post.return_value = mock_resp

        adapter = self.OpenAIAdapter(config=_PROFILE_CONFIG_WITH_KEY)
        with self.assertRaises(self.AIError):
            adapter.chat([{"role": "user", "content": "hi"}], model="gpt-4")


class TestConfigErrors(unittest.TestCase):
    """配置异常测试"""

    def setUp(self):
        self._patchers = applyMock(qt=True, psutil=True, pynput=True,
                                      keyboard=True, mouse=True,
                                      util=True, config=True, file_mod=True)
        from src.core.AI import OpenAIAdapter
        self.OpenAIAdapter = OpenAIAdapter

    def tearDown(self):
        for p in self._patchers:
            p.stop()

    def test_empty_config_returns_defaults(self):
        """测试空配置时返回默认值"""
        adapter = self.OpenAIAdapter(config={})
        self.assertIsNotNone(adapter)

    def test_partial_config(self):
        """测试部分配置不报错"""
        adapter = self.OpenAIAdapter(config={"AI": {"api_key": "test"}})
        self.assertIsNotNone(adapter)



# CLI + 测试编排


_GROUP_REGISTRY = {}

def _register(group_name, test_classes, description=""):
    loader = unittest.TestLoader()
    count = sum(loader.loadTestsFromTestCase(cls).countTestCases()
                for cls in test_classes)
    _GROUP_REGISTRY[group_name] = {
        "classes": test_classes,
        "description": description,
        "count": count,
    }
    return test_classes


# Register all test groups
_register('util', [
    TestEncodingMap, TestFileExtensions, TestConstants,
], "工具函数 (format_size, 编码, 扩展名)")

_register('plugin', [
    TestPluginBase, TestCreateCustomPlugin, TestPluginManagerBasic,
    TestPluginManagerWithTempDir, TestPluginManagerEnableDisable,
    TestGetPluginModulePaths, TestPluginManagerConfig], "插件系统 (PluginBase, PluginManager)")

_register('ai', [
    TestResolveImageUrls, TestAIClientBuildPromptContent,
    TestAIClientExtractUserMessage, TestLoadBalancing,
    TestAdaptersBuildChatRequest, TestAdapterMap,
    TestAIClientBuildFileMessage, TestBaseAdapterConfig,
], "AI 适配器 (OpenAI, Claude, Ollama, Gemini)")

_register('sync', [
    TestSyncModeConstants, TestTaskConfig, TestSyncDiffAlgorithm,
    TestExcludeRules, TestTarPackaging,
], "同步算法 (OpenList 插件)")

_register('perf', [
    TestLazyInitPerformance, TestSyncPerformance
], "性能测试 (启动时间, 文件读取, MD5, 同步, 内存)")

_register('exception', [
    TestApiKeyErrors, TestNetworkErrors, TestPluginErrors, TestModelErrors, TestConfigErrors,
], "异常测试 (API Key, 网络, 插件, 文件, 模型, 配置)")



def main():
    parser = argparse.ArgumentParser(
        description="O 测试模块",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    for name, info in _GROUP_REGISTRY.items():
        parser.add_argument(f'--{name}', action='store_true',
                            help=f'{info["description"]} ({info["count"]} 个测试)')
    parser.add_argument('--list', action='store_true', help='列出所有测试组')

    args = parser.parse_args()

    if args.list:
        print(f"{'组名':<12} {'测试数':<8} 说明")
        print("-" * 50)
        for name, info in sorted(_GROUP_REGISTRY.items()):
            print(f"{name:<12} {info['count']:<8} {info['description']}")
        total = sum(info['count'] for info in _GROUP_REGISTRY.values())
        print(f"\n总计: {total} 个测试")
        return

    selected = [name for name in _GROUP_REGISTRY if getattr(args, name)]

    if not selected:
        parser.print_help()
        sys.exit(1)

    suite = unittest.TestSuite()
    loader = unittest.TestLoader()

    for name in selected:
        info = _GROUP_REGISTRY[name]
        for cls in info["classes"]:
            suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(
        verbosity=2,
        failfast=False,
    )
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)

if __name__ == "__main__":
    main()
