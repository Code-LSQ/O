"""测试模块，始终详细输出

用法:
    --util                    仅 util 测试
    --plugin                  仅 plugin 测试
    --ai                      仅 AI adapter 测试
    --sync                    仅 sync 测试
    --perf                    性能测试
    --exception               异常测试
    --all                     运行全部测试
    -m                 启动程序本体并启用 tracemalloc 内存追踪 + Qt 对象计数
    -h, --help                帮助


维护说明:
    - 所有 TestCase 统一使用 applyMock() 工厂注入 mock 依赖，
      setUp() 中调用 addCleanup(p.stop) 管理 patchers，禁止使用 tearDown 手工 stop。
    - 临时目录在 setUp() 中创建，通过 addCleanup 注册清理，禁止 try/finally。
    - 组内多个 TestCase 共享同一 mock 组合时，创建 _GroupNameBase 基类避免重复。
    - 辅助方法统一使用小驼峰命名法（camelCase）
    - 测试类在类级 import 模块和函数，后续通过 self.xxx 访问
    - 继承 Base TestCase 的子类如需额外初始化，必须先调用 super().setUp()，确保基类 mock 已就位。
新增分组:
    1. 在 _GROUP_REGISTRY 注册 (group_name, test_class_list)
    2. 按需调用 applyMock(qt=True, util=True, ...) 注入依赖
    3. main() 自动注册 --group_name CLI 参数

"""

import os
import gc
import sys
import argparse
import json
import shutil
import tempfile
import time
import types
import unittest
import fnmatch
import tarfile
import zipfile
import tracemalloc
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests
from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.util import logger

# Mock 基础设施 — 统一工厂


def _makeModule(name, **attrs):
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
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        self._init(*args, **kwargs)

    def _init(self, *args, **kwargs):
        pass


_SHARED_UTIL_ATTRS = {
    "Singleton": _MockSingleton,
    "logger": MagicMock(),
    "APP_NAME": "O",
    "EXTENSION": {
        "TXET": {".txt", ".py", ".js", ".json"},
        "Markdown": {".md", ".markdown"},
        "IMAGE": {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp"},
        "ZIP": {".zip", ".jar", ".apk"},
        "TAR": {".tar", ".tgz"},
        "ARCHIVE": {".gz", ".bz2", ".xz", ".7z", ".rar"},
        "AUDIO": {".mp3", ".wav", ".flac"},
        "VIDEO": {".mp4", ".avi", ".mkv"},
        "EXECUTE": {".exe", ".dll", ".bin"},
        "DOCUMENT": {".pdf", ".epub"},
        "FONT": {".ttf", ".otf"},
        "DATABASE": {".db", ".sqlite"},
        "DISK": {".iso"},
    },
    "TEXT_EXTENSIONS": {".txt", ".md", ".py", ".js", ".json", ".html", ".css", ".xml"},
    "TAR_EXTENSIONS": {".tar", ".tar.gz", ".tgz"},
    "ENCODING_MAP": {"UTF-8": "utf-8", "GBK": "gb18030", "Shift-JIS": "shift_jis"},
    "formatFileSize": MagicMock(return_value="1.0 KB"),
    "folderLastModified": MagicMock(return_value=0),
    "parseMtime": lambda x: x,
    "getFilePath": MagicMock(),
    "imageBase64": MagicMock(return_value=("image/png", "base64data")),
    "messageBox": MagicMock(),
    "dialogBox": MagicMock(),
    "data_dir": MagicMock(),
    "plugin_dir": MagicMock(),
}


def _makeQtCore():
    """Create Qt mock modules (PySide6 / QtCore / QtGui / QtWidgets)"""
    return {
        "PySide6": MagicMock(),
        "PySide6.QtCore": _makeModule(
            "PySide6.QtCore",
            QThread=MagicMock,
            Signal=MagicMock,
            QObject=MagicMock,
            Qt=MagicMock(),
            QTimer=MagicMock,
            QThreadPool=MagicMock,
            QRunnable=MagicMock,
            Slot=MagicMock,
            QUrl=MagicMock,
            QMetaObject=MagicMock,
            QSize=MagicMock,
            QEvent=MagicMock,
            QFileInfo=MagicMock,
        ),
        "PySide6.QtGui": MagicMock(),
        "PySide6.QtWidgets": MagicMock(),
    }


def _makeUtil():
    """Create src.util mock module (auto-fills missing attrs via MagicMock)"""
    mod = MagicMock(name="src.util")
    for k, v in _SHARED_UTIL_ATTRS.items():
        setattr(mod, k, v)
    return {"src.util": mod}


def _makeFile():
    """Create src.file mock module"""
    return {
        "src.file": _makeModule(
            "src.file",
            FileSelect=MagicMock(),
            format_file_size=MagicMock(return_value="1.0 KB"),
            fileTree=MagicMock(return_value=[]),
            filterFiles=MagicMock(return_value=[]),
        )
    }


def _makeConfig():
    """Create src.config mock module"""
    return {
        "src.config": _makeModule(
            "src.config",
            getConfig=MagicMock(return_value={}),
        )
    }


def applyMock(
    *,
    qt=True,
    psutil=False,
    pynput=False,
    keyboard=False,
    mouse=False,
    requests_mod=False,
    markdown=False,
    util=False,
    real_logger_util=False,
    file_mod=False,
    config=False,
    real_plugin=False,
):
    """按需组装 mock 环境，每个 setUp() 中调用

    返回 patcher 列表，调用方需在 addCleanup 中对每个 patcher 执行 stop()。
    """
    patchers = []

    if qt:
        p = patch.dict("sys.modules", _makeQtCore())
        p.start()
        patchers.append(p)

    if psutil:
        p = patch.dict("sys.modules", {"psutil": MagicMock()})
        p.start()
        patchers.append(p)

    if pynput:
        p = patch.dict("sys.modules", {"pynput": MagicMock()})
        p.start()
        patchers.append(p)

    if keyboard:
        p = patch.dict("sys.modules", {"pynput.keyboard": MagicMock()})
        p.start()
        patchers.append(p)

    if mouse:
        p = patch.dict("sys.modules", {"pynput.mouse": MagicMock()})
        p.start()
        patchers.append(p)

    if requests_mod:
        p = patch.dict("sys.modules", {"requests": MagicMock()})
        p.start()
        patchers.append(p)

    if markdown:
        p = patch.dict("sys.modules", {"markdown": MagicMock()})
        p.start()
        patchers.append(p)

    if util:
        if real_logger_util:
            import importlib

            real_util = importlib.import_module("src.util")
            attrs = dict(_SHARED_UTIL_ATTRS)
            attrs["logger"] = real_util.logger
            attrs["getTimestamp"] = MagicMock(return_value="2025-01-01 00:00:00")
            p = patch.dict("sys.modules", {"src.util": _makeModule("src.util", **attrs)})
        else:
            p = patch.dict("sys.modules", _makeUtil())
        p.start()
        patchers.append(p)

    if file_mod:
        p = patch.dict("sys.modules", _makeFile())
        p.start()
        patchers.append(p)

    if config:
        p = patch.dict("sys.modules", _makeConfig())
        p.start()
        patchers.append(p)

    if real_plugin:
        if "src.plugin" in sys.modules:
            del sys.modules["src.plugin"]
        import src.plugin

        p = patch.dict("sys.modules", {"src.plugin": src.plugin})
        p.start()
        patchers.append(p)

    return patchers


# Group: util (12 tests)


class TestEncodingMap(unittest.TestCase):
    def setUp(self):
        for p in applyMock(util=True):
            self.addCleanup(p.stop)

    def testEncodingMapContainsCommon(self):
        encodings = sys.modules["src.util"].ENCODING_MAP
        self.assertIn("UTF-8", encodings)
        self.assertIn("GBK", encodings)
        self.assertEqual(encodings["UTF-8"], "utf-8")

    def testEncodingMapValuesAreStrings(self):
        for key, val in sys.modules["src.util"].ENCODING_MAP.items():
            self.assertIsInstance(key, str)
            self.assertIsInstance(val, str)


class TestFileExtensions(unittest.TestCase):
    def setUp(self):
        for p in applyMock(util=True):
            self.addCleanup(p.stop)

    def testTextExtensionsContainCommon(self):
        exts = sys.modules["src.util"].TEXT_EXTENSIONS
        for ext in [".txt", ".md", ".py", ".json"]:
            self.assertIn(ext, exts)


class TestConstants(unittest.TestCase):
    def setUp(self):
        for p in applyMock(util=True):
            self.addCleanup(p.stop)

    def testAppName(self):
        self.assertEqual(sys.modules["src.util"].APP_NAME, "O")


# Group: plugin (48 tests)


class _PluginTestBase(unittest.TestCase):
    from src.plugin import PluginManager, PluginBase

    def setUp(self):
        for p in applyMock(qt=True, util=True, psutil=True, pynput=True, real_plugin=True):
            self.addCleanup(p.stop)


class TestPluginBase(_PluginTestBase):

    def testDefaultAttributes(self):
        plugin = self.PluginBase()
        self.assertEqual(plugin.name, "Unnamed Plugin")
        self.assertEqual(plugin.description, "")
        self.assertEqual(plugin.version, "1.0.0")
        self.assertEqual(plugin.author, "")
        self.assertFalse(plugin.enabled)
        self.assertEqual(plugin.settings, {})

    def testInitializeCalledWithoutError(self):
        plugin = self.PluginBase()
        plugin.initialize()

    def testGetActionReturnsNoneByDefault(self):
        plugin = self.PluginBase()
        self.assertIsNone(plugin.getAction())

    def testLoadConfig(self):
        plugin = self.PluginBase()
        plugin.loadConfig()
        self.assertEqual(plugin.settings, {})

    def testSaveConfig(self):
        plugin = self.PluginBase()
        plugin.settings = {"a": 1}
        result = plugin.saveConfig()
        self.assertEqual(result, {"a": 1})

    def testOnFileOpenDoesNotRaise(self):
        plugin = self.PluginBase()
        plugin.onFileOpen("/some/path")

    def testCleanupDoesNotRaise(self):
        plugin = self.PluginBase()
        plugin.cleanup()

    def testMainWindowNoneByDefault(self):
        plugin = self.PluginBase()
        self.assertIsNone(plugin.main_window)

    def testMainWindowSetInInit(self):
        mw = MagicMock()
        plugin = self.PluginBase(main_window=mw)
        self.assertEqual(plugin.main_window, mw)

    def testCustomName(self):
        class TestPlugin(self.PluginBase):
            name = "MyPlugin"
            description = "A test plugin"

        plugin = TestPlugin()
        self.assertEqual(plugin.name, "MyPlugin")
        self.assertEqual(plugin.description, "A test plugin")


class TestCreateCustomPlugin(_PluginTestBase):

    def testPluginWithCustomGetAction(self):
        class MenuPlugin(self.PluginBase):
            name = "MenuPlugin"

            def getAction(self):
                return "menu_action"

        plugin = MenuPlugin()
        self.assertEqual(plugin.getAction(), "menu_action")

    def testPluginLifecycleMethods(self):
        calls = []

        class LifecyclePlugin(self.PluginBase):
            name = "LifecyclePlugin"

            def __init__(self, main_window=None):
                super().__init__(main_window)
                self.initialized = False

            def initialize(self):
                calls.append("initialize")

            def cleanup(self):
                calls.append("cleanup")

        plugin = LifecyclePlugin()
        plugin.initialize()
        plugin.cleanup()
        self.assertEqual(calls, ["initialize", "cleanup"])


class TestPluginManagerBasic(_PluginTestBase):

    def testSingletonPattern(self):
        pm1 = self.PluginManager()
        pm2 = self.PluginManager()
        self.assertIs(pm1, pm2)

    def testSingletonThroughGetPluginManager(self):
        from src.plugin import getPluginManager

        pm1 = getPluginManager()
        pm2 = getPluginManager()
        self.assertIs(pm1, pm2)

    def testInitialState(self):
        pm = self.PluginManager()
        self.assertEqual(pm.plugins, {})

    def testInitialAttributesExist(self):
        pm = self.PluginManager()
        self.assertTrue(hasattr(pm, "enabled_plugins"))
        self.assertTrue(hasattr(pm, "plugin_dir"))

    def testMainWindowSettable(self):
        mw = MagicMock()
        pm = self.PluginManager(main_window=mw)
        self.assertEqual(pm.main_window, mw)


class _PluginWithTempDirBase(_PluginTestBase):
    def setUp(self):
        super().setUp()
        self.temp_plugin_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.temp_plugin_dir, ignore_errors=True))
        self.pm = self.PluginManager()
        self.pm.plugin_dir = Path(self.temp_plugin_dir)

    def _createPluginFile(self, name, content):
        path = os.path.join(self.temp_plugin_dir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path


class TestPluginManagerWithTempDir(_PluginWithTempDirBase):

    def testScanEmptyDirReturnsEmpty(self):
        plugins = self.pm.scanPlugins()
        self.assertEqual(plugins, [])

    def testSkipInitFiles(self):
        self._createPluginFile("__init__.py", "")
        self._createPluginFile("_hidden.py", "")
        plugins = self.pm.scanPlugins()
        self.assertEqual(plugins, [])

    def testScanValidPluginFile(self):
        self._createPluginFile(
            "my_plugin.py",
            """
from src.plugin import PluginBase

class MyPlugin(PluginBase):
    name = "MyPlugin"
    description = "Test"
""",
        )
        plugins = self.pm.scanPlugins()
        self.assertIn("my_plugin", plugins)

    def testScanPluginMissingBaseClass(self):
        self._createPluginFile(
            "bad.py",
            """
class NotAPlugin:
    pass
""",
        )
        result = self.pm.scanPlugins()
        self.assertIn("bad", result)
        self.assertFalse(self.pm.enablePlugin("bad"))

    def testEnableUnknownPluginReturnsFalse(self):
        result = self.pm.enablePlugin("nonexistent")
        self.assertFalse(result)


class TestPluginManagerEnableDisable(_PluginWithTempDirBase):

    def testEnableDisableCycle(self):
        self._createPluginFile(
            "cycle_plugin.py",
            """
from src.plugin import PluginBase

class CyclePlugin(PluginBase):
    name = "CyclePlugin"
""",
        )
        self.pm.scanPlugins()
        result = self.pm.enablePlugin("cycle_plugin")
        self.assertTrue(result)
        self.assertTrue(self.pm.isPluginEnabled("cycle_plugin"))
        self.assertIn("cycle_plugin", self.pm.plugins)
        self.pm.disablePlugin("cycle_plugin")
        self.assertFalse(self.pm.isPluginEnabled("cycle_plugin"))
        self.assertNotIn("cycle_plugin", self.pm.plugins)

    def testEnableTwiceReturnsTrue(self):
        self._createPluginFile(
            "dup.py",
            """
from src.plugin import PluginBase

class DupPlugin(PluginBase):
    name = "DupPlugin"
""",
        )
        self.pm.scanPlugins()
        self.assertTrue(self.pm.enablePlugin("dup"))
        self.assertTrue(self.pm.enablePlugin("dup"))

    def testDisableNotEnabledDoesNotRaise(self):
        self.pm.disablePlugin("not_enabled")

    def testGetAllPlugins(self):
        self._createPluginFile(
            "p1.py",
            """
from src.plugin import PluginBase

class P1(PluginBase):
    name = "Plugin1"
""",
        )
        self.pm.scanPlugins()
        self.pm.enablePlugin("p1")
        all_p = self.pm.allPlugins()
        self.assertIn("p1", all_p)

    def testGetEnabledPlugins(self):
        self._createPluginFile(
            "en.py",
            """
from src.plugin import PluginBase

class En(PluginBase):
    name = "EnabledPlugin"
""",
        )
        self.pm.scanPlugins()
        self.pm.enablePlugin("en")
        self.assertTrue(self.pm.isPluginEnabled("en"))


class TestGetPluginModulePaths(_PluginTestBase):

    def testNonexistentDirReturnsEmpty(self):
        pm = self.PluginManager()
        pm.plugin_dir = Path(r"C:\nonexistent_xyz_plugin_dir")
        result = pm.scanPlugins()
        self.assertEqual(result, [])


class TestPluginManagerConfig(_PluginWithTempDirBase):
    def _makeConfig(self, data=None):
        config = MagicMock()
        config_data = data or {}

        def getSideEffect(key, default=None):
            return config_data.get(key, default)

        config.get.side_effect = getSideEffect

        def setSideEffect(key, value):
            config_data[key] = value

        config.set.side_effect = setSideEffect
        return config, config_data

    def testInitConfigLoadsEnabledPlugins(self):
        config, _ = self._makeConfig({"Plugin": {"p1": {"enabled": True}}})
        self._createPluginFile(
            "p1.py",
            """
from src.plugin import PluginBase

class P1(PluginBase):
    name = "P1"
    description = "Test plugin"
""",
        )
        self.pm.initConfig(config)
        self.assertTrue(self.pm.isPluginEnabled("p1"))
        self.assertIn("p1", self.pm.plugins)

    def testInitConfigDefaultEnabled(self):
        config, _ = self._makeConfig({"Plugin": {}})
        self._createPluginFile(
            "p1.py",
            """
from src.plugin import PluginBase

class P1(PluginBase):
    name = "P1"
""",
        )
        self.pm.initConfig(config)
        self.assertTrue(self.pm.isPluginEnabled("p1"))

    def testSaveConfigWritesEnabledAndSettings(self):
        config, config_data = self._makeConfig()
        self.pm.enabled_plugins = {"p1": True}
        self.pm._scan_cache = {"p1": ("plugin.p1", Path("p1.py"), None)}
        self.pm.plugins["p1"] = MagicMock()
        self.pm.plugins["p1"].settings = {"key": "val"}
        self.pm.saveConfig(config)
        self.assertIn("Plugin", config_data)
        self.assertEqual(config_data["Plugin"]["p1"]["enabled"], True)
        self.assertEqual(config_data["Plugin"]["p1"]["key"], "val")


# Group: ai (61 tests)


class _AITestBase(unittest.TestCase):
    def setUp(self):
        for p in applyMock(
            qt=True, util=True, pynput=True, keyboard=True, mouse=True, config=True, file_mod=True
        ):
            self.addCleanup(p.stop)


class TestResolveImageUrls(_AITestBase):
    def setUp(self):
        super().setUp()
        from src.core.AI import resolveImageUrls

        self.resolveImageUrls = resolveImageUrls

    def testNoImagesNoChange(self):
        messages = [{"role": "user", "content": "hello"}]
        result = self.resolveImageUrls(messages)
        self.assertEqual(result, messages)

    def testNonFileUrlNotTouched(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}}
                ],
            }
        ]
        original = json.loads(json.dumps(messages))
        self.resolveImageUrls(messages)
        self.assertEqual(messages, original)

    def testFileUrlNoRealFileLogsError(self):
        messages = [
            {
                "role": "user",
                "content": [{"type": "image_url", "url": "file:///nonexistent/img.png"}],
            }
        ]
        self.resolveImageUrls(messages)
        part = messages[0]["content"][0]
        self.assertEqual(part["type"], "text")
        self.assertIn("图片加载失败", part["text"])

    def testDifferentUrlKeyHandling(self):
        messages = [
            {
                "role": "user",
                "content": [{"type": "image_url", "url": "file:///nonexistent/img.png"}],
            }
        ]
        self.resolveImageUrls(messages)
        part = messages[0]["content"][0]
        self.assertEqual(part["type"], "text")

    def testNonDictPartIgnored(self):
        messages = [{"role": "user", "content": ["not a dict"]}]
        self.resolveImageUrls(messages)
        self.assertEqual(messages[0]["content"][0], "not a dict")

    def testNonImageTypeIgnored(self):
        messages = [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]
        self.resolveImageUrls(messages)
        self.assertEqual(messages[0]["content"][0]["text"], "hello")


class TestAIClientBuildPromptContent(_AITestBase):
    def setUp(self):
        super().setUp()
        from src.core.AI import getAIClient

        self.getAIClient = getAIClient

    def testWithRequestPlaceholder(self):
        client = self.getAIClient(config={})
        result = client._buildPromptContent("Please translate: {request}", "hello world")
        self.assertEqual(result, "Please translate: hello world")

    def testWithoutPlaceholderAppends(self):
        client = self.getAIClient(config={})
        result = client._buildPromptContent("You are a translator.", "hello world")
        self.assertEqual(result, "You are a translator.\n\nhello world")

    def testEmptyPrompt(self):
        client = self.getAIClient(config={})
        result = client._buildPromptContent("", "user text")
        self.assertEqual(result, "\n\nuser text")

    def testEmptyUserMessage(self):
        client = self.getAIClient(config={})
        result = client._buildPromptContent("prefix {request} suffix", "")
        self.assertEqual(result, "prefix  suffix")

    def testMultiplePlaceholders(self):
        client = self.getAIClient(config={})
        result = client._buildPromptContent("{request} and {request}", "text")
        self.assertEqual(result, "text and text")


class TestAIClientExtractUserMessage(_AITestBase):
    def setUp(self):
        super().setUp()
        from src.core.AI import getAIClient

        self.getAIClient = getAIClient

    def testSimpleTextMessage(self):
        client = self.getAIClient(config={})
        messages = [{"role": "user", "content": "hello"}]
        result = client._extractUserMessage(messages)
        self.assertEqual(result, "hello")

    def testLastUserMessage(self):
        client = self.getAIClient(config={})
        messages = [
            {"role": "assistant", "content": "response"},
            {"role": "user", "content": "final question"},
        ]
        result = client._extractUserMessage(messages)
        self.assertEqual(result, "final question")

    def testMultipartContent(self):
        client = self.getAIClient(config={})
        messages = [
            {
                "role": "user",
                "content": [{"type": "text", "text": "part1"}, {"type": "text", "text": "part2"}],
            }
        ]
        result = client._extractUserMessage(messages)
        self.assertEqual(result, "part1\npart2")

    def testEmptyMessages(self):
        client = self.getAIClient(config={})
        result = client._extractUserMessage([])
        self.assertEqual(result, "")

    def testNoUserMessage(self):
        client = self.getAIClient(config={})
        messages = [{"role": "assistant", "content": "only assistant"}]
        result = client._extractUserMessage(messages)
        self.assertEqual(result, "")

    def testMixedContentTypesInMultipart(self):
        client = self.getAIClient(config={})
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "text part"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,xyz"}},
                    {"type": "text", "text": "more text"},
                ],
            }
        ]
        result = client._extractUserMessage(messages)
        self.assertIn("text part", result)
        self.assertIn("more text", result)
        self.assertNotIn("image", result)


class TestLoadBalancing(_AITestBase):
    def setUp(self):
        super().setUp()
        from src.core.AI import getAIClient

        self.getAIClient = getAIClient
        client = self.getAIClient(config={})
        client.__class__._lb_failures = {}
        client.__class__._lb_disabled = {}
        self.addCleanup(self._cleanLb)

    def _cleanLb(self):
        from src.core.AI import AIClient
        AIClient._lb_failures = {}
        AIClient._lb_disabled = {}

    def testLbDisabledNoGroups(self):
        config = {"load_balance": {"enabled": False}}
        client = self.getAIClient(config=config)
        result = client._lbPickGroups()
        self.assertIsNone(result)

    def testLbDisabledNoConfig(self):
        config = {}
        client = self.getAIClient(config=config)
        result = client._lbPickGroups()
        self.assertIsNone(result)

    def testLbSingleProfile(self):
        config = {
            "load_balance": {
                "enabled": True,
                "profiles": {"DeepSeek": {"priority": 1, "weight": 1}},
            }
        }
        client = self.getAIClient(config=config)
        result = client._lbPickGroups()
        self.assertEqual(result, [["DeepSeek"]])

    def testLbMultipleProfilesSamePriority(self):
        config = {
            "load_balance": {
                "enabled": True,
                "profiles": {"A": {"priority": 1, "weight": 1}, "B": {"priority": 1, "weight": 1}},
            }
        }
        client = self.getAIClient(config=config)
        result = client._lbPickGroups()
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]), 2)
        self.assertIn("A", result[0])
        self.assertIn("B", result[0])

    def testLbDifferentPriorities(self):
        config = {
            "load_balance": {
                "enabled": True,
                "profiles": {
                    "Primary": {"priority": 1, "weight": 1},
                    "Secondary": {"priority": 2, "weight": 1},
                },
            }
        }
        client = self.getAIClient(config=config)
        result = client._lbPickGroups()
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], ["Primary"])
        self.assertEqual(result[1], ["Secondary"])

    def testLbPriorityZeroDisabled(self):
        config = {
            "load_balance": {
                "enabled": True,
                "profiles": {
                    "Disabled": {"priority": 0, "weight": 1},
                    "Active": {"priority": 1, "weight": 1},
                },
            }
        }
        client = self.getAIClient(config=config)
        result = client._lbPickGroups()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], ["Active"])

    def testLbFailureTracking(self):
        client = self.getAIClient(config={})

        self.assertFalse(client.__class__._lb_disabled.get("Test", False))

        client._lbRecord("Test", False)
        self.assertEqual(client.__class__._lb_failures.get("Test"), 1)
        self.assertFalse(client.__class__._lb_disabled.get("Test", False))

        client._lbRecord("Test", False)
        client._lbRecord("Test", False)
        self.assertTrue(client.__class__._lb_disabled.get("Test", False))

    def testLbRecoveryAfterSuccess(self):
        client = self.getAIClient(config={})

        client._lbRecord("Test", False)
        client._lbRecord("Test", False)
        client._lbRecord("Test", False)
        self.assertTrue(client.__class__._lb_disabled.get("Test", False))

        client._lbRecord("Test", True)
        self.assertFalse(client.__class__._lb_disabled.get("Test", False))
        self.assertNotIn("Test", client.__class__._lb_failures)

    def testLbDisabledProfileExcluded(self):
        config = {
            "load_balance": {
                "enabled": True,
                "profiles": {
                    "Bad": {"priority": 1, "weight": 1},
                    "Good": {"priority": 1, "weight": 1},
                },
            }
        }
        client = self.getAIClient(config=config)
        client.__class__._lb_disabled = {"Bad": True}
        result = client._lbPickGroups()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], ["Good"])

    def testLbAllDisabledReturnsNone(self):
        config = {
            "load_balance": {"enabled": True, "profiles": {"A": {"priority": 1, "weight": 1}}}
        }
        client = self.getAIClient(config=config)
        client.__class__._lb_disabled = {"A": True}
        result = client._lbPickGroups()
        self.assertIsNone(result)


class TestAdaptersBuildChatRequest(_AITestBase):
    def setUp(self):
        super().setUp()
        from src.core.AI import OpenAIAdapter, ClaudeAdapter, OllamaAdapter, GeminiAdapter, AIError

        self.OpenAIAdapter = OpenAIAdapter
        self.ClaudeAdapter = ClaudeAdapter
        self.OllamaAdapter = OllamaAdapter
        self.GeminiAdapter = GeminiAdapter
        self.AIError = AIError

    def testOpenaiAdapterBuildRequest(self):
        adapter = self.OpenAIAdapter(
            config={}, api_key="test-key", api_url="https://api.openai.com"
        )
        messages = [{"role": "user", "content": "hello"}]
        request = adapter.buildChatRequest("gpt-4", messages, 0.7, 2000)
        self.assertEqual(request["model"], "gpt-4")
        self.assertEqual(request["messages"], messages)
        self.assertEqual(request["temperature"], 0.7)
        self.assertEqual(request["max_tokens"], 2000)

    def testOpenaiAdapterHeaders(self):
        adapter = self.OpenAIAdapter(config={}, api_key="sk-test", api_url="https://api.openai.com")
        headers = adapter.getHeaders("sk-test")
        self.assertEqual(headers["Authorization"], "Bearer sk-test")
        self.assertEqual(headers["Content-Type"], "application/json")

    def testOpenaiAdapterApiUrl(self):
        adapter = self.OpenAIAdapter(config={}, api_key="key", api_url="https://api.deepseek.com")
        url = adapter.getApiUrl()
        self.assertEqual(url, "https://api.deepseek.com/chat/completions")

    def testOpenaiAdapterParseResponse(self):
        adapter = self.OpenAIAdapter(config={}, api_key="key", api_url="https://api.openai.com")
        mock_response = MagicMock()
        mock_response.json.return_value = {"choices": [{"message": {"content": "Hello, world!"}}]}
        result = adapter.parseChatResponse(mock_response)
        self.assertEqual(result, "Hello, world!")

    def testOpenaiAdapterParseEmptyChoices(self):
        adapter = self.OpenAIAdapter(config={}, api_key="key", api_url="https://api.openai.com")
        mock_response = MagicMock()
        mock_response.json.return_value = {"choices": []}
        with self.assertRaises(self.AIError):
            adapter.parseChatResponse(mock_response)

    def testOpenaiAdapterParseUsage(self):
        adapter = self.OpenAIAdapter(config={}, api_key="key", api_url="https://api.openai.com")
        data = {"usage": {"prompt_tokens": 10, "completion_tokens": 20}}
        in_t, out_t = adapter.parseUsage(data)
        self.assertEqual(in_t, 10)
        self.assertEqual(out_t, 20)

    def testOpenaiAdapterParseUsageMissing(self):
        adapter = self.OpenAIAdapter(config={}, api_key="key", api_url="https://api.openai.com")
        in_t, out_t = adapter.parseUsage({})
        self.assertEqual(in_t, 0)
        self.assertEqual(out_t, 0)

    def testOpenaiAdapterGetModelsUrl(self):
        adapter = self.OpenAIAdapter(config={}, api_key="key", api_url="https://api.deepseek.com")
        url = adapter.getModelListUrl()
        self.assertEqual(url, "https://api.deepseek.com/models")

    def testClaudeAdapterBuildRequest(self):
        adapter = self.ClaudeAdapter(
            config={}, api_key="sk-ant-test", api_url="https://api.anthropic.com"
        )
        messages = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "hello"},
        ]
        request = adapter.buildChatRequest("claude-3-opus", messages, 0.5, 1000)
        self.assertEqual(request["model"], "claude-3-opus")
        self.assertNotIn("system", [m["role"] for m in request["messages"]])
        self.assertEqual(len(request["messages"]), 1)
        self.assertEqual(request["messages"][0]["role"], "user")

    def testClaudeAdapterHeaders(self):
        adapter = self.ClaudeAdapter(
            config={}, api_key="sk-ant-test", api_url="https://api.anthropic.com"
        )
        headers = adapter.getHeaders("sk-ant-test")
        self.assertEqual(headers["x-api-key"], "sk-ant-test")
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertIn("anthropic-version", headers)

    def testClaudeAdapterApiUrl(self):
        adapter = self.ClaudeAdapter(config={}, api_key="key", api_url="https://api.anthropic.com")
        url = adapter.getApiUrl()
        self.assertEqual(url, "https://api.anthropic.com/v1/messages")

    def testClaudeAdapterParseResponse(self):
        adapter = self.ClaudeAdapter(config={}, api_key="key", api_url="https://api.anthropic.com")
        mock_response = MagicMock()
        mock_response.json.return_value = {"content": [{"text": "Claude response"}]}
        result = adapter.parseChatResponse(mock_response)
        self.assertEqual(result, "Claude response")

    def testClaudeAdapterParseEmptyContent(self):
        adapter = self.ClaudeAdapter(config={}, api_key="key", api_url="https://api.anthropic.com")
        mock_response = MagicMock()
        mock_response.json.return_value = {}
        with self.assertRaises(self.AIError):
            adapter.parseChatResponse(mock_response)

    def testClaudeAdapterParseUsage(self):
        adapter = self.ClaudeAdapter(config={}, api_key="key", api_url="https://api.anthropic.com")
        data = {"usage": {"input_tokens": 15, "output_tokens": 25}}
        in_t, out_t = adapter.parseUsage(data)
        self.assertEqual(in_t, 15)
        self.assertEqual(out_t, 25)

    def testOllamaAdapterBuildRequestNoImages(self):
        adapter = self.OllamaAdapter(config={}, api_key="", api_url="http://127.0.0.1:11434")
        messages = [{"role": "user", "content": "hello"}]
        request = adapter.buildChatRequest("llama3", messages, 0.7, 2000)
        self.assertEqual(request["model"], "llama3")
        self.assertIn("options", request)
        self.assertEqual(request["options"]["num_predict"], 2000)

    def testOllamaAdapterHeaders(self):
        adapter = self.OllamaAdapter(config={}, api_key="", api_url="http://127.0.0.1:11434")
        headers = adapter.getHeaders("")
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertNotIn("Authorization", headers)

    def testOllamaAdapterApiUrl(self):
        adapter = self.OllamaAdapter(config={}, api_key="", api_url="http://127.0.0.1:11434")
        url = adapter.getApiUrl()
        self.assertEqual(url, "http://127.0.0.1:11434/api/chat")

    def testOllamaAdapterParseResponse(self):
        adapter = self.OllamaAdapter(config={}, api_key="", api_url="http://127.0.0.1:11434")
        mock_response = MagicMock()
        mock_response.json.return_value = {"message": {"content": "Ollama reply"}}
        result = adapter.parseChatResponse(mock_response)
        self.assertEqual(result, "Ollama reply")

    def testOllamaAdapterGetModelsUrl(self):
        adapter = self.OllamaAdapter(config={}, api_key="", api_url="http://127.0.0.1:11434")
        url = adapter.getModelListUrl()
        self.assertEqual(url, "http://127.0.0.1:11434/api/tags")

    def testGeminiAdapterBuildRequest(self):
        adapter = self.GeminiAdapter(
            config={}, api_key="gemini-key", api_url="https://generativelanguage.googleapis.com"
        )
        messages = [{"role": "user", "content": "hello"}]
        request = adapter.buildChatRequest("gemini-pro", messages, 0.7, 2000)
        self.assertIn("contents", request)
        self.assertIn("generationConfig", request)
        self.assertEqual(request["contents"][0]["role"], "user")
        self.assertEqual(request["generationConfig"]["temperature"], 0.7)
        self.assertEqual(request["generationConfig"]["maxOutputTokens"], 2000)

    def testGeminiAdapterRoleMapping(self):
        adapter = self.GeminiAdapter(
            config={}, api_key="key", api_url="https://generativelanguage.googleapis.com"
        )
        messages = [{"role": "assistant", "content": "response"}]
        request = adapter.buildChatRequest("gemini-pro", messages, 0.7, 2000)
        self.assertEqual(request["contents"][0]["role"], "model")

    def testGeminiAdapterParseResponse(self):
        adapter = self.GeminiAdapter(
            config={}, api_key="key", api_url="https://generativelanguage.googleapis.com"
        )
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": "Gemini response"}]}}]
        }
        result = adapter.parseChatResponse(mock_response)
        self.assertEqual(result, "Gemini response")

    def testGeminiAdapterEmptyCandidates(self):
        adapter = self.GeminiAdapter(
            config={}, api_key="key", api_url="https://generativelanguage.googleapis.com"
        )
        mock_response = MagicMock()
        mock_response.json.return_value = {"candidates": []}
        with self.assertRaises(self.AIError):
            adapter.parseChatResponse(mock_response)

    def testGeminiAdapterParseUsage(self):
        adapter = self.GeminiAdapter(
            config={}, api_key="key", api_url="https://generativelanguage.googleapis.com"
        )
        data = {"usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 10}}
        in_t, out_t = adapter.parseUsage(data)
        self.assertEqual(in_t, 5)
        self.assertEqual(out_t, 10)


class TestAdapterMap(_AITestBase):
    def setUp(self):
        super().setUp()
        from src.core.AI import (
            AI_ADAPTER,
            getAdapterEndpoint,
            OpenAIAdapter,
            ClaudeAdapter,
            OllamaAdapter,
            GeminiAdapter,
        )

        self.AI_ADAPTER = AI_ADAPTER
        self.getAdapterEndpoint = getAdapterEndpoint
        self.OpenAIAdapter = OpenAIAdapter
        self.ClaudeAdapter = ClaudeAdapter
        self.OllamaAdapter = OllamaAdapter
        self.GeminiAdapter = GeminiAdapter

    def testAdapterMapContainsKnownServices(self):
        names = [name for name, cls, url in self.AI_ADAPTER if cls is not None]
        expected = ["Claude", "DeepSeek", "Gemini", "Ollama", "OpenAI", "OpenRouter"]
        for name in expected:
            self.assertIn(name, names)

    def testGetAdapterEndpointReturnsCorrectType(self):
        adapter = self.getAdapterEndpoint("Claude", {}, api_key="key", api_url="url")
        self.assertIsInstance(adapter, self.ClaudeAdapter)

        adapter = self.getAdapterEndpoint("Ollama", {}, api_key="", api_url="url")
        self.assertIsInstance(adapter, self.OllamaAdapter)

        adapter = self.getAdapterEndpoint("Gemini", {}, api_key="key", api_url="url")
        self.assertIsInstance(adapter, self.GeminiAdapter)

        adapter = self.getAdapterEndpoint("DeepSeek", {}, api_key="key", api_url="url")
        self.assertIsInstance(adapter, self.OpenAIAdapter)

    def testUnknownEndpointFallsBackToOpenai(self):
        adapter = self.getAdapterEndpoint("Unknown", {}, api_key="key", api_url="url")
        self.assertIsInstance(adapter, self.OpenAIAdapter)


class TestAIClientBuildFileMessage(_AITestBase):
    def setUp(self):
        super().setUp()
        self.test_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.test_dir, ignore_errors=True))
        from src.core.AI import getAIClient

        self.getAIClient = getAIClient

    def _makeFile(self, name, content=b"test"):
        path = os.path.join(self.test_dir, name)
        with open(path, "wb") as f:
            f.write(content)
        return path

    def testTextFileMessage(self):
        client = self.getAIClient(config={})
        path = self._makeFile("test.txt", b"hello world")
        result = client.buildFileMessage(path)
        self.assertIsNotNone(result)
        self.assertEqual(result[0]["role"], "user")
        self.assertIn("hello world", result[0]["content"])

    def testLargeFileSkipped(self):
        client = self.getAIClient(config={})
        path = self._makeFile("large.bin", b"x" * (4 * 1024 * 1024 + 1))
        result = client.buildFileMessage(path)
        self.assertIsNone(result)

    def testBinaryFileReadWithErrorsReplaced(self):
        client = self.getAIClient(config={})
        path = self._makeFile("binary.bin", bytes(range(256)))
        result = client.buildFileMessage(path)
        self.assertIsNotNone(result)


# Group: sync (20 tests)


class _SyncTestBase(unittest.TestCase):
    def setUp(self):
        for p in applyMock(
            qt=True, util=True, psutil=True, requests_mod=True, file_mod=True
        ):
            self.addCleanup(p.stop)


class TestSyncModeConstants(_SyncTestBase):
    from plugin.OpenList import MODE_SYNC, MODE_BACKUP, TASK_STATUS_RUNNING, TASK_STATUS_SUCCESS, TASK_STATUS_FAILED, TASK_STATUS_ABORTED

    def testModeConstants(self):
        self.assertEqual(self.MODE_SYNC, "sync")
        self.assertEqual(self.MODE_BACKUP, "backup")

    def testTaskStatusConstants(self):
        self.assertEqual(self.TASK_STATUS_RUNNING, "running")
        self.assertEqual(self.TASK_STATUS_SUCCESS, "success")
        self.assertEqual(self.TASK_STATUS_FAILED, "failed")
        self.assertEqual(self.TASK_STATUS_ABORTED, "aborted")


class TestTaskConfig(_SyncTestBase):
    from plugin.OpenList import TaskConfig
    def setUp(self):
        super().setUp()

    def testDefaultValues(self):

        cfg = self.TaskConfig()
        self.assertEqual(cfg.name, "")
        self.assertEqual(cfg.src_path, "")
        self.assertEqual(cfg.dst_path, "")
        self.assertEqual(cfg.exclude_rules, "")
        self.assertEqual(cfg.mode, "backup")
        self.assertFalse(cfg.confirm_before_sync)
        self.assertEqual(cfg.tar_folders, "")
        self.assertEqual(cfg.tree_folders, "")

    def testToDictRoundtrip(self):

        cfg = self.TaskConfig(
            name="test_task",
            src_path="/local/path",
            dst_path="/remote/path",
            exclude_rules="*.pyc\n__pycache__",
            mode="sync",
            confirm_before_sync=True,
            tar_folders="/data/tar",
            tree_folders="/data/tree",
        )
        d = cfg.toDict()
        self.assertEqual(d["name"], "test_task")
        self.assertEqual(d["mode"], "sync")
        self.assertTrue(d["confirm_before_sync"])
        restored = self.TaskConfig.fromDict(d)
        self.assertEqual(restored.name, "test_task")
        self.assertEqual(restored.mode, "sync")
        self.assertTrue(restored.confirm_before_sync)

    def testFromDictMissingKeys(self):
        cfg = self.TaskConfig.fromDict({"name": "minimal"})
        self.assertEqual(cfg.name, "minimal")
        self.assertEqual(cfg.mode, "backup")
        self.assertEqual(cfg.src_path, "")

    def testBackupModeDefault(self):
        cfg = self.TaskConfig.fromDict({"name": "backup_task"})
        self.assertEqual(cfg.mode, "backup")


class TestSyncDiffAlgorithm(_SyncTestBase):
    def setUp(self):
        super().setUp()
        self.local_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.local_dir, ignore_errors=True))
        self.remote_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.remote_dir, ignore_errors=True))

    def _createFile(self, folder, rel_path, content=b""):
        full = os.path.join(folder, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(content)
        return rel_path

    def _scanDir(self, folder):
        """扫描目录，返回 {rel_path: {size, mtime}}"""
        files = {}
        for root, dirs, fnames in os.walk(folder):
            for fname in fnames:
                full = os.path.join(root, fname)
                rel = os.path.relpath(full, folder).replace("\\", "/")
                stat = os.stat(full)
                files[rel] = {"size": stat.st_size, "mtime": int(stat.st_mtime)}
        return files

    def _makeWorker(self, mode="sync"):
        """创建 SyncWorker 实例用于测试"""
        from plugin.OpenList import SyncWorker, TaskConfig, MODE_SYNC, MODE_BACKUP

        mode_map = {"sync": MODE_SYNC, "backup": MODE_BACKUP}
        task = TaskConfig(mode=mode_map.get(mode, MODE_SYNC))
        worker = SyncWorker(MagicMock(), task)
        worker._abort = False
        return worker

    def testLocalFileNotInRemote(self):
        self._createFile(self.local_dir, "new.txt", b"local only")
        local = self._scanDir(self.local_dir)
        remote = self._scanDir(self.remote_dir)
        worker = self._makeWorker("sync")
        to_upload, to_delete = worker._compareFiles(local, remote)
        self.assertIn("new.txt", to_upload)
        self.assertEqual(len(to_delete), 0)

    def testRemoteFileNotInLocalSyncMode(self):
        self._createFile(self.remote_dir, "old.txt", b"remote only")
        local = self._scanDir(self.local_dir)
        remote = self._scanDir(self.remote_dir)
        worker = self._makeWorker("sync")
        to_upload, to_delete = worker._compareFiles(local, remote)
        self.assertIn("old.txt", to_delete)

    def testBackupModeNoDelete(self):
        self._createFile(self.remote_dir, "extra.txt", b"extra")
        local = self._scanDir(self.local_dir)
        remote = self._scanDir(self.remote_dir)
        worker = self._makeWorker("backup")
        to_upload, to_delete = worker._compareFiles(local, remote)
        self.assertEqual(len(to_delete), 0)

    def testIdenticalFilesNoSyncNeeded(self):
        content = b"same content"
        self._createFile(self.local_dir, "match.txt", content)
        self._createFile(self.remote_dir, "match.txt", content)
        local = self._scanDir(self.local_dir)
        remote = self._scanDir(self.remote_dir)
        worker = self._makeWorker("sync")
        to_upload, to_delete = worker._compareFiles(local, remote)
        self.assertNotIn("match.txt", to_upload)

    def testFileContentChangedNeedsUpdate(self):
        self._createFile(self.local_dir, "changed.txt", b"new version!")
        self._createFile(self.remote_dir, "changed.txt", b"old version")
        local = self._scanDir(self.local_dir)
        remote = self._scanDir(self.remote_dir)
        worker = self._makeWorker("sync")
        to_upload, to_delete = worker._compareFiles(local, remote)
        self.assertIn("changed.txt", to_upload)

    def testNestedFolderStructure(self):
        self._createFile(self.local_dir, "a/b/c/deep.txt", b"deep")
        self._createFile(self.local_dir, "root.txt", b"root")
        self._createFile(self.remote_dir, "root.txt", b"root")
        local = self._scanDir(self.local_dir)
        remote = self._scanDir(self.remote_dir)
        worker = self._makeWorker("sync")
        to_upload, to_delete = worker._compareFiles(local, remote)
        self.assertTrue(any("deep.txt" in k for k in to_upload))

    def testBidirectionalDiff(self):
        self._createFile(self.local_dir, "only_local.txt", b"A")
        self._createFile(self.remote_dir, "only_remote.txt", b"B")
        self._createFile(self.local_dir, "both_diff.txt", b"content C1 longer")
        self._createFile(self.remote_dir, "both_diff.txt", b"content C2")
        self._createFile(self.local_dir, "both_same.txt", b"D")
        self._createFile(self.remote_dir, "both_same.txt", b"D")
        local = self._scanDir(self.local_dir)
        remote = self._scanDir(self.remote_dir)
        worker = self._makeWorker("sync")
        to_upload, to_delete = worker._compareFiles(local, remote)
        self.assertIn("only_local.txt", to_upload)
        self.assertIn("only_remote.txt", to_delete)
        self.assertIn("both_diff.txt", to_upload)
        self.assertNotIn("both_same.txt", to_upload)


class TestExcludeRules(_SyncTestBase):

    def testGlobExcludeMatch(self):
        rules = ["*.pyc", "*/__pycache__/*"]
        should_exclude = ["script.pyc", os.path.join("sub", "__pycache__", "cache.py")]
        should_keep = ["script.py", "data.txt"]

        for path in should_exclude:
            matched = any(
                fnmatch.fnmatch(path, rule) or fnmatch.fnmatch(os.path.basename(path), rule)
                for rule in rules
            )
            self.assertTrue(matched, f"{path} should be excluded")

        for path in should_keep:
            matched = any(
                fnmatch.fnmatch(path, rule) or fnmatch.fnmatch(os.path.basename(path), rule)
                for rule in rules
            )
            self.assertFalse(matched, f"{path} should not be excluded")

    def testExcludeGitFolder(self):
        rule = "*/.git/*"
        self.assertTrue(fnmatch.fnmatch("project/.git/config", rule))
        self.assertFalse(fnmatch.fnmatch("project/src/main.py", rule))

    def testMultipleExcludeRules(self):
        rules = ["*.log", "*.tmp", "*.bak"]
        for r in rules:
            self.assertTrue(fnmatch.fnmatch(f"file{r}", r))


class TestTarPackaging(_SyncTestBase):
    def setUp(self):
        super().setUp()
        self.test_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.test_dir, ignore_errors=True))
        self.tar_path = os.path.join(self.test_dir, "test.tar")

    def _createFile(self, rel, content=b""):
        path = os.path.join(self.test_dir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(content)

    def testCreateTarWithFiles(self):
        self._createFile("file1.txt", b"content1")
        self._createFile("file2.txt", b"content2")

        with tarfile.open(self.tar_path, "w") as tar:
            tar.add(os.path.join(self.test_dir, "file1.txt"), arcname="file1.txt")
            tar.add(os.path.join(self.test_dir, "file2.txt"), arcname="file2.txt")

        self.assertTrue(os.path.exists(self.tar_path))
        self.assertGreater(os.path.getsize(self.tar_path), 0)

        with tarfile.open(self.tar_path, "r") as tar:
            names = tar.getnames()
        self.assertIn("file1.txt", names)
        self.assertIn("file2.txt", names)

    def testTarPreservesContent(self):
        self._createFile("data.bin", b"\x00\x01\x02\x03")
        with tarfile.open(self.tar_path, "w") as tar:
            tar.add(os.path.join(self.test_dir, "data.bin"), arcname="data.bin")

        with tarfile.open(self.tar_path, "r") as tar:
            f = tar.extractfile("data.bin")
            content = f.read()
        self.assertEqual(content, b"\x00\x01\x02\x03")


# Group: perf (性能测试)


class TestLazyInitPerformance(unittest.TestCase):

    def setUp(self):
        for p in applyMock(
            qt=True, util=True, psutil=True, requests_mod=True, file_mod=True
        ):
            self.addCleanup(p.stop)

    def testTimePerfCounterBaseline(self):
        start = time.perf_counter()

        time.sleep(0.01)
        elapsed = time.perf_counter() - start
        logger.info("time.perf_counter 基准测试: %.4f 秒 (预期 ~0.01)", elapsed)
        self.assertGreater(elapsed, 0)
        self.assertLess(elapsed, 1.0)


class TestSyncPerformance(unittest.TestCase):

    def setUp(self):
        for p in applyMock(
            qt=True, psutil=True, pynput=True, markdown=True, util=True, real_logger_util=True
        ):
            self.addCleanup(p.stop)
        self.local_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.local_dir, ignore_errors=True))
        self.remote_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.remote_dir, ignore_errors=True))

    def _createFile(self, folder, rel_path, content=b""):
        full = os.path.join(folder, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(content)

    def _listFiles(self, folder):
        result = set()
        for root, _, files in os.walk(folder):
            for f in files:
                rel = os.path.relpath(os.path.join(root, f), folder)
                result.add(rel)
        return result

    def _runDiffTest(self, total, remote_count, expected_upload, threshold):
        for i in range(total):
            self._createFile(self.local_dir, f"file_{i}.txt", f"content_{i}".encode())
        for i in range(remote_count):
            self._createFile(self.remote_dir, f"file_{i}.txt", f"content_{i}".encode())

        start = time.perf_counter()
        local = self._listFiles(self.local_dir)
        remote = self._listFiles(self.remote_dir)
        to_upload = local - remote
        to_delete = remote - local
        elapsed = time.perf_counter() - start

        logger.info(
            "%d 文件 diff 耗时: %.4f 秒 (上传=%d, 删除=%d)",
            total, elapsed, len(to_upload), len(to_delete),
        )
        self.assertEqual(len(to_upload), expected_upload)
        self.assertEqual(len(to_delete), 0)
        self.assertLess(elapsed, threshold, f"diff 耗时 {elapsed:.4f} 秒，超过阈值 {threshold} 秒")

    def testDiff100Files(self):
        self._runDiffTest(100, 80, 20, 2.0)

    def testDiff1000Files(self):
        self._runDiffTest(1000, 800, 200, 5.0)


# Group: exception (异常测试)

_PROFILE_CONFIG_WITH_KEY = {
    "AI": {
        "active_profile": "default",
        "profiles": {
            "default": {
                "api_key": "test_key_123",
                "api_url": "https://api.openai.com/v1",
            }
        },
    }
}

_PROFILE_CONFIG_NO_KEY = {
    "AI": {
        "active_profile": "default",
        "profiles": {
            "default": {
                "api_url": "https://api.openai.com/v1",
            }
        },
    }
}


class _ExceptionTestBase(unittest.TestCase):
    def setUp(self):
        for p in applyMock(
            qt=True,
            psutil=True,
            pynput=True,
            keyboard=True,
            mouse=True,
            util=True,
            config=True,
            file_mod=True,
        ):
            self.addCleanup(p.stop)


class TestApiKeyErrors(_ExceptionTestBase):
    def setUp(self):
        super().setUp()
        from src.core.AI import OpenAIAdapter, AIError

        self.OpenAIAdapter = OpenAIAdapter
        self.AIError = AIError

    @patch("requests.Session.post")
    def testOpenai401Error(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.json.return_value = {"error": {"message": "Incorrect API key"}}
        mock_post.return_value = mock_resp

        adapter = self.OpenAIAdapter(config=_PROFILE_CONFIG_WITH_KEY)
        with self.assertRaises(self.AIError) as ctx:
            adapter.chat([{"role": "user", "content": "hi"}], model="gpt-4")
        self.assertIn("401", str(ctx.exception))

    @patch("requests.Session.post")
    def testOpenai403Error(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.json.return_value = {"error": {"message": "Account suspended"}}
        mock_post.return_value = mock_resp

        adapter = self.OpenAIAdapter(config=_PROFILE_CONFIG_WITH_KEY)
        with self.assertRaises(self.AIError) as ctx:
            adapter.chat([{"role": "user", "content": "hi"}], model="gpt-4")
        self.assertIn("403", str(ctx.exception))

    def testMissingApiKey(self):
        adapter = self.OpenAIAdapter(config=_PROFILE_CONFIG_NO_KEY)
        with self.assertRaises(self.AIError):
            adapter.chat([], model="gpt-4")

    def testMissingApiUrl(self):
        cfg = {
            "AI": {
                "active_profile": "default",
                "profiles": {"default": {"api_key": "test_key"}},
            }
        }
        adapter = self.OpenAIAdapter(config=cfg)
        url = adapter.getApiUrl()
        self.assertIn("chat/completions", url)


class TestNetworkErrors(_ExceptionTestBase):
    def setUp(self):
        super().setUp()
        from src.core.AI import OpenAIAdapter, AIError

        self.OpenAIAdapter = OpenAIAdapter
        self.AIError = AIError

    @patch("requests.Session.post")
    def testTimeoutError(self, mock_post):
        mock_post.side_effect = requests.exceptions.Timeout("Connection timed out")

        adapter = self.OpenAIAdapter(config=_PROFILE_CONFIG_WITH_KEY)
        with self.assertRaises(self.AIError) as ctx:
            adapter.chat([{"role": "user", "content": "hi"}], model="gpt-4")
        self.assertIn("超时", str(ctx.exception))

    @patch("requests.Session.post")
    def testConnectionError(self, mock_post):
        mock_post.side_effect = requests.exceptions.ConnectionError("Connection refused")

        adapter = self.OpenAIAdapter(config=_PROFILE_CONFIG_WITH_KEY)
        with self.assertRaises(self.AIError) as ctx:
            adapter.chat([{"role": "user", "content": "hi"}], model="gpt-4")
        self.assertIn("网络", str(ctx.exception))


class TestPluginErrors(_ExceptionTestBase):
    def setUp(self):
        super().setUp()
        for p in applyMock(real_plugin=True):
            self.addCleanup(p.stop)
        from src.plugin import PluginManager

        self.PluginManager = PluginManager

    def testEnableNonExistentPlugin(self):
        mgr = self.PluginManager()
        result = mgr.enablePlugin("nonexistent_plugin")
        self.assertFalse(result)

    def testDisableNonExistentPlugin(self):
        mgr = self.PluginManager()
        mgr.disablePlugin("nonexistent_plugin")

    def testReloadNonExistentPlugin(self):
        mgr = self.PluginManager()
        result = mgr.reloadPlugin("nonexistent_plugin")
        self.assertFalse(result)

    def testLoadPluginClassInvalidName(self):
        mgr = self.PluginManager()
        result = mgr.loadPluginClass("invalid")
        self.assertIsNone(result)

    def testScanCacheAfterFailedLoad(self):
        mgr = self.PluginManager()
        mgr.loadPluginClass("invalid")
        self.assertIsNotNone(mgr._scan_cache)


class TestModelErrors(_ExceptionTestBase):
    def setUp(self):
        super().setUp()
        from src.core.AI import OpenAIAdapter, AIError

        self.OpenAIAdapter = OpenAIAdapter
        self.AIError = AIError

    @patch("requests.Session.post")
    def testEmptyResponseText(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"choices": [{"message": {"content": ""}}]}
        mock_post.return_value = mock_resp

        adapter = self.OpenAIAdapter(config=_PROFILE_CONFIG_WITH_KEY)
        text, _, _ = adapter.chat([{"role": "user", "content": "hi"}], model="gpt-4")
        self.assertEqual(text, "")

    @patch("requests.Session.post")
    def testMalformedJsonResponse(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = json.JSONDecodeError("Expecting value", "", 0)
        mock_post.return_value = mock_resp

        adapter = self.OpenAIAdapter(config=_PROFILE_CONFIG_WITH_KEY)
        with self.assertRaises(self.AIError) as ctx:
            adapter.chat([{"role": "user", "content": "hi"}], model="gpt-4")
        self.assertIn("格式错误", str(ctx.exception))

    @patch("requests.Session.post")
    def testMissingChoicesKey(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": "123"}
        mock_post.return_value = mock_resp

        adapter = self.OpenAIAdapter(config=_PROFILE_CONFIG_WITH_KEY)
        with self.assertRaises(self.AIError):
            adapter.chat([{"role": "user", "content": "hi"}], model="gpt-4")


class TestConfigErrors(_ExceptionTestBase):
    def setUp(self):
        super().setUp()
        from src.core.AI import OpenAIAdapter

        self.OpenAIAdapter = OpenAIAdapter

    def testEmptyConfigReturnsDefaults(self):
        adapter = self.OpenAIAdapter(config={})
        self.assertIsNotNone(adapter)

    def testPartialConfig(self):
        adapter = self.OpenAIAdapter(config={"AI": {"api_key": "test"}})
        self.assertIsNotNone(adapter)


# Group: md (Markdown 处理)


class TestExtractToc(unittest.TestCase):
    """纯逻辑，无需 mock"""
    from src.core.md import extractToc

    def testEmptyContent(self):
        self.assertEqual(self.extractToc(""), [])

    def testNoHeadings(self):
        result = self.extractToc("普通文本\n\n没有标题\n")
        self.assertEqual(result, [])

    def testSimpleHeadings(self):
        result = self.extractToc("# H1\n## H2\n### H3")
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0]["text"], "H1")
        self.assertEqual(result[0]["level"], 1)
        self.assertEqual(result[1]["text"], "H2")
        self.assertEqual(result[1]["level"], 2)
        self.assertEqual(result[2]["text"], "H3")
        self.assertEqual(result[2]["level"], 3)

    def testMixedContent(self):
        content = "# 标题\n\n段落文字\n\n## 子标题\n\n- 列表项\n\n### 三级"
        result = self.extractToc(content)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0]["line"], 1)
        self.assertEqual(result[1]["line"], 5)
        self.assertEqual(result[2]["line"], 9)

    def testAnchorGeneration(self):
        content = "# Hello World\n# Hello World\n"
        result = self.extractToc(content)
        self.assertEqual(len(result), 2)
        self.assertNotEqual(result[0]["anchor"], result[1]["anchor"])

    def testChineseHeadings(self):
        content = "# 中文标题\n## 二级标题\n"
        result = self.extractToc(content)
        self.assertEqual(len(result), 2)
        self.assertIn("中文", result[0]["text"])

    def testLineNumberCorrect(self):
        content = "开头\n\n# 标题在第3行\n\n中间\n\n## 在第6行"
        result = self.extractToc(content)
        self.assertEqual(result[0]["line"], 3)
        self.assertEqual(result[1]["line"], 7)


class TestRenderMarkdown(unittest.TestCase):
    from src.core.md import renderMarkdown, renderForView

    def setUp(self):
        for p in applyMock(util=True):
            self.addCleanup(p.stop)

    def testEmptyString(self):
        result = self.renderMarkdown("")
        self.assertIsNotNone(result)
        self.assertIn("</html>", result)

    def testBasicMarkdown(self):
        result = self.renderMarkdown("# Hello")
        self.assertIn("Hello", result)
        self.assertIn("</html>", result)

    def testCodeBlock(self):
        result = self.renderMarkdown("```python\nprint('hi')\n```")
        self.assertIn("hljs", result)

    def testTable(self):
        result = self.renderMarkdown("| A | B |\n| - | - |\n| 1 | 2 |")
        self.assertIn("<table>", result)

    def testList(self):
        result = self.renderMarkdown("- item1\n- item2")
        self.assertIn("item1", result)
        self.assertIn("<li>", result)

    def testInlineCode(self):
        result = self.renderMarkdown("这是 `code` 测试")
        self.assertIn("<code>", result)

    def testBoldAndItalic(self):
        result = self.renderMarkdown("**粗体** *斜体*")
        self.assertIn("<strong>", result)
        self.assertIn("<em>", result)

    def testLink(self):
        result = self.renderMarkdown("[GitHub](https://github.com)")
        self.assertIn('href="https://github.com"', result)

    def testChineseContent(self):
        result = self.renderMarkdown("你好世界")
        self.assertIn("你好世界", result)

    def testBlockquote(self):
        result = self.renderMarkdown("> 引用内容")
        self.assertIn("<blockquote>", result)

    def testHorizontalRule(self):
        result = self.renderMarkdown("---")
        self.assertIn("<hr", result)

    def testNestedList(self):
        result = self.renderMarkdown("- 一级\n  - 二级")
        self.assertIn("一级", result)
        self.assertIn("二级", result)

    def testRenderForViewEmpty(self):
        html, ok = self.renderForView("")
        self.assertIsNone(html)
        self.assertFalse(ok)

    def testRenderForViewValid(self):
        html, ok = self.renderForView("**bold**")
        self.assertIsNotNone(html)
        self.assertTrue(ok)
        self.assertIn("<strong>", html)


# Group: timer (定时器)


class TestLRUCache(unittest.TestCase):
    from src.core.timer import LRUCache

    def setUp(self):
        for p in applyMock(qt=True, util=True):
            self.addCleanup(p.stop)

    def testSetAndGet(self):
        c = self.LRUCache(max_size=3)
        c.set("a", 1)
        self.assertEqual(c.get("a"), 1)

    def testGetDefault(self):
        c = self.LRUCache(max_size=3)
        self.assertIsNone(c.get("missing"))
        self.assertEqual(c.get("missing", 42), 42)

    def testEviction(self):
        c = self.LRUCache(max_size=2)
        c.set("a", 1)
        c.set("b", 2)
        c.set("c", 3)
        self.assertNotIn("a", c)
        self.assertIn("b", c)
        self.assertIn("c", c)

    def testReordering(self):
        c = self.LRUCache(max_size=2)
        c.set("a", 1)
        c.set("b", 2)
        c.get("a")
        c.set("c", 3)
        self.assertIn("a", c)
        self.assertNotIn("b", c)

    def testClear(self):
        c = self.LRUCache(max_size=3)
        c.set("a", 1)
        c.set("b", 2)
        c.clear()
        self.assertEqual(len(c), 0)
        self.assertNotIn("a", c)

    def testLen(self):
        c = self.LRUCache(max_size=3)
        self.assertEqual(len(c), 0)
        c.set("a", 1)
        self.assertEqual(len(c), 1)
        c.set("b", 2)
        self.assertEqual(len(c), 2)

    def testContains(self):
        c = self.LRUCache(max_size=3)
        c.set("key", "val")
        self.assertTrue("key" in c)
        self.assertFalse("missing" in c)

    def testZeroMaxSize(self):
        c = self.LRUCache(max_size=0)
        c.set("a", 1)
        self.assertEqual(len(c), 0)
        self.assertNotIn("a", c)

    def testOverwriteSameKey(self):
        c = self.LRUCache(max_size=5)
        c.set("a", 1)
        c.set("a", 2)
        self.assertEqual(c.get("a"), 2)
        self.assertEqual(len(c), 1)


class TestWeakCallbackSet(unittest.TestCase):
    from src.core.timer import WeakCallbackSet

    def setUp(self):
        for p in applyMock(qt=True, util=True):
            self.addCleanup(p.stop)

    def testAddAndIterate(self):
        s = self.WeakCallbackSet()
        def cb():
            pass
        s.add(cb)
        self.assertEqual(list(s), [cb])

    def testRemove(self):
        s = self.WeakCallbackSet()
        def cb():
            pass
        s.add(cb)
        s.remove(cb)
        self.assertEqual(len(s), 0)

    def testBool(self):
        s = self.WeakCallbackSet()
        self.assertFalse(s)
        def cb():
            pass
        s.add(cb)
        self.assertTrue(s)

    def testLen(self):
        s = self.WeakCallbackSet()
        def cb1():
            pass
        def cb2():
            pass
        s.add(cb1)
        s.add(cb2)
        self.assertEqual(len(s), 2)
        s.remove(cb1)
        self.assertEqual(len(s), 1)

    def testAddDuplicate(self):
        s = self.WeakCallbackSet()
        def cb():
            pass
        self.assertTrue(s.add(cb))
        self.assertIsNotNone(s.add(cb))

    def testRemoveNonExistent(self):
        s = self.WeakCallbackSet()
        s.remove(lambda: None)

    def testAddLambda(self):
        s = self.WeakCallbackSet()
        s.add(lambda: None)
        self.assertEqual(len(s), 1)


class TestCronField(unittest.TestCase):
    from src.core.timer import _CronField

    def testStar(self):
        f = self._CronField("*", 0, 59)
        self.assertTrue(f.match(0))
        self.assertTrue(f.match(30))
        self.assertTrue(f.match(59))
        self.assertFalse(f.match(-1))
        self.assertFalse(f.match(60))

    def testSingleValue(self):
        f = self._CronField("30", 0, 59)
        self.assertTrue(f.match(30))
        self.assertFalse(f.match(0))
        self.assertFalse(f.match(59))

    def testStep(self):
        f = self._CronField("*/5", 0, 59)
        self.assertTrue(f.match(0))
        self.assertTrue(f.match(5))
        self.assertTrue(f.match(55))
        self.assertFalse(f.match(3))
        self.assertFalse(f.match(59))

    def testRange(self):
        f = self._CronField("10-20", 0, 59)
        self.assertTrue(f.match(10))
        self.assertTrue(f.match(15))
        self.assertTrue(f.match(20))
        self.assertFalse(f.match(9))
        self.assertFalse(f.match(21))

    def testMultipleValues(self):
        f = self._CronField("0,30,45", 0, 59)
        self.assertTrue(f.match(0))
        self.assertTrue(f.match(30))
        self.assertTrue(f.match(45))
        self.assertFalse(f.match(15))

    def testRangeWithStep(self):
        f = self._CronField("10-20/5", 0, 59)
        self.assertTrue(f.match(10))
        self.assertTrue(f.match(15))
        self.assertTrue(f.match(20))
        self.assertFalse(f.match(11))

    def testOutOfRangeRaises(self):
        with self.assertRaises(ValueError):
            self._CronField("60", 0, 59)

    def testNextMatch(self):
        f = self._CronField("10,20,30", 0, 59)
        self.assertEqual(f.nextMatch(5), 10)
        self.assertEqual(f.nextMatch(10), 10)
        self.assertEqual(f.nextMatch(11), 20)
        self.assertEqual(f.nextMatch(35), None)

    def testDayOfWeek7to0(self):
        f = self._CronField("0-7", 0, 7)
        self.assertTrue(f.match(0))
        self.assertTrue(f.match(7))


class TestCronExpr(unittest.TestCase):
    from datetime import datetime
    from src.core.timer import _CronExpr

    def testEveryMinute(self):
        e = self._CronExpr("* * * * *")
        now = self.datetime(2026, 7, 23, 10, 30)
        n = e.nextMatch(now)
        self.assertEqual(n.minute, 31)

    def testSpecificMinute(self):
        e = self._CronExpr("0 * * * *")
        now = self.datetime(2026, 7, 23, 10, 30)
        n = e.nextMatch(now)
        self.assertEqual(n.minute, 0)
        self.assertEqual(n.hour, 11)

    def testEvery5Minutes(self):
        e = self._CronExpr("*/5 * * * *")
        now = self.datetime(2026, 7, 23, 10, 33)
        n = e.nextMatch(now)
        self.assertEqual(n.minute, 35)

    def testHourBoundary(self):
        e = self._CronExpr("0 * * * *")
        now = self.datetime(2026, 7, 23, 10, 0)
        n = e.nextMatch(now)
        self.assertEqual(n.hour, 11)

    def testDayBoundary(self):
        e = self._CronExpr("0 0 * * *")
        now = self.datetime(2026, 7, 23, 10, 0)
        n = e.nextMatch(now)
        self.assertEqual(n.hour, 0)
        self.assertEqual(n.day, 24)

    def testWeekday(self):
        e = self._CronExpr("0 9 * * 1-5")
        now = self.datetime(2026, 7, 23, 10, 0)
        n = e.nextMatch(now)
        self.assertEqual(n.hour, 9)
        self.assertEqual(n.day, 24)

    def testInvalidFieldCount(self):
        with self.assertRaises(ValueError):
            self._CronExpr("* * * *")

    def testSpecificMonth(self):
        e = self._CronExpr("0 0 1 1 *")
        now = self.datetime(2026, 7, 23, 10, 0)
        n = e.nextMatch(now)
        self.assertEqual(n.month, 1)
        self.assertEqual(n.year, 2027)

    def testCronWeekday(self):
        from src.core.timer import _cronWeekday
        self.assertEqual(_cronWeekday(2026, 7, 23), 4)
        self.assertEqual(_cronWeekday(2026, 7, 19), 0)


# Group: file (文件工具)


class TestCompileRules(unittest.TestCase):
    from src.file import _compileSingleRule, compileRules, _matchRelPath

    def testEmptyRule(self):
        self.assertIsNone(self._compileSingleRule(""))

    def testSimpleGlob(self):
        result = self._compileSingleRule("*.pyc")
        self.assertIsNotNone(result)
        pattern, is_dir, root_only = result
        self.assertFalse(is_dir)
        self.assertFalse(root_only)
        self.assertIsNotNone(pattern.match("test.pyc"))
        self.assertIsNone(pattern.match("test.py"))

    def testDirExclude(self):
        result = self._compileSingleRule("*/__pycache__/")
        self.assertIsNotNone(result)
        pattern, is_dir, root_only = result
        self.assertTrue(is_dir)

    def testRootOnly(self):
        result = self._compileSingleRule("/file.txt")
        self.assertIsNotNone(result)
        pattern, is_dir, root_only = result
        self.assertTrue(root_only)

    def testCompileRulesList(self):
        rules = ["*.pyc", "*.log", ""]
        compiled = self.compileRules(rules)
        self.assertEqual(len(compiled), 2)

    def testMatchRelPath(self):
        pattern, _, _ = self._compileSingleRule("*.pyc")
        self.assertTrue(self._matchRelPath("test.pyc", pattern, False))
        self.assertFalse(self._matchRelPath("test.py", pattern, False))

    def testMatchDirOnly(self):
        pattern, _, _ = self._compileSingleRule("*/git/")
        self.assertTrue(self._matchRelPath("project/git", pattern, True))
        self.assertFalse(self._matchRelPath(".gitignore", pattern, True))


class TestIsExcluded(unittest.TestCase):
    from src.file import _compileSingleRule, isExcluded

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.d, ignore_errors=True))

    def testNotExcluded(self):
        f = os.path.join(self.d, "test.txt")
        open(f, "w").close()
        rules = [self._compileSingleRule("*.pyc")]
        self.assertFalse(self.isExcluded(f, self.d, rules))

    def testExcludedByGlob(self):
        f = os.path.join(self.d, "test.pyc")
        open(f, "w").close()
        rules = [self._compileSingleRule("*.pyc")]
        self.assertTrue(self.isExcluded(f, self.d, rules))

    def testExcludedDir(self):
        project = os.path.join(self.d, "project")
        os.mkdir(project)
        sub = os.path.join(project, "__pycache__")
        os.mkdir(sub)
        rules = [self._compileSingleRule("*/__pycache__/")]
        self.assertTrue(self.isExcluded(sub, self.d, rules))

    def testRootOnlyRule(self):
        os.mkdir(os.path.join(self.d, "sub"))
        f = os.path.join(self.d, "sub", ".git")
        open(f, "w").close()
        rules = [self._compileSingleRule("/.git")]
        self.assertFalse(self.isExcluded(f, self.d, rules))


# Group: config (配置)


class TestConfigAccess(unittest.TestCase):
    def setUp(self):
        for p in applyMock(qt=True, util=True):
            self.addCleanup(p.stop)
        from src.config import ConfigManager
        ConfigManager._instance = None
        self.cm = ConfigManager()

    def testGetTopLevel(self):
        self.cm.config = {"theme": "Dark"}
        self.assertEqual(self.cm.get("theme"), "Dark")

    def testGetNested(self):
        self.cm.config = {"Edit": {"font_size": 14}}
        self.assertEqual(self.cm.get("Edit.font_size"), 14)

    def testGetNestedDefault(self):
        self.cm.config = {}
        self.assertEqual(self.cm.get("Edit.font_size", 12), 12)

    def testGetMissingDefault(self):
        self.cm.config = {}
        self.assertIsNone(self.cm.get("nonexistent"))

    def testSetTopLevel(self):
        self.cm.config = {}
        self.cm.set("theme", "Dark")
        self.assertEqual(self.cm.config["theme"], "Dark")

    def testSetNested(self):
        self.cm.config = {}
        self.cm.set("Edit.font_size", 14)
        self.assertEqual(self.cm.config["Edit"]["font_size"], 14)

    def testSetDeepNested(self):
        self.cm.config = {}
        self.cm.set("a.b.c", "deep")
        self.assertEqual(self.cm.config["a"]["b"]["c"], "deep")

    def testGetPartialPath(self):
        self.cm.config = {"a": "not_dict"}
        self.assertIsNone(self.cm.get("a.b"))

    def testDeepUpdate(self):
        base = {"a": 1, "b": {"c": 2}}
        update = {"b": {"d": 3}, "e": 4}
        result = self.cm._deepUpdate(base, update)
        self.assertEqual(result["a"], 1)
        self.assertEqual(result["b"]["c"], 2)
        self.assertEqual(result["b"]["d"], 3)
        self.assertEqual(result["e"], 4)


class TestConfigRecentFav(unittest.TestCase):
    def setUp(self):
        for p in applyMock(qt=True, util=True):
            self.addCleanup(p.stop)
        from src.config import ConfigManager
        ConfigManager._instance = None
        cm = ConfigManager()
        cm.config = {"Edit": {"recent": [], "favorites": []}}
        self.cm = cm

    def testAddRecent(self):
        self.cm.addRecentFile("/path/file.txt")
        recent = self.cm.get("Edit.recent")
        self.assertIn(os.path.normpath("/path/file.txt"), recent)

    def testAddRecentDuplicate(self):
        self.cm.addRecentFile("/path/file.txt")
        self.cm.addRecentFile("/path/file.txt")
        recent = self.cm.get("Edit.recent")
        self.assertEqual(len(recent), 1)

    def testAddRecentMax10(self):
        for i in range(15):
            self.cm.addRecentFile(f"/path/file_{i}.txt")
        recent = self.cm.get("Edit.recent")
        self.assertLessEqual(len(recent), 10)

    def testAddFavorite(self):
        self.cm.addFavorite("/path/fav.txt")
        self.assertTrue(self.cm.isFavorite("/path/fav.txt"))

    def testRemoveFavorite(self):
        self.cm.addFavorite("/path/fav.txt")
        self.cm.removeFavorite("/path/fav.txt")
        self.assertFalse(self.cm.isFavorite("/path/fav.txt"))

    def testIsFavoriteNotFound(self):
        self.assertFalse(self.cm.isFavorite("/nonexistent"))

    def testAddRecentNormalizesPath(self):
        self.cm.addRecentFile("C:/path\\file.txt")
        recent = self.cm.get("Edit.recent")
        self.assertIn("C:\\path\\file.txt", recent)


# Group: input (输入)


class TestParseHotkey(unittest.TestCase):
    def setUp(self):
        for p in applyMock(qt=True, util=True, pynput=True):
            self.addCleanup(p.stop)

    def _listener(self):
        from src.core.input import GlobalHotkeyListener
        return GlobalHotkeyListener()

    def testCtrlShiftA(self):
        l = self._listener()
        result = l._parseHotkey("ctrl+shift+a")
        self.assertIsNotNone(result)
        self.assertIn("ctrl_l", result)
        self.assertIn("shift_l", result)
        self.assertIn("a", result)

    def testAltF4(self):
        l = self._listener()
        result = l._parseHotkey("alt+f4")
        self.assertIn("alt_l", result)
        self.assertIn("f4", result)

    def testWinR(self):
        l = self._listener()
        result = l._parseHotkey("win+r")
        self.assertIn("cmd", result)
        self.assertIn("r", result)

    def testCaseInsensitive(self):
        l = self._listener()
        result = l._parseHotkey("Ctrl+Shift+E")
        self.assertIn("ctrl_l", result)
        self.assertIn("shift_l", result)
        self.assertIn("e", result)

    def testEmptyString(self):
        l = self._listener()
        result = l._parseHotkey("")
        self.assertIsNone(result)

    def testSingleKey(self):
        l = self._listener()
        result = l._parseHotkey("f1")
        self.assertEqual(result, {"f1"})

    def testSuperAlias(self):
        l = self._listener()
        result = l._parseHotkey("super+x")
        self.assertIn("cmd", result)
        self.assertIn("x", result)

    def testMetaAlias(self):
        l = self._listener()
        result = l._parseHotkey("meta+v")
        self.assertIn("cmd", result)
        self.assertIn("v", result)

    def testPunctuation(self):
        l = self._listener()
        result = l._parseHotkey("Ctrl+;")
        self.assertEqual(result, {"ctrl_l", ";"})

    def testPunctuationMinus(self):
        l = self._listener()
        result = l._parseHotkey("Ctrl+-")
        self.assertEqual(result, {"ctrl_l", "-"})


class TestCheckHotkey(unittest.TestCase):
    def setUp(self):
        for p in applyMock(qt=True, util=True, pynput=True):
            self.addCleanup(p.stop)

    def _makeListener(self):
        from src.core.input import GlobalHotkeyListener
        l = GlobalHotkeyListener()
        l._modifier_press_order = ["ctrl_l", "c"]
        return l

    def testExactMatch(self):
        l = self._makeListener()
        hotkey = {"ctrl_l", "c"}
        pressed = {"ctrl_l", "c", "shift_l"}
        self.assertTrue(l._checkHotkey(pressed, hotkey))

    def testNoMatchMissingKey(self):
        l = self._makeListener()
        hotkey = {"ctrl_l", "c"}
        pressed = {"ctrl_l"}
        self.assertFalse(l._checkHotkey(pressed, hotkey))

    def testNoMatchEmptyHotkey(self):
        l = self._makeListener()
        self.assertFalse(l._checkHotkey(set(), set()))

    def testCtrlRForCtrlL(self):
        l = self._makeListener()
        hotkey = {"ctrl_l", "c"}
        pressed = {"ctrl_r", "c"}
        l._modifier_press_order = ["ctrl_r", "c"]
        self.assertTrue(l._checkHotkey(pressed, hotkey))

    def testNoModifiersMatch(self):
        l = self._makeListener()
        hotkey = {"f1"}
        pressed = {"f1"}
        l._modifier_press_order = ["f1"]
        self.assertTrue(l._checkHotkey(pressed, hotkey))

    def testNoneHotkey(self):
        l = self._makeListener()
        self.assertFalse(l._checkHotkey({"a"}, None))

    def testAltRForAltL(self):
        l = self._makeListener()
        hotkey = {"alt_l", "x"}
        pressed = {"alt_r", "x"}
        l._modifier_press_order = ["alt_r", "x"]
        self.assertTrue(l._checkHotkey(pressed, hotkey))

    def testShiftVariant(self):
        l = self._makeListener()
        hotkey = {"shift_l", "a"}
        pressed = {"shift_r", "a"}
        l._modifier_press_order = ["shift_r", "a"]
        self.assertTrue(l._checkHotkey(pressed, hotkey))

    def testPunctuationMatch(self):
        l = self._makeListener()
        hotkey = {"ctrl_l", ";"}
        pressed = {"ctrl_l", ";"}
        l._modifier_press_order = ["ctrl_l", ";"]
        self.assertTrue(l._checkHotkey(pressed, hotkey))


class TestHotkeyNormalKey(unittest.TestCase):
    def setUp(self):
        for p in applyMock(qt=True, util=True, pynput=True):
            self.addCleanup(p.stop)

    def testNormalKeyTrue(self):
        from src.core.input import GlobalHotkeyListener
        self.assertTrue(GlobalHotkeyListener._isHotkeyNormalKey({"ctrl_l", "0"}, "0"))

    def testModifierKeyFalse(self):
        from src.core.input import GlobalHotkeyListener
        self.assertFalse(GlobalHotkeyListener._isHotkeyNormalKey({"ctrl_l", "0"}, "ctrl_l"))

    def testUnrelatedKeyFalse(self):
        from src.core.input import GlobalHotkeyListener
        self.assertFalse(GlobalHotkeyListener._isHotkeyNormalKey({"ctrl_l", "0"}, "c"))

    def testEmptyHotkeyFalse(self):
        from src.core.input import GlobalHotkeyListener
        self.assertFalse(GlobalHotkeyListener._isHotkeyNormalKey(None, "0"))


class TestPruneVkState(unittest.TestCase):
    def setUp(self):
        for p in applyMock(qt=True, util=True, pynput=True):
            self.addCleanup(p.stop)

    def _listener(self):
        from src.core.input import GlobalHotkeyListener
        return GlobalHotkeyListener()

    def testPruneRemovesStaleKeepsHeld(self):
        l = self._listener()
        l._vk_pressed = {0x43, 0x41, 0x58}
        # 0x43、0x41 仍在按下，0x58 已抬起但残留
        with patch("src.core.input.isKeyDown", side_effect=lambda vk: vk in (0x43, 0x41)):
            l._pruneVkState(0x43)
        self.assertEqual(l._vk_pressed, {0x43, 0x41})

    def testKeepCurrentVkOnLag(self):
        l = self._listener()
        l._vk_pressed = {0x30, 0x41}
        # GetAsyncKeyState 对刚按下的键可能有一拍延迟，当前键必须豁免
        with patch("src.core.input.isKeyDown", return_value=False):
            l._pruneVkState(0x30)
        self.assertEqual(l._vk_pressed, {0x30})

    def testPruneRemovesStaleOrder(self):
        l = self._listener()
        l._vk_pressed = {0x11, 0x30, 0x41}
        l._modifier_press_order = ["ctrl_l", "0", "a"]
        # 0x30(0) 已抬起但残留，0x11(ctrl)、0x41(a) 仍在按下
        with patch("src.core.input.isKeyDown", side_effect=lambda vk: vk in (0x11, 0x41)):
            l._pruneVkState(0x11)
        self.assertEqual(l._vk_pressed, {0x11, 0x41})
        self.assertEqual(l._modifier_press_order, ["ctrl_l", "a"])

    def testPruneClearsStaleFired(self):
        l = self._listener()
        l._vk_pressed = {0x11, 0x30}
        l._tool_hotkeys["Ctrl+0"] = {"name": "t"}
        l._tool_hotkeys_cache["Ctrl+0"] = {"ctrl_l", "0"}
        l._tool_hotkeys_fired.add("Ctrl+0")
        # ctrl、0 均已抬起，按 x 时自愈应清掉已触发集合
        with patch("src.core.input.isKeyDown", return_value=False):
            l._pruneVkState(0x58)
        self.assertEqual(l._vk_pressed, set())
        self.assertNotIn("Ctrl+0", l._tool_hotkeys_fired)

    def testPruneRestoresOrderedPress(self):
        # 回归：0 的 KEYUP 丢失导致顺序记录残留，自愈后重按 Ctrl+0（正确顺序）应能触发
        l = self._listener()
        l._tool_hotkeys["Ctrl+0"] = {"name": "t"}
        l._tool_hotkeys_cache["Ctrl+0"] = {"ctrl_l", "0"}
        l._vk_pressed = {0x11, 0x30}
        l._modifier_press_order = ["ctrl_l", "0"]
        with patch("src.core.input.isKeyDown", side_effect=lambda vk: vk == 0x11):
            l._pruneVkState(0x11)
        self.assertEqual(l._modifier_press_order, ["ctrl_l"])
        l._vk_pressed.add(0x30)
        l._modifier_press_order.append("0")
        tool, hotkey = l._matchToolHotkeys({"ctrl_l", "0"}, "0")
        self.assertEqual(hotkey, "Ctrl+0")


class TestMatchToolHotkeys(unittest.TestCase):
    def setUp(self):
        for p in applyMock(qt=True, util=True, pynput=True):
            self.addCleanup(p.stop)

    def _listener(self):
        from src.core.input import GlobalHotkeyListener
        l = GlobalHotkeyListener()
        l._tool_hotkeys["Ctrl+0"] = {"name": "test"}
        l._tool_hotkeys_cache["Ctrl+0"] = {"ctrl_l", "0"}
        l._modifier_press_order = ["ctrl_l", "0"]
        return l

    def testNormalKeyTriggers(self):
        l = self._listener()
        tool, hotkey = l._matchToolHotkeys({"ctrl_l", "0"}, "0")
        self.assertEqual(hotkey, "Ctrl+0")
        self.assertEqual(tool["name"], "test")

    def testOtherKeyWithStaleNormalKeyDoesNotTrigger(self):
        # 回归：按下 Ctrl+C 时残留 '0'，但当前键是 'c'，不应触发 Ctrl+0
        l = self._listener()
        tool, hotkey = l._matchToolHotkeys({"ctrl_l", "c", "0"}, "c")
        self.assertIsNone(tool)
        self.assertIsNone(hotkey)

    def testModifierKeydownDoesNotTrigger(self):
        l = self._listener()
        tool, hotkey = l._matchToolHotkeys({"ctrl_l", "0"}, "ctrl_l")
        self.assertIsNone(tool)
        self.assertIsNone(hotkey)

    def testPunctuationHotkeyTriggers(self):
        l = self._listener()
        l._tool_hotkeys["Ctrl+;"] = {"name": "punc"}
        l._tool_hotkeys_cache["Ctrl+;"] = {"ctrl_l", ";"}
        l._modifier_press_order = ["ctrl_l", ";"]
        tool, hotkey = l._matchToolHotkeys({"ctrl_l", ";"}, ";")
        self.assertEqual(hotkey, "Ctrl+;")
        self.assertEqual(tool["name"], "punc")


class TestCodeToKey(unittest.TestCase):
    def setUp(self):
        for p in applyMock(qt=True, util=True, pynput=True):
            self.addCleanup(p.stop)

    def testPunctuation(self):
        from src.core.input import codeToKey, Qt
        self.assertEqual(codeToKey(Qt.Key.Key_Semicolon), ";")
        self.assertEqual(codeToKey(Qt.Key.Key_Comma), ",")
        self.assertEqual(codeToKey(Qt.Key.Key_Period), ".")
        self.assertEqual(codeToKey(Qt.Key.Key_Slash), "/")
        self.assertEqual(codeToKey(Qt.Key.Key_Backslash), "\\")
        self.assertEqual(codeToKey(Qt.Key.Key_BracketLeft), "[")
        self.assertEqual(codeToKey(Qt.Key.Key_BracketRight), "]")
        self.assertEqual(codeToKey(Qt.Key.Key_Minus), "-")
        self.assertEqual(codeToKey(Qt.Key.Key_Equal), "=")
        self.assertEqual(codeToKey(Qt.Key.Key_QuoteLeft), "`")
        self.assertEqual(codeToKey(Qt.Key.Key_Apostrophe), "'")

    def testPlusExcluded(self):
        # '+' 是解析分隔符且与 '=' 共用物理键，必须不可捕获
        from src.core.input import codeToKey, Qt
        self.assertIsNone(codeToKey(Qt.Key.Key_Plus))

    def testLettersAndDigits(self):
        from src.core.input import codeToKey, Qt
        self.assertEqual(codeToKey(Qt.Key.Key_A), "A")
        self.assertEqual(codeToKey(Qt.Key.Key_0), "0")
        self.assertEqual(codeToKey(Qt.Key.Key_F12), "F12")
        self.assertEqual(codeToKey(Qt.Key.Key_Return), "Return")


# Group: resolution (分辨率)


class TestParseResolution(unittest.TestCase):
    from plugin.Resolution import parseResolution

    def testStandard(self):
        result = self.parseResolution("1920×1080")
        self.assertEqual(result, (1920, 1080))

    def testAltSeparator(self):
        result = self.parseResolution("800x600")
        self.assertIsNone(result)

    def testWithWhitespace(self):
        result = self.parseResolution("  2560×1440  ")
        self.assertEqual(result, (2560, 1440))

    def testInvalidText(self):
        result = self.parseResolution("abc×def")
        self.assertIsNone(result)

    def testEmptyString(self):
        result = self.parseResolution("")
        self.assertIsNone(result)

    def testZeroValue(self):
        result = self.parseResolution("0×1080")
        self.assertIsNone(result)

    def testNegativeValue(self):
        result = self.parseResolution("-1920×1080")
        self.assertIsNone(result)

    def testPartial(self):
        result = self.parseResolution("1920×")
        self.assertIsNone(result)


# Group: toolbox (RenameItem)


class TestRenameItem(unittest.TestCase):
    from plugin.ToolBox import RenameItem

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.test_dir, ignore_errors=True))
        self.file_path = os.path.join(self.test_dir, "hello_world.txt")
        open(self.file_path, "w").close()

    def testFindReplace(self):
        item = self.RenameItem(self.file_path)
        item.applyFindReplace("hello", "hi")
        self.assertEqual(item.new_name, "hi_world.txt")

    def testFindReplaceCaseSensitive(self):
        item = self.RenameItem(self.file_path)
        item.applyFindReplace("Hello", "Hi", case_sensitive=True)
        self.assertEqual(item.new_name, "hello_world.txt")

    def testFindReplaceCaseInsensitive(self):
        item = self.RenameItem(self.file_path)
        item.applyFindReplace("Hello", "Hi", case_sensitive=False)
        self.assertEqual(item.new_name, "Hi_world.txt")

    def testPrefix(self):
        item = self.RenameItem(self.file_path)
        item.applyPrefix("new_")
        self.assertEqual(item.new_name, "new_hello_world.txt")

    def testSuffix(self):
        item = self.RenameItem(self.file_path)
        item.applySuffix("_v2")
        self.assertEqual(item.new_name, "hello_world_v2.txt")

    def testNumberingPrefix(self):
        item = self.RenameItem(self.file_path)
        item.applyNumbering(1, 1, "prefix", 3)
        self.assertEqual(item.new_name, "001_hello_world.txt")

    def testNumberingSuffix(self):
        item = self.RenameItem(self.file_path)
        item.applyNumbering(5, 2, "suffix", 2)
        self.assertEqual(item.new_name, "hello_world_05.txt")

    def testNumberingReplace(self):
        item = self.RenameItem(self.file_path)
        item.applyNumbering(1, 1, "replace", 3)
        self.assertEqual(item.new_name, "001.txt")

    def testFindReplaceEmptyFind(self):
        item = self.RenameItem(self.file_path)
        item.applyFindReplace("", "x")
        self.assertEqual(item.new_name, "hello_world.txt")

    def testDirectoryPrefix(self):
        item = self.RenameItem(self.test_dir)
        item.applyPrefix("pfx_")
        self.assertEqual(item.new_name, "pfx_" + os.path.basename(self.test_dir))

    def testGetNewPath(self):
        item = self.RenameItem(self.file_path)
        item.applyPrefix("new_")
        expected = os.path.join(self.test_dir, "new_hello_world.txt")
        self.assertEqual(item.getNewPath(), expected)


# Group: update (更新)


class TestGetReleaseInfo(unittest.TestCase):
    from src.core.update import getReleaseInfo

    @patch("requests.get")
    def testSuccess(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "tag_name": "v1.2.3",
            "body": "Release notes",
            "assets": [{"name": "O.exe"}]
        }
        mock_get.return_value = mock_resp
        result = self.getReleaseInfo("https://example.com/release")
        self.assertIsNotNone(result)
        self.assertEqual(result["version"], "1.2.3")
        self.assertEqual(result["body"], "Release notes")
        self.assertEqual(len(result["assets"]), 1)

    @patch("requests.get")
    def testNetworkError(self, mock_get):
        mock_get.side_effect = requests.exceptions.RequestException("timeout")
        result = self.getReleaseInfo("https://example.com/release")
        self.assertIsNone(result)

    @patch("requests.get")
    def testBadJson(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = json.JSONDecodeError("bad", "", 0)
        mock_get.return_value = mock_resp
        result = self.getReleaseInfo("https://example.com/release")
        self.assertIsNone(result)

    @patch("requests.get")
    def testMissingTag(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"body": "notes"}
        mock_get.return_value = mock_resp
        result = self.getReleaseInfo("https://example.com/release")
        self.assertIsNone(result)

    @patch("requests.get")
    def testHttpError(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.raise_for_status.side_effect = requests.exceptions.RequestException("404")
        mock_get.return_value = mock_resp
        result = self.getReleaseInfo("https://example.com/release")
        self.assertIsNone(result)


class TestExtractUpdate(unittest.TestCase):
    from src.core.update import extractUpdate

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.test_dir, ignore_errors=True))
        self.zip_path = self.test_dir / "update.zip"
        self.extract_dir = self.test_dir / "extracted"

    def testExtractValidZip(self):
        with zipfile.ZipFile(self.zip_path, "w") as zf:
            zf.writestr("file1.txt", "hello")
            zf.writestr("sub/file2.txt", "world")
        result = self.extractUpdate(self.zip_path, self.extract_dir)
        self.assertTrue(result)
        self.assertTrue((self.extract_dir / "file1.txt").exists())
        self.assertTrue((self.extract_dir / "sub" / "file2.txt").exists())
        self.assertEqual((self.extract_dir / "file1.txt").read_text(), "hello")

    def testExtractBadZip(self):
        self.zip_path.write_bytes(b"not a zip file")
        result = self.extractUpdate(self.zip_path, self.extract_dir)
        self.assertFalse(result)

    def testExtractOverwritesExisting(self):
        self.extract_dir.mkdir()
        (self.extract_dir / "old.txt").write_text("old")
        with zipfile.ZipFile(self.zip_path, "w") as zf:
            zf.writestr("new.txt", "new")
        result = self.extractUpdate(self.zip_path, self.extract_dir)
        self.assertTrue(result)
        self.assertTrue((self.extract_dir / "new.txt").exists())
        self.assertFalse((self.extract_dir / "old.txt").exists())

    def testNonExistentZip(self):
        result = self.extractUpdate(self.test_dir / "nonexistent.zip", self.extract_dir)
        self.assertFalse(result)

    def testEmptyZip(self):
        with zipfile.ZipFile(self.zip_path, "w") as zf:
            pass
        result = self.extractUpdate(self.zip_path, self.extract_dir)
        self.assertTrue(result)


# CLI + 测试编排


_GROUP_REGISTRY = {}


def _register(group_name, test_classes, description=""):
    loader = unittest.TestLoader()
    count = sum(loader.loadTestsFromTestCase(cls).countTestCases() for cls in test_classes)
    _GROUP_REGISTRY[group_name] = {
        "classes": test_classes,
        "description": description,
        "count": count,
    }
    return test_classes


# Register all test groups
_register(
    "util",
    [
        TestEncodingMap,
        TestFileExtensions,
        TestConstants,
    ],
    "工具函数 (format_size, 编码, 扩展名)",
)

_register(
    "plugin",
    [
        TestPluginBase,
        TestCreateCustomPlugin,
        TestPluginManagerBasic,
        TestPluginManagerWithTempDir,
        TestPluginManagerEnableDisable,
        TestGetPluginModulePaths,
        TestPluginManagerConfig,
    ],
    "插件系统 (PluginBase, PluginManager)",
)

_register(
    "ai",
    [
        TestResolveImageUrls,
        TestAIClientBuildPromptContent,
        TestAIClientExtractUserMessage,
        TestLoadBalancing,
        TestAdaptersBuildChatRequest,
        TestAdapterMap,
        TestAIClientBuildFileMessage,
    ],
    "AI 适配器 (OpenAI, Claude, Ollama, Gemini)",
)

_register(
    "sync",
    [
        TestSyncModeConstants,
        TestTaskConfig,
        TestSyncDiffAlgorithm,
        TestExcludeRules,
        TestTarPackaging,
    ],
    "同步算法 (OpenList 插件)",
)

_register(
    "perf",
    [TestLazyInitPerformance, TestSyncPerformance],
    "性能测试 (启动时间, 文件读取, MD5, 同步, 内存)",
)

_register(
    "exception",
    [
        TestApiKeyErrors,
        TestNetworkErrors,
        TestPluginErrors,
        TestModelErrors,
        TestConfigErrors,
    ],
    "异常测试 (API Key, 网络, 插件, 文件, 模型, 配置)",
)

_register(
    "md",
    [
        TestExtractToc,
        TestRenderMarkdown,
    ],
    "Markdown 处理 (extractToc, renderMarkdown)",
)

_register(
    "timer",
    [
        TestLRUCache,
        TestWeakCallbackSet,
        TestCronField,
        TestCronExpr,
    ],
    "定时器 (LRUCache, WeakCallbackSet, CronField, CronExpr)",
)

_register(
    "file",
    [
        TestCompileRules,
        TestIsExcluded,
    ],
    "文件工具 (compileRules, isExcluded)",
)

_register(
    "config",
    [
        TestConfigAccess,
        TestConfigRecentFav,
    ],
    "配置管理 (get/set, recent, favorites)",
)

_register(
    "input",
    [
        TestParseHotkey,
        TestCheckHotkey,
        TestHotkeyNormalKey,
        TestPruneVkState,
        TestMatchToolHotkeys,
        TestCodeToKey,
    ],
    "输入处理 (parseHotkey, checkHotkey, 残留自愈)",
)

_register(
    "resolution",
    [
        TestParseResolution,
    ],
    "分辨率 (self.parseResolution)",
)

_register(
    "toolbox",
    [
        TestRenameItem,
    ],
    "工具箱 (RenameItem)",
)

_register(
    "update",
    [
        TestGetReleaseInfo,
        TestExtractUpdate,
    ],
    "更新 (getReleaseInfo, extractUpdate)",
)


# 内存调试工具 (用于 -m)


class _MemState:
    started = False
    prev_snapshot = None
    count = 0


_MEM = _MemState()


def _memInit():
    """初始化 tracemalloc 追踪"""
    gc.collect()

    tracemalloc.start()
    _MEM.started = True
    _MEM.prev_snapshot = None
    _MEM.count = 0
    logger.info("tracemalloc 追踪已启动")


def _gcTypeStats():
    """统计 gc 中存活对象按类型分布 TOP10 + Qt 类型汇总"""
    from collections import Counter

    counter = Counter(type(o).__name__ for o in gc.get_objects())
    qt_total = sum(v for k, v in counter.items() if k.startswith(("Q", "Py")))
    top = counter.most_common(10)
    logger.info(f"--- gc 对象 TOP10 (Qt 共 {qt_total} 个) ---")
    for name, count in top:
        logger.info(f"  {name}: {count}")


def _memTakeSnapshot():
    """拍快照并输出内存总量 TOP20 + gc 类型分布"""
    if not _MEM.started:
        return

    gc.collect()
    current = tracemalloc.take_snapshot()
    _MEM.count += 1

    stats = current.statistics("lineno")
    total_size = sum(stat.size for stat in stats)
    total_count = sum(stat.count for stat in stats)

    if total_size < 1024:
        logger.info(
            f"=== 内存快照 #{_MEM.count} 总量 {total_size:.0f}B / {total_count} 对象 TOP20 ==="
        )
    elif total_size < 1024 * 1024:
        logger.info(
            f"=== 内存快照 #{_MEM.count} 总量 {total_size/1024:.0f}KB / {total_count} 对象 TOP20 ==="
        )
    else:
        logger.info(
            f"=== 内存快照 #{_MEM.count} 总量 {total_size/1024/1024:.1f}MB / {total_count} 对象 TOP20 ==="
        )
    for i, stat in enumerate(stats[:20]):
        logger.info(f"  #{i+1} {stat}")
    _gcTypeStats()


def _countQtObjects(widget, max_depth=20):
    """递归统计 QObject 数量和类型"""
    from collections import Counter

    counter = Counter()

    def walk(obj, depth=0):
        if depth > max_depth:
            return
        counter[type(obj).__name__] += 1
        for child in obj.children():
            walk(child, depth + 1)

    walk(widget)
    return counter


def _logQtObjects(widget):
    """记录 Qt 对象统计到日志"""
    counter = _countQtObjects(widget)
    total = sum(counter.values())
    top = counter.most_common(15)
    logger.info(f"=== Qt 对象统计 (共 {total} 个) TOP15 ===")
    for name, count in top:
        logger.info(f"  {name}: {count}")


def _patchMainWindow():
    """给 MainWindow.__init__ 打补丁，注入内存追踪和 Qt 对象计数定时器"""
    from src.main import MainWindow

    original_init = MainWindow.__init__

    def patchedInit(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        timer = QTimer(self)
        timer.timeout.connect(
            lambda: (
                _memTakeSnapshot(),
                _logQtObjects(self),
            )
        )
        timer.start(5000)

    MainWindow.__init__ = patchedInit
    logger.info("MainWindow.__init__")


def main():
    parser = argparse.ArgumentParser(
        description="O 测试模块",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    for name, info in _GROUP_REGISTRY.items():
        parser.add_argument(
            f"--{name}", action="store_true", help=f'{info["description"]} ({info["count"]} 个测试)'
        )
    parser.add_argument("--all", action="store_true", help="运行全部测试")
    parser.add_argument("-m", "--me", action="store_true", help="启动程序本体并启用内存追踪")

    args = parser.parse_args()

    if args.me:
        _memInit()
        sys.argv = [sys.argv[0]]
        from o import main as launch_app
        _patchMainWindow()
        launch_app()
        return

    if args.all:
        selected = list(_GROUP_REGISTRY.keys())
    else:
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
