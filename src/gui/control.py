import os

from PySide6.QtWidgets import QToolBar, QToolButton, QMenu, QWidget, QSizePolicy, QDialog, QLabel, QListWidget, QPushButton, QHBoxLayout, QVBoxLayout
from PySide6.QtGui import QKeySequence, QAction, QIcon, QMouseEvent
from PySide6.QtCore import Qt, Signal, QPoint

from src.util import tr, ENCODING_MAP, EXTENSION, icon_dir, messageBox, logger
from src.plugin import getPluginManager
from src.config import DEFAULT_CONFIG, getConfig
from src.gui.view import ViewMode

def getMaxIcon(theme="Light"):
    suffix = "-wh" if theme in {"Dark", "UI_Dark"} else ""
    # 如果主题在这个集合中，使用白色的最大化图标，否则使用黑色的

    max_icon = QIcon(str(icon_dir / f"max{suffix}.svg"))
    maxed_icon = QIcon(str(icon_dir / f"maxed{suffix}.svg"))
    return max_icon, maxed_icon

class WindowMouse:
    """窗口拖拽和调整大小"""

    isDragging = Signal(bool)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setMouseTracking(True)
        self._resize_edge = None
        self._dragging = False
        self._start_global_pos = QPoint()
        self._start_window_pos = QPoint()
        self._start_size = None
        self._title_bar_height = 32
        self._is_maximized = False
        self._pre_maximize_geometry = None

    def _get_edge(self, pos: QPoint):
        """获取鼠标位置的边缘方向"""
        w, h = self.width(), self.height()
        x, y = pos.x(), pos.y()
        edge_size = 8

        if x < edge_size:
            return "left"
        if x > w - edge_size:
            return "right"
        if y < edge_size:
            return "top"
        if y > h - edge_size:
            return "bottom"
        return None

    def _is_on_title_bar(self, pos: QPoint):
        """检查是否在标题栏区域（用于拖拽，不包含顶部边缘区域）"""
        edge_size = 8
        return 0 <= pos.x() < self.width() and edge_size <= pos.y() <= self._title_bar_height

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() != Qt.MouseButton.LeftButton:
            return

        pos = event.position().toPoint()
        self._start_global_pos = event.globalPosition().toPoint()
        self._start_window_pos = self.pos()
        self._start_size = self.size()

        if self._is_on_title_bar(pos):
            self._dragging = True
            self.grabMouse()
            self.isDragging.emit(True)
        else:
            edge = self._get_edge(pos)
            if edge:
                self._resize_edge = edge
                self._dragging = True
                self.grabMouse()
                self.isDragging.emit(True)

    def mouseMoveEvent(self, event: QMouseEvent):
        if not self._dragging:
            return

        gpos = event.globalPosition().toPoint()

        if self._resize_edge:
            self._do_resize(gpos)
        else:
            self._do_drag(gpos)

    def _do_drag(self, gpos: QPoint):
        """执行窗口拖拽"""
        dx = gpos.x() - self._start_global_pos.x()
        dy = gpos.y() - self._start_global_pos.y()
        x = self._start_window_pos.x() + dx
        y = self._start_window_pos.y() + dy
        self.move(x, y)

    def _do_resize(self, gpos: QPoint):
        """执行窗口大小调整"""
        dx = gpos.x() - self._start_global_pos.x()
        dy = gpos.y() - self._start_global_pos.y()

        x = self._start_window_pos.x()
        y = self._start_window_pos.y()
        w = self._start_size.width()
        h = self._start_size.height()
        edge = self._resize_edge

        if edge == "left":
            x += dx
            w -= dx
        elif edge == "right":
            w += dx
        elif edge == "top":
            y += dy
            h -= dy
        elif edge == "bottom":
            h += dy

        min_w = self.minimumWidth() or 200
        min_h = self.minimumHeight() or 150

        if w < min_w:
            if edge == "left":
                x = self._start_window_pos.x() + self._start_size.width() - min_w
            w = min_w
        if h < min_h:
            if edge == "top":
                y = self._start_window_pos.y() + self._start_size.height() - min_h
            h = min_h

        if edge in ("left", "top"):
            self.setGeometry(x, y, w, h)
        else:
            self.resize(w, h)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._dragging:
            self.releaseMouse()
            self._resize_edge = None
            self._dragging = False
            self.isDragging.emit(False)

    def _toggle_maximize(self):
        if self._is_maximized:
            self._is_maximized = False
            if self._pre_maximize_geometry:
                self.setGeometry(self._pre_maximize_geometry)
            elif hasattr(self, "_fallback_size"):
                self.resize(*self._fallback_size)
            self.window_control.update_max_button(False)
        else:
            self._pre_maximize_geometry = self.geometry()
            self._is_maximized = True
            screen = self.screen()
            if screen:
                self.setGeometry(screen.availableGeometry())
            self.window_control.update_max_button(True)

class WindowControl:
    def __init__(self, main_window):
        self.main = main_window
        self._current_theme = "Light"

    def createWindowButton(self, container):
        theme = self.main.config.get("theme", "Light")
        self._current_theme = theme
        max_icon, maxed_icon = getMaxIcon(theme)
        
        self.main.min_btn = QPushButton("—")
        self.main.min_btn.setObjectName("min_btn")
        self.main.min_btn.setFixedSize(40, 32)
        self.main.min_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.main.min_btn.clicked.connect(self.main.showMinimized)

        self.main.max_btn = QPushButton()
        self.main.max_btn.setObjectName("max_btn")
        self.main.max_btn.setIcon(max_icon)
        self.main.max_btn.setFixedSize(40, 32)
        self.main.max_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.main.max_btn.clicked.connect(self.main._toggle_maximize)

        self.main.close_btn = QPushButton("×")
        self.main.close_btn.setObjectName("close_btn")
        self.main.close_btn.setFixedSize(40, 32)
        self.main.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.main.close_btn.clicked.connect(self.main.close)

        if isinstance(container, QToolBar):
            container.addWidget(self.main.min_btn)
            container.addWidget(self.main.max_btn)
            container.addWidget(self.main.close_btn)
        else:
            container.addWidget(self.main.min_btn)
            container.addWidget(self.main.max_btn)
            container.addWidget(self.main.close_btn)

    def update_max_button(self, is_maximized: bool):
        if hasattr(self.main, 'max_btn'):
            max_icon, maxed_icon = getMaxIcon(self._current_theme)
            self.main.max_btn.setIcon(maxed_icon if is_maximized else max_icon)

    def update_icons_for_theme(self, theme: str):
        self._current_theme = theme
        max_icon, maxed_icon = getMaxIcon(theme)
        if hasattr(self.main, 'max_btn'):
            is_maximized = self.main.isMaximized()
            self.main.max_btn.setIcon(maxed_icon if is_maximized else max_icon)


class MenuControl:
    """菜单/工具栏控制器 - 管理菜单构建和工具栏"""

    def __init__(self, main_window):
        self.main = main_window
        self.config = main_window.config
        self.shortcut_map = {}
        self._apply_shortcuts(DEFAULT_CONFIG["Edit"]["shortcuts"])
    
    def _apply_shortcuts(self, shortcuts: dict):
        self.shortcut_map = shortcuts
        if not hasattr(self.main, 'action_new'):
            return
        action_map = {
            "new_file": self.main.action_new,
            "open_file": self.main.action_open,
            "save_file": self.main.action_save,
            "save_as": self.main.action_save_as,
            "find": self.main.action_find,
            "replace": getattr(self.main, 'action_replace', None),
            "undo": self.main.action_undo,
            "redo": self.main.action_redo,
            "minimize": getattr(self.main, 'action_minimize', None),
        }
        for action_name, action in action_map.items():
            if action and action_name in shortcuts:
                action.setShortcut(QKeySequence(shortcuts[action_name]))

    def create_toolbar(self) -> QToolBar:
        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        toolbar.setFixedHeight(32)
        toolbar.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        toolbar.mouseDoubleClickEvent = self.main._on_toolbar_double_click
        return toolbar

    def createWindowButton(self, toolbar: QToolBar):
        self.main.window_control.createWindowButton(toolbar)

    def build_view_menu(self) -> QToolButton:
        btn, menu = self.createMenuButton(tr("模式") + "(&V)")

        menu.setStyleSheet("QMenu::indicator:checked { background-color: palette(text); border-radius: 4px; width: 8px; height: 8px; margin: 2px 2px; }")

        self._view_actions = {}
        for mode in ViewMode.ALL:
            action = QAction(tr(mode), self.main)
            action.setCheckable(True)
            action.triggered.connect(lambda checked, m=mode: self.main.change_view_mode(m))
            menu.addAction(action)
            self._view_actions[mode] = action

        menu.aboutToShow.connect(self._sync_view_menu)
        return btn

    def _sync_view_menu(self):
        editor = self.main.get_current_editor()
        current = self._detect_current_mode(editor)
        supported = self._get_supported_modes(editor)
        for mode, action in self._view_actions.items():
            action.setVisible(mode in supported)
            action.setChecked(mode == current)

    def _get_supported_modes(self, editor):
        if not editor or not editor.file_path:
            return [ViewMode.TEXT, ViewMode.HEX]

        ext = os.path.splitext(editor.file_path)[1].lower()
        supported = [ViewMode.TEXT, ViewMode.HEX]

        for mode, keys in ViewMode.EXT_KEYS.items():
            if any(ext in EXTENSION[k] for k in keys):
                supported.append(mode)

        return supported

    def _detect_current_mode(self, editor):
        if not editor:
            return ViewMode.TEXT
        if editor._comic_view_enabled or editor._is_zip_gallery:
            return ViewMode.GALLERY
        if editor._is_pdf:
            return ViewMode.PDF
        if editor.is_image:
            return ViewMode.IMAGE
        return editor.view_mode

    def createMenuButton(self, title: str) -> tuple[QToolButton, QMenu]:
        btn = QToolButton()
        btn.setText(title)
        btn.setMinimumWidth(90)
        btn.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(btn)
        btn.setMenu(menu)
        return btn, menu

    def _add_action(self, menu: QMenu, text: str, callback, shortcut=None) -> QAction:
        action = QAction(text, self.main)
        if shortcut:
            action.setShortcut(shortcut)
        action.triggered.connect(callback)
        menu.addAction(action)
        return action

    def build_file_menu(self) -> tuple[QToolButton, QMenu]:
        btn, menu = self.createMenuButton(tr("文件") + "(&F)")

        self.main.action_new = self._add_action(menu, tr("新建") + "(&N)", self.main.new_file, QKeySequence.StandardKey.New)
        self.main.action_open = self._add_action(menu, tr("打开") + "(&O)", self.main.open_file, QKeySequence.StandardKey.Open)
        self.main.action_open_folder = self._add_action(menu, tr("打开文件夹") + "(&D)", self.main.open_folder_dialog)
        self.main.action_folder_view = self._add_action(menu, tr("文件夹视图"), self.main.toggle_folder_panel)

        menu.addSeparator()

        self.main.action_save = self._add_action(menu, tr("保存") + "(&S)", self.main.save_file, QKeySequence.StandardKey.Save)
        self.main.action_save_as = self._add_action(menu, tr("另存为") + "(&A)", self.main.save_file_as, QKeySequence.StandardKey.SaveAs)

        menu.addSeparator()

        favorites_menu = QMenu(tr("收藏夹"), self.main)
        menu.addMenu(favorites_menu)
        self.main.favorites_menu = favorites_menu
        self.main._update_favorites_menu()

        recent_menu = QMenu(tr("最近打开"), self.main)
        menu.addMenu(recent_menu)
        self.main.recent_menu = recent_menu
        self.main._update_recent_menu()

        menu.addSeparator()

        self.main.action_exit = self._add_action(menu, tr("退出") + "(&X)", self.main.close, QKeySequence("Ctrl+Q"))

        btn.setMenu(menu)
        return btn, menu

    def build_edit_menu(self) -> tuple[QToolButton, QMenu]:
        btn, menu = self.createMenuButton(tr("编辑") + "(&E)")

        self.main.action_undo = self._add_action(menu, tr("撤销") + "(&U)", self.main.undo, QKeySequence.StandardKey.Undo)
        self.main.action_redo = self._add_action(menu, tr("重做") + "(&R)", self.main.redo, QKeySequence.StandardKey.Redo)

        menu.addSeparator()

        self.main.action_find = self._add_action(menu, tr("查找") + "(&F)", self.main.showFindRe, QKeySequence.StandardKey.Find)
        self.main.action_replace = self._add_action(menu, tr("替换") + "(&H)", self.main.showFindRe, QKeySequence("Ctrl+H"))

        menu.addSeparator()

        self.main.action_select_all = self._add_action(menu, tr("全选") + "(&A)", self.main.select_all, QKeySequence.StandardKey.SelectAll)

        menu.addSeparator()

        encoding_menu = QMenu(tr("编码"), self.main)
        menu.addMenu(encoding_menu)

        def add_encoding_actions(format_str, callback):
            for enc in ENCODING_MAP:
                action = QAction(format_str.format(enc), self.main)
                action.setData(enc)
                action.triggered.connect(callback)
                encoding_menu.addAction(action)

        add_encoding_actions(tr("以 {} 编码打开"), self.main._on_encoding_open_triggered)
        encoding_menu.addSeparator()
        add_encoding_actions(tr("保存为 {} 编码"), self.main._on_encoding_save_triggered)

        text_process_menu = QMenu(tr("文本处理"), self.main)
        menu.addMenu(text_process_menu)

        text_process_menu.addAction(self._add_action(text_process_menu, tr("去空行"), self.main.remove_empty_lines))
        text_process_menu.addAction(self._add_action(text_process_menu, tr("去行首空格"), self.main.strip_leading_space))
        text_process_menu.addAction(self._add_action(text_process_menu, tr("去行尾空格"), self.main.strip_trailing_space))
        text_process_menu.addAction(self._add_action(text_process_menu, tr("行首缩进"), self.main.indent_lines))

        btn.setMenu(menu)
        return btn, menu

    def build_settings_action(self) -> QAction:
        settings_action = QAction(tr("设置"), self.main)
        settings_action.setShortcut(QKeySequence("Ctrl+,"))
        settings_action.triggered.connect(self.main.show_settings)
        return settings_action

    def buildMenuBar(self, toolbar: QToolBar):
        file_btn, _ = self.build_file_menu()
        edit_btn, _ = self.build_edit_menu()
        view_btn = self.build_view_menu()
        settings_action = self.build_settings_action()

        toolbar.addWidget(file_btn)
        toolbar.addWidget(edit_btn)
        toolbar.addWidget(view_btn)
        toolbar.addAction(settings_action)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)

        toolbar.addWidget(self.main.cpu_label)
        self.createWindowButton(toolbar)
        self._apply_shortcuts(self.config.get("Edit.shortcuts", {}))


def show_plugin_dialog(parent=None):
    """显示插件管理对话框"""
    pm = getPluginManager()

    dialog = QDialog(parent)
    dialog.setAttribute(Qt.WA_DeleteOnClose)
    dialog.setWindowTitle(tr("插件管理"))
    dialog.setMinimumWidth(450)

    layout = QVBoxLayout(dialog)

    label = QLabel(tr("已安装插件"))
    layout.addWidget(label)

    plugin_list = QListWidget()
    available = pm.scanPlugins()

    def _item_text(name):
        enabled = pm.isPluginEnabled(name)
        status = tr("启用") if enabled else tr("禁用")
        plugin = pm.plugins.get(name)
        if plugin:
            return f"{plugin.description} ({status})"
        cls = pm.getPluginClass(name)
        display = getattr(cls, "description", name) if cls else name
        return f"{display} ({status})"

    for name in available:
        plugin_list.addItem(_item_text(name))

    layout.addWidget(plugin_list)

    btn_layout = QHBoxLayout()

    def _toggle(enable):
        row = plugin_list.currentRow()
        if row < 0 or row >= len(available):
            return
        name = available[row]
        if enable:
            pm.enablePlugin(name)
            logger.info(name + " " + tr("插件已启用"))
        else:
            pm.disablePlugin(name)
            logger.info(name + " " + tr("插件已禁用"))
        pm.saveConfig(getConfig())
        item = plugin_list.item(row)
        if item:
            item.setText(_item_text(name))

    enable_btn = QPushButton(tr("启用"))
    enable_btn.clicked.connect(lambda: _toggle(True))
    btn_layout.addWidget(enable_btn)

    disable_btn = QPushButton(tr("禁用"))
    disable_btn.clicked.connect(lambda: _toggle(False))
    btn_layout.addWidget(disable_btn)

    def _delete():
        row = plugin_list.currentRow()
        if row < 0 or row >= len(available):
            return
        name = available[row]
        if not messageBox(dialog, tr("确认删除"),
                          tr("确定要删除插件") + f" '{name}' " + tr("吗？") + "\n" +
                          tr("这将同时删除插件文件和相关数据")):
            return
        errors = pm.deletePlugin(name)
        pm.saveConfig(getConfig())
        if errors:
            messageBox(dialog, tr("删除失败"), tr("删除过程出现问题") + "\n".join(errors), 1)
        else:
            messageBox(dialog, tr("删除成功"), tr("插件") + name + tr("已完全删除"), 1)
        dialog.close()
        pm.reloadPlugins()

    delete_btn = QPushButton(tr("删除插件"))
    delete_btn.clicked.connect(_delete)
    btn_layout.addWidget(delete_btn)

    close_btn = QPushButton(tr("取消"))
    close_btn.clicked.connect(dialog.close)
    btn_layout.addWidget(close_btn)

    layout.addLayout(btn_layout)
    dialog.exec()
