import os
import re
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QTabWidget, QMessageBox, QMenu, QStatusBar, QLabel, QListWidget, QSplitter, QListWidgetItem, QPlainTextEdit, QFileDialog
from PySide6.QtGui import QAction, QCloseEvent, QDragEnterEvent, QDropEvent, QTextCursor
from PySide6.QtCore import Qt, QTimer

from src.config import SettingsDialog, getConfig
from src.util import root, logger, tr, encodingName, APP_NAME, getFilePath, urlToPath, restartApplication, messageBox, inputDialog, UsageMonitor, showFile, ENCODING_MAP
from src.system import setMenu, isMenuRegister
from src.plugin import getPluginManager
from src.file import ArchiveItemModel, FolderPanelManager, createBackup
from src.core.md import extractToc
from src.gui.find_re import FindReplaceDialog
from src.gui.tab import EditorTab
from src.gui.view import ViewMode, readFileLimit
from src.gui.control import WindowMouse, WindowControl, MenuControl


class EditorWindow(WindowMouse, QMainWindow):
    """编辑器窗口"""

    def __init__(self, app: QApplication=None, file_path=None, main_window=None):
        super().__init__()
        
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(f"{APP_NAME}")
        self.setAcceptDrops(True)
        self.app = app
        self.config = getConfig()
        self._main_window = main_window
        self._initCoreAttributes()
        self.initUI()
        self.applyConfig()
        if self.config.get("context_menu", False) and not isMenuRegister():
            logger.info("正在注册右键菜单")
            setMenu(True)
        self._initFromConfig(file_path)
        self._initialization_complete = True
    
    def _initCoreAttributes(self):
        """初始化核心属性"""
        self.find_replace_dialog = None
        self.auto_save_timer = None
        self._fallback_size = (1000, 650)
        self._initialization_complete = False
        self.window_control = WindowControl(self)
        self.action_minimize = QAction(self)
        self.action_minimize.triggered.connect(self.showMinimized)
        self.addAction(self.action_minimize)

    def _initFromConfig(self, file_path) -> str:
        """从配置恢复状态"""
        self._loadOpenFiles()
        if file_path:
            if os.path.isdir(file_path):
                self.loadFolder(file_path)
            else:
                self.openFilePath(file_path)
        if (last_folder := self.config.get("Edit.folder", "")) and os.path.isdir(last_folder):
            self.loadFolder(last_folder)
    
    def close(self):
        """关闭编辑器窗口"""
        super().close()
    
    def changeEvent(self, event):
        """窗口状态变化事件 - 最小化时暂停定时器"""
        if event.type() == event.Type.WindowStateChange:
            if self.windowState() & Qt.WindowState.WindowMinimized:
                self._usage_timer_was_active = self._usage_monitor.pause()
            else:
                self._usage_monitor.resume(getattr(self, '_usage_timer_was_active', False))
        super().changeEvent(event)
    
    def _onToolbarDoubleClick(self, event):
        """工具栏双击事件 - 最大化/还原窗口"""
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggleMax()
    
    def initUI(self):
        """初始化用户界面"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        self._menu_controller = MenuControl(self)
        self._toolbar = self._menu_controller.createToolbar()
        self._toolbar.mouseDoubleClickEvent = self._onToolbarDoubleClick
        
        # CPU / 内存 显示标签
        self.cpu_label = QLabel(self)
        self.cpu_label.setObjectName("cpu_label")

        self._menu_controller.buildMenuBar(self._toolbar)
        
        layout.addWidget(self._toolbar)
        
        self._usage_monitor = UsageMonitor(self, self.cpu_label, self.config)
        self._usage_monitor.sync()

        # 水平布局用于放置文件夹面板和编辑器
        content_layout = QHBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        layout.addLayout(content_layout)
        
        # 左侧文件夹视图（延迟创建）
        self._folder_panel_wrapper = None
        self._folder_placeholder = None
        self.archive_model = ArchiveItemModel()
        
        # 编辑器区域
        editor_widget = QWidget()
        editor_layout = QVBoxLayout(editor_widget)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        
        # 初始化状态栏（在 tab 创建之前，确保 _connectCursorPosition 访问时不崩溃）
        self.setStatusBar(QStatusBar(self))
        
        self.cursor_pos_label = QLabel("")
        self.cursor_pos_label.setMinimumWidth(120)
        self.cursor_pos_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.cursor_pos_label.setStyleSheet("background: transparent;")
        self.statusBar().addPermanentWidget(self.cursor_pos_label)

        self.encoding_label = QLabel("UTF-8")
        self.encoding_label.setStyleSheet("background: transparent;")
        self.statusBar().addPermanentWidget(self.encoding_label)
        
        # 根据配置决定是否显示标签页
        if self.config.get("Edit.multi_tab", True):
            # 标签页区域容器
            tab_container = QWidget()
            tab_layout = QHBoxLayout(tab_container)
            tab_layout.setContentsMargins(0, 0, 0, 0)
            tab_layout.setSpacing(0)
            
            # 标签页
            self.tab_widget = QTabWidget()
            self.tab_widget.setDocumentMode(True)
            self.tab_widget.setTabsClosable(True)
            self.tab_widget.setMovable(True)
            self.tab_widget.tabCloseRequested.connect(self.closeTab)
            self.tab_widget.currentChanged.connect(self._onTabChanged)
            self.tab_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self.tab_widget.customContextMenuRequested.connect(self._tabContextMenu)
            tab_layout.addWidget(self.tab_widget)
            
            editor_layout.addWidget(tab_container)
            self._use_tabs = True
        else:
            # 单标签页模式：不显示标签栏
            self._use_tabs = False
            self.tab_widget = None
            self.single_editor = None
        
        if self._use_tabs:
            # 先恢复上次未关闭的文件，如果有则不创建空白标签
            openFiles = self.config.get("Edit.open", [])
            if not openFiles:
                self.addTab()
        
        # 使用splitter分割文件夹视图和编辑器
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # 文件夹面板占位
        placeholder = QWidget()
        placeholder.setFixedWidth(0)
        placeholder.hide()
        self.splitter.addWidget(placeholder)
        self._folder_placeholder = placeholder
        
        # 编辑器和标题面板的容器
        editor_container = QWidget()
        editor_container_layout = QHBoxLayout(editor_container)
        editor_container_layout.setContentsMargins(0, 0, 0, 0)
        editor_container_layout.setSpacing(0)
        
        # 标题面板
        self._toc_panel = TocPanel(self, self.splitter)
        toc_panel_widget = self._toc_panel.create()
        toc_panel_widget.hide()
        editor_container_layout.addWidget(toc_panel_widget)
        
        # 编辑器区域
        editor_container_layout.addWidget(editor_widget, 1)
        
        self.splitter.addWidget(editor_container)
        
        # 初始设置sizes和禁用splitter分隔条
        self.splitter.setSizes([0, 800])
        self.splitter.handle(0).setEnabled(False)
        
        content_layout.addWidget(self.splitter)
        
        # 初始化文件夹面板管理器（在splitter创建之后）
        self._folder_panel_manager = FolderPanelManager(self, self.splitter, self.config, self._folder_placeholder, self)
        
        # 单标签页模式下直接创建编辑器
        if not self._use_tabs:
            self._initSingleEditor(editor_layout)
    
    def _onFolderHeaderClicked(self, event):
        """点击父文件夹标签时切换到上级目录"""
        self._folder_panel_manager._onHeaderClicked(event)

    def openFolderDialog(self):
        """打开文件夹对话框"""
        folder = getFilePath(self, tr("选择文件夹"), mode="dir")
        if folder:
            self.loadFolder(folder)
    
    def loadFolder(self, folder_path: str):
        """加载文件夹"""
        self._folder_panel_manager.load(folder_path)
    
    def toggleFolderPanel(self):
        """切换文件夹面板 - 只有加载和删除两种状态"""
        if self._folder_panel_manager.isVisible():
            self._folder_panel_manager.close()
            self.statusBar().showMessage(tr("已关闭文件夹视图"), 2000)
        else:
            folder = str(root)
            if not os.path.isdir(folder):
                folder = getFilePath(self, tr("选择文件夹"), mode="dir")
                if not folder:
                    return
            self.loadFolder(folder)
    
    def _initSingleEditor(self, parent_layout):
        """初始化单标签页模式的编辑器"""
        self.single_editor = EditorTab()
        self.single_editor.setContent("")
        self.single_editor.file_opened.connect(self.openFilePath)
        self.single_editor.folder_opened.connect(self.loadFolder)
        self._connectCursorPosition(self.single_editor)
        
        self._applyEditorSettings(self.single_editor)
        
        parent_layout.addWidget(self.single_editor)
        
    def applyConfig(self):
        # 应用配置到界面
        width = self.config.get("Edit.width")
        height = self.config.get("Edit.height")
        x = self.config.get("Edit.x")
        y = self.config.get("Edit.y")
        
        if x and y:
            self.setGeometry(x, y, width, height)
        else:
            self.resize(width, height)
        
        if self.tab_widget:
            self.tab_widget.setTabsClosable(True)
            self.tab_widget.setDocumentMode(True)
        
        self._applyEditorSettings()
        self._reloadEditorShortcuts()
        
        self._setupAutoSave()
    
    def _iterEditors(self):
        """遍历所有编辑器（tabs 模式和单标签模式统一）"""
        if self._use_tabs and self.tab_widget:
            for i in range(self.tab_widget.count()):
                widget = self.tab_widget.widget(i)
                if isinstance(widget, EditorTab):
                    yield widget
        elif hasattr(self, 'single_editor') and self.single_editor:
            yield self.single_editor
    
    def _applyEditorSettings(self, editor: EditorTab = None):
        """应用编辑器设置（字体、行距等）"""
        auto_wrap = self.config.get("Edit.wrap", False)
        line_numbers = self.config.get("Edit.line_numbers", False)
        line_spacing = self.config.get("Edit.line_spacing", 0)
        auto_indent = self.config.get("Edit.indent", True)
        
        def applyToEditor(ed):
            ed.text_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth if auto_wrap else QPlainTextEdit.LineWrapMode.NoWrap)
            ed.text_edit.setLineNumbersVisible(line_numbers)
            ed.setLineSpacing(line_spacing)
            ed.text_edit.setAutoIndent(auto_indent)
        
        if editor:
            applyToEditor(editor)
        else:
            for ed in self._iterEditors():
                applyToEditor(ed)

    def _reloadEditorShortcuts(self):
        for ed in self._iterEditors():
            if hasattr(ed, 'text_edit') and hasattr(ed.text_edit, '_reloadShortcuts'):
                ed.text_edit._reloadShortcuts()

    def _setupAutoSave(self):
        """设置自动保存"""
        if self.auto_save_timer:
            self.auto_save_timer.stop()
            self.auto_save_timer.deleteLater()
            self.auto_save_timer = None
        
        if self.config.get("Edit.auto_save", False):
            interval = self.config.get("Edit.auto_save_interval", 60) * 1000
            self.auto_save_timer = QTimer(self)
            self.auto_save_timer.timeout.connect(self._doAutoSave)
            self.auto_save_timer.start(interval)
    
    def _doAutoSave(self):
        """执行自动保存"""
        for i, editor in enumerate(self._iterEditors()):
            file_path = editor.file_path
            if editor.getHandler(ViewMode.GALLERY).is_viewing_archive_image or not file_path or not editor.is_modified:
                continue
            
            try:
                encoding = editor.encoding
                createBackup(file_path, self.config)
                with open(file_path, "w", encoding=encoding) as f:
                    f.write(editor.text_edit.toPlainText())
                editor.markSaved()
                
                if self.tab_widget:
                    self.tab_widget.setTabText(i, editor.getTitle())
                
                logger.info(f"自动保存: {file_path}")
            except Exception:
                logger.exception("自动保存失败")
    
    def dragEnterEvent(self, event: QDragEnterEvent):
        """拖拽进入事件"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
    
    def dropEvent(self, event: QDropEvent):
        """拖拽放下事件"""
        urls = event.mimeData().urls()
        if urls:
            for url in urls:
                file_path = urlToPath(url)
                if os.path.exists(file_path):
                    if os.path.isdir(file_path):
                        self.loadFolder(file_path)
                    else:
                        self.openFilePath(file_path)
                    event.acceptProposedAction()
    
    def getEditor(self) -> EditorTab:
        """获取当前编辑器"""
        if self._use_tabs and self.tab_widget:
            widget = self.tab_widget.currentWidget()
            if isinstance(widget, EditorTab):
                return widget
        elif hasattr(self, 'single_editor') and self.single_editor:
            return self.single_editor
        return None
    
    def _connectCursorPosition(self, editor: EditorTab):
        """连接编辑器光标信号到状态栏标签"""
        if not editor:
            return
        is_special = editor._current_mode in (ViewMode.IMAGE, ViewMode.GALLERY, ViewMode.PDF)
        self.cursor_pos_label.setVisible(not is_special)

        def callback(line: int, col: int):
            if hasattr(self, 'cursor_pos_label'):
                self.cursor_pos_label.setText(tr("行") + f" {line}, " + tr("列") + f" {col}")
        editor._cursor_position_callback = callback
        tc = editor.text_edit.textCursor()
        line = tc.blockNumber() + 1
        col = tc.columnNumber() + 1
        if hasattr(self, 'cursor_pos_label'):
            self.cursor_pos_label.setText(tr("行") + f" {line}, " + tr("列") + f" {col}")

    def _onTabChanged(self, index: int):
        """切换标签页时更新编码显示及光标位置"""
        if not hasattr(self, 'encoding_label'):
            return
        if hasattr(self, '_last_tab_index') and self._last_tab_index >= 0 and self.tab_widget:
            old = self.tab_widget.widget(self._last_tab_index)
            if isinstance(old, EditorTab):
                old.getHandler(old._current_mode).deactivate(old)
        self._last_tab_index = index
        editor = self.getEditor()
        if editor:
            if editor._current_mode:
                handler = editor.getHandler(editor._current_mode)
                handler.activate(editor)
            self._connectCursorPosition(editor)
            is_special_mode = editor._current_mode in (ViewMode.IMAGE, ViewMode.GALLERY, ViewMode.PDF)
            self.encoding_label.setVisible(not is_special_mode)
            self.cursor_pos_label.setVisible(not is_special_mode)
            if not is_special_mode:
                encoding = editor.encoding or "UTF-8"
                self.encoding_label.setText(encodingName(encoding))
        else:
            self.encoding_label.setVisible(True)
            self.encoding_label.setText("UTF-8")
    
    def addTab(self, file_path: str = None, content: str = None) -> EditorTab:
        """添加新的编辑器标签页"""
        editor = EditorTab()
        if file_path:
            editor.setFilePath(file_path)
        if content is not None:
            editor.setContent(content)
        
        self._applyEditorSettings(editor)
        
        editor.file_opened.connect(self.openFilePath)
        editor.folder_opened.connect(self.loadFolder)
        editor.file_changed.connect(self._onFileChanged)
        editor.markdown_mode_changed.connect(self._onMarkdownModeChanged)
        self._connectCursorPosition(editor)
        
        title = editor.getTitle()
        self.tab_widget.addTab(editor, title)
        self.tab_widget.setCurrentWidget(editor)
        
        return editor
    
    def _onFileChanged(self, changed: bool):
        """处理编辑器文件更改信号"""
        editor = self.sender()
        if not editor or not isinstance(editor, EditorTab):
            return
        self.updateTabTitle(editor)
    
    def _onMarkdownModeChanged(self, is_markdown: bool):
        """处理markdown模式切换"""
        editor = self.sender()
        if not editor or not isinstance(editor, EditorTab):
            return
        
        if is_markdown and editor._current_mode == ViewMode.MARKDOWN:
            self._toc_panel.updateToc(editor._original_content)
        else:
            self._toc_panel.hidePanel()
    
    def updateTabTitle(self, editor: EditorTab):
        """更新标签页标题"""
        if not self.tab_widget:
            return
        index = self.tab_widget.indexOf(editor)
        if index >= 0:
            self.tab_widget.setTabText(index, editor.getTitle())
    
    def closeTab(self, index: int):
        """关闭指定索引的标签页"""
        if index < 0 or not self.tab_widget:
            return
        
        editor = self.tab_widget.widget(index)
        if not isinstance(editor, EditorTab):
            return
        
        if editor.is_modified:
            reply = messageBox(self, tr("保存确认"), tr("是否保存更改") + " \"" + editor.getTitle() + "\"?", 3)

            if reply == QMessageBox.StandardButton.Save:
                if not self.saveFile():
                    return
            elif reply == QMessageBox.StandardButton.Cancel:
                return

        self.tab_widget.removeTab(index)

        editor.text_edit.setZoomCallback(None)
        editor.text_edit.removeEventFilter(editor)
        editor.text_edit._parent_tab = None

        for mode, handler in list(editor.handlers.items()):
            handler.deactivate(editor)
            handler.close(editor)
        editor.handlers.clear()

        editor._markdown_cache.clear()
        editor._page_buffer.clear()

        if editor.highlighter:
            editor.highlighter.setDocument(None)
            editor.highlighter.deleteLater()
            editor.highlighter = None

        editor.text_edit.setDocument(None)
        editor.deleteLater()
        
        self._toc_panel.hidePanel()
        
        if self.tab_widget.count() == 0:
            self.addTab()
    
    def _tabContextMenu(self, pos):
        """显示标签页右键菜单"""
        tab_bar = self.tab_widget.tabBar()
        index = tab_bar.tabAt(pos)
        if index < 0:
            return
        
        menu = QMenu(self)
        
        editor = self.tab_widget.widget(index)
        file_path = editor.file_path if editor and not editor.getHandler(ViewMode.GALLERY).is_viewing_archive_image else None
        
        rename_action = QAction(tr("重命名"), self)
        rename_action.triggered.connect(lambda checked, i=index: self._renameTabFile(i))
        menu.addAction(rename_action)
        
        open_folder_action = QAction(tr("在文件资源管理器中显示"), self)
        open_folder_action.triggered.connect(lambda checked, fp=file_path: showFile(fp, self))
        open_folder_action.setEnabled(bool(file_path))
        menu.addAction(open_folder_action)
        
        menu.addSeparator()
        
        if file_path and self.config.isFavorite(file_path):
            remove_fav_action = QAction(tr("从收藏夹移除"), self)
            remove_fav_action.triggered.connect(lambda checked, fp=file_path: self.delFav(fp))
            menu.addAction(remove_fav_action)
        else:
            add_fav_action = QAction(tr("添加到收藏夹"), self)
            add_fav_action.triggered.connect(lambda checked, fp=file_path: self.addFav(fp))
            add_fav_action.setEnabled(bool(file_path))
            menu.addAction(add_fav_action)
        
        menu.exec_(tab_bar.mapToGlobal(pos))
    
    def _renameTabFile(self, index: int):
        """重命名标签页对应的文件"""
        editor = self.tab_widget.widget(index)
        if not editor:
            return
        file_path = editor.file_path
        if editor.getHandler(ViewMode.GALLERY).is_viewing_archive_image or not file_path:
            self.statusBar().showMessage(tr("未保存的文件无法重命名"), 2000)
            return
        
        new_name = inputDialog(self, tr("重命名文件"), tr("新文件名"), default=os.path.basename(file_path))
        if new_name:
            new_path = os.path.normpath(os.path.join(os.path.dirname(file_path), new_name))
            if os.path.exists(new_path):
                messageBox(self, tr("错误"), tr("文件名已存在"), 1)
                return
            try:
                os.rename(file_path, new_path)
                editor.setFilePath(new_path)
                self.tab_widget.setTabText(index, editor.getTitle())
                self.statusBar().showMessage(tr("已重命名为") + ": " + new_name, 2000)
            except Exception as e:
                messageBox(self, tr("错误"), tr("重命名失败") + ": " + str(e), 1)
    
    def _updateFavoritesMenu(self):
        """更新收藏夹菜单"""
        if not hasattr(self, 'favorites_menu'):
            return
        self.favorites_menu.clear()
        
        favorites = self.config.get("Edit.favorites", [])
        if not favorites:
            empty_action = QAction(tr("无收藏文件"), self)
            empty_action.setEnabled(False)
            self.favorites_menu.addAction(empty_action)
        else:
            for file_path in favorites:
                normalized_path = os.path.normpath(file_path)
                name = os.path.basename(normalized_path) if os.path.basename(normalized_path) else normalized_path
                action = QAction(name, self)
                action.setData(normalized_path)
                action.triggered.connect(lambda checked, p=normalized_path: self._openFavoriteItem(p))
                self.favorites_menu.addAction(action)
        
        self.favorites_menu.addSeparator()
        
        clear_action = QAction(tr("清空收藏夹"), self)
        clear_action.triggered.connect(self._clearFavorites)
        self.favorites_menu.addAction(clear_action)
    
    def _openFavoriteItem(self, file_path: str):
        """打开收藏项（文件或文件夹）"""
        normalized_path = os.path.normpath(file_path)
        if os.path.isdir(normalized_path):
            self._folder_panel_manager.load(normalized_path)
        elif os.path.isfile(normalized_path):
            self.openFilePath(normalized_path)
    
    def _clearFavorites(self):
        """清空收藏夹"""
        self.config.set("Edit.favorites", [])
        self._updateFavoritesMenu()
        self.statusBar().showMessage(tr("已清空收藏夹"), 2000)
    
    def _updateRecentMenu(self):
        """更新最近打开菜单"""
        if not hasattr(self, 'recent_menu'):
            return
        self.recent_menu.clear()
        
        recent_files = self.config.get("Edit.recent", [])
        if not recent_files:
            empty_action = QAction(tr("无最近文件"), self)
            empty_action.setEnabled(False)
            self.recent_menu.addAction(empty_action)
        else:
            for file_path in recent_files:
                name = Path(file_path).name
                action = QAction(name, self)
                action.setData(file_path)
                action.triggered.connect(lambda checked, p=file_path: self.openFilePath(p))
                self.recent_menu.addAction(action)
        
        self.recent_menu.addSeparator()
        
        clear_action = QAction(tr("清空最近记录"), self)
        clear_action.triggered.connect(self._clearRecent)
        self.recent_menu.addAction(clear_action)
    
    def _clearRecent(self):
        self.config.set("Edit.recent", [])
        self._updateRecentMenu()
        self.statusBar().showMessage(tr("已清空最近记录"), 2000)
    
    def addFav(self, file_path: str):
        """添加文件到收藏夹"""
        if file_path:
            normalized_path = os.path.normpath(file_path)
            self.config.addFavorite(normalized_path)
            self._updateFavoritesMenu()
            self.statusBar().showMessage(tr("已添加到收藏夹") + ": " + os.path.basename(normalized_path), 2000)
    
    def delFav(self, file_path: str):
        """从收藏夹移除"""
        if file_path:
            self.config.removeFavorite(file_path)
            self._updateFavoritesMenu()
            self.statusBar().showMessage(tr("已从收藏夹移除") + ": " + os.path.basename(file_path), 2000)
    
    def openFile(self):
        """打开文件对话框"""
        file_path = getFilePath(self, tr("打开文件"), tr("所有文件") + " (*.*);;" + tr("文本文件") + " (*.txt *.md)")
        if not file_path:
            return
        self.openFilePath(file_path)

    def openFilePath(self, file_path: str):
        """打开指定路径的文件"""
        file_path = os.path.normpath(file_path)
        if not file_path:
            return

        if os.path.isdir(file_path):
            self.loadFolder(file_path)
            return

        # 避免循环依赖
        from src.gui.tab import EditorTab

        if not self.config.get("Edit.multi_tab", False):
            editor = self.getEditor()
            self._doOpenFile(file_path)
            return

        if self.tab_widget:
            for i in range(self.tab_widget.count()):
                editor = self.tab_widget.widget(i)
                if isinstance(editor, EditorTab) and editor.file_path == file_path:
                    self.tab_widget.setCurrentIndex(i)
                    self.statusBar().showMessage(tr("文件已在标签页中打开") + ": " + str(file_path), 3000)
                    return

        self._doOpenFile(file_path)

    def _doOpenFile(self, file_path: str):
        """实际打开文件的逻辑"""
        if not file_path or not os.path.isfile(file_path):
            messageBox(self, tr("打开失败"), tr("文件不存在或路径无效") + ": " + str(file_path), 1)
            return

        # 插件 fileHandlers 优先级最高
        for can_handle, open_file in getPluginManager().fileHandlers:
            if can_handle(file_path):
                open_file(file_path, self)
                self.config.addRecentFile(file_path)
                return

        if self._use_tabs:
            editor = self.addTab(file_path)
        else:
            editor = self.single_editor
            editor.setFilePath(file_path)

        editor.file_path = file_path
        editor.setupHighlighter()

        ViewMode.openFile(editor, file_path)
        if self._use_tabs:
            self._onTabChanged(self.tab_widget.currentIndex())

        self.encoding_label.setText(encodingName(editor.encoding) if editor.encoding else "")
        self._toc_panel.hidePanel()
        self.config.addRecentFile(file_path)
        self.statusBar().showMessage(
            tr("已打开") + ": " + os.path.abspath(file_path), 3000)

    def saveFile(self) -> bool:
        """保存当前文件（支持大文件翻页合并保存）"""
        editor = self.getEditor()
        if not editor:
            return False

        file_path = editor.file_path

        if editor.getHandler(ViewMode.GALLERY).is_viewing_archive_image or not file_path:
            return self.saveFileAs()

        encoding = editor.encoding

        backup_path = None
        if file_path:
            backup_path = createBackup(file_path, self.config)

        try:
            # 大文件翻页模式下：合并各页内容再写出
            if editor._is_truncated:
                with open(file_path, "w", encoding=encoding) as f:
                    f.write(editor._assembleContent())
            else:
                with open(file_path, "w", encoding=encoding) as f:
                    f.write(editor.text_edit.toPlainText())

            editor.markSaved()
            if self.tab_widget:
                self.tab_widget.setTabText(self.tab_widget.currentIndex(), editor.getTitle())

            if backup_path:
                self.statusBar().showMessage(tr("已保存") + ": " + file_path + " - " + encoding + " " + tr("已备份"), 3000)
            else:
                self.statusBar().showMessage(tr("已保存") + ": " + file_path + " - " + encoding, 3000)
            return True

        except Exception as e:
            messageBox(self, tr("保存失败"), tr("保存文件时发生错误") + ": " + str(e), 1)
            return False

    def saveFileAs(self) -> bool:
        """另存为"""
        editor = self.getEditor()
        if not editor:
            return False

        file_path, _ = QFileDialog.getSaveFileName(self, tr("另存为"), "", tr("所有文件") + " (*.*);;" + tr("文本文件") + " (*.txt)")

        if not file_path:
            return False

        editor.setFilePath(file_path)
        return self.saveFile()

    def openWithEnc(self, encoding: str):
        """以指定编码打开当前文件"""
        editor = self.getEditor()
        if not editor:
            self.statusBar().showMessage(tr("没有打开的文件"), 2000)
            return

        file_path = editor.file_path
        if editor.getHandler(ViewMode.GALLERY).is_viewing_archive_image or not file_path:
            self.statusBar().showMessage(tr("文件未保存，无法以指定编码打开"), 2000)
            return

        actual_encoding = ENCODING_MAP.get(encoding, encoding.lower())

        try:
            content, total_lines, loaded_lines, truncated, _ = \
                readFileLimit(file_path, max_lines=50000, start_line=0, encoding=actual_encoding)
            editor.setContent(content)
            editor.encoding = actual_encoding
            if truncated > 0 and hasattr(editor, 'setTruncated'):
                editor.setTruncated(total_lines, loaded_lines, file_path, actual_encoding)
            elif hasattr(editor, 'clearTruncated'):
                editor.clearTruncated()
            display = encodingName(actual_encoding)
            self.encoding_label.setText(display)
            self.statusBar().showMessage(tr("已重新打开") + ": " + file_path + " - " + display, 3000)
        except Exception as e:
            messageBox(self, tr("打开失败"), tr("编码读取文件失败") + " - " + encoding + ": " + str(e), 1)

    def saveWithEnc(self, encoding: str):
        """以指定编码保存当前文件"""
        editor = self.getEditor()
        if not editor:
            self.statusBar().showMessage(tr("没有打开的文件"), 2000)
            return

        file_path = editor.file_path
        if editor.getHandler(ViewMode.GALLERY).is_viewing_archive_image or not file_path:
            self.statusBar().showMessage(tr("文件未保存，请先保存文件"), 2000)
            return

        actual_encoding = ENCODING_MAP.get(encoding, encoding.lower())

        try:
            with open(file_path, "w", encoding=actual_encoding) as f:
                    f.write(editor.text_edit.toPlainText())
            editor.encoding = actual_encoding
            display = encodingName(actual_encoding)
            self.encoding_label.setText(display)
            self.statusBar().showMessage(tr("已保存") + ": " + file_path + " - " + display, 3000)
        except Exception as e:
            messageBox(self, tr("保存失败"), tr("编码保存文件失败") + " - " + encoding + ": " + str(e), 1)

    def undo(self):
        editor = self.getEditor()
        if editor:
            editor.text_edit.undo()
    
    def redo(self):
        editor = self.getEditor()
        if editor:
            editor.text_edit.redo()
    
    def selectAll(self):
        """全选"""
        editor = self.getEditor()
        if editor:
            editor.text_edit.selectAll()
    
    def _getFindDialog(self):
        """获取或创建查找替换对话框"""
        if self.find_replace_dialog is None:
            self.find_replace_dialog = FindReplaceDialog(self, self.config)
            self.find_replace_dialog.find_requested.connect(self._onFindRequested)
            self.find_replace_dialog.replace_requested.connect(self._onReplaceRequested)
            self.find_replace_dialog.replace_all_requested.connect(self._onReplaceAll)
        return self.find_replace_dialog
    
    def _onFindRequested(self, text: str, case_sensitive: bool, regex: bool, forward: bool):
        """处理查找请求"""
        editor = self.getEditor()
        if not editor:
            return
        editor.findText(text, forward, case_sensitive, regex)
    
    def _onReplaceRequested(self, find_text: str, replace_text: str, 
                              case_sensitive: bool, regex: bool):
        """处理替换请求"""
        editor = self.getEditor()
        if not editor:
            return
        editor.replaceText(find_text, replace_text, case_sensitive, regex)
    
    def _onReplaceAll(self, find_text: str, replace_text: str,
                                  case_sensitive: bool, regex: bool):
        """处理全部替换请求"""
        editor = self.getEditor()
        if not editor:
            return
        content = editor.text_edit.toPlainText()
        if regex:
            flags = 0 if case_sensitive else re.IGNORECASE
            new_content = re.sub(find_text, replace_text, content, flags=flags)
        else:
            if case_sensitive:
                new_content = content.replace(find_text, replace_text)
            else:
                flags = re.IGNORECASE
                new_content = re.sub(find_text, replace_text, content, flags=flags)
        
        # 只有内容实际改变时才更新
        if new_content != content:
            # 保存原始内容引用和脏状态
            original_content = editor._original_content
            was_dirty = editor.is_modified
            
            # 调用 setContent，它会重置原始内容和修改状态
            editor.setContent(new_content)
            
            # 恢复原始内容引用
            editor._original_content = original_content
            
            # 判断文件是否应该标记为已修改
            if new_content == original_content:
                # 替换后内容与原始内容相同，文件恢复干净
                editor.is_modified = False
                # 如果文件之前是脏的，发出干净信号
                if was_dirty:
                    editor.file_changed.emit(False)
            else:
                # 替换后内容与原始内容不同，文件为脏
                editor.is_modified = True
                # 如果文件之前是干净的，发出修改信号
                if not was_dirty:
                    editor.file_changed.emit(True)
        # 内容未改变时不执行任何操作，避免重置修改状态
    
    def showFindRe(self):
        """显示查找对话框"""
        editor = self.getEditor()
        if editor:
            # 如果有选中的文本，设置为查找内容
            cursor = editor.text_edit.textCursor()
            if cursor.hasSelection():
                selected = cursor.selectedText()
                dialog = self._getFindDialog()
                dialog.setFindText(selected)
        dialog = self._getFindDialog()
        dialog.find_edit.setFocus()
        dialog.find_edit.selectAll()
        dialog.show()
    
    def _encodingOpen(self):
        action = self.sender()
        if action and hasattr(action, 'data'):
            encoding = action.data()
            if encoding:
                self.openWithEnc(encoding)

    def _encodingSave(self):
        action = self.sender()
        if action and hasattr(action, 'data'):
            encoding = action.data()
            if encoding:
                self.saveWithEnc(encoding)
    
    def setViewMode(self, mode: str):
        """改变查看模式"""
        editor = self.getEditor()
        if not editor:
            return

        if editor.view_mode == mode:
            if mode in (ViewMode.IMAGE, ViewMode.GALLERY, ViewMode.PDF):
                handler = editor.getHandler(mode)
                handler.deactivate(editor)
                handler.close(editor)
                handler.open(editor, editor.file_path)
                handler.activate(editor)
            else:
                ViewMode.switchMode(editor, ViewMode.TEXT)
        else:
            ViewMode.switchMode(editor, mode)

        is_special = editor._current_mode in (ViewMode.IMAGE, ViewMode.GALLERY, ViewMode.PDF)
        self.cursor_pos_label.setVisible(not is_special)
        self.encoding_label.setVisible(not is_special)

        self.statusBar().showMessage(tr("查看模式") + ": " + mode, 2000)
    
    def showSettings(self):
        """显示设置对话框"""
        dialog = SettingsDialog(self.config, self)
        dialog.settings_changed.connect(self.onSettingsChanged)
        dialog.multi_tab_changed.connect(lambda enabled: restartApplication(self))
        dialog.restart_required.connect(lambda: restartApplication(self))
        dialog.exec()
    
    def onSettingsChanged(self, settings: dict):
        """设置更改后应用"""
        self._setupAutoSave()
        self._applyEditorSettings()
        if self._main_window:
            self._main_window.applyTheme(self)
        
        if "Edit.shortcuts" in settings:
            self._menu_controller._setKeys(settings["Edit.shortcuts"])
        
        self._usage_monitor.sync()
        
        self.statusBar().showMessage(tr("设置已保存"), 2000)

    
    def _checkUnsavedFiles(self, editors: list) -> bool:
        """检查未保存的文件，返回是否取消关闭"""
        for i, editor in enumerate(editors):
            if not isinstance(editor, EditorTab):
                continue
            if editor.is_modified and self.tab_widget:
                self.tab_widget.setCurrentIndex(i)
                reply = messageBox(self, tr("保存确认"), tr("是否保存更改") + " \"" + editor.getTitle() + "\"?", 3)
                
                if reply == QMessageBox.StandardButton.Save:
                    if not self.saveFile():
                        return True
                elif reply == QMessageBox.StandardButton.Cancel:
                    return True
        return False
    
    def closeEvent(self, event: QCloseEvent):
        """窗口关闭事件"""
        editors = list(self._iterEditors())
        if self._checkUnsavedFiles(editors):
            event.ignore()
            return
        
        self.config.updateWindowGeometry(self.geometry())
        self._saveOpenFiles()
        self.config.save()
        
        event.accept()
    
    def removeEmptyLines(self):
        editor = self.getEditor()
        if not editor:
            return
        count = editor.stripEmptyLines()
        self.statusBar().showMessage(str(count) + " " + tr("行空行已去除"), 2000)

    def stripLeadingSpace(self):
        editor = self.getEditor()
        if not editor:
            return
        editor.stripLeading()
        self.statusBar().showMessage(tr("已去除行首空格"), 2000)

    def stripTrailingSpace(self):
        editor = self.getEditor()
        if not editor:
            return
        editor.stripTrailing()
        self.statusBar().showMessage(tr("已去除行尾空格"), 2000)

    def indentLines(self):
        editor = self.getEditor()
        if not editor:
            return
        editor.indentLines()
        self.statusBar().showMessage(tr("已添加行首缩进"), 2000)
    
    def _saveOpenFiles(self):
        """保存当前打开的文件列表"""
        openFiles = []
        for editor in self._iterEditors():
            file_path = editor.file_path
            if not editor.getHandler(ViewMode.GALLERY).is_viewing_archive_image and file_path:
                openFiles.append(file_path)
        
        self.config.set("Edit.open", openFiles)
    
    def _loadOpenFiles(self):
        """加载上次未关闭的文件"""
        openFiles = self.config.get("Edit.open", [])
        
        if not openFiles:
            return
        
        # 单标签页模式下只加载第一个文件
        if not self._use_tabs:
            # 只加载第一个文件
            loaded_file = None
            for file_path in openFiles:
                if Path(file_path).exists():
                    self.openFilePath(file_path)
                    loaded_file = file_path
                    break
            
            # 如果加载了文件且openFiles中有多个文件，更新配置只保留加载的文件
            if loaded_file and len(openFiles) > 1:
                self.config.set("Edit.open", [loaded_file])
                self.config.save()
        else:
            for file_path in openFiles:
                if Path(file_path).exists():
                    self.openFilePath(file_path)
    
class TocPanel:
    """标题面板管理类"""
    
    def __init__(self, main_window, splitter):
        self.main_window = main_window
        self.splitter = splitter
        self.panel = None
        self.list_widget = None
        self._headings = []
        
    def create(self) -> QWidget:
        """创建标题面板"""
        self.panel = QWidget()
        self.panel.setFixedWidth(250)
        self.panel.setObjectName("toc_panel")
        
        layout = QVBoxLayout(self.panel)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(0)
        
        self.list_widget = QListWidget()
        self.list_widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list_widget.setObjectName("toc_list")
        self.list_widget.itemClicked.connect(self._onItemClicked)
        layout.addWidget(self.list_widget)
        
        return self.panel
    
    def updateToc(self, content: str):
        """更新标题内容"""
        self.list_widget.clear()
        self._headings = extractToc(content)
        
        if not self._headings:
            self.panel.hide()
            return
        
        self.panel.show()
        
        font_sizes = {1: 14, 2: 13, 3: 12, 4: 11, 5: 10, 6: 9}
        
        for heading in self._headings:
            item = QListWidgetItem(heading['text'])
            item.setData(Qt.ItemDataRole.UserRole, heading)
            font = item.font()
            font.setPointSize(font_sizes.get(heading['level'], 10))
            item.setFont(font)
            self.list_widget.addItem(item)
    
    def _onItemClicked(self, item):
        """标题项点击事件"""
        heading = item.data(Qt.ItemDataRole.UserRole)
        if not heading:
            return
        
        editor = self.main_window.getEditor()
        if not editor or not hasattr(editor, 'text_edit') or not editor.text_edit:
            return
        
        char_pos = heading.get('char_pos', 0)
        if char_pos < 0:
            return
        
        try:
            cursor = editor.text_edit.textCursor()
            doc = editor.text_edit.document()
            if doc:
                safe_pos = min(char_pos, max(0, doc.characterCount() - 1))
                cursor.setPosition(safe_pos)
                cursor.movePosition(QTextCursor.MoveOperation.StartOfLine)
                editor.text_edit.setTextCursor(cursor)
                layout = editor.text_edit.document().documentLayout()
                block_rect = layout.blockBoundingRect(cursor.block())
                vsb = editor.text_edit.verticalScrollBar()
                vsb.setValue(int(block_rect.top()))
        except Exception:
            logger.exception("恢复光标位置失败")
    
    def hidePanel(self):
        """隐藏标题面板"""
        if self.panel:
            self.panel.hide()
    
    def showPanel(self):
        """显示标题面板"""
        if self.panel and self._headings:
            self.panel.show()
