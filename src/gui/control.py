from functools import partial

from PySide6.QtWidgets import QToolBar, QToolButton, QMenu, QWidget, QSizePolicy, QDialog, QLabel, QListWidget, QPushButton, QHBoxLayout, QVBoxLayout
from PySide6.QtGui import QKeySequence, QAction, QIcon
from PySide6.QtCore import Qt

from src.util import tr, scale_value, ENCODING_MAP, icon_dir, messageBox
from src.plugin import getPluginManager, pluginActionMenu
from src.config import DEFAULT_CONFIG

def getMaxIcon(theme="Light"):
    suffix = "-wh" if theme in {"Dark", "UI_Dark"} else ""
    # 如果主题在这个集合中，使用白色的最大化图标，否则使用黑色的

    max_icon = QIcon(str(icon_dir / f"max{suffix}.svg"))
    maxed_icon = QIcon(str(icon_dir / f"maxed{suffix}.svg"))
    return max_icon, maxed_icon

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
        self.main.min_btn.setFixedSize(scale_value(40), scale_value(32))
        self.main.min_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.main.min_btn.clicked.connect(self.main.showMinimized)

        self.main.max_btn = QPushButton()
        self.main.max_btn.setObjectName("max_btn")
        self.main.max_btn.setIcon(max_icon)
        self.main.max_btn.setFixedSize(scale_value(40), scale_value(32))
        self.main.max_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.main.max_btn.clicked.connect(self.main._toggle_maximize)

        self.main.close_btn = QPushButton("×")
        self.main.close_btn.setObjectName("close_btn")
        self.main.close_btn.setFixedSize(scale_value(40), scale_value(32))
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
        }
        for action_name, action in action_map.items():
            if action and action_name in shortcuts:
                action.setShortcut(QKeySequence(shortcuts[action_name]))

    def create_toolbar(self) -> QToolBar:
        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        toolbar.setFixedHeight(scale_value(32))
        toolbar.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        toolbar.mouseDoubleClickEvent = self.main._on_toolbar_double_click
        return toolbar

    def createWindowButton(self, toolbar: QToolBar):
        self.main.window_control.createWindowButton(toolbar)

    def createMenuButton(self, title: str) -> tuple[QToolButton, QMenu]:
        btn = QToolButton()
        btn.setText(title)
        btn.setMinimumWidth(scale_value(90))
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

        for mode in [tr("十六进制")]:
            action = QAction(mode, self.main)
            action.triggered.connect(partial(self.main.change_view_mode, mode))
            menu.addAction(action)

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

    def build_plugin_menu(self) -> tuple[QToolButton, QMenu]:
        btn, menu = self.createMenuButton(tr("插件") + "(&P)")

        self._add_action(menu, tr("插件管理"), self.main._show_plugin_manager)

        menu.addSeparator()

        self.main.action_plugin_placeholder = QAction(tr("无插件"), self.main)
        self.main.action_plugin_placeholder.setEnabled(False)
        menu.addAction(self.main.action_plugin_placeholder)

        self.main.plugin_menu = menu
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
        plugin_btn, _ = self.build_plugin_menu()
        settings_action = self.build_settings_action()

        toolbar.addWidget(file_btn)
        toolbar.addWidget(edit_btn)
        toolbar.addWidget(plugin_btn)
        toolbar.addAction(settings_action)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)

        toolbar.addWidget(self.main.cpu_label)
        self.createWindowButton(toolbar)


class PluginControl:
    """插件控制器 - 管理插件的加载、启用、禁用和删除"""

    def __init__(self, main_window):
        self.main = main_window
        self.plugin_manager = None

    def init_plugins(self):
        """初始化插件系统"""
        self.plugin_manager = getPluginManager(self.main)
        self.plugin_manager.initConfig(self.main.config)
        self.refresh_plugin_menu()

    def save_plugin_config(self):
        """保存插件配置到 config.json"""
        self.plugin_manager.saveConfig(self.main.config)

    def refresh_plugin_menu(self):
        """刷新插件菜单"""
        available_plugins = self.plugin_manager.scanPlugins()
        if available_plugins:
            self.update_plugin_menu(available_plugins)

    def update_plugin_menu(self, available_plugins: list):
        """更新插件菜单"""
        if not hasattr(self.main, 'plugin_menu') or not self.main.plugin_menu:
            return

        plugin_menu: QMenu = self.main.plugin_menu
        plugin_menu.clear()

        plugin_manager_action = QAction(tr("插件管理"), self.main)
        plugin_manager_action.triggered.connect(self.show_plugin_manager)
        plugin_menu.addAction(plugin_manager_action)

        reload_action = QAction(tr("重载插件"), self.main)
        reload_action.triggered.connect(self.reloadPlugins)
        plugin_menu.addAction(reload_action)

        plugin_menu.addSeparator()

        for description, menu_item, _ in pluginActionMenu(self.plugin_manager, self.main):
            if isinstance(menu_item, QMenu):
                menu_item.setTitle(description)
                plugin_menu.addMenu(menu_item)
            else:
                plugin_menu.addAction(menu_item)


    def run_plugin(self, plugin):
        """直接运行已加载的插件"""
        if hasattr(plugin, 'show_ocr_dialog'):
            plugin.show_ocr_dialog()
        elif hasattr(plugin, 'run'):
            plugin.run()
        elif hasattr(plugin, 'show'):
            plugin.show()
        else:
            menu = plugin.getAction()
            if menu and isinstance(menu, QMenu) and menu.actions():
                action = menu.actions()[0]
                action.trigger()
            else:
                messageBox(self.main, tr("提示"), tr("插件") + f"{getattr(plugin, 'description', tr('未知'))}" + tr("暂无功能或未实现"), 1)

    def reloadPlugins(self):
        """重新加载所有插件"""
        self.plugin_manager.initConfig(self.main.config)
        self.refresh_plugin_menu()
        self.main.statusBar().showMessage(tr("插件已重新加载"), 2000)

    def show_plugin_manager(self):
        """显示插件管理对话框"""
        dialog = QDialog(self.main)
        dialog.setWindowTitle(tr("插件管理"))
        dialog.setMinimumWidth(450)

        layout = QVBoxLayout(dialog)

        label = QLabel(tr("已安装插件"))
        layout.addWidget(label)

        plugin_list = QListWidget()
        available_plugins = self.plugin_manager.scanPlugins()

        for plugin_name in available_plugins:
            plugin_list.addItem(self._getPluginItem(plugin_name))

        layout.addWidget(plugin_list)

        btn_layout = QHBoxLayout()

        enable_btn = QPushButton(tr("启用"))
        enable_btn.clicked.connect(partial(self.toggle_plugin, available_plugins, plugin_list, True))
        btn_layout.addWidget(enable_btn)

        disable_btn = QPushButton(tr("禁用"))
        disable_btn.clicked.connect(partial(self.toggle_plugin, available_plugins, plugin_list, False))
        btn_layout.addWidget(disable_btn)

        delete_btn = QPushButton(tr("删除插件"))
        delete_btn.clicked.connect(partial(self.delete_plugin, available_plugins, plugin_list, dialog))
        btn_layout.addWidget(delete_btn)

        close_btn = QPushButton(tr("取消"))
        close_btn.clicked.connect(dialog.close)
        btn_layout.addWidget(close_btn)

        layout.addLayout(btn_layout)

        dialog.exec()

    def delete_plugin(self, plugins: list, plugin_list: QListWidget, dialog: QDialog):
        """删除插件"""
        current_row = plugin_list.currentRow()
        if current_row < 0 or current_row >= len(plugins):
            return

        plugin_name = plugins[current_row]

        if not messageBox(self.main, tr("确认删除"), tr("确定要删除插件") + f" '{plugin_name}' " + tr("吗？") + "\n" + tr("这将同时删除插件文件和相关数据")):
            return

        errors = self.plugin_manager.deletePlugin(plugin_name)
        self.save_plugin_config()

        if errors:
            messageBox(self.main, tr("删除失败"), tr("删除过程出现问题") + "\n".join(errors), 1)
        else:
            messageBox(self.main, tr("删除成功"), tr("插件") + plugin_name + tr("已完全删除"), 1)

        dialog.close()
        self.plugin_manager.reloadPlugins()
        self.refresh_plugin_menu()

    def _getPluginItem(self, plugin_name: str) -> str:
        """获取插件列表项的显示文本"""
        is_enabled = self.plugin_manager.isPluginEnabled(plugin_name)
        plugin = self.plugin_manager.plugins.get(plugin_name)
        if plugin:
            return f"{plugin.description} ({tr('启用') if is_enabled else tr('禁用')})"
        cls = self.plugin_manager.getPluginClass(plugin_name)
        name = getattr(cls, 'description', plugin_name) if cls else plugin_name
        return f"{name} ({tr('未加载')})"

    def toggle_plugin(self, plugins: list, plugin_list: QListWidget, enable: bool):
        """启用或禁用插件"""
        current_row = plugin_list.currentRow()
        if current_row < 0 or current_row >= len(plugins):
            return

        plugin_name = plugins[current_row]

        if enable:
            self.plugin_manager.enablePlugin(plugin_name)
            self.main.statusBar().showMessage(plugin_name + " " + tr("插件已启用"), 2000)
        else:
            self.plugin_manager.disablePlugin(plugin_name)
            self.main.statusBar().showMessage(plugin_name + " " + tr("插件已禁用"), 2000)

        self.save_plugin_config()
        self.refresh_plugin_menu()

        item = plugin_list.item(current_row)
        if item:
            item.setText(self._getPluginItem(plugin_name))
