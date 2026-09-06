import os
import re
import time
import shutil
import fnmatch
from pathlib import Path
from functools import partial
from typing import Tuple, Optional

from PySide6.QtWidgets import QWidget, QDialog, QTextEdit, QVBoxLayout, QLabel, QMenu, QFileSystemModel, QTreeView, QProgressBar, QDialogButtonBox
from PySide6.QtCore import Qt, QModelIndex, QDir, QAbstractItemModel
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QAction

from src.api import logger, getTimestamp, EXTENSION, ENCODING_MAP, dialogBox, messageBox, tr, openTerminal, showFile, formatFileSize, backup_dir, fileType
from src.gui.view import ViewMode, listArchive, readArchive


SUPPORTED_ENCODINGS = list(ENCODING_MAP.values())

# 排除规则的默认模式，
EXCLUDE_PATTERNS = ["*.pyc", "*/__pycache__/", "*/.git/"]

# QToolTip 的排除规则提示，file.py 是惰性加载，如果之后修改，可能导致翻译失效
EXCLUDE_TIPS = [
    tr("每行一项，支持通配符"),
    tr("排除单个") + " /file.txt",
    tr("排除所有") + " */file.txt",
    tr("排除根目录下的") + " /folder/",
    tr("排除所有") + " *.pyc",
    tr("排除所有") + " */.git/",
]

def readEncoding(file_path: str, encoding: str = "utf-8", auto_detect: bool = True) -> Tuple[str, str]:
    """读取文件并自动检测编码
    Returns: (content, actual_encoding)
    """
    if not file_path:
        raise FileNotFoundError("文件路径为空")
    
    _path = Path(file_path)
    
    if not _path.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")
    
    try:
        with open(_path, "r", encoding=encoding, newline="") as f:
            content = f.read()
        return content, encoding
    except UnicodeDecodeError:
        if not auto_detect:
            raise ValueError(f"无法以 {encoding} 编码读取文件: {file_path}")
    
    for enc in SUPPORTED_ENCODINGS:
        try:
            with open(_path, "r", encoding=enc, newline="") as f:
                content = f.read()
            return content, enc
        except UnicodeDecodeError:
            continue
    
    try:
        with open(_path, "r", encoding="utf-8", errors="replace", newline="") as f:
            content = f.read()
        return content, "utf-8"
    except Exception:
        raise ValueError(f"无法使用支持的编码读取文件: {file_path}")


def createBackup(file_path: str, config=None) -> Optional[str]:
    """创建文件备份，返回备份路径；config 中 history_backup 为 False 时跳过"""
    if config is not None and not config.get("Edit.backup", True):
        return None
        
    file_path = Path(file_path)
    if not file_path.exists():
        return None
    
    backup_dir.mkdir(parents=True, exist_ok=True)
    
    backup_name = f"{file_path.stem}_{getTimestamp()}{file_path.suffix}"
    backup_path = backup_dir / backup_name
    
    try:
        shutil.copyfile(file_path, backup_path)
        logger.info(f"文件备份成功: {backup_path}")
        
        _cleanBackups()
        
        return str(backup_path)
    except Exception:
        logger.exception("备份创建失败")
        return None


def _cleanBackups() -> None:
    """清理超过指定天数的旧备份文件"""
    try:
        if not backup_dir.exists():
            return
        
        current_time = time.time()
        cutoff_time = current_time - (7 * 86400)
        
        for backup_file in backup_dir.iterdir():
            if backup_file.is_file():
                file_mtime = backup_file.stat().st_mtime
                if file_mtime < cutoff_time:
                    backup_file.unlink()
                    logger.info(f"已删除过期备份: {backup_file.name}")
    except Exception:
        logger.exception("清理旧备份失败")


class FileSelect(QDialog):
    """文件选择对话框，支持拖放和排除规则"""

    def __init__(self, parent=None, scanner=None):
        super().__init__(parent)
        self._scanner = scanner
        self._worker = None
        self._scanning = False
        self._result = None
        self.setWindowTitle(tr("选择文件夹"))
        self.resize(500, 400)
        self.setAcceptDrops(True)

        self.label_path = QLabel(tr("文件夹路径（每行一个，支持拖放）"))
        self.path_edit = QTextEdit()
        self.path_edit.setStyleSheet("background: #eeeeee")
        self.label_exclude = QLabel(tr("排除规则"))
        self.exclude_edit = QTextEdit()
        self.exclude_edit.setStyleSheet("background: #eeeeee")
        self.exclude_edit.setToolTip("\n".join(EXCLUDE_TIPS))
        self.exclude_edit.setPlainText('\n'.join(EXCLUDE_PATTERNS))
        self.path_edit.setAcceptDrops(False)
        self.exclude_edit.setAcceptDrops(False)

        self.scan_status = QLabel()
        self.scan_status.hide()
        self.progress_bar = QProgressBar()
        self.progress_bar.hide()

        main_layout = QVBoxLayout()
        self.setLayout(main_layout)
        main_layout.addWidget(self.label_path)
        main_layout.addWidget(self.path_edit)
        main_layout.addWidget(self.label_exclude)
        main_layout.addWidget(self.exclude_edit)
        main_layout.addWidget(self.scan_status)
        main_layout.addWidget(self.progress_bar)
        self._box = dialogBox(main_layout, self, show=False)

    def dragEnterEvent(self, event: QDragEnterEvent):
        """拖拽进入事件"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent):
        """拖拽放下事件"""
        urls = event.mimeData().urls()
        if urls:
            current_text = self.path_edit.toPlainText()
            new_paths = []
            for url in urls:
                if url.isLocalFile():
                    new_paths.append(os.path.normpath(url.toLocalFile()))

            if new_paths:
                if current_text:
                    self.path_edit.setPlainText(current_text + '\n' + '\n'.join(new_paths))
                else:
                    self.path_edit.setPlainText('\n'.join(new_paths))

        event.acceptProposedAction()

    def accept(self):
        """确定：校验路径后启动扫描，扫描期间再次点击则取消"""
        if self._scanning:
            self._cancelScan()
            return
        paths = self.getPaths()
        rules = self.getExcludes()
        if not paths:
            return
        if self._scanner is None:
            super().accept()
            return
        self._startScan(paths, rules)

    def _startScan(self, paths: list, rules: list):
        """创建工作线程并开始扫描，扫描期间禁用输入"""
        self._scanning = True
        self._result = None
        self.path_edit.setEnabled(False)
        self.exclude_edit.setEnabled(False)
        self.progress_bar.setRange(0, 0)
        self.scan_status.setText(tr("正在扫描文件"))
        self.progress_bar.show()
        self.scan_status.show()
        self._box.button(QDialogButtonBox.StandardButton.Ok).setText(tr("取消"))
        self._box.button(QDialogButtonBox.StandardButton.Cancel).setEnabled(False)
        self._worker = self._scanner(paths, rules, self._onProgress, self._onScanDone, self._onScanError)
        self._worker.start()

    def _onProgress(self, cur: int, total: int):
        """扫描进度回调，total 为 0 时表示收集文件阶段，进度条转圈"""
        if total > 0:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(cur)
            self.scan_status.setText(f"{tr('已扫描')} {cur}/{total}")
        else:
            self.progress_bar.setRange(0, 0)
            self.scan_status.setText(tr("正在扫描文件"))

    def _onScanDone(self, duplicates):
        if not self._scanning:
            return
        self._scanning = False
        self._worker = None
        self._result = duplicates
        self._resetUI()
        super().accept()

    def _onScanError(self, error: str):
        if not self._scanning:
            return
        self._scanning = False
        self._worker = None
        self._resetUI()
        messageBox(self, tr("错误"), tr("扫描失败") + ": " + error, 1)

    def _cancelScan(self):
        if self._worker:
            self._worker.cancel()
            if self._worker.isRunning():
                self._worker.wait()
        self._worker = None
        self._scanning = False
        self.reject()

    def _resetUI(self):
        self.path_edit.setEnabled(True)
        self.exclude_edit.setEnabled(True)
        self.progress_bar.hide()
        self.scan_status.hide()
        self._box.button(QDialogButtonBox.StandardButton.Ok).setText(tr("确定"))
        self._box.button(QDialogButtonBox.StandardButton.Cancel).setEnabled(True)

    def closeEvent(self, event):
        if self._scanning and self._worker:
            self._worker.cancel()
            if self._worker.isRunning():
                self._worker.wait()
            self._worker = None
            self._scanning = False
        super().closeEvent(event)

    def getPaths(self) -> list[str]:
        """获取用户输入的路径列表"""
        text = self.path_edit.toPlainText()
        lines = text.splitlines()
        return [line.strip() for line in lines if line.strip()]

    def getExcludes(self) -> list[str]:
        """获取用户输入的排除规则列表"""
        text = self.exclude_edit.toPlainText()
        lines = text.splitlines()
        return [line.strip() for line in lines if line.strip()]

    @staticmethod
    def select(parent=None, default_paths: list[str] = None, default_exclude_rules: list[str] = None, scanner=None) -> tuple:
        """显示文件选择对话框并返回结果
        scanner 提供时为扫描回调（对话框内扫描），返回 (duplicates, paths, rules)；
        否则仅选择，返回 (files, paths, rules)"""
        dialog = FileSelect(parent, scanner=scanner)
        if default_paths:
            dialog.path_edit.setPlainText('\n'.join(default_paths))
        if default_exclude_rules:
            dialog.exclude_edit.setPlainText('\n'.join(default_exclude_rules))
        result = dialog.exec()
        if result == QDialog.DialogCode.Accepted:
            paths = dialog.getPaths()
            rules = dialog.getExcludes()
            if scanner:
                return dialog._result, paths, rules
            files = collectFiles(paths, rules)
            return files, paths, rules
        return None


def _compileSingleRule(rule: str) -> tuple[re.Pattern, bool, bool]:
    """将单条规则转换为预编译的正则表达式"""
    rule = rule.strip()
    if not rule:
        return None

    normalized = rule.replace('\\', '/')
    is_dir = normalized.endswith('/')
    root_only = normalized.startswith('/')
    pattern = normalized.strip('/')

    if not pattern:
        return None

    fnmatch_pattern = fnmatch.translate(pattern)
    fnmatch_pattern = fnmatch_pattern.replace("(?s:", "").rstrip(")\\Z")

    if is_dir:
        # 以 */ 开头的目录规则（如 */__pycache__/）把前缀改为可选，使根目录的同名目录也能匹配。
        # 若不处理，编译出的 .*/ 会强制要求至少一层目录前缀，根目录永远匹配不到。
        if not root_only and pattern.startswith('*/') and fnmatch_pattern.startswith('.*/'):
            fnmatch_pattern = "(?:.*/)?" + fnmatch_pattern[len('.*/'):]
        fnmatch_pattern = fnmatch_pattern.rstrip('$') + '/?$'

    try:
        return (re.compile(fnmatch_pattern, re.IGNORECASE), is_dir, root_only)
    except re.error:
        return None


def compileRules(rules: list[str]) -> list[tuple[re.Pattern, bool, bool]]:
    """将排除规则列表预编译为正则表达式列表"""
    compiled = []
    for rule in rules:
        pattern = _compileSingleRule(rule)
        if pattern is not None:
            compiled.append(pattern)
    return compiled


def _matchRelPath(rel_path: str, pattern: re.Pattern, is_dir: bool, root_only: bool = False) -> bool:
    """匹配相对路径与预编译规则"""
    if is_dir and not rel_path.endswith('/'):
        rel_path += '/'
        return pattern.fullmatch(rel_path) is not None
    else:
        if pattern.fullmatch(rel_path) is not None:
            return True
        if root_only:
            return False
        file_name = rel_path.split('/')[-1]
        return pattern.fullmatch(file_name) is not None


def isExcluded(file_path: str, base_path: str, exclude_rules: list[tuple[re.Pattern, bool, bool]]) -> bool:
    """判断文件或目录是否应该被排除"""
    file_path = os.path.normpath(file_path)
    base_path = os.path.normpath(base_path)

    rel_path = os.path.relpath(file_path, base_path)
    rel_path_normalized = rel_path.replace(os.sep, '/')

    if rel_path_normalized == '.':
        return False

    is_dir = os.path.isdir(file_path)

    for pattern, rule_is_dir, root_only in exclude_rules:
        if rule_is_dir and not is_dir:
            continue

        if _matchRelPath(rel_path_normalized, pattern, rule_is_dir, root_only):
            return True

    return False


def filterFiles(base_path: str, exclude_rules_raw: list[str], abort_check=None) -> list[str]:
    """过滤目录下符合规则的文件，abort_check 返回 True 时提前终止遍历"""
    if not os.path.isdir(base_path):
        return []

    result = []
    base_path = os.path.normpath(base_path)

    exclude_rules = compileRules(exclude_rules_raw)

    for root, dirs, files in os.walk(base_path, onerror=lambda _: None):  # 静默跳过不可访问的目录
        if abort_check and abort_check():
            break
        dirs_to_remove = []
        for d in dirs:
            dir_path = os.path.join(root, d)
            if isExcluded(dir_path, base_path, exclude_rules):
                dirs_to_remove.append(d)

        for d in dirs_to_remove:
            dirs.remove(d)

        for f in files:
            file_path = os.path.join(root, f)
            if not isExcluded(file_path, base_path, exclude_rules):
                result.append(file_path)

    return result


def collectFiles(paths: list[str], rules: list[str], abort_check=None) -> list[str]:
    """从多个路径收集文件，abort_check 返回 True 时提前终止"""
    all_files = []
    for path in paths:
        if os.path.isdir(path):
            all_files.extend(filterFiles(path, rules, abort_check))
            if abort_check and abort_check():
                break
    return all_files


class ArchiveFileItem:
    """压缩包内的文件或目录项"""
    def __init__(self, name: str, is_dir: bool, size: int = 0, parent_path: str = "", archive_path: str = ""):
        self.name = name
        self.is_dir = is_dir
        self.size = size
        self.parent_path = parent_path
        self.archive_path = archive_path
        self.full_path = parent_path + name if parent_path else name
        self.children = []
        self.parent = None
    
    def appendChild(self, child):
        child.parent = self
        self.children.append(child)
    
    def childCount(self):
        return len(self.children)
    
    def child(self, row: int):
        return self.children[row] if 0 <= row < len(self.children) else None
    
    def row(self):
        return self.parent.children.index(self) if self.parent else 0


class ArchiveItemModel(QAbstractItemModel):
    """支持压缩包内容的自定义模型"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.root_item = ArchiveFileItem("", True)
        self.archive_path = ""
    
    def loadArchive(self, archive_path: str):
        """加载压缩包内容"""
        self.archive_path = archive_path
        self.beginResetModel()
        self.root_item = ArchiveFileItem("", True)
        
        items = listArchive(archive_path)
        if not items:
            self.endResetModel()
            return
        
        name_to_item = {}
        
        for item in items:
            name = item["name"]
            is_dir = item["is_dir"]
            size = item["size"]
            
            if name.endswith("/") and not is_dir:
                is_dir = True
            
            parts = name.rstrip("/").split("/")
            
            if len(parts) == 1:
                new_item = ArchiveFileItem(parts[0], is_dir, size, "", archive_path)
                self.root_item.appendChild(new_item)
                name_to_item[name.rstrip("/")] = new_item
            else:
                parent_name = "/".join(parts[:-1])
                parent_item = name_to_item.get(parent_name)
                if parent_item is None:
                    parent_item = self.root_item
                new_item = ArchiveFileItem(parts[-1], is_dir, size, parent_name + "/" if parent_name else "", archive_path)
                parent_item.appendChild(new_item)
                name_to_item[name.rstrip("/")] = new_item
        
        self.endResetModel()
    
    def columnCount(self, parent=QModelIndex()):
        return 1
    
    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        
        item = index.internalPointer()
        if role == Qt.ItemDataRole.DisplayRole:
            name = item.name
            if item.is_dir and not name.endswith("/"):
                name += "/"
            return name
        elif role == Qt.ItemDataRole.UserRole:
            return item
        return None
    
    def index(self, row: int, column: int, parent=QModelIndex()):
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        
        if not parent.isValid():
            parent_item = self.root_item
        else:
            parent_item = parent.internalPointer()
        
        child = parent_item.children[row] if row < len(parent_item.children) else None
        if child:
            return self.createIndex(row, column, child)
        return QModelIndex()
    
    def parent(self, index):
        if not index.isValid():
            return QModelIndex()
        
        item = index.internalPointer()
        if item.parent:
            if item.parent == self.root_item:
                return QModelIndex()
            return self.createIndex(item.parent.row(), 0, item.parent)
        return QModelIndex()
    
    def rowCount(self, parent=QModelIndex()):
        if not parent.isValid():
            return len(self.root_item.children)
        
        parent_item = parent.internalPointer()
        return parent_item.childCount() if parent_item else 0


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

    def __init__(self, parent, splitter, config, placeholder, main_window):
        self.parent = parent
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

    def create(self) -> QWidget:
        """创建文件夹面板"""

        self.panel = QWidget()
        layout = QVBoxLayout(self.panel)
        layout.setContentsMargins(0, 0, 0, 0)

        self.header = QLabel("..")
        self.header.setObjectName("folder_header")
        self.header.setToolTip(tr("点击返回上级文件夹"))
        self.header.setCursor(Qt.CursorShape.PointingHandCursor)
        self.header.mousePressEvent = self._onHeaderClicked
        self.header.hide()
        layout.addWidget(self.header)

        self.model = FileSystemModel()
        self.model.setRootPath("")
        self.model.setNameFilters(['*'])
        self.model.setNameFilterDisables(False)
        self.model.setFilter(QDir.Filter.AllDirs | QDir.Filter.Files | QDir.Filter.NoDotAndDotDot)

        self.tree = QTreeView()
        self.tree.setModel(self.model)
        self.tree.setHeaderHidden(True)
        self.tree.setAnimated(False)
        self.tree.setItemsExpandable(True)
        self.tree.setDragEnabled(True)
        self.tree.setSelectionMode(QTreeView.SelectionMode.ExtendedSelection)

        self.tree.doubleClicked.connect(self._onTreeDblClick)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._showTreeMenu)

        layout.addWidget(self.tree)

        return self.panel

    def _onTreeDblClick(self, index):
        """文件夹树双击事件"""
        if self.current_archive_path:
            self._dblClickArc(index)
            return

        file_path = self.model.filePath(index)

        if not file_path:
            return

        if os.path.isdir(file_path):
            self.load(file_path)
        elif fileType(file_path, "TAR") or fileType(file_path, "ZIP"):
            self.load(file_path)
        else:
            try:
                self.parent.openFilePath(file_path)
            except Exception as e:
                messageBox(self.parent, tr("打开失败"), tr("无法打开文件") + f": {e}", 1)

    def _dblClickArc(self, index):
        """压缩包内文件双击事件"""
        item = index.internalPointer()
        if not item:
            return

        if item.is_dir:
            if item.childCount() > 0:
                if self.tree.isExpanded(index):
                    self.tree.collapse(index)
                else:
                    self.tree.expand(index)
        else:
            name_lower = item.full_path.lower()
            is_image = any(name_lower.endswith(ext) for ext in EXTENSION["IMAGE"])

            content = readArchive(self.current_archive_path, item.full_path)
            if content is None:
                messageBox(self.parent, tr("错误"), tr("无法读取压缩包内文件"), 1)
                return

            archive_name = os.path.basename(self.current_archive_path)
            display_path = f"{archive_name}/{item.full_path}"

            if is_image:
                if self.main_window._use_tabs:
                    editor = self.main_window.addTab(display_path)
                    editor.file_path = self.current_archive_path
                    gallery = editor.getHandler(ViewMode.GALLERY)
                    gallery.archive_type = "zip" if fileType(self.current_archive_path, "ZIP") else "tar"
                    handler = editor.getHandler(ViewMode.IMAGE)
                    handler.openArchiveImage(editor, self.current_archive_path, item.full_path)
                else:
                    editor = self.main_window.single_editor
                    editor.file_path = self.current_archive_path
                    gallery = editor.getHandler(ViewMode.GALLERY)
                    gallery.archive_type = "zip" if fileType(self.current_archive_path, "ZIP") else "tar"
                    handler = editor.getHandler(ViewMode.IMAGE)
                    handler.openArchiveImage(editor, self.current_archive_path, item.full_path)
            else:
                try:
                    content_str = content.decode("utf-8", errors="replace")
                except Exception:
                    content_str = str(content)

                if self.main_window._use_tabs:
                    editor = self.main_window.addTab(display_path, content_str)
                else:
                    editor = self.main_window.single_editor
                    editor.setContent(content_str)
                    editor.setFilePath(display_path)

            self.main_window.config.addRecentFile(self.current_archive_path)
            self.main_window.statusBar().showMessage(tr("已打开") + " " + display_path, 3000)

    def _showTreeMenu(self, pos):
        """显示文件夹树右键菜单"""
        menu = QMenu(self.parent)

        close_action = QAction(tr("关闭文件夹视图"), self.parent)
        close_action.triggered.connect(self.close)
        menu.addAction(close_action)

        index = self.tree.indexAt(pos)
        if index.isValid():
            item_path = self.model.filePath(index)
            if isinstance(item_path, str) and item_path:
                openTerminal_action = QAction(tr("在终端中打开"), self.parent)
                openTerminal_action.triggered.connect(partial(self.openTerminal, item_path))
                menu.addAction(openTerminal_action)

                show_in_explorer_action = QAction(tr("在文件资源管理器中显示"), self.parent)
                show_in_explorer_action.triggered.connect(lambda checked=False, fp=item_path: showFile(fp, self.parent))
                menu.addAction(show_in_explorer_action)

                menu.addSeparator()

                item_path_norm = os.path.normpath(item_path)
                if self.main_window.config.isFavorite(item_path_norm):
                    remove_fav_action = QAction(tr("从收藏夹移除"), self.parent)
                    remove_fav_action.triggered.connect(lambda checked, fp=item_path_norm: self.main_window.delFav(fp))
                    menu.addAction(remove_fav_action)
                else:
                    add_fav_action = QAction(tr("添加到收藏夹"), self.parent)
                    add_fav_action.triggered.connect(lambda checked, fp=item_path_norm: self.main_window.addFav(fp))
                    menu.addAction(add_fav_action)

                if os.path.isfile(item_path):
                    move_to_trash_action = QAction(tr("移动到回收站"), self.parent)
                    move_to_trash_action.triggered.connect(lambda checked, fp=item_path: self.recycle(fp))
                    menu.addAction(move_to_trash_action)
        elif self.folder_path is not False and self.folder_path and isinstance(self.folder_path, str):
            openTerminal_action = QAction(tr("在终端中打开"), self.parent)
            openTerminal_action.triggered.connect(partial(self.openTerminal, self.folder_path))
            menu.addAction(openTerminal_action)

            show_in_explorer_action = QAction(tr("在文件资源管理器中显示"), self.parent)
            show_in_explorer_action.triggered.connect(lambda: showFile(self.folder_path, self.parent))
            menu.addAction(show_in_explorer_action)

            menu.addSeparator()

            folder_path_norm = os.path.normpath(self.folder_path)
            if self.main_window.config.isFavorite(folder_path_norm):
                remove_fav_action = QAction(tr("从收藏夹移除"), self.parent)
                remove_fav_action.triggered.connect(lambda checked, fp=folder_path_norm: self.main_window.delFav(fp))
                menu.addAction(remove_fav_action)
            else:
                add_fav_action = QAction(tr("添加到收藏夹"), self.parent)
                add_fav_action.triggered.connect(lambda checked, fp=folder_path_norm: self.main_window.addFav(fp))
                menu.addAction(add_fav_action)

        menu.exec_(self.tree.mapToGlobal(pos))

    def recycle(self, item_path: str):
        """移动文件到回收站"""
        from src.system import moveTrash
        if moveTrash(item_path):
            self.parent.statusBar().showMessage(tr("已移动到回收站") + f": {item_path}", 2000)
            if self.panel is not None and self.folder_path:
                self.load(self.folder_path)
        else:
            messageBox(self.parent, tr("错误"), tr("移动到回收站失败"), 1)

    def openTerminal(self, path):
        """在终端中打开"""
        try:
            if not isinstance(path, str) or not path:
                logger.warning(f"无效的路径: type={type(path).__name__}, value={path!r}")
                return
            if openTerminal(path):
                self.parent.statusBar().showMessage(tr("已打开终端") + f": {os.path.normpath(path)}", 2000)
        except Exception:
            logger.exception("打开终端失败")
            self.parent.statusBar().showMessage(tr("打开终端失败"), 2000)

    def _onHeaderClicked(self, event):
        """点击父文件夹标签时切换到上级目录"""
        if self.current_archive_path:
            parent_path = os.path.dirname(self.current_archive_path)
            if parent_path and parent_path != self.current_archive_path:
                self.load(parent_path)
        elif self.folder_path:
            parent_path = os.path.dirname(self.folder_path)
            if parent_path and parent_path != self.folder_path:
                self.load(parent_path)

    def ensureCreated(self):
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
            self.setSizes(self.main_window.width())

    def load(self, folder_path: str):
        """加载文件夹"""
        self.ensureCreated()

        if fileType(folder_path, "TAR") or fileType(folder_path, "ZIP"):
            self._loadArchive(folder_path)
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
            self.setSizes(self.main_window.width())

    def _loadArchive(self, archive_path: str):
        """加载压缩包内容"""
        if self.archive_model is None:
            self.archive_model = ArchiveItemModel()

        self.current_archive_path = archive_path
        self.archive_model.loadArchive(archive_path)

        self.tree.setModel(self.archive_model)

        parent_path = os.path.dirname(archive_path)
        if parent_path:
            self.header.setText(f".. {os.path.basename(archive_path)}")
            self.header.setToolTip(archive_path)
            self.header.show()
        else:
            self.header.hide()

        self.parent.statusBar().showMessage(tr("已加载压缩包") + f": {os.path.abspath(archive_path)}", 3000)

    def setSizes(self, available_width: int):
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
            if self.model:
                self.model.deleteLater()
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

    def isVisible(self) -> bool:
        return self.panel is not None
