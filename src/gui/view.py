import os
import sys
import shutil
import subprocess
from functools import partial

from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QMenu, QFileSystemModel, QTreeView
from PySide6.QtGui import QAction
from PySide6.QtCore import Qt, QDir, QModelIndex

from src.util import logger, openTerminal, EXTENSION, showFile, messageBox, tr, formatFileSize
from src.file import ArchiveItemModel


# class ViewMode():
#     考虑编辑器上方增加模式按钮
#     TEXT = 0
#     Markdown = 1
#     HEX = 2
#     IMAGE = 3
#     GALLERY = 4
#     PDF = 5

# def setViewMode(self, mode: ViewMode):
#     self.text_edit.setVisible(mode in (ViewMode.TEXT, ViewMode.HEX))
#     self.image_scroll.setVisible(mode == ViewMode.IMAGE)
#     self._gallery_widget.setVisible(mode == ViewMode.GALLERY)
#     self._pagination_bar.setVisible(mode == ViewMode.TEXT and self._is_truncated)
#     self.is_image = (mode == ViewMode.IMAGE)


class FileSystemModel(QFileSystemModel):
    """文件系统模型 - 只暴露名称和大小列，大小显示为 KB/MB"""
    def columnCount(self, parent=QModelIndex()):
        return 2

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and index.column() == 1:
            path = self.filePath(index)
            if os.path.isfile(path):
                return formatFileSize(os.path.getsize(path))
            return ""
        return super().data(index, role)


class FolderPanelManager:
    """文件夹面板管理器 - 管理文件夹树视图面板"""
    
    def __init__(self, parent, file_op, splitter, config, placeholder, main_window):
        self.parent = parent
        self.file_op = file_op
        self.splitter = splitter
        self.config = config
        self.placeholder = placeholder
        self.main_window = main_window
        
        self.panel = None
        self.model = None
        self.tree = None
        self.header = None
        self.folder_path = None
        self.current_archive_path = None
        self.archive_model = None
        self._folder_panel_width = 300
    
    def _init_archive_model(self):
        """初始化压缩包模型"""
        self.archive_model = ArchiveItemModel(self.file_op)
    
    def create(self) -> QWidget:
        """创建文件夹面板"""
        
        self.panel = QWidget()
        layout = QVBoxLayout(self.panel)
        layout.setContentsMargins(0, 0, 0, 0)
        
        self.header = QLabel("..")
        self.header.setObjectName("folder_header")
        self.header.setToolTip(tr("点击返回上级文件夹"))
        self.header.setCursor(Qt.CursorShape.PointingHandCursor)
        self.header.mousePressEvent = self._on_header_clicked
        self.header.hide()
        layout.addWidget(self.header)
        
        self.model = FileSystemModel()
        self.model.setRootPath('')
        self.model.setNameFilters(['*'])
        self.model.setNameFilterDisables(False)
        self.model.setFilter(QDir.Filter.AllDirs | QDir.Filter.Files | QDir.Filter.NoDotAndDotDot)
        
        self.tree = QTreeView()
        self.tree.setModel(self.model)
        self.tree.setHeaderHidden(True)
        self.tree.setAnimated(False)
        self.tree.setAcceptDrops(True)
        self.tree.setDragDropMode(QTreeView.DragDropMode.DragDrop)
        self.tree.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.tree.setItemsExpandable(True)
        self.tree.setSelectionMode(QTreeView.SelectionMode.ExtendedSelection)
        
        self.tree.doubleClicked.connect(self._on_tree_double_clicked)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_tree_context_menu)
        self.tree.dragEnterEvent = self._tree_drag_enter
        self.tree.dragMoveEvent = self._tree_drag_move
        self.tree.dropEvent = self._tree_drop
        
        layout.addWidget(self.tree)
        
        return self.panel
    
    def _on_tree_double_clicked(self, index):
        """文件夹树双击事件"""
        if self.current_archive_path:
            self._handle_archive_item_double_clicked(index)
            return
        
        file_path = self.model.filePath(index)
        
        if not file_path:
            return
        
        if os.path.isdir(file_path):
            self.load(file_path)
        elif self.file_op.is_tar_file(file_path) or self.file_op.is_zip_file(file_path):
            self.load(file_path)
        else:
            try:
                self.parent.open_file_path(file_path)
            except Exception as e:
                messageBox(self.parent, tr("打开失败"), tr("无法打开文件") + f": {e}", 1)
    
    def _tree_drag_enter(self, event):
        if self.current_archive_path:
            event.ignore()
            return
        QTreeView.dragEnterEvent(self.tree, event)

    def _tree_drag_move(self, event):
        if self.current_archive_path:
            event.ignore()
            return
        QTreeView.dragMoveEvent(self.tree, event)

    def _tree_drop(self, event):
        if self.current_archive_path:
            event.ignore()
            return
        event.ignore()

    def _handle_archive_item_double_clicked(self, index):
        """压缩包内文件双击事件"""
        item = index.internalPointer()
        if not item:
            return
        
        if item.is_dir:
            if item.child_count() > 0:
                if self.tree.isExpanded(index):
                    self.tree.collapse(index)
                else:
                    self.tree.expand(index)
        else:
            name_lower = item.full_path.lower()
            is_image = any(name_lower.endswith(ext) for ext in EXTENSION["IMAGE"])
            
            content = self.file_op.read_archive_file(self.current_archive_path, item.full_path)
            if content is None:
                messageBox(self.parent, tr("错误"), tr("无法读取压缩包内文件"), 1)
                return
            
            archive_name = os.path.basename(self.current_archive_path)
            display_path = f"{archive_name}/{item.full_path}"
            
            if is_image:
                if self.main_window._use_tabs:
                    editor = self.main_window.add_new_tab(display_path, "")
                    editor._archive_type = self.file_op.is_zip_file(self.current_archive_path) and 'zip' or 'tar'
                    editor.file_path = self.current_archive_path
                    editor._load_single_archive_image(item.full_path)
                else:
                    editor = self.main_window.single_editor
                    editor._archive_type = self.file_op.is_zip_file(self.current_archive_path) and 'zip' or 'tar'
                    editor.file_path = self.current_archive_path
                    editor._load_single_archive_image(item.full_path)
            else:
                try:
                    content_str = content.decode('utf-8', errors='replace')
                except Exception:
                    content_str = str(content)
                
                if self.main_window._use_tabs:
                    editor = self.main_window.add_new_tab(display_path, content_str)
                else:
                    editor = self.main_window.single_editor
                    editor.set_content(content_str)
                    editor.set_file_path(display_path)
            
            self.main_window.config.add_recent_file(self.current_archive_path)
            self.main_window.statusBar().showMessage(tr("已打开") + f": {display_path}", 3000)
    
    def _show_tree_context_menu(self, pos):
        """显示文件夹树右键菜单"""
        menu = QMenu(self.parent)
        
        close_action = QAction(tr("关闭文件夹视图"), self.parent)
        close_action.triggered.connect(self.close)
        menu.addAction(close_action)
        
        index = self.tree.indexAt(pos)
        if index.isValid():
            item_path = self.model.filePath(index)
            if isinstance(item_path, str) and item_path:
                open_terminal_action = QAction(tr("在终端中打开"), self.parent)
                open_terminal_action.triggered.connect(partial(self.open_terminal, item_path))
                menu.addAction(open_terminal_action)

                show_in_explorer_action = QAction(tr("在文件资源管理器中显示"), self.parent)
                show_in_explorer_action.triggered.connect(lambda checked=False, fp=item_path: showFile(fp, self.parent))
                menu.addAction(show_in_explorer_action)
                
                menu.addSeparator()
                
                item_path_norm = os.path.normpath(item_path)
                if self.main_window.config.is_favorite(item_path_norm):
                    remove_fav_action = QAction(tr("从收藏夹移除"), self.parent)
                    remove_fav_action.triggered.connect(lambda checked, fp=item_path_norm: self.main_window.remove_from_favorites(fp))
                    menu.addAction(remove_fav_action)
                else:
                    add_fav_action = QAction(tr("添加到收藏夹"), self.parent)
                    add_fav_action.triggered.connect(lambda checked, fp=item_path_norm: self.main_window.add_to_favorites(fp))
                    menu.addAction(add_fav_action)
                
                if os.path.isfile(item_path):
                    move_to_trash_action = QAction(tr("移动到回收站"), self.parent)
                    move_to_trash_action.triggered.connect(lambda checked, fp=item_path: self.move_to_trash(fp))
                    menu.addAction(move_to_trash_action)
        elif self.folder_path is not False and self.folder_path and isinstance(self.folder_path, str):
            open_terminal_action = QAction(tr("在终端中打开"), self.parent)
            open_terminal_action.triggered.connect(partial(self.open_terminal, self.folder_path))
            menu.addAction(open_terminal_action)

            show_in_explorer_action = QAction(tr("在文件资源管理器中显示"), self.parent)
            show_in_explorer_action.triggered.connect(lambda: showFile(self.folder_path, self.parent))
            menu.addAction(show_in_explorer_action)
            
            menu.addSeparator()
            
            folder_path_norm = os.path.normpath(self.folder_path)
            if self.main_window.config.is_favorite(folder_path_norm):
                remove_fav_action = QAction(tr("从收藏夹移除"), self.parent)
                remove_fav_action.triggered.connect(lambda checked, fp=folder_path_norm: self.main_window.remove_from_favorites(fp))
                menu.addAction(remove_fav_action)
            else:
                add_fav_action = QAction(tr("添加到收藏夹"), self.parent)
                add_fav_action.triggered.connect(lambda checked, fp=folder_path_norm: self.main_window.add_to_favorites(fp))
                menu.addAction(add_fav_action)

        menu.exec_(self.tree.mapToGlobal(pos))
    
    def move_to_trash(self, item_path: str):
        """移动文件到回收站"""
        try:
            if sys.platform == "win32":
                item_path = os.path.abspath(item_path)
                result = subprocess.run(
                    ['powershell', '-Command',
                     f'Add-Type -AssemblyName Microsoft.VisualBasic; [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile("{item_path}", "OnlyErrorDialogs", "SendToRecycleBin")'],
                    capture_output=True, text=True
                )
                if result.returncode == 0:
                    logger.info(f"成功移动到回收站: {item_path}")
                    self.parent.statusBar().showMessage(tr("已移动到回收站") + f": {item_path}", 2000)
                    self._on_file_deleted(item_path)
                else:
                    logger.error(f"移动到回收站失败: {item_path}, 错误: {result.stderr}")
                    messageBox(self.parent, tr("错误"), tr("移动到回收站失败") + f": {result.stderr}", 1)
            else:
                trash_path = os.path.expanduser("~/.local/share/Trash/files/")
                os.makedirs(trash_path, exist_ok=True)
                if os.path.isfile(item_path):
                    shutil.move(item_path, trash_path)
                else:
                    shutil.move(item_path, trash_path)
                logger.info(f"成功移动到回收站: {item_path}")
                self.parent.statusBar().showMessage(tr("已移动到回收站") + f": {item_path}", 2000)
                self._on_file_deleted(item_path)
        except Exception as e:
            logger.error(f"移动到回收站异常: {item_path}, 错误: {e}")
            messageBox(self.parent, tr("错误"), tr("移动到回收站失败") + f": {e}", 1)
    
    def _on_file_deleted(self, deleted_path: str):
        """文件删除后刷新视图"""
        self.refresh_after_delete(deleted_path)
    
    def open_terminal(self, path):
        """在终端中打开"""
        try:
            if not isinstance(path, str) or not path:
                logger.warning(f"无效的路径: type={type(path).__name__}, value={path!r}")
                return
            if openTerminal(path):
                self.parent.statusBar().showMessage(tr("已打开命令行") + f": {os.path.normpath(path)}", 2000)
        except Exception:
            logger.exception("打开命令行失败")
            self.parent.statusBar().showMessage(tr("打开命令行失败"), 2000)
    
    def _on_header_clicked(self, event):
        """点击父文件夹标签时切换到上级目录"""
        if self.current_archive_path:
            parent_path = os.path.dirname(self.current_archive_path)
            if parent_path and parent_path != self.current_archive_path:
                self.load(parent_path)
        elif self.folder_path:
            parent_path = os.path.dirname(self.folder_path)
            if parent_path and parent_path != self.folder_path:
                self.load(parent_path)
    
    def ensure_created(self):
        """确保面板已创建"""
        if self.panel is not None:
            return
        
        self.create()
        
        if self.placeholder is not None:
            index = self.splitter.indexOf(self.placeholder)
            if index >= 0:
                self.splitter.replaceWidget(index, self.panel)
            self.placeholder.deleteLater()
            self.placeholder = None
        else:
            index = self.splitter.count() - 1
            if index >= 0:
                self.splitter.insertWidget(index, self.panel)
        
        self.splitter.handle(0).setEnabled(True)
        self.panel.setMinimumWidth(250)
        self.panel.show()

        if self.main_window:
            self.set_sizes(self.main_window.width())
    
    def load(self, folder_path: str):
        """加载文件夹"""
        self.ensure_created()
        
        if self.file_op.is_tar_file(folder_path) or self.file_op.is_zip_file(folder_path):
            self._load_archive(folder_path)
        else:
            self.current_archive_path = None
            self.folder_path = folder_path
            
            self.model.setRootPath(folder_path)
            root_index = self.model.index(folder_path)
            if self.tree.model() is not self.model:
                self.tree.setModel(self.model)
                self.tree.setHeaderHidden(True)
            self.tree.setRootIndex(root_index)
            
            parent_path = os.path.dirname(folder_path)
            if parent_path and parent_path != folder_path:
                self.header.setText(f".. {os.path.basename(folder_path)}")
                self.header.setToolTip(folder_path)
                self.header.show()
            else:
                self.header.hide()
            
            self.config.set("Edit.folder", folder_path)
            self.config.save()
            
            self.parent.statusBar().showMessage(tr("已加载文件夹") + f": {os.path.abspath(folder_path)}", 3000)
        
        if self.main_window:
            self.set_sizes(self.main_window.width())
    
    def _load_archive(self, archive_path: str):
        """加载压缩包内容"""
        if self.archive_model is None:
            self._init_archive_model()
        
        self.current_archive_path = archive_path
        self.archive_model.load_archive(archive_path)
        
        self.tree.setModel(self.archive_model)
        
        parent_path = os.path.dirname(archive_path)
        if parent_path:
            self.header.setText(f".. {os.path.basename(archive_path)}")
            self.header.setToolTip(archive_path)
            self.header.show()
        else:
            self.header.hide()
        
        self.parent.statusBar().showMessage(tr("已加载压缩包") + f": {os.path.abspath(archive_path)}", 3000)
    
    def set_sizes(self, available_width: int):
        """设置面板尺寸"""
        if not self.panel:
            return
        
        self.splitter.handle(0).setEnabled(True)
        
        if available_width <= 0:
            return
        
        self.tree.setColumnWidth(0, 220)
        self.tree.setColumnWidth(1, 70)
        folder_width = 320
        
        editor_width = available_width - folder_width
        if editor_width < 400:
            editor_width = 400
            folder_width = available_width - editor_width
        
        self.splitter.setSizes([folder_width, editor_width])
    
    def close(self):
        """关闭并删除文件夹视图"""
        self.config.set("Edit.folder", "")
        self.config.save()
        
        if self.panel is not None:
            panel_index = self.splitter.indexOf(self.panel)
            if panel_index < 0:
                panel_index = 1
            
            self.panel.setParent(None)
            self.panel.deleteLater()
            self.panel = None
            self.model = None
            self.tree = None
            self.header = None
            self.folder_path = None
            self.current_archive_path = None
            
            if self.placeholder is None:
                placeholder = QWidget()
                self.splitter.insertWidget(panel_index, placeholder)
                self.placeholder = placeholder
            self.placeholder.setFixedWidth(0)
            self.placeholder.hide()
            
            if self.splitter.count() > 1:
                self.splitter.handle(0).setEnabled(False)
    
    def is_visible(self) -> bool:
        return self.panel is not None
    
    def refresh_after_delete(self, deleted_path: str):
        """删除文件后刷新"""
        if self.panel is not None and self.folder_path:
            self.load(self.folder_path)
