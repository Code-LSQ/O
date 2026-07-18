from PySide6.QtWidgets import QToolBar, QToolButton, QMenu, QWidget, QSizePolicy, QDialog, QLabel, QListWidget, QPushButton, QHBoxLayout, QVBoxLayout
from PySide6.QtGui import QKeySequence, QAction, QIcon, QMouseEvent
from PySide6.QtCore import Qt, Signal, QPoint

from src.util import tr, ENCODING_MAP, icon_dir, messageBox, logger, UsageMonitor
from src.plugin import getPluginManager
from src.config import DEFAULT_CONFIG, getConfig
from src.gui.view import ViewMode

def getMaxIcon(theme="Light"):
    suffix = "-wh" if theme in {"Dark", "UI_Dark"} else ""
    # 如果主题在这个集合中，使用白色的最大化图标，否则使用黑色的

    max_icon = QIcon(str(icon_dir / f"max{suffix}.svg"))
    maxed_icon = QIcon(str(icon_dir / f"maxed{suffix}.svg"))
    return max_icon, maxed_icon

class WindowControl:
    """窗口拖拽、调整大小和窗口按钮控制"""

    isDragging = Signal(bool)

    def __init__(self, *args, fallback_size=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._fallback_size = fallback_size
        self.setMouseTracking(True)
        self._resize_edge = None
        self._dragging = False
        self._start_global_pos = QPoint()
        self._start_window_pos = QPoint()
        self._start_size = None
        self._title_bar_height = 32
        self._is_maximized = False
        self._pre_maximize_geometry = None

    def _getEdge(self, pos: QPoint):
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

    def _isTitle(self, pos: QPoint):
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

        if self._isTitle(pos):
            self._dragging = True
            self.grabMouse()
            self.isDragging.emit(True)
        else:
            edge = self._getEdge(pos)
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
            self._doResize(gpos)
        else:
            self._doDrag(gpos)

    def _doDrag(self, gpos: QPoint):
        """执行窗口拖拽"""
        dx = gpos.x() - self._start_global_pos.x()
        dy = gpos.y() - self._start_global_pos.y()
        x = self._start_window_pos.x() + dx
        y = self._start_window_pos.y() + dy
        self.move(x, y)

    def _doResize(self, gpos: QPoint):
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

    def toggleMax(self):
        if self._is_maximized:
            self._is_maximized = False
            if self._pre_maximize_geometry:
                self.setGeometry(self._pre_maximize_geometry)
            elif self._fallback_size is not None:
                self.resize(*self._fallback_size)
            self.updateMaxBtn(False)
        else:
            self._pre_maximize_geometry = self.geometry()
            self._is_maximized = True
            screen = self.screen()
            if screen:
                self.setGeometry(screen.availableGeometry())
            self.updateMaxBtn(True)

    def createWindowButton(self, container):
        theme = getConfig().get("theme", "Light")
        max_icon, maxed_icon = getMaxIcon(theme)
        
        self.min_btn = QPushButton("—")
        self.min_btn.setObjectName("min_btn")
        self.min_btn.setFixedSize(40, 32)
        self.min_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.min_btn.clicked.connect(self.showMinimized)

        self.max_btn = QPushButton()
        self.max_btn.setObjectName("max_btn")
        self.max_btn.setIcon(max_icon)
        self.max_btn.setFixedSize(40, 32)
        self.max_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.max_btn.clicked.connect(self.toggleMax)

        self.close_btn = QPushButton("×")
        self.close_btn.setObjectName("close_btn")
        self.close_btn.setFixedSize(40, 32)
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.clicked.connect(self.close)

        container.addWidget(self.min_btn)
        container.addWidget(self.max_btn)
        container.addWidget(self.close_btn)

    def updateMaxBtn(self, is_maximized: bool):
        if hasattr(self, 'max_btn'):
            theme = getConfig().get("theme", "Light")
            max_icon, maxed_icon = getMaxIcon(theme)
            self.max_btn.setIcon(maxed_icon if is_maximized else max_icon)

    def createMonitor(self):
        self.cpu_label = QLabel(self)
        self.cpu_label.setObjectName("cpu_label")
        self._usage_monitor = UsageMonitor(self, self.cpu_label, getConfig())
        self._usage_monitor.sync()

    def updateIcons(self, theme: str):
        max_icon, maxed_icon = getMaxIcon(theme)
        if hasattr(self, 'max_btn'):
            is_maximized = self.isMaximized()
            self.max_btn.setIcon(maxed_icon if is_maximized else max_icon)


class MenuControl:
    """菜单/工具栏控制器 - 管理菜单构建和工具栏"""

    def __init__(self, main_window):
        self.main = main_window
        self.config = main_window.config
        self.shortcut_map = {}
        self._setKeys(DEFAULT_CONFIG["Edit"]["shortcuts"])
    
    def _setKeys(self, shortcuts: dict):
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

    def createToolbar(self) -> QToolBar:
        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        toolbar.setFixedHeight(32)
        toolbar.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        toolbar.mouseDoubleClickEvent = self.main.onToolbarDblClick
        return toolbar

    def createWindowButton(self, toolbar: QToolBar):
        self.main.createWindowButton(toolbar)

    def buildViewMenu(self) -> QToolButton:
        btn, menu = self.createMenuButton(tr("模式") + "(&V)")

        menu.setStyleSheet("QMenu::indicator:checked { background-color: palette(text); border-radius: 4px; width: 8px; height: 8px; margin: 2px 2px; }")

        self._view_actions = {}
        for mode in ViewMode.ALL:
            action = QAction(tr(mode), self.main)
            action.setCheckable(True)
            action.triggered.connect(lambda checked, m=mode: self.main.setViewMode(m))
            menu.addAction(action)
            self._view_actions[mode] = action

        menu.aboutToShow.connect(self._syncViewMenu)
        return btn

    def _syncViewMenu(self):
        editor = self.main.getEditor()
        current = self._detectMode(editor)
        supported = self._getModes(editor)
        for mode, action in self._view_actions.items():
            action.setVisible(mode in supported)
            action.setChecked(mode == current)

    def _getModes(self, editor):
        if not editor:
            return [ViewMode.TEXT, ViewMode.HEX]
        return ViewMode.supportedModes(editor.file_path)

    def _detectMode(self, editor):
        if not editor:
            return ViewMode.TEXT
        return ViewMode.getCurrent(editor)

    def createMenuButton(self, title: str) -> tuple[QToolButton, QMenu]:
        btn = QToolButton()
        btn.setText(title)
        btn.setMinimumWidth(90)
        btn.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(btn)
        btn.setMenu(menu)
        return btn, menu

    def _addAction(self, menu: QMenu, text: str, callback, shortcut=None) -> QAction:
        action = QAction(text, self.main)
        if shortcut:
            action.setShortcut(shortcut)
        action.triggered.connect(callback)
        menu.addAction(action)
        return action

    def buildFileMenu(self) -> tuple[QToolButton, QMenu]:
        btn, menu = self.createMenuButton(tr("文件") + "(&F)")

        self.main.action_new = self._addAction(menu, tr("新建") + "(&N)", lambda: (self.main.addTab(), self.main.statusBar().showMessage(tr("新建文件"), 3000)), QKeySequence.StandardKey.New)
        self.main.action_open = self._addAction(menu, tr("打开") + "(&O)", self.main.openFile, QKeySequence.StandardKey.Open)
        self.main.action_open_folder = self._addAction(menu, tr("打开文件夹") + "(&D)", self.main.openFolderDialog)
        self.main.action_folder_view = self._addAction(menu, tr("文件夹视图"), self.main.toggleFolderPanel)

        menu.addSeparator()

        self.main.action_save = self._addAction(menu, tr("保存") + "(&S)", self.main.saveFile, QKeySequence.StandardKey.Save)
        self.main.action_save_as = self._addAction(menu, tr("另存为") + "(&A)", self.main.saveFileAs, QKeySequence.StandardKey.SaveAs)

        menu.addSeparator()

        favorites_menu = QMenu(tr("收藏夹"), self.main)
        menu.addMenu(favorites_menu)
        self.main.favorites_menu = favorites_menu
        self.main._updateFavoritesMenu()

        recent_menu = QMenu(tr("最近打开"), self.main)
        menu.addMenu(recent_menu)
        self.main.recent_menu = recent_menu
        self.main._updateRecentMenu()

        menu.addSeparator()

        self.main.action_exit = self._addAction(menu, tr("退出") + "(&X)", self.main.close, QKeySequence("Ctrl+Q"))

        btn.setMenu(menu)
        return btn, menu

    def buildEditMenu(self) -> tuple[QToolButton, QMenu]:
        btn, menu = self.createMenuButton(tr("编辑") + "(&E)")

        self.main.action_undo = self._addAction(menu, tr("撤销") + "(&U)", self.main.undo, QKeySequence.StandardKey.Undo)
        self.main.action_redo = self._addAction(menu, tr("重做") + "(&R)", self.main.redo, QKeySequence.StandardKey.Redo)

        menu.addSeparator()

        self.main.action_find = self._addAction(menu, tr("查找") + "(&F)", self.main.showFindRe, QKeySequence.StandardKey.Find)
        self.main.action_replace = self._addAction(menu, tr("替换") + "(&H)", self.main.showFindRe, QKeySequence("Ctrl+H"))

        menu.addSeparator()

        self.main.action_select_all = self._addAction(menu, tr("全选") + "(&A)", self.main.selectAll, QKeySequence.StandardKey.SelectAll)

        menu.addSeparator()

        encoding_menu = QMenu(tr("编码"), self.main)
        menu.addMenu(encoding_menu)

        def addEncItems(suffix, callback):
            for enc in ENCODING_MAP:
                action = QAction(enc + " " + suffix, self.main)
                action.setData(enc)
                action.triggered.connect(callback)
                encoding_menu.addAction(action)

        addEncItems(tr("编码打开"), self.main._encodingOpen)
        encoding_menu.addSeparator()
        addEncItems(tr("编码保存"), self.main._encodingSave)

        text_process_menu = QMenu(tr("文本处理"), self.main)
        menu.addMenu(text_process_menu)

        text_process_menu.addAction(self._addAction(text_process_menu, tr("去空行"), self.main.removeEmptyLines))
        text_process_menu.addAction(self._addAction(text_process_menu, tr("去行首空格"), self.main.stripLeadingSpace))
        text_process_menu.addAction(self._addAction(text_process_menu, tr("去行尾空格"), self.main.stripTrailingSpace))
        text_process_menu.addAction(self._addAction(text_process_menu, tr("行首缩进"), self.main.indentLines))

        btn.setMenu(menu)
        return btn, menu

    def settingsAction(self) -> QAction:
        settings_action = QAction(tr("设置"), self.main)
        settings_action.setShortcut(QKeySequence("Ctrl+,"))
        settings_action.triggered.connect(self.main.showSettings)
        return settings_action

    def buildMenuBar(self, toolbar: QToolBar):
        file_btn, _ = self.buildFileMenu()
        edit_btn, _ = self.buildEditMenu()
        view_btn = self.buildViewMenu()
        settings_action = self.settingsAction()

        toolbar.addWidget(file_btn)
        toolbar.addWidget(edit_btn)
        toolbar.addWidget(view_btn)
        toolbar.addAction(settings_action)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)

        toolbar.addWidget(self.main.cpu_label)
        self.createWindowButton(toolbar)
        self._setKeys(self.config.get("Edit.shortcuts", {}))


def managePlugins(parent=None):
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

    def _itemText(name):
        enabled = pm.isPluginEnabled(name)
        status = tr("启用") if enabled else tr("禁用")
        plugin = pm.plugins.get(name)
        if plugin:
            return f"{plugin.description} ({status})"
        cls = pm.pluginClass(name)
        display = getattr(cls, "description", name) if cls else name
        return f"{display} ({status})"

    for name in available:
        plugin_list.addItem(_itemText(name))

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
            item.setText(_itemText(name))

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
                          tr("是否确认删除插件") + " " + name + "\n" +
                          tr("这将同时删除插件文件和相关数据")):
            return
        errors = pm.deletePlugin(name)
        pm.saveConfig(getConfig())
        if errors:
            messageBox(dialog, tr("删除失败"), tr("删除过程出现问题") + "\n".join(errors), 1)
        else:
            messageBox(dialog, tr("删除成功"), tr("已完全删除插件") + " " + name, 1)
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
