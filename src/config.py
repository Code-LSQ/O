import os
import copy
import json
import tempfile
from pathlib import Path
from typing import Any, Dict

from PySide6.QtWidgets import QApplication, QWidget, QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout, QLabel, QLineEdit, QSpinBox, QDoubleSpinBox, QCheckBox, QComboBox, QPushButton, QListWidget, QListWidgetItem, QAbstractSpinBox, QTableWidget, QTableWidgetItem, QHeaderView, QTextEdit, QFontComboBox, QScrollArea, QStackedWidget, QFrame
from PySide6.QtCore import Signal, Qt, QEvent, QSize

from src.util import root, config_file, logger, Singleton, ManagePair, dictDialog, setAutoStart, setWindowsMenu, Translator, tr, systemLanguage, convertPath, getFilePath, theme_dir, dialogBox, messageBox, inputDialog
from src.core.input import translate_key_to_str, KeyCaptureFilter

DEFAULT_CONFIG = {
    "theme": "Light",
    "language": "简体中文",
    "font_family": "微软雅黑",
    "font_size": 12,
    "tray": False,
    "usage": True,
    "auto_start": False,
    "context_menu": False,
    "Launch": {
        "Runtime": {
            "Python": "",
            "Java": "",
            "Temp_Path": []
        },
        "hotkey": "",
        "mouse_side": False,
        "double_ctrl": False,
        "capture": True,
        "on_top": False,
        "run_hide": False,
        "hover_switch": False,
        "layout": "horizontal",
        "path_mode": "absolute",
        "active_group": "默认",
        "g_w": 80, "g_h": 30,
        "i_w": 100, "i_h": 75,
        "icon": 32, "padding": 8,
        "width": 600,
        "height": 400,
        "x": None,
        "y": None,
        "tools": {"默认": []}
    },
    "Edit": {
        "width": 1000,
        "height": 650,
        "x": None,
        "y": None,
        "line_spacing": 0,
        "multi_tab": True,
        "wrap": False,
        "indent": True,
        "line_numbers": False,
        "auto_save": False,
        "auto_save_interval": 60,
        "backup": True,
        "recent": [],
        "open": [],
        "folder": "",
        "favorites": [],
        "shortcuts": {
            "new_file": "Ctrl+N",
            "open_file": "Ctrl+O",
            "save_file": "Ctrl+S",
            "save_as": "Ctrl+Shift+S",
            "find": "Ctrl+F",
            "replace": "Ctrl+H",
            "undo": "Ctrl+Z",
            "redo": "Ctrl+Y",
            "go_to_line": "Ctrl+G",
            "jump_next": "Ctrl+D"
        },
        "find_presets": [],
        "engine": {
            "Bing": "https://cn.bing.com/search?q={query}",
            "Google": "https://www.google.com/search?q={query}",
            "GitHub": "https://github.com/search?q={query}"
        }
    },
    "Plugin": {},
    "AI": {
        "enabled": False,
        "stream": False,
        "autocomplete": False,
        "dialog": "",
        "active": "默认配置",
        "profiles": {
            "默认配置": {
                "api_key": "",
                "model": "",
                "api_url": "",
                "custom_url": "",
                "temperature": 0.7,
                "max_tokens": 2000,
                "endpoint": ""
            }
        },
        "load_balance": {
            "enabled": False,
            "profiles": {}
        },
        "prompts": {
            "系统提示词": "",
            "自动补全": "请根据以下内容补全后续内容，输出不要超过100个字符，不要与已有内容重复，只输出补全的部分：\n\n{request}",
            "提取内容": "请提取以下内容中的关键信息，按条理清晰的结构输出，不需要额外解释。",
            "代码": "你是一位经验丰富的软件工程师，在多种编程语言、框架、设计模式和最佳实践方面拥有广泛的知识。请帮助我编写和优化以下代码。\n\n{request}",
            "翻译": "你是一名翻译，请将以下文本 {request} 翻译成中文，你只需要返回翻译结果，无需额外解释。",
            "写作": "你是一名作家，请帮助我改进以下文本 {request} 的流畅性和表达，不需要过多的修饰和形容词。"
        }
    }
}


_BUILTIN_PROMPTS = ["系统提示词", "自动补全"]

class ConfigManager(Singleton):
    _initialized = False

    def _init_impl(self, config_path: Path = None):
        if config_path is None:
            self.config_path = config_file
        elif Path(config_path).is_absolute():
            self.config_path = Path(os.path.normpath(config_path)).resolve()
        else:
            self.config_path = root / config_path
        self.config: Dict[str, Any] = {}
        self._load()

    @staticmethod
    def _deep_update(base: dict, update: dict) -> dict:
        for key, value in update.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                ConfigManager._deep_update(base[key], value)
            else:
                base[key] = value
        return base

    def _load(self):
        """加载配置文件"""
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    loaded_config = json.load(f)
                self.config = copy.deepcopy(DEFAULT_CONFIG)
                self.config = self._deep_update(self.config, loaded_config)
                logger.info(f"配置文件加载成功: {self.config_path}")
            except json.JSONDecodeError:
                logger.exception("配置文件格式错误")
                self._backup_and_reset_config()
                logger.info(f"配置文件已备份，创建默认配置")
            except Exception:
                logger.exception("配置文件加载失败")
                self.config = copy.deepcopy(DEFAULT_CONFIG)
                self.config["language"] = systemLanguage()
        else:
            self.config = copy.deepcopy(DEFAULT_CONFIG)
            self.config["language"] = systemLanguage()
            self.save()
            logger.info(f"配置文件不存在，已创建默认配置: {self.config_path}")

    def _backup_and_reset_config(self):
        """备份损坏的配置文件并重置为默认配置"""
        backup_path = self.config_path.with_suffix('.json.bak')
        try:
            if self.config_path.exists():
                self.config_path.rename(backup_path)
        except Exception:
            logger.exception("备份配置文件失败")
        self.config = copy.deepcopy(DEFAULT_CONFIG)
        self.config["language"] = systemLanguage()
        self.save()

    def save(self):
        """原子写入配置文件"""
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(suffix='.tmp', prefix='config_', dir=self.config_path.parent)
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(fd)
            os.replace(tmp, str(self.config_path))
            logger.info(f"配置文件已保存: {self.config_path}")
        except Exception:
            logger.exception("配置文件保存失败")
            if tmp and os.path.exists(tmp):
                os.unlink(tmp)

    def get(self, key: str, default: Any = None) -> Any:
        """获取配置项，支持点号访问嵌套字段如AI.enabled"""
        if '.' in key:
            parts = key.split('.')
            value = self.config
            for part in parts:
                if isinstance(value, dict):
                    value = value.get(part)
                else:
                    return default
            return value if value is not None else default
        return self.config.get(key, default)

    def set(self, key: str, value: Any):
        if '.' in key:
            parts = key.split('.')
            target = self.config
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            target[parts[-1]] = value
        else:
            self.config[key] = value

    def update_window_geometry(self, geometry):
        self.set("Edit.width", geometry.width())
        self.set("Edit.height", geometry.height())
        self.set("Edit.x", geometry.x())
        self.set("Edit.y", geometry.y())

    def add_recent_file(self, file_path: str):
        file_path = os.path.normpath(file_path)
        recent = self.get("Edit.recent", [])
        if file_path in recent:
            recent.remove(file_path)
        recent.insert(0, file_path)
        self.set("Edit.recent", recent[:10])

    def get_favorites(self) -> list:
        return self.get("Edit.favorites", [])

    def add_favorite(self, file_path: str):
        file_path = os.path.normpath(file_path)
        favorites = self.get("Edit.favorites", [])
        if file_path not in favorites:
            favorites.insert(0, file_path)
            self.set("Edit.favorites", favorites)

    def remove_favorite(self, file_path: str):
        file_path = os.path.normpath(file_path)
        favorites = self.get("Edit.favorites", [])
        if file_path in favorites:
            favorites.remove(file_path)
            self.set("Edit.favorites", favorites)

    def is_favorite(self, file_path: str) -> bool:
        file_path = os.path.normpath(file_path)
        return file_path in self.get("Edit.favorites", [])


def getConfig(config_path: str = "data/config.json") -> ConfigManager:
    """获取配置管理器单例"""
    return ConfigManager(config_path)

class SettingsDialog(QDialog):
    """设置对话框"""

    settings_changed = Signal(dict)
    multi_tab_changed = Signal(bool)
    restart_required = Signal()
    
    SHORTCUT_MAP = {
        "new_file": "新建", "open_file": "打开", "save_file": "保存", "save_as": "另存为",
        "find": "查找", "replace": "替换", "undo": "撤销", "redo": "重做",
        "go_to_line": "跳转到行", "jump_next": "选择下一个匹配"
    }

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self._working_profiles = copy.deepcopy(self.config.get("AI.profiles", {"默认配置": {}}))
        self._working_load_balance = copy.deepcopy(self.config.get("AI.load_balance", {"enabled": False, "profiles": {}}))
        self.setWindowTitle(tr("设置"))
        self.setMinimumSize(500, 500)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)

        content_layout = QHBoxLayout()
        self.tab_list = QListWidget()
        self.tab_list.setFixedWidth(80)
        self.tab_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.stack = QStackedWidget()
        content_layout.addWidget(self.tab_list)
        content_layout.addWidget(self.stack, 1)
        layout.addLayout(content_layout)

        tabs = [
            (self.init_options_tab(), tr("选项")),
            (self.init_edit_tab(), tr("编辑")),
            (self.init_ai_tab(), "AI"),
            (self.init_shortcuts_tab(), tr("快捷键")),
        ]
        for widget, name in tabs:
            scroll = QScrollArea()
            scroll.setWidget(widget)
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            self.stack.addWidget(scroll)
            item = QListWidgetItem(name)
            item.setSizeHint(QSize(0, 36))
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.tab_list.addItem(item)
        self.tab_list.currentRowChanged.connect(self.stack.setCurrentIndex)

        # 按钮
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        self.btn_ok = QPushButton(tr("确定"))
        self.btn_ok.clicked.connect(self.accept)
        button_layout.addWidget(self.btn_ok)
        
        self.btn_cancel = QPushButton(tr("取消"))
        self.btn_cancel.clicked.connect(self.reject)
        button_layout.addWidget(self.btn_cancel)
        
        layout.addLayout(button_layout)

    def getThemes(self):
        """获取可用的主题列表（从src/theme目录下扫描qss文件）"""
        themes = []
        for file in theme_dir.iterdir():
            if file.suffix.lower() == '.qss':
                themes.append(file.stem)
        return sorted(themes)

    def init_options_tab(self):
        """选项设置"""
        tab = QWidget()
        layout = QFormLayout(tab)

        self.language_combo = QComboBox()
        self.language_combo.addItems(Translator().getLanguages())
        current_lang = self.config.get("language", "简体中文")
        idx = self.language_combo.findText(current_lang)
        self.language_combo.setCurrentIndex(idx if idx >= 0 else 0)
        layout.addRow(tr("语言"), self.language_combo)

        self.theme_combo = QComboBox()
        available_themes = self.getThemes()
        self.theme_combo.addItems(available_themes)
        current_theme = self.config.get("theme", "Light")
        idx = self.theme_combo.findText(current_theme)
        if idx >= 0:
            self.theme_combo.setCurrentIndex(idx)
        layout.addRow(tr("主题"), self.theme_combo)

        self.font_family_edit = QFontComboBox()
        self.font_family_edit.setCurrentText(self.config.get("font_family", "微软雅黑"))
        layout.addRow(tr("字体"), self.font_family_edit)

        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(6, 72)
        self.font_size_spin.setValue(self.config.get("font_size", 12))
        self.font_size_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        layout.addRow(tr("字号"), self.font_size_spin)

        self.context_menu_check = QCheckBox(tr("右键菜单"))
        self.context_menu_check.setChecked(self.config.get("context_menu", False))

        self.auto_start = QCheckBox(tr("开机自启动"))
        self.auto_start.setChecked(self.config.get("auto_start", False))

        self.tray = QCheckBox(tr("最小化到托盘"))
        self.tray.setChecked(self.config.get("tray", False))

        self.usage_label = QCheckBox(tr("显示资源占用"))
        self.usage_label.setChecked(self.config.get("usage", True))

        option_grid_widget = QWidget()
        option_grid = QGridLayout(option_grid_widget)
        option_grid.setContentsMargins(20, 0, 0, 0)
        option_grid.addWidget(self.context_menu_check, 0, 0)
        option_grid.addWidget(self.auto_start, 0, 1)
        option_grid.addWidget(self.tray, 1, 0)
        option_grid.addWidget(self.usage_label, 1, 1)
        option_grid.setColumnStretch(0, 1)
        option_grid.setColumnStretch(1, 1)
        layout.addRow(option_grid_widget)

        launcher_config = self.config.get("Launch", {})

        # 启动选项
        self.launcher_mouse_side_check = QCheckBox(tr("鼠标侧键响应"))
        self.launcher_mouse_side_check.setChecked(launcher_config.get("mouse_side", False))

        self.launcher_on_top_check = QCheckBox(tr("置顶显示"))
        self.launcher_on_top_check.setChecked(launcher_config.get("on_top", False))

        self.launcher_hide = QCheckBox("运行后隐藏")
        self.launcher_hide.setChecked(launcher_config.get("run_hide", False))

        self.launcher_hover_switch_check = QCheckBox("悬停切换分组")
        self.launcher_hover_switch_check.setChecked(launcher_config.get("hover_switch", False))

        self.launcher_double_ctrl_check = QCheckBox("连按 Ctrl 响应")
        self.launcher_double_ctrl_check.setChecked(launcher_config.get("double_ctrl", False))

        self.launcher_capture_check = QCheckBox("获取剪贴板与选中")
        self.launcher_capture_check.setChecked(launcher_config.get("capture", True))

        launch_grid_widget = QWidget()
        launch_grid = QGridLayout(launch_grid_widget)
        launch_grid.setContentsMargins(20, 0, 0, 0)
        launch_grid.addWidget(self.launcher_mouse_side_check, 0, 0)
        launch_grid.addWidget(self.launcher_hover_switch_check, 0, 1)
        launch_grid.addWidget(self.launcher_on_top_check, 1, 0)
        launch_grid.addWidget(self.launcher_hide, 1, 1)

        launch_grid.addWidget(self.launcher_double_ctrl_check, 2, 0)
        launch_grid.addWidget(self.launcher_capture_check, 2, 1)
        launch_grid.setColumnStretch(0, 1)
        launch_grid.setColumnStretch(1, 1)
        layout.addRow(launch_grid_widget)

        # 启动快捷键
        self.launcher_hotkey_edit = QLineEdit()
        self.launcher_hotkey_edit.setText(launcher_config.get("hotkey", ""))
        self.launcher_hotkey_edit.setPlaceholderText("点击输入快捷键")
        self.launcher_key_capture_filter = KeyCaptureFilter(self)
        self.launcher_key_capture_filter.key_captured.connect(self._on_launcher_key_captured)
        self.launcher_hotkey_edit.installEventFilter(self.launcher_key_capture_filter)
        layout.addRow("快捷键", self.launcher_hotkey_edit)

        # 分组排列
        self.launcher_layout_combo = QComboBox()
        self.launcher_layout_combo.addItems(["横向", "纵向"])
        current_layout = launcher_config.get("layout", "horizontal")
        self.launcher_layout_combo.setCurrentIndex(0 if current_layout == "horizontal" else 1)
        layout.addRow("分组排列", self.launcher_layout_combo)

        self.launcher_path_mode_combo = QComboBox()
        self.launcher_path_mode_combo.addItems(["绝对路径", "相对路径"])
        current_path_mode = launcher_config.get("path_mode", "absolute")
        self.launcher_path_mode_combo.setCurrentIndex(0 if current_path_mode == "absolute" else 1)
        layout.addRow("路径", self.launcher_path_mode_combo)

        # 启动器外观
        self.g_w_spin = QSpinBox()
        self.g_w_spin.setRange(40, 200)
        self.g_w_spin.setValue(launcher_config.get("g_w", 80))
        self.g_w_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.g_h_spin = QSpinBox()
        self.g_h_spin.setRange(20, 100)
        self.g_h_spin.setValue(launcher_config.get("g_h", 30))
        self.g_h_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)

        g_layout = QHBoxLayout()
        g_layout.addWidget(QLabel("宽"))
        g_layout.addWidget(self.g_w_spin)
        g_layout.addWidget(QLabel("高"))
        g_layout.addWidget(self.g_h_spin)
        g_layout.addStretch()
        layout.addRow("分组按钮", g_layout)

        self.i_w_spin = QSpinBox()
        self.i_w_spin.setRange(40, 300)
        self.i_w_spin.setValue(launcher_config.get("i_w", 100))
        self.i_w_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.i_h_spin = QSpinBox()
        self.i_h_spin.setRange(40, 200)
        self.i_h_spin.setValue(launcher_config.get("i_h", 75))
        self.i_h_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)

        i_layout = QHBoxLayout()
        i_layout.addWidget(QLabel("宽"))
        i_layout.addWidget(self.i_w_spin)
        i_layout.addWidget(QLabel("高"))
        i_layout.addWidget(self.i_h_spin)
        i_layout.addStretch()
        layout.addRow("项目按钮", i_layout)

        self.icon_spin = QSpinBox()
        self.icon_spin.setRange(16, 128)
        self.icon_spin.setValue(launcher_config.get("icon", 32))
        self.icon_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.padding_spin = QSpinBox()
        self.padding_spin.setRange(0, 40)
        self.padding_spin.setValue(launcher_config.get("padding", 8))
        self.padding_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        ip_layout = QHBoxLayout()
        ip_layout.addWidget(QLabel("图标大小"))
        ip_layout.addWidget(self.icon_spin)
        ip_layout.addWidget(QLabel("边距"))
        ip_layout.addWidget(self.padding_spin)
        ip_layout.addStretch()
        layout.addRow(ip_layout)

        # Python 环境
        python_layout = QHBoxLayout()
        self.python_path_edit = QLineEdit()
        self.python_path_edit.setText(self.config.get("Launch.Runtime.Python", ""))
        python_layout.addWidget(self.python_path_edit)

        self.python_browse_btn = QPushButton("浏览")
        self.python_browse_btn.clicked.connect(lambda: getFilePath(self, "Python", "Python (*.exe);;所有文件 (*)", edit=self.python_path_edit))
        python_layout.addWidget(self.python_browse_btn)
        layout.addRow("Python", python_layout)

        # Java 环境
        java_layout = QHBoxLayout()
        self.java_path_edit = QLineEdit()
        self.java_path_edit.setText(self.config.get("Launch.Runtime.Java", ""))
        java_layout.addWidget(self.java_path_edit)

        self.java_browse_btn = QPushButton("浏览")
        self.java_browse_btn.clicked.connect(lambda: getFilePath(self, "Java", "Java (*.exe);;所有文件 (*)", edit=self.java_path_edit))
        java_layout.addWidget(self.java_browse_btn)
        layout.addRow("Java", java_layout)

        # 环境变量
        layout.addRow(QLabel("环境变量"))
        self.temp_paths_edit = QTextEdit()
        self.temp_paths_edit.setPlaceholderText("每行一个文件夹")
        self.temp_paths_edit.setStyleSheet("background: #dddddd")
        env_config = self.config.get("Launch.Runtime", {})
        Temp_Path = env_config.get("Temp_Path", [])
        self.temp_paths_edit.setPlainText("\n".join(Temp_Path))
        self.temp_paths_edit.setMinimumHeight(80)
        layout.addRow("", self.temp_paths_edit)

        self._launcher_config = launcher_config
        return tab

    def init_edit_tab(self):
        """编辑器设置"""
        tab = QWidget()
        layout = QFormLayout(tab)

        self.line_numbers = QCheckBox(tr("行号"))
        self.line_numbers.setChecked(self.config.get("Edit.line_numbers", False))

        self.multi_tab_check = QCheckBox(tr("多标签页"))
        self.multi_tab_check.setChecked(self.config.get("Edit.multi_tab", True))

        self.history_backup_check = QCheckBox(tr("历史版本"))
        self.history_backup_check.setChecked(self.config.get("Edit.backup", True))

        self.auto_wrap = QCheckBox(tr("自动换行"))
        self.auto_wrap.setChecked(self.config.get("Edit.wrap", False))

        self.auto_indent = QCheckBox("自动缩进")
        self.auto_indent.setChecked(self.config.get("Edit.indent", True))

        self.line_spacing_spin = QSpinBox()
        self.line_spacing_spin.setRange(0, 100)
        self.line_spacing_spin.setValue(self.config.get("Edit.line_spacing", 0))
        self.line_spacing_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.line_spacing_spin.setToolTip("0=禁用")
        layout.addRow(tr("行距"), self.line_spacing_spin)

        editor_grid_widget = QWidget()
        editor_grid = QGridLayout(editor_grid_widget)
        editor_grid.setContentsMargins(20, 0, 0, 0)
        editor_grid.addWidget(self.auto_wrap, 0, 0)
        editor_grid.addWidget(self.auto_indent, 0, 1)
        editor_grid.addWidget(self.line_numbers, 1, 0)
        editor_grid.addWidget(self.multi_tab_check, 1, 1)
        editor_grid.addWidget(self.history_backup_check, 2, 0)
        editor_grid.setColumnStretch(0, 1)
        editor_grid.setColumnStretch(1, 1)
        layout.addRow(editor_grid_widget)

        # 自动保存
        auto_save_layout = QHBoxLayout()
        self.auto_save_check = QCheckBox("启用")
        self.auto_save_check.setChecked(self.config.get("Edit.auto_save", False))
        auto_save_layout.addWidget(self.auto_save_check)
        self.auto_save_interval = QSpinBox()
        self.auto_save_interval.setRange(10, 600)
        self.auto_save_interval.setSuffix(" 秒")
        self.auto_save_interval.setValue(self.config.get("Edit.auto_save_interval", 60))
        self.auto_save_interval.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        auto_save_layout.addWidget(self.auto_save_interval)
        layout.addRow("自动保存", auto_save_layout)

        # 搜索引擎管理
        self._init_search_engines_section(layout)

        return tab

    def _init_search_engines_section(self, layout):
        """搜索引擎管理"""
        self.search_engines_list = QListWidget()
        self.search_engines_list.setMaximumHeight(80)
        search_engines = self.config.get("Edit.engine", {})
        for name, url in search_engines.items():
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, url)
            self.search_engines_list.addItem(item)
        layout.addRow(self.search_engines_list)

        engine_btn_layout = QHBoxLayout()
        self.add_engine_btn = QPushButton(tr("添加"))
        self.add_engine_btn.clicked.connect(self._add_search_engine)
        engine_btn_layout.addWidget(self.add_engine_btn)

        self.edit_engine_btn = QPushButton(tr("编辑"))
        self.edit_engine_btn.clicked.connect(self._edit_search_engine)
        engine_btn_layout.addWidget(self.edit_engine_btn)

        self.remove_engine_btn = QPushButton(tr("删除"))
        self.remove_engine_btn.clicked.connect(self._remove_search_engine)
        engine_btn_layout.addWidget(self.remove_engine_btn)

        engine_btn_layout.addStretch(1)
        layout.setLayout(11, QFormLayout.ItemRole.SpanningRole, engine_btn_layout)

    def _engine_dialog(self, title: str, name: str = "", url: str = ""):
        """搜索引擎编辑对话框，返回 (名称, URL) 或 (None, None)"""
        while True:
            dialog = QDialog(self)
            dialog.setWindowTitle(title)
            dialog.setMinimumWidth(400)

            d_layout = QVBoxLayout(dialog)
            form_layout = QFormLayout()

            name_edit = QLineEdit()
            name_edit.setText(name)
            name_edit.setPlaceholderText("搜索引擎名称")
            form_layout.addRow("名称", name_edit)

            url_edit = QLineEdit()
            url_edit.setText(url)
            url_edit.setPlaceholderText("URL，使用 {query} 作为搜索关键词占位符")
            form_layout.addRow("URL", url_edit)

            d_layout.addLayout(form_layout)

            if dialogBox(d_layout, dialog):
                n = name_edit.text().strip()
                u = url_edit.text().strip()
                if n and u:
                    if "{query}" not in u:
                        messageBox(self, "警告", "URL必须包含 {query} 占位符", 1)
                        continue
                    return n, u
            return None, None

    def _add_search_engine(self):
        name, url = self._engine_dialog("添加搜索引擎")
        if name and url:
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, url)
            self.search_engines_list.addItem(item)

    def _edit_search_engine(self):
        current_item = self.search_engines_list.currentItem()
        if not current_item:
            messageBox(self, "警告", "请先选择一个要编辑的搜索引擎", 1)
            return
        name, url = self._engine_dialog("编辑搜索引擎", current_item.text(), current_item.data(Qt.ItemDataRole.UserRole))
        if name and url:
            current_item.setText(name)
            current_item.setData(Qt.ItemDataRole.UserRole, url)

    def _remove_search_engine(self):
        """删除搜索引擎"""
        current_row = self.search_engines_list.currentRow()
        if current_row >= 0:
            self.search_engines_list.takeItem(current_row)

    def _get_current_profile(self):
        """获取当前选中的profile配置"""
        profile_name = self.ai_profile_combo.currentText()
        if not profile_name:
            return {}
        profiles = self._working_profiles
        return profiles.get(profile_name, {})
    
    def _get_api_url_from_endpoint_name(self, endpoint_name):
        """根据端点名称获取API URL"""
        if endpoint_name == "自定义":
            return self.ai_custom_url_edit.text().strip() if hasattr(self, 'ai_custom_url_edit') else ""
        
        for name, cls, url in AI_ADAPTER:
            if name == endpoint_name:
                return url
        return ""


    def init_ai_tab(self):
        """AI设置"""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        top_row = QHBoxLayout()
        self.ai_enabled_check = QCheckBox("启用AI功能")
        self.ai_enabled_check.setChecked(self.config.get("AI.enabled", False))
        top_row.addWidget(self.ai_enabled_check)
        top_row.addStretch()
        self.ai_autocomplete_check = QCheckBox("自动补全")
        self.ai_autocomplete_check.setChecked(self.config.get("AI.autocomplete", False))
        top_row.addWidget(self.ai_autocomplete_check)
        top_row.addStretch()
        self.ai_stream_check = QCheckBox("流式输出")
        self.ai_stream_check.setChecked(self.config.get("AI.stream", True))
        top_row.addWidget(self.ai_stream_check)
        top_row.addStretch()
        layout.addLayout(top_row)

        self._setup_profile_section(layout)
        self._setup_endpoint_section(layout)
        self._setup_api_key_section(layout)
        self._setup_model_section(layout)
        self._setup_parameter_section(layout)
        self._setup_prompt_section(layout)

        self.refresh_model_btn.clicked.connect(self._refresh_models)
        self._load_current_profile()

        layout.addStretch()
        return tab

    def _setup_profile_section(self, parent_layout):
        """配置选择区"""
        layout = QHBoxLayout()
        layout.addWidget(QLabel("当前配置"))
        self.ai_profile_combo = QComboBox()
        profiles = self._working_profiles
        for name in profiles.keys():
            self.ai_profile_combo.addItem(name, name)
        active_profile = self.config.get("AI.active", "默认配置")
        idx = self.ai_profile_combo.findText(active_profile)
        if idx >= 0:
            self.ai_profile_combo.setCurrentIndex(idx)
        self.ai_profile_combo.currentIndexChanged.connect(self._on_profile_changed)
        layout.addWidget(self.ai_profile_combo)

        self.rename_profile_btn = QPushButton("重命名")
        self.rename_profile_btn.setFixedWidth(70)
        self.rename_profile_btn.clicked.connect(self._rename_profile)
        layout.addWidget(self.rename_profile_btn)

        self.new_profile_btn = QPushButton("新建")
        self.new_profile_btn.setFixedWidth(60)
        self.new_profile_btn.clicked.connect(self._new_profile)
        layout.addWidget(self.new_profile_btn)

        self.copy_profile_btn = QPushButton("复制")
        self.copy_profile_btn.setFixedWidth(60)
        self.copy_profile_btn.clicked.connect(self._copy_profile)
        layout.addWidget(self.copy_profile_btn)

        self.delete_profile_btn = QPushButton("删除")
        self.delete_profile_btn.setFixedWidth(60)
        self.delete_profile_btn.clicked.connect(self._delete_profile)
        layout.addWidget(self.delete_profile_btn)

        layout.addStretch()
        parent_layout.addLayout(layout)

    def _setup_endpoint_section(self, parent_layout):
        """API端点配置区"""
        layout = QFormLayout()
        self.ai_combo = QComboBox()

        active_profile_name = self.config.get("AI.active", "默认配置")
        profiles = self.config.get("AI.profiles", {})
        profile = profiles.get(active_profile_name, {})
        selected_endpoint = profile.get("endpoint", "")

        for name, cls, url in AI_ADAPTER:
            self.ai_combo.addItem(name, url)

        index = self.ai_combo.findText(selected_endpoint)
        if index >= 0:
            self.ai_combo.setCurrentIndex(index)

        self.ai_custom_url_edit = QLineEdit()
        self.ai_custom_url_edit.setPlaceholderText("输入API地址")

        current_url = ""
        for name, cls, url in AI_ADAPTER:
            if name == selected_endpoint:
                current_url = url
                break
        if selected_endpoint == "自定义":
            current_url = profile.get("custom_url", "")
        self.ai_custom_url_edit.setText(current_url)

        self.ai_combo.currentTextChanged.connect(self._on_endpoint_changed)
        layout.addRow("API 接口", self.ai_combo)
        layout.addRow("接口地址", self.ai_custom_url_edit)
        parent_layout.addLayout(layout)

    def _setup_api_key_section(self, parent_layout):
        """API Key区"""
        layout = QHBoxLayout()
        layout.addWidget(QLabel("API Key"))
        self.ai_api_key_edit = QLineEdit()
        self.ai_api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self.ai_api_key_edit)
        parent_layout.addLayout(layout)

    def _setup_model_section(self, parent_layout):
        """模型选择区"""
        layout = QHBoxLayout()
        layout.addWidget(QLabel("模型"))
        self.refresh_model_btn = QPushButton("刷新")
        self.refresh_model_btn.setToolTip("刷新模型列表")
        self.refresh_model_btn.setFixedWidth(70)
        layout.addWidget(self.refresh_model_btn)

        self.ai_model = QComboBox()
        self.ai_model.setEditable(True)
        self.ai_model.setFixedWidth(300)
        layout.addWidget(self.ai_model)
        parent_layout.addLayout(layout)

    def _setup_parameter_section(self, parent_layout):
        """参数区（温度/Token + 按钮）"""
        row = QHBoxLayout()
        left = QHBoxLayout()
        left.addWidget(QLabel("温度"))
        self.ai_temperature_spin = QDoubleSpinBox()
        self.ai_temperature_spin.setRange(0.0, 1.0)
        self.ai_temperature_spin.setSingleStep(0.1)
        self.ai_temperature_spin.setFixedWidth(120)
        self.ai_temperature_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        left.addWidget(self.ai_temperature_spin)
        left.addStretch()
        row.addLayout(left)
        right = QHBoxLayout()
        right.addStretch()
        right.addWidget(QLabel("最大Token"))
        self.ai_max_tokens_spin = QSpinBox()
        self.ai_max_tokens_spin.setRange(100, 10000)
        self.ai_max_tokens_spin.setFixedWidth(120)
        self.ai_max_tokens_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        right.addWidget(self.ai_max_tokens_spin)
        row.addLayout(right)
        parent_layout.addLayout(row)

        self._lb_status_layout = QVBoxLayout()
        self._refresh_lb_status()
        parent_layout.addLayout(self._lb_status_layout)

        btn_row = QHBoxLayout()
        self.balance_btn = QPushButton("负载均衡")
        self.balance_btn.clicked.connect(self._show_balance_dialog)
        btn_row.addWidget(self.balance_btn)
        self.test_btn = QPushButton("测试")
        self.test_btn.clicked.connect(self._show_test_dialog)
        btn_row.addWidget(self.test_btn)
        btn_row.addStretch()
        parent_layout.addLayout(btn_row)

    def _refresh_lb_status(self):
        while self._lb_status_layout.count():
            item = self._lb_status_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            if item.layout():
                while item.layout().count():
                    child = item.layout().takeAt(0)
                    if child.widget():
                        child.widget().deleteLater()

        disabled = getattr(AIClient, '_lb_disabled', {})

        for name in self._working_profiles:
            if name not in disabled:
                continue

            row = QHBoxLayout()
            row.addWidget(QLabel(name))
            lbl = QLabel("连续失败，禁用中")
            lbl.setStyleSheet("color: red; font-weight: bold")
            row.addWidget(lbl)
            btn = QPushButton("启用")
            btn.setFixedWidth(50)
            btn.clicked.connect(lambda checked, n=name: self._enable_lb_profile(n))
            row.addWidget(btn)
            row.addStretch()
            self._lb_status_layout.addLayout(row)

    def _enable_lb_profile(self, name):
        AIClient._lb_disabled.pop(name, None)
        AIClient._lb_failures.pop(name, None)
        self._refresh_lb_status()

    def _setup_prompt_section(self, parent_layout):
        """提示词管理区"""
        self._init_prompt_manager()
        layout = QVBoxLayout()
        layout.addWidget(self.prompt_manager.pair_list)

        btn_layout = QHBoxLayout()
        btn_layout.addWidget(self.prompt_manager.add_btn)
        btn_layout.addWidget(self.prompt_manager.edit_btn)
        btn_layout.addWidget(self.prompt_manager.delete_btn)
        layout.addLayout(btn_layout)
        parent_layout.addLayout(layout)

    def _show_balance_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("负载均衡配置")
        dlg.setMinimumSize(300, 300)
        layout = QVBoxLayout(dlg)

        self._lb_enable_cb = QCheckBox("启用负载均衡")
        self._lb_enable_cb.setChecked(self._working_load_balance.get("enabled", False))
        layout.addWidget(self._lb_enable_cb)

        layout.addWidget(QLabel("优先级（0 禁用，1-10 值越小越优先）；权重：同优先级内按比例分配"))
        existing = self._working_load_balance.get("profiles", {})
        profiles = self._working_profiles
        rows = []
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_widget = QWidget()
        form = QFormLayout(scroll_widget)
        for name in profiles:
            cfg = existing.get(name, {"priority": 1, "weight": 1})
            priority_sb = QSpinBox()
            priority_sb.setRange(0, 10)
            priority_sb.setSpecialValueText("禁用")
            priority_sb.setValue(cfg.get("priority", 1))
            priority_sb.setMinimumWidth(60)
            priority_sb.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            weight_sb = QSpinBox()
            weight_sb.setRange(1, 100)
            weight_sb.setValue(cfg.get("weight", 1))
            weight_sb.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            h = QHBoxLayout()
            h.addWidget(QLabel("优先级"))
            h.addWidget(priority_sb)
            h.addSpacing(10)
            h.addWidget(QLabel("权重"))
            h.addWidget(weight_sb)
            h.addStretch()
            form.addRow(name, h)
            rows.append((name, priority_sb, weight_sb))
        scroll.setWidget(scroll_widget)
        layout.addWidget(scroll)

        if dialogBox(layout, dlg):
            profiles_dict = {}
            for name, p_sb, w_sb in rows:
                p = p_sb.value()
                profiles_dict[name] = {"priority": p, "weight": w_sb.value()}
            self._working_load_balance = {
                "enabled": self._lb_enable_cb.isChecked(),
                "profiles": profiles_dict
            }

    def _show_test_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("测试配置连通性")
        dlg.setMinimumWidth(450)
        layout = QVBoxLayout(dlg)

        profiles = self._working_profiles
        items = []
        for name in profiles:
            cb = QCheckBox(name)
            cb.setChecked(False)
            status = QLabel("等待测试")
            status.setFixedWidth(200)
            row = QHBoxLayout()
            row.addWidget(cb)
            row.addWidget(status)
            row.addStretch()
            layout.addLayout(row)
            items.append((name, cb, status))

        btn_layout = QHBoxLayout()
        def _test_selected():
            self._run_test(dlg, items, selected_only=True)
        def _test_all():
            self._run_test(dlg, items, selected_only=False)

        test_sel_btn = QPushButton("测试已选")
        test_sel_btn.clicked.connect(_test_selected)
        btn_layout.addWidget(test_sel_btn)
        test_all_btn = QPushButton("测试全部")
        test_all_btn.clicked.connect(_test_all)
        btn_layout.addWidget(test_all_btn)
        btn_layout.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(dlg.close)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)
        dlg.exec()

    def _run_test(self, dlg, items, selected_only):
        profiles = self._working_profiles
        for name, cb, status in items:
            if selected_only and not cb.isChecked():
                continue
            status.setText("测试中...")
            status.setStyleSheet("color: orange")
            QApplication.processEvents()
            try:
                profile = profiles.get(name, {})
                api_url = profile.get("api_url", "")
                if not api_url:
                    status.setText("API URL 未设置")
                    status.setStyleSheet("color: red")
                    continue
                is_ollama = "127.0.0.1:11434" in api_url or "localhost:11434" in api_url
                if not is_ollama and not profile.get("api_key"):
                    status.setText("API Key 未设置")
                    status.setStyleSheet("color: red")
                    continue
                ep_name = "自定义"
                for name, cls, url in AI_ADAPTER:
                    if url == api_url:
                        ep_name = name
                        break
                adapter = get_adapter_endpoint(ep_name, self.config,
                                                api_key=profile.get("api_key", ""),
                                                api_url=api_url)
                test_model = profile.get("model", "")
                if not test_model:
                    status.setText("模型未设置")
                    status.setStyleSheet("color: red")
                    continue
                test_msg = [{"role": "user", "content": "Hello"}]
                adapter.chat(messages=test_msg, model=test_model, temperature=0.7, max_tokens=10)
                status.setText("响应正常")
                status.setStyleSheet("color: green")
            except Exception as e:
                status.setText(f"× {str(e)[:40]}")
                status.setStyleSheet("color: red")
            QApplication.processEvents()

    def _init_prompt_manager(self):
        """初始化提示词管理器"""

        def set_pairs(pairs):
            self.prompt_manager.pair_list.clear()
            if isinstance(pairs, dict):
                items = pairs.items()
            elif isinstance(pairs, list):
                items = [(p.get("name",""), p.get("value","")) for p in pairs if isinstance(p, dict)]
            else:
                items = []
            for name, value in items:
                builtin = name in _BUILTIN_PROMPTS
                display = name + "  (内置)" if builtin else name
                item = QListWidgetItem(display)
                item.setData(Qt.ItemDataRole.UserRole, {"name": name, "value": value})
                self.prompt_manager.pair_list.addItem(item)

        def get_pairs():
            pairs = {}
            for i in range(self.prompt_manager.pair_list.count()):
                item = self.prompt_manager.pair_list.item(i)
                data = item.data(Qt.ItemDataRole.UserRole)
                name = data.get("name", item.text()) if isinstance(data, dict) else item.text()
                value = data.get("value", "") if isinstance(data, dict) else (data if isinstance(data, str) else "")
                pairs[name] = value
            return pairs

        self.prompt_manager = ManagePair(self)
        self.prompt_manager.set_pairs = set_pairs
        self.prompt_manager.get_pairs = get_pairs
        set_pairs(self.config.get("AI.prompts", {}))

        def pair_dialog(title, initial_name="", initial_value="", builtin=False):
            if builtin:
                dialog = QDialog(self)
                dialog.setWindowTitle(title)
                dialog.setMinimumSize(500, 450)

                d_layout = QVBoxLayout(dialog)
                form_layout = QFormLayout()
                form_layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)

                name_edit = QLineEdit()
                name_edit.setText(initial_name)
                name_edit.setReadOnly(True)
                name_edit.setStyleSheet("background: #e0e0e0;")
                form_layout.addRow("提示词名称", name_edit)

                value_edit = QTextEdit()
                value_edit.setPlainText(initial_value)
                value_edit.setAcceptRichText(False)
                form_layout.addRow("提示词内容", value_edit)

                d_layout.addLayout(form_layout)

                if dialogBox(d_layout, dialog):
                    name = name_edit.text().strip()
                    value = value_edit.toPlainText().strip()
                    if not name:
                        messageBox(self, "警告", "提示词名称不能为空", 1)
                        initial_name, initial_value = name, value
                        return pair_dialog(title, initial_name, initial_value, True)
                    return name, value
                return None, None
            return dictDialog(self, title,
                              name="提示词名称", value="提示词内容",
                              name_text=initial_name, value_text=initial_value,
                              textedit=True)

        self.prompt_manager.set_pairs = set_pairs
        self.prompt_manager.get_pairs = get_pairs
        self.prompt_manager.pair_dialog = pair_dialog
        self.prompt_list = self.prompt_manager.pair_list

        def add():
            result = self.prompt_manager.pair_dialog("添加")
            if result[0] is not None:
                name, value = result
                item = QListWidgetItem(name)
                item.setData(Qt.ItemDataRole.UserRole, {"name": name, "value": value})
                self.prompt_manager.pair_list.addItem(item)

        def edit():
            current_item = self.prompt_manager.pair_list.currentItem()
            if not current_item:
                messageBox(self, "警告", "请先选择一个要编辑的项", 1)
                return

            old_data = current_item.data(Qt.ItemDataRole.UserRole)
            if isinstance(old_data, dict):
                old_name = old_data.get("name", "")
                old_value = old_data.get("value", "")
            else:
                old_name = current_item.text()
                old_value = old_data if isinstance(old_data, str) else ""
            builtin = old_name in _BUILTIN_PROMPTS

            result = self.prompt_manager.pair_dialog("编辑", old_name, old_value, builtin)
            if result[0] is not None:
                name, value = result
                display = name
                if builtin:
                    display += "  (内置)"
                current_item.setText(display)
                current_item.setData(Qt.ItemDataRole.UserRole, {"name": name, "value": value})

        self.prompt_manager.add = add
        self.prompt_manager.edit = edit
        self.prompt_manager.add_btn.clicked.disconnect()
        self.prompt_manager.add_btn.clicked.connect(add)
        self.prompt_manager.edit_btn.clicked.disconnect()
        self.prompt_manager.edit_btn.clicked.connect(edit)

        def delete():
            current_item = self.prompt_manager.pair_list.currentItem()
            if not current_item:
                messageBox(self, "警告", "请先选择一个要删除的项", 1)
                return
            data = current_item.data(Qt.ItemDataRole.UserRole)
            name = data.get("name", "") if isinstance(data, dict) else current_item.text()
            if name in _BUILTIN_PROMPTS:
                messageBox(self, "禁止删除", "内置提示词不可删除", 1)
                return
            if messageBox(self, "确认删除", f"确定要删除 '{current_item.text()}' 吗？"):
                row = self.prompt_manager.pair_list.row(current_item)
                self.prompt_manager.pair_list.takeItem(row)

        self.prompt_manager.delete = delete
        self.prompt_manager.delete_btn.clicked.disconnect()
        self.prompt_manager.delete_btn.clicked.connect(delete)

    @staticmethod
    def _unique_name(base: str, existing: set) -> str:
        if base not in existing:
            return base
        counter = 1
        while f"{base}{counter}" in existing:
            counter += 1
        return f"{base}{counter}"

    def _on_endpoint_changed(self, text):
        """API端点选择变化"""
        if hasattr(self, 'ai_custom_url_edit'):
            if text == "自定义":
                self.ai_custom_url_edit.clear()
                self.ai_custom_url_edit.setPlaceholderText("输入自定义API地址")
            else:
                for name, cls, url in AI_ADAPTER:
                    if name == text:
                        self.ai_custom_url_edit.setText(url)
                        break

    def _apply_profile_to_ui(self, profile: dict):
        self.ai_api_key_edit.setText(profile.get("api_key", ""))
        model = profile.get("model", "")
        self.ai_model.clear()
        if model:
            self.ai_model.addItem(model)
        api_url = profile.get("api_url", "")
        for name, cls, url in AI_ADAPTER:
            if url == api_url:
                idx = self.ai_combo.findText(name)
                if idx >= 0:
                    self.ai_combo.setCurrentIndex(idx)
                break
        if self.ai_combo.currentText() == "自定义":
            self.ai_custom_url_edit.setText(profile.get("custom_url", ""))
        self.ai_temperature_spin.setValue(profile.get("temperature", 0.7))
        self.ai_max_tokens_spin.setValue(profile.get("max_tokens", 2000))

    def _on_profile_changed(self, index):
        profile_name = self.ai_profile_combo.currentText()
        if not profile_name:
            return
        self._apply_profile_to_ui(self._working_profiles.get(profile_name, {}))

    def _load_current_profile(self):
        profile_name = self.ai_profile_combo.currentText()
        if not profile_name:
            return
        self._apply_profile_to_ui(self._working_profiles.get(profile_name, {}))

    def _rename_profile(self):
        """重命名当前配置"""
        old_name = self.ai_profile_combo.currentText()
        if not old_name:
            return
        profiles = self._working_profiles
        
        new_name = inputDialog(self, "重命名配置", "新名称", default=old_name)
        if new_name is not None:
            if not new_name or new_name == old_name:
                new_name = self._unique_name(old_name, profiles)
            if new_name in profiles:
                messageBox(self, "警告", "配置名称已存在", 1)
                return
            profile_data = profiles.pop(old_name)
            profiles[new_name] = profile_data
            self.ai_profile_combo.setItemText(self.ai_profile_combo.currentIndex(), new_name)

    def _new_profile(self):
        """新建配置"""
        profiles = self._working_profiles
        
        new_name = self._unique_name("新配置", profiles)
        name = inputDialog(self, "新建配置", "配置名称", default=new_name)
        if name:
            if name in profiles:
                messageBox(self, "警告", "配置名称已存在", 1)
                return
            
            profiles[name] = {
                "api_key": "",
                "model": "",
                "api_url": "https://api.deepseek.com",
                "custom_url": "",
                "temperature": 0.7,
                "max_tokens": 2000
            }
            
            self.ai_profile_combo.addItem(name, name)
            self.ai_profile_combo.setCurrentIndex(self.ai_profile_combo.count() - 1)
    
    def _delete_profile(self):
        """删除当前选中的配置"""
        current_name = self.ai_profile_combo.currentText()
        if not current_name:
            return
        
        profiles = self._working_profiles
        
        if len(profiles) <= 1:
            messageBox(self, "警告", "至少保留一个配置，不能删除", 1)
            return
        
        if messageBox(self, "确认删除", f"确定要删除配置 \"{current_name}\" 吗？"):
            profiles.pop(current_name, None)
            self.ai_profile_combo.removeItem(self.ai_profile_combo.currentIndex())
            if profiles:
                first_name = list(profiles.keys())[0]
                idx = self.ai_profile_combo.findText(first_name)
                if idx >= 0:
                    self.ai_profile_combo.setCurrentIndex(idx)
    
    def _copy_profile(self):
        """复制当前配置"""
        current_name = self.ai_profile_combo.currentText()
        if not current_name:
            return
        profiles = self._working_profiles
        if current_name not in profiles:
            return

        default_name = self._unique_name(current_name, profiles)
        name = inputDialog(self, "复制配置", "新配置名称", default=default_name)
        if name:
            if name in profiles:
                messageBox(self, "警告", "配置名称已存在", 1)
                return
            profiles[name] = dict(profiles[current_name])
            self.ai_profile_combo.addItem(name, name)
            self.ai_profile_combo.setCurrentIndex(self.ai_profile_combo.count() - 1)
    
    def _refresh_models(self):
        """刷新模型列表"""
        
        # 检查API Key是否设置
        api_key = self.ai_api_key_edit.text().strip()
        endpoint_name = ""
        api_url = ""
        
        # 从下拉框获取选中的端点
        if hasattr(self, 'ai_combo'):
            endpoint_name = self.ai_combo.currentText()
            
            if endpoint_name == "自定义":
                api_url = self.ai_custom_url_edit.text().strip() if hasattr(self, 'ai_custom_url_edit') else ""
            else:
                for name, cls, url in AI_ADAPTER:
                    if name == endpoint_name:
                        api_url = url
                        break
        
        is_ollama = endpoint_name == "Ollama" or "127.0.0.1:11434" in api_url or "localhost:11434" in api_url

        if not is_ollama and not api_key:
            messageBox(self, "警告", "请先设置 API Key", 1)
            return
        
        if not endpoint_name:
            messageBox(self, "警告", "请先选择 API 端点", 1)
            return
        
        # 保存按钮原始状态
        original_text = self.refresh_model_btn.text()
        original_enabled = self.refresh_model_btn.isEnabled()
        
        # 设置按钮为加载状态
        self.refresh_model_btn.setText("刷新中...")
        self.refresh_model_btn.setEnabled(False)
        
        # 显示等待光标
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        
        try:
            adapter = get_adapter_endpoint(endpoint_name, self.config, api_key=api_key, api_url=api_url)
            
            models = adapter.get_models()
            
            # 更新模型下拉框
            self.ai_model.clear()
            self.ai_model.addItems(models)
            
            # 选择第一个模型或保持当前选择
            if models:
                current_model = self.ai_model.currentText()
                if current_model in models:
                    index = self.ai_model.findText(current_model)
                    if index >= 0:
                        self.ai_model.setCurrentIndex(index)
                else:
                    self.ai_model.setCurrentIndex(0)
            
            messageBox(self, "刷新成功", f"已获取 {len(models)} 个模型", 1)
            
        except Exception as e:
            messageBox(self, "刷新失败", f"获取模型列表失败: {str(e)}", 1)
        finally:
            # 恢复按钮状态
            self.refresh_model_btn.setText(original_text)
            self.refresh_model_btn.setEnabled(original_enabled)
            QApplication.restoreOverrideCursor()

    def init_shortcuts_tab(self):
        """快捷键设置"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        
        self.shortcuts_table = QTableWidget()
        self.shortcuts_table.setColumnCount(2)
        self.shortcuts_table.setHorizontalHeaderLabels(["操作", "快捷键"])
        self.shortcuts_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.shortcuts_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.shortcuts_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectItems)
        self.shortcuts_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.shortcuts_table.horizontalHeader().setHighlightSections(False)
        self.shortcuts_table.setStyleSheet("""
            QTableWidget { outline: none; border: none; }
            QTableWidget::item:selected { background: #dddddd; color: inherit; border: none; outline: none; }
        """)
        
        default_shortcuts = DEFAULT_CONFIG["Edit"]["shortcuts"]
        saved_shortcuts = self.config.get("Edit.shortcuts", {})
        
        self.shortcuts_table.setRowCount(len(self.SHORTCUT_MAP))
        for i, key in enumerate(self.SHORTCUT_MAP):
            display_key = self.SHORTCUT_MAP[key]
            display_name = tr(display_key, display_key)
            action_item = QTableWidgetItem(display_name)
            action_item.setFlags(action_item.flags() & ~(Qt.ItemFlag.ItemIsEditable | Qt.ItemFlag.ItemIsSelectable))
            self.shortcuts_table.setItem(i, 0, action_item)
            
            keyseq = saved_shortcuts.get(key, default_shortcuts.get(key, ""))
            key_item = QTableWidgetItem(keyseq)
            key_item.setData(Qt.ItemDataRole.UserRole, key)
            self.shortcuts_table.setItem(i, 1, key_item)
        
        layout.addWidget(self.shortcuts_table)
        
        self.shortcuts_table.cellDoubleClicked.connect(self._on_shortcuts_cell_double_clicked)
        self.shortcuts_table.viewport().installEventFilter(self)
        
        reset_btn = QPushButton("重置为默认值")
        reset_btn.clicked.connect(self._reset_shortcuts_to_default)
        layout.addWidget(reset_btn)
        
        self._shortcuts_capturing = False
        self._capturing_row = -1
        return tab
    
    def _on_shortcuts_cell_double_clicked(self, row, column):
        """双击快捷键列时开始捕获"""
        if column == 1:
            self._shortcuts_capturing = True
            self._capturing_row = row
            self.shortcuts_table.setCurrentCell(row, 1)
            key_item = self.shortcuts_table.item(row, 1)
            key_item.setFlags(key_item.flags() | Qt.ItemFlag.ItemIsEditable)
            self.shortcuts_table.openPersistentEditor(key_item)
            editor = self.shortcuts_table.indexWidget(self.shortcuts_table.model().index(row, 1))
            if editor:
                self._shortcut_editor = editor
                self._shortcut_key_filter = KeyCaptureFilter(self)
                self._shortcut_key_filter.key_captured.connect(lambda seq: self._on_shortcut_key_captured(seq, row))
                self._shortcut_key_filter.capture_cancelled.connect(lambda: self._on_shortcut_cancelled(row))
                editor.installEventFilter(self._shortcut_key_filter)
                editor.setFocus()

    def _on_shortcut_key_captured(self, seq, row):
        """处理快捷键捕获"""
        key_item = self.shortcuts_table.item(row, 1)
        if key_item:
            key_item.setText(seq)
        if hasattr(self, '_shortcut_editor') and self._shortcut_editor:
            self._shortcut_editor.setText(seq)
            self._shortcut_editor.selectAll()

    def _on_shortcut_cancelled(self, row):
        """取消快捷键捕获"""
        self._cleanup_shortcut_editor(row)

    def _cleanup_shortcut_editor(self, row):
        """清理快捷键编辑器"""
        key_item = self.shortcuts_table.item(row, 1)
        if key_item:
            self.shortcuts_table.closePersistentEditor(key_item)
        if hasattr(self, '_shortcut_key_filter'):
            self._shortcut_key_filter.deleteLater()
            del self._shortcut_key_filter
        if hasattr(self, '_shortcut_editor'):
            del self._shortcut_editor
        self._shortcuts_capturing = False
        self._capturing_row = -1
    
    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.KeyPress and self._shortcuts_capturing and obj == self.shortcuts_table.viewport():
                key = event.key()
                if key == Qt.Key.Key_Escape:
                    self._shortcuts_capturing = False
                    self._capturing_row = -1
                    return True
                seq = translate_key_to_str(event)
                if seq:
                    key_item = self.shortcuts_table.item(self._capturing_row, 1)
                    if key_item:
                        key_item.setText(seq)
                self._shortcuts_capturing = False
                self._capturing_row = -1
                return True
        return super().eventFilter(obj, event)

    def _on_launcher_key_captured(self, seq):
        """处理启动器快捷键捕获"""
        self.launcher_hotkey_edit.setText(seq)
    
    def _reset_shortcuts_to_default(self):
        """重置快捷键为默认值"""
        default_shortcuts = DEFAULT_CONFIG["Edit"]["shortcuts"]
        self.shortcuts_table.setRowCount(len(self.SHORTCUT_MAP))
        for i, key in enumerate(self.SHORTCUT_MAP):
            display_key = self.SHORTCUT_MAP[key]
            display_name = tr(display_key, display_key)
            self.shortcuts_table.setItem(i, 0, QTableWidgetItem(display_name))
            self.shortcuts_table.item(i, 0).setFlags(self.shortcuts_table.item(i, 0).flags() & ~Qt.ItemFlag.ItemIsEditable)
            keyseq = default_shortcuts.get(key, "")
            self.shortcuts_table.setItem(i, 1, QTableWidgetItem(keyseq))

    def getSetting(self) -> dict:
        """获取设置"""
        # 获取选中的API端点
        api_url = ""
        custom_url = ""
        
        # 从下拉框获取选中的端点
        if hasattr(self, 'ai_combo'):
            endpoint_name = self.ai_combo.currentText()
            if endpoint_name == "自定义":
                api_url = self.ai_custom_url_edit.text().strip() if hasattr(self, 'ai_custom_url_edit') else ""
                custom_url = api_url
            else:
                for name, cls, url in AI_ADAPTER:
                    if name == endpoint_name:
                        api_url = url
                        break
        
        # 获取自定义URL（如果没有从上面获取）
        if not custom_url and hasattr(self, 'ai_custom_url_edit'):
            custom_url = self.ai_custom_url_edit.text().strip()
        
        # 构建提示词列表
        prompts = self.prompt_manager.get_pairs()
        
        # 构建快捷键字典
        shortcuts = {}
        if hasattr(self, 'shortcuts_table') and self.shortcuts_table:
            for i, key in enumerate(self.SHORTCUT_MAP):
                key_item = self.shortcuts_table.item(i, 1)
                if key_item:
                    keyseq = key_item.text()
                    if keyseq:
                        shortcuts[key] = keyseq
        
        return {
            "language": self.language_combo.currentText(),
            "theme": self.theme_combo.currentText(),
            "font_family": self.font_family_edit.currentText(),
            "font_size": self.font_size_spin.value(),
            "tray": self.tray.isChecked(),
            "usage": self.usage_label.isChecked(),
            "auto_start": self.auto_start.isChecked(),
            "context_menu": self.context_menu_check.isChecked(),
            "Edit.multi_tab": self.multi_tab_check.isChecked(),
            "Edit.backup": self.history_backup_check.isChecked(),
            "Edit.wrap": self.auto_wrap.isChecked(),
            "Edit.indent": self.auto_indent.isChecked(),
            "Edit.line_numbers": self.line_numbers.isChecked(),
            "Edit.line_spacing": self.line_spacing_spin.value(),
            "Edit.auto_save": self.auto_save_check.isChecked(),
            "Edit.auto_save_interval": self.auto_save_interval.value(),
            "Edit.shortcuts": shortcuts,
            "Plugin": self.config.get("Plugin", {}),
            "AI": {
                "enabled": self.ai_enabled_check.isChecked(),
                "active": self.ai_profile_combo.currentText(),
                "profiles": self._build_ai_profiles(),
                "prompts": prompts,
                "autocomplete": self.ai_autocomplete_check.isChecked() if hasattr(self, 'ai_autocomplete_check') else False,
                "stream": self.ai_stream_check.isChecked() if hasattr(self, 'ai_stream_check') else True,
                "load_balance": self._working_load_balance
            },
            "Edit.engine": self.searchEngine()
        }

    def searchEngine(self) -> dict:
        """获取搜索引擎列表"""
        engines = {}
        for i in range(self.search_engines_list.count()):
            item = self.search_engines_list.item(i)
            name = item.text()
            url = item.data(Qt.ItemDataRole.UserRole)
            engines[name] = url
        return engines

    def _build_ai_profiles(self) -> dict:
        """构建AI配置profile"""
        profiles = self._working_profiles
        
        current_profile_name = self.ai_profile_combo.currentText()
        
        endpoint_name = self.ai_combo.currentText()
        api_url = self.ai_custom_url_edit.text().strip()
        
        profiles[current_profile_name] = {
            "api_key": self.ai_api_key_edit.text(),
            "api_url": api_url,
            "custom_url": self.ai_custom_url_edit.text().strip(),
            "model": self.ai_model.currentText(),
            "temperature": self.ai_temperature_spin.value(),
            "max_tokens": self.ai_max_tokens_spin.value(),
            "endpoint": endpoint_name
        }
        
        return profiles

    def accept(self):
        settings = self.getSetting()

        old_multi_tab = self.config.get("Edit.multi_tab", True)
        new_multi_tab = settings.get("Edit.multi_tab", True)
        old_language = self.config.get("language", "简体中文")
        new_language = settings.get("language", "简体中文")
        old_theme = self.config.get("theme", "Light")
        new_theme = settings.get("theme", "Light")

        new_auto_start = settings.get("auto_start", False)
        old_auto_start = self.config.get("auto_start", False)
        if old_auto_start != new_auto_start:
            setAutoStart(new_auto_start)
        old_context_menu = self.config.get("context_menu", False)
        new_context_menu = settings.get("context_menu", False)
        if old_context_menu != new_context_menu:
            setWindowsMenu(new_context_menu)

        if hasattr(self, '_launcher_config') and isinstance(self._launcher_config, dict):
            launch = dict(self._launcher_config)
            hotkey_text = self.launcher_hotkey_edit.text().strip()
            launch["hotkey"] = hotkey_text

            layout_text = self.launcher_layout_combo.currentText()
            launch["layout"] = "horizontal" if layout_text == "横向" else "vertical"

            launch["mouse_side"] = self.launcher_mouse_side_check.isChecked()
            launch["double_ctrl"] = self.launcher_double_ctrl_check.isChecked()
            launch["capture"] = self.launcher_capture_check.isChecked()
            launch["on_top"] = self.launcher_on_top_check.isChecked()
            launch["run_hide"] = self.launcher_hide.isChecked()

            path_mode_text = self.launcher_path_mode_combo.currentText()
            launch["path_mode"] = "absolute" if path_mode_text == "绝对路径" else "relative"

            old_path_mode = self._launcher_config.get("path_mode", "absolute")
            if old_path_mode != launch["path_mode"]:
                launch["tools"] = self.convertToolPath(launch["path_mode"], launch.get("tools", {}))

            launch["hover_switch"] = self.launcher_hover_switch_check.isChecked()

            launch["g_w"] = self.g_w_spin.value()
            launch["g_h"] = self.g_h_spin.value()
            launch["i_w"] = self.i_w_spin.value()
            launch["i_h"] = self.i_h_spin.value()
            launch["icon"] = self.icon_spin.value()
            launch["padding"] = self.padding_spin.value()

            launch["Runtime"] = {
                "Python": os.path.normpath(self.python_path_edit.text()) if self.python_path_edit.text() else "",
                "Java": os.path.normpath(self.java_path_edit.text()) if self.java_path_edit.text() else "",
                "Temp_Path": [os.path.normpath(p.strip()) for p in self.temp_paths_edit.toPlainText().splitlines() if p.strip()]
            }

            self.config.set("Launch", launch)

        for key, value in settings.items():
            self.config.set(key, value)

        self.config.save()
        self.settings_changed.emit(settings)

        if old_multi_tab != new_multi_tab:
            self.multi_tab_changed.emit(new_multi_tab)

        if old_language != new_language or old_theme != new_theme:
            self.restart_required.emit()

        try:
            app = QApplication.instance()
            if app:
                for widget in app.topLevelWidgets():
                    if hasattr(widget, 'startGlobalListener'):
                        widget.startGlobalListener()
                        break
        except Exception:
            logger.exception("启动全局监听失败")

        super().accept()

    def convertToolPath(self, new_mode: str, tools: dict):
        """转换所有工具的路径模式"""
        tools = copy.deepcopy(tools)
        for group_tools in tools.values():
            for tool in group_tools:
                if tool.get("type") == "预设":
                    continue
                for key in ("path", "cwd", "icon"):
                    if key in tool and tool[key]:
                        tool[key] = convertPath(tool[key], new_mode)
        return tools


# 延迟导入，避免循环依赖。使用适配器获取模型
from src.core.AI import AIClient, AI_ADAPTER, get_adapter_endpoint