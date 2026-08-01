import os
import copy
import json
import tempfile
from typing import Any, Dict

from PySide6.QtWidgets import QApplication, QWidget, QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout, QLabel, QLineEdit, QSpinBox, QCheckBox, QComboBox, QPushButton, QListWidget, QListWidgetItem, QAbstractSpinBox, QTextEdit, QFontComboBox, QScrollArea, QStackedWidget, QFrame, QSlider, QColorDialog
from PySide6.QtCore import Signal, Qt, QSize
from PySide6.QtGui import QColor

from src.util import config_file, logger, Singleton, tr, systemLanguage, convertPath, filePathWidget, theme_dir, user_dir, lang_dir, dialogBox, messageBox
from src.core.input import KeyCaptureFilter
from src.system import setAutoStart, setMenu

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
        "Runtime": {"Python": "", "Java": "", "Temp_Path": []},
        "hotkey": "",
        "mouse_side": False,
        "double_ctrl": False,
        "on_top": False,
        "run_hide": False,
        "hover_switch": False,
        "layout": "horizontal",
        "path_mode": "absolute",
        "active_group": "",
        "g_w": 90,
        "g_h": 30,
        "i_w": 100,
        "i_h": 75,
        "icon": 32,
        "padding": 8,
        "width": 600,
        "height": 400,
        "x": None,
        "y": None,
        "opacity": 1.0,
        "bg_image": "",
        "colors": {},
        "tools": {}
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
            "jump_next": "Ctrl+D",
            "minimize": "Escape"
        },
        "find_presets": [],
        "engine": {
            "Bing": "https://cn.bing.com/search?q={query}",
            "Google": "https://www.google.com/search?q={query}",
            "GitHub": "https://github.com/search?q={query}"
        }
    },
    "Plugin": {}
}


class ConfigManager(Singleton):
    _initialized = False

    def _init(self):
        self.config: Dict[str, Any] = {}
        self._load()

    @staticmethod
    def _deepUpdate(base: dict, update: dict) -> dict:
        for key, value in update.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                ConfigManager._deepUpdate(base[key], value)
            else:
                base[key] = value
        return base

    def _load(self):
        """加载配置文件"""
        if config_file.exists():
            try:
                with open(config_file, "r", encoding="utf-8") as f:
                    loaded_config = json.load(f)
                self.config = copy.deepcopy(DEFAULT_CONFIG)
                self.config = self._deepUpdate(self.config, loaded_config)
                logger.info(f"配置文件加载成功: {config_file}")
            except json.JSONDecodeError:
                logger.exception("配置文件格式错误")
                self._backupReset()
                logger.info("配置文件已备份，创建默认配置")
            except Exception:
                logger.exception("配置文件加载失败")
                self.config = copy.deepcopy(DEFAULT_CONFIG)
                self.config["language"] = systemLanguage()
        else:
            self.config = copy.deepcopy(DEFAULT_CONFIG)
            self.config["language"] = systemLanguage()
            self.save()
            logger.info(f"配置文件不存在，已创建默认配置: {config_file}")

    def _backupReset(self):
        """备份损坏的配置文件并重置为默认配置"""
        backup_path = config_file.with_suffix('.json.bak')
        try:
            if config_file.exists():
                config_file.rename(backup_path)
        except Exception:
            logger.exception("备份配置文件失败")
        self.config = copy.deepcopy(DEFAULT_CONFIG)
        self.config["language"] = systemLanguage()
        self.save()

    def save(self):
        """原子写入配置文件"""
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(suffix='.tmp', prefix='config_', dir=config_file.parent)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(fd)
            os.replace(tmp, str(config_file))
            logger.info(f"配置文件已保存: {config_file}")
        except Exception:
            logger.exception("配置文件保存失败")
            if tmp and os.path.exists(tmp):
                os.unlink(tmp)

    def get(self, key: str, default: Any = None) -> Any:
        """获取配置项，支持点号访问嵌套字段"""
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

    def updateWindowGeometry(self, geometry):
        self.set("Edit.width", geometry.width())
        self.set("Edit.height", geometry.height())
        self.set("Edit.x", geometry.x())
        self.set("Edit.y", geometry.y())

    def addRecentFile(self, file_path: str):
        file_path = os.path.normpath(file_path)
        recent = self.get("Edit.recent", [])
        if file_path in recent:
            recent.remove(file_path)
        recent.insert(0, file_path)
        self.set("Edit.recent", recent[:10])

    def addFavorite(self, file_path: str):
        file_path = os.path.normpath(file_path)
        favorites = self.get("Edit.favorites", [])
        if file_path not in favorites:
            favorites.insert(0, file_path)
            self.set("Edit.favorites", favorites)

    def removeFavorite(self, file_path: str):
        file_path = os.path.normpath(file_path)
        favorites = self.get("Edit.favorites", [])
        if file_path in favorites:
            favorites.remove(file_path)
            self.set("Edit.favorites", favorites)

    def isFavorite(self, file_path: str) -> bool:
        file_path = os.path.normpath(file_path)
        return file_path in self.get("Edit.favorites", [])


def getConfig() -> ConfigManager:
    """获取配置管理器单例"""
    return ConfigManager()

class SettingsDialog(QDialog):
    """设置对话框"""

    settings_changed = Signal(dict)
    multi_tab_changed = Signal(bool)
    restart_required = Signal()
    
    SHORTCUT_MAP = {
        "new_file": "新建", "open_file": "打开", "save_file": "保存", "save_as": "另存为",
        "find": "查找", "replace": "替换", "undo": "撤销", "redo": "重做",
        "go_to_line": "跳转到行", "jump_next": "选择下一个匹配",
        "minimize": "最小化"
    }

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle(tr("设置"))
        self.setMinimumSize(500, 500)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.initUI()

    def initUI(self):
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
            (self.initOptions(), tr("选项")),
            (self.initEdit(), tr("编辑")),
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

        self._plugin_tabs = []
        from src.plugin import getPluginManager
        pm = getPluginManager()
        for plugin in pm.plugins.values():
            try:
                w = plugin.configWidget(self.stack)
                if w is not None:
                    scroll = QScrollArea()
                    scroll.setWidget(w)
                    scroll.setWidgetResizable(True)
                    scroll.setFrameShape(QFrame.Shape.NoFrame)
                    self.stack.addWidget(scroll)
                    item = QListWidgetItem(plugin.description or plugin.name)
                    item.setSizeHint(QSize(0, 36))
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    self.tab_list.addItem(item)
                    self._plugin_tabs.append(plugin)
            except Exception:
                logger.exception(f"插件 {plugin.name} 设置页加载失败")

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
        """获取可用的主题列表（内置 src/theme 与用户 data/user，同名时用户优先）"""
        themes = {}
        for scan_dir in (theme_dir, user_dir):
            if not scan_dir.exists():
                continue
            for file in scan_dir.iterdir():
                if file.suffix.lower() == '.qss':
                    themes[file.stem] = True
        return sorted(themes)

    def initOptions(self):
        """选项设置"""
        tab = QWidget()
        layout = QFormLayout(tab)
        layout.setLabelAlignment(Qt.AlignmentFlag.AlignCenter)

        self.language_combo = QComboBox()
        self.language_combo.addItem("简体中文", "简体中文")
        for file in sorted(lang_dir.glob("*.json")):
            try:
                data = json.loads(file.read_text(encoding="utf-8"))
                name = data.get("翻译")
                if name:
                    self.language_combo.addItem(name, file.stem)
            except Exception:
                logger.exception(f"读取语言文件失败 {file}")
        current_lang = self.config.get("language", "简体中文")
        idx = self.language_combo.findData(current_lang)
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

        # 启动器配色
        self._colors = dict(self.config.get("Launch.colors", {}))
        color_row = QWidget()
        color_row_layout = QHBoxLayout(color_row)
        color_row_layout.setContentsMargins(20, 0, 0, 0)
        color_row_layout.setSpacing(20)
        color_slots = [
            ("title_co", tr("标题栏")),
            ("group_co", tr("分组栏")),
            ("tool_co", tr("工具栏")),
        ]
        for slot, label in color_slots:
            slot_box = QWidget()
            slot_layout = QHBoxLayout(slot_box)
            slot_layout.setContentsMargins(0, 0, 0, 0)
            slot_layout.setSpacing(4)
            swatch = QPushButton()
            swatch.setFixedSize(22, 22)
            swatch.setToolTip(tr("点击选择颜色"))
            color = self._colors.get(slot, "")
            if color:
                swatch.setStyleSheet(f"background-color: {color};")
                swatch.setToolTip(color)
            clear_btn = QPushButton(tr("清除"))
            clear_btn.setFixedWidth(50)
            clear_btn.setToolTip(tr("恢复为主题默认"))
            swatch.clicked.connect(lambda _, s=slot, b=swatch: self._pickColor(s, b))
            clear_btn.clicked.connect(lambda _, s=slot, b=swatch: self._clearColor(s, b))
            slot_layout.addWidget(QLabel(label))
            slot_layout.addWidget(swatch)
            slot_layout.addWidget(clear_btn)
            color_row_layout.addWidget(slot_box)
        color_row_layout.addStretch()
        layout.addRow(color_row)

        # 背景图
        self.bg_image_edit, self.bg_image_browse = filePathWidget(
            self, layout, tr("背景图"), tr("选择背景图片"),
            tr("图片文件") + " (*.png *.jpg *.jpeg *.bmp *.gif);;" + tr("所有文件") + " (*)")
        self.bg_image_edit.setText(self.config.get("Launch.bg_image", ""))

        # 透明度
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(30, 100)
        self.opacity_slider.setValue(int(self.config.get("Launch.opacity", 1.0) * 100))
        self.opacity_label = QLabel(f"{self.opacity_slider.value()}%")
        self.opacity_slider.valueChanged.connect(
            lambda v: self.opacity_label.setText(f"{v}%"))
        opacity_layout = QHBoxLayout()
        opacity_layout.addWidget(self.opacity_slider)
        opacity_layout.addWidget(self.opacity_label)
        layout.addRow(tr("透明度"), opacity_layout)

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

        self.launcher_hide = QCheckBox(tr("运行后隐藏"))
        self.launcher_hide.setChecked(launcher_config.get("run_hide", False))

        self.launcher_hover_switch_check = QCheckBox(tr("悬停切换分组"))
        self.launcher_hover_switch_check.setChecked(launcher_config.get("hover_switch", False))

        self.launcher_double_ctrl_check = QCheckBox(tr("连按 Ctrl 响应"))
        self.launcher_double_ctrl_check.setChecked(launcher_config.get("double_ctrl", False))

        launch_grid_widget = QWidget()
        launch_grid = QGridLayout(launch_grid_widget)
        launch_grid.setContentsMargins(20, 0, 0, 0)
        launch_grid.addWidget(self.launcher_mouse_side_check, 0, 0)
        launch_grid.addWidget(self.launcher_hover_switch_check, 0, 1)
        launch_grid.addWidget(self.launcher_on_top_check, 1, 0)
        launch_grid.addWidget(self.launcher_hide, 1, 1)

        launch_grid.addWidget(self.launcher_double_ctrl_check, 2, 0, 1, 2)
        launch_grid.setColumnStretch(0, 1)
        launch_grid.setColumnStretch(1, 1)
        layout.addRow(launch_grid_widget)

        # 启动快捷键
        self.launcher_hotkey_edit = QLineEdit()
        self.launcher_hotkey_edit.setText(launcher_config.get("hotkey", ""))
        self.launcher_hotkey_edit.setToolTip(tr("点击输入快捷键"))
        self.launcher_key_capture_filter = KeyCaptureFilter(self)
        self.launcher_key_capture_filter.key_captured.connect(self._onHotkeyCapture)
        self.launcher_hotkey_edit.installEventFilter(self.launcher_key_capture_filter)
        layout.addRow(tr("快捷键"), self.launcher_hotkey_edit)

        # 分组排列
        self.launcher_layout_combo = QComboBox()
        self.launcher_layout_combo.addItem(tr("横向"), "horizontal")
        self.launcher_layout_combo.addItem(tr("纵向"), "vertical")
        current_layout = launcher_config.get("layout", "horizontal")
        idx = self.launcher_layout_combo.findData(current_layout)
        self.launcher_layout_combo.setCurrentIndex(idx if idx >= 0 else 0)
        layout.addRow(tr("分组排列"), self.launcher_layout_combo)

        self.launcher_path_mode_combo = QComboBox()
        self.launcher_path_mode_combo.addItem(tr("绝对路径"), "absolute")
        self.launcher_path_mode_combo.addItem(tr("相对路径"), "relative")
        current_path_mode = launcher_config.get("path_mode", "absolute")
        idx = self.launcher_path_mode_combo.findData(current_path_mode)
        self.launcher_path_mode_combo.setCurrentIndex(idx if idx >= 0 else 0)
        layout.addRow(tr("路径"), self.launcher_path_mode_combo)

        # 启动器外观
        self.g_w_spin = QSpinBox()
        self.g_w_spin.setRange(40, 200)
        self.g_w_spin.setValue(launcher_config.get("g_w", 90))
        self.g_w_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.g_h_spin = QSpinBox()
        self.g_h_spin.setRange(20, 100)
        self.g_h_spin.setValue(launcher_config.get("g_h", 30))
        self.g_h_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)

        g_layout = QHBoxLayout()
        g_layout.addWidget(QLabel(tr("宽")))
        g_layout.addWidget(self.g_w_spin)
        g_layout.addWidget(QLabel(tr("高")))
        g_layout.addWidget(self.g_h_spin)
        g_layout.addStretch()
        layout.addRow(tr("分组按钮"), g_layout)

        self.i_w_spin = QSpinBox()
        self.i_w_spin.setRange(40, 300)
        self.i_w_spin.setValue(launcher_config.get("i_w", 100))
        self.i_w_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.i_h_spin = QSpinBox()
        self.i_h_spin.setRange(40, 200)
        self.i_h_spin.setValue(launcher_config.get("i_h", 75))
        self.i_h_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)

        i_layout = QHBoxLayout()
        i_layout.addWidget(QLabel(tr("宽")))
        i_layout.addWidget(self.i_w_spin)
        i_layout.addWidget(QLabel(tr("高")))
        i_layout.addWidget(self.i_h_spin)
        i_layout.addStretch()
        layout.addRow(tr("项目按钮"), i_layout)

        self.icon_spin = QSpinBox()
        self.icon_spin.setRange(16, 128)
        self.icon_spin.setValue(launcher_config.get("icon", 32))
        self.icon_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.padding_spin = QSpinBox()
        self.padding_spin.setRange(0, 40)
        self.padding_spin.setValue(launcher_config.get("padding", 8))
        self.padding_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        ip_layout = QHBoxLayout()
        ip_layout.addWidget(QLabel(tr("图标大小")))
        ip_layout.addWidget(self.icon_spin)
        ip_layout.addWidget(QLabel(tr("边距")))
        ip_layout.addWidget(self.padding_spin)
        ip_layout.addStretch()
        layout.addRow(ip_layout)

        # Python 环境
        self.python_path_edit, self.python_browse_btn = filePathWidget(self, layout, "Python", "Python", "Python (*.exe);;" + tr("所有文件") + " (*)")
        self.python_path_edit.setText(self.config.get("Launch.Runtime.Python", ""))

        # Java 环境
        self.java_path_edit, self.java_browse_btn = filePathWidget(self, layout, "Java", "Java", "Java (*.exe);;" + tr("所有文件") + " (*)")
        self.java_path_edit.setText(self.config.get("Launch.Runtime.Java", ""))

        # 环境变量
        self.temp_paths_edit = QTextEdit()
        self.temp_paths_edit.setToolTip(tr("KEY=VALUE 或文件夹路径，每行一个"))
        self.temp_paths_edit.setStyleSheet("background: #dddddd")
        Temp_Path = self.config.get("Launch.Runtime.Temp_Path", [])
        self.temp_paths_edit.setPlainText("\n".join(Temp_Path))
        self.temp_paths_edit.setMinimumHeight(80)
        layout.addRow(tr("环境变量"), self.temp_paths_edit)

        self._launcher_config = launcher_config
        return tab

    def initEdit(self):
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

        self.auto_indent = QCheckBox(tr("自动缩进"))
        self.auto_indent.setChecked(self.config.get("Edit.indent", True))

        self.line_spacing_spin = QSpinBox()
        self.line_spacing_spin.setRange(0, 100)
        self.line_spacing_spin.setValue(self.config.get("Edit.line_spacing", 0))
        self.line_spacing_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.line_spacing_spin.setToolTip("0=" + tr("禁用"))
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
        self.auto_save_check = QCheckBox(tr("启用"))
        self.auto_save_check.setChecked(self.config.get("Edit.auto_save", False))
        auto_save_layout.addWidget(self.auto_save_check)
        self.auto_save_interval = QSpinBox()
        self.auto_save_interval.setRange(10, 600)
        self.auto_save_interval.setSuffix(" " + tr("秒"))
        self.auto_save_interval.setValue(self.config.get("Edit.auto_save_interval", 60))
        self.auto_save_interval.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        auto_save_layout.addWidget(self.auto_save_interval)
        layout.addRow(tr("自动保存"), auto_save_layout)

        # 搜索引擎管理
        self._initEngines(layout)

        # 快捷键
        header_label = QLabel("<b>" + tr("操作") + "</b>")
        header_shortcut = QLabel("<b>" + tr("快捷键") + "</b>")
        layout.addRow(header_label, header_shortcut)

        default_shortcuts = DEFAULT_CONFIG["Edit"]["shortcuts"]
        saved_shortcuts = self.config.get("Edit.shortcuts", {})

        self.shortcut_edits = {}
        for key in self.SHORTCUT_MAP:
            line_edit = QLineEdit()
            keyseq = saved_shortcuts.get(key, default_shortcuts.get(key, ""))
            line_edit.setText(keyseq)
            line_edit.setMaximumWidth(150)
            line_edit.setToolTip(tr("点击后按下快捷键"))
            key_filter = KeyCaptureFilter(self)
            key_filter.key_captured.connect(lambda seq, le=line_edit: self._onShortcutCapture(seq, le))
            line_edit.installEventFilter(key_filter)
            self.shortcut_edits[key] = line_edit
            layout.addRow(tr(self.SHORTCUT_MAP[key]), line_edit)

        reset_btn = QPushButton(tr("重置为默认值"))
        reset_btn.clicked.connect(self._setDefaults)
        layout.addRow(reset_btn)

        return tab

    def _initEngines(self, layout):
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
        self.add_engine_btn = QPushButton(tr("新建"))
        self.add_engine_btn.clicked.connect(self._addEngine)
        engine_btn_layout.addWidget(self.add_engine_btn)

        self.edit_engine_btn = QPushButton(tr("编辑"))
        self.edit_engine_btn.clicked.connect(self._editEngine)
        engine_btn_layout.addWidget(self.edit_engine_btn)

        self.remove_engine_btn = QPushButton(tr("删除"))
        self.remove_engine_btn.clicked.connect(self._delEngine)
        engine_btn_layout.addWidget(self.remove_engine_btn)

        engine_btn_layout.addStretch(1)
        layout.setLayout(11, QFormLayout.ItemRole.SpanningRole, engine_btn_layout)

    def _engineDialog(self, title: str, name: str = "", url: str = ""):
        """搜索引擎编辑对话框，返回 (名称, URL) 或 (None, None)"""
        while True:
            dialog = QDialog(self)
            dialog.setWindowTitle(title)
            dialog.setMinimumWidth(400)

            d_layout = QVBoxLayout(dialog)
            form_layout = QFormLayout()

            name_edit = QLineEdit()
            name_edit.setText(name)
            form_layout.addRow(tr("名称"), name_edit)

            url_edit = QLineEdit()
            url_edit.setText(url)
            url_edit.setToolTip(tr("使用 {query} 作为搜索关键词占位符"))
            form_layout.addRow("URL", url_edit)

            d_layout.addLayout(form_layout)

            if dialogBox(d_layout, dialog):
                n = name_edit.text().strip()
                u = url_edit.text().strip()
                if n and u:
                    if "{query}" not in u:
                        messageBox(self, tr("警告"), "URL " + tr("必须包含 {query} 占位符"), 1)
                        continue
                    return n, u
            return None, None

    def _addEngine(self):
        name, url = self._engineDialog(tr("添加搜索引擎"))
        if name and url:
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, url)
            self.search_engines_list.addItem(item)

    def _editEngine(self):
        current_item = self.search_engines_list.currentItem()
        if not current_item:
            messageBox(self, tr("警告"), tr("请先选择一个要编辑的搜索引擎"), 1)
            return
        name, url = self._engineDialog(tr("编辑搜索引擎"), current_item.text(), current_item.data(Qt.ItemDataRole.UserRole))
        if name and url:
            current_item.setText(name)
            current_item.setData(Qt.ItemDataRole.UserRole, url)

    def _delEngine(self):
        """删除搜索引擎"""
        current_row = self.search_engines_list.currentRow()
        if current_row >= 0:
            self.search_engines_list.takeItem(current_row)

    def _onShortcutCapture(self, seq, line_edit):
        """处理快捷键捕获"""
        line_edit.setText(seq)

    def eventFilter(self, obj, event):
        return super().eventFilter(obj, event)

    def _onHotkeyCapture(self, seq):
        """处理启动器快捷键捕获"""
        self.launcher_hotkey_edit.setText(seq)

    def _pickColor(self, slot, button):
        """选择配色并更新色块"""
        initial = QColor(self._colors.get(slot, "")) if self._colors.get(slot) else QColor()
        color = QColorDialog.getColor(initial, self, tr("选择颜色"))
        if color.isValid():
            self._colors[slot] = color.name()
            button.setStyleSheet(f"background-color: {color.name()};")
            button.setToolTip(color.name())

    def _clearColor(self, slot, button):
        """清除配色，恢复主题默认"""
        self._colors.pop(slot, None)
        button.setStyleSheet("")
        button.setToolTip(tr("点击选择颜色"))

    def _setDefaults(self):
        """重置快捷键为默认值"""
        default_shortcuts = DEFAULT_CONFIG["Edit"]["shortcuts"]
        for key in self.SHORTCUT_MAP:
            line_edit = self.shortcut_edits.get(key)
            if line_edit:
                line_edit.setText(default_shortcuts.get(key, ""))

    def getSetting(self) -> dict:
        """获取设置"""
        shortcuts = {}
        for key in self.SHORTCUT_MAP:
            line_edit = self.shortcut_edits.get(key)
            if line_edit:
                keyseq = line_edit.text()
                if keyseq:
                    shortcuts[key] = keyseq
        
        return {
            "language": self.language_combo.currentData(),
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
            "Launch.bg_image": self.bg_image_edit.text().strip(),
            "Launch.opacity": self.opacity_slider.value() / 100,
            "Launch.colors": {k: v for k, v in self._colors.items() if v},
            "Plugin": self.config.get("Plugin", {}),
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
            setMenu(new_context_menu)

        if hasattr(self, '_launcher_config') and isinstance(self._launcher_config, dict):
            launch = dict(self.config.get("Launch", {}))
            hotkey_text = self.launcher_hotkey_edit.text().strip()
            launch["hotkey"] = hotkey_text

            launch["layout"] = self.launcher_layout_combo.currentData()

            launch["mouse_side"] = self.launcher_mouse_side_check.isChecked()
            launch["double_ctrl"] = self.launcher_double_ctrl_check.isChecked()
            launch["on_top"] = self.launcher_on_top_check.isChecked()
            launch["run_hide"] = self.launcher_hide.isChecked()

            launch["path_mode"] = self.launcher_path_mode_combo.currentData()

            old_path_mode = launch.get("path_mode", "absolute")
            if old_path_mode != launch["path_mode"]:
                launch["tools"] = self.convertToolPath(launch["path_mode"], launch.get("tools", {}))

            launch["hover_switch"] = self.launcher_hover_switch_check.isChecked()

            launch["g_w"] = self.g_w_spin.value()
            launch["g_h"] = self.g_h_spin.value()
            launch["i_w"] = self.i_w_spin.value()
            launch["i_h"] = self.i_h_spin.value()
            launch["icon"] = self.icon_spin.value()
            launch["padding"] = self.padding_spin.value()

            lines = []
            for line in self.temp_paths_edit.toPlainText().splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    lines.append(line)
                else:
                    lines.append(os.path.normpath(line))

            launch["Runtime"] = {
                "Python": os.path.normpath(self.python_path_edit.text()) if self.python_path_edit.text() else "",
                "Java": os.path.normpath(self.java_path_edit.text()) if self.java_path_edit.text() else "",
                "Temp_Path": lines,
            }

            self.config.set("Launch", launch)

        for key, value in settings.items():
            self.config.set(key, value)

        for plugin in self._plugin_tabs:
            try:
                # saveConfig 返回 False 表示校验失败，保持对话框不关闭
                if plugin.saveConfig(save=False) is False:
                    return
            except Exception:
                logger.exception(f"插件 {plugin.name} 设置保存失败")
                return

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
