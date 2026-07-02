import os
import re
import tarfile
import zipfile
import shutil
import fnmatch
import time
from pathlib import Path
from datetime import datetime
from typing import Tuple, Optional

from PySide6.QtWidgets import QDialog, QLabel, QTextEdit, QFileDialog, QVBoxLayout
from PySide6.QtCore import Qt, QModelIndex, QDir, QAbstractItemModel
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from src.util import Singleton, data_dir, logger, getTimestamp, EXTENSION, ENCODING_MAP, encodingName, getFilePath, dialogBox, messageBox
from src.plugin import getPluginManager
from src.core.md import render_markdown

SUPPORTED_ENCODINGS = list(ENCODING_MAP.values())

def readEncoding(file_path: str, encoding: str = 'utf-8', auto_detect: bool = True) -> Tuple[str, str]:
    """读取文件并自动检测编码
    Returns: (content, actual_encoding)
    """
    if not file_path:
        raise FileNotFoundError("文件路径为空")
    
    _path = Path(file_path)
    
    if not _path.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")
    
    try:
        with open(_path, 'r', encoding=encoding, newline='') as f:
            content = f.read()
        return content, encoding
    except UnicodeDecodeError:
        if not auto_detect:
            raise ValueError(f"无法以 {encoding} 编码读取文件: {file_path}")
    
    for enc in SUPPORTED_ENCODINGS:
        try:
            with open(_path, 'r', encoding=enc, newline='') as f:
                content = f.read()
            return content, enc
        except UnicodeDecodeError:
            continue
    
    try:
        with open(_path, 'r', encoding='utf-8', errors='replace', newline='') as f:
            content = f.read()
        return content, 'utf-8'
    except Exception:
        raise ValueError(f"无法使用支持的编码读取文件: {file_path}")

def pdfView(tab, file_path: str) -> bool:
    """使用 QPdfView 渲染 PDF（懒加载 QtPdf DLL）"""
    from PySide6.QtPdf import QPdfDocument
    from PySide6.QtPdfWidgets import QPdfView

    class _PdfViewer(QPdfView):
        def __init__(self, parent=None):
            super().__init__(parent)
            self._zoom_factor = 1.0
            self.setPageMode(QPdfView.PageMode.MultiPage)
            self.setZoomMode(QPdfView.ZoomMode.FitInView)
            self.setZoomFactor(1.0)
            self.setAutoFillBackground(True)
            palette = self.palette()
            palette.setColor(self.backgroundRole(), Qt.GlobalColor.white)
            self.setPalette(palette)
            self.setZoomMode(QPdfView.ZoomMode.Custom)

        def wheelEvent(self, event):
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                delta = event.angleDelta().y()
                if delta > 0:
                    self._zoom_factor *= 1.1
                else:
                    self._zoom_factor /= 1.1
                self._zoom_factor = max(0.1, min(10.0, self._zoom_factor))
                self.setZoomFactor(self._zoom_factor)
                event.accept()
                return
            super().wheelEvent(event)

    try:
        pdf_document = QPdfDocument()
        pdf_document.load(file_path)

        error = pdf_document.error()
        if error != QPdfDocument.Error.None_:
            logger.error(f"PDF加载错误: {error}")
            return False

        page_count = pdf_document.pageCount()
        if page_count <= 0:
            logger.error(f"PDF页数为0: {file_path}")
            return False

        tab._is_pdf = True
        tab._pdf_page_count = page_count
        tab._pdf_file_path = file_path

        pdf_view = _PdfViewer()
        pdf_view.setDocument(pdf_document)

        tab.text_edit.hide()
        tab.image_scroll.hide()
        if hasattr(tab, '_gallery_widget') and tab._gallery_widget:
            tab._gallery_widget.hide()

        tab._pdf_view = pdf_view
        tab._pdf_document = pdf_document
        tab.layout().addWidget(pdf_view)
        pdf_view.show()

        tab.is_image = True
        tab.set_line_numbers_visible(False)

        logger.info(f"PDF渲染完成: {file_path}, 页数: {page_count}")
        return True
    except Exception:
        logger.exception("渲染PDF失败")
        return False


class FileOperation(Singleton):
    """文件操作核心类：读、写、备份、压缩包浏览、Markdown 渲染"""

    def _init_impl(self):
        self.backup_dir = data_dir / "backup"
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def readFileLimit(self, file_path: str, max_lines: int = 50000, start_line: int = 0, encoding: str = None) -> Tuple[str, int, int, int, str]:
        """读取文件，带行数限制，支持跳过行数（用于翻页）
        Args:
            file_path: 文件路径
            max_lines: 最多读取行数
            start_line: 起始行号（0-based，跳过前 start_line 行）
            encoding: 指定编码（不为 None 时跳过自动检测）
        
        Returns:
                content: 本次读取的内容
                total_lines: 文件总行数
                loaded_lines: 实际读取的行数
                is_truncated: 1=有下一页, 0=已读完, -1=出错
                encoding: 实际使用的编码
        """
        try:
            _path = Path(file_path)
            if encoding:
                encodings_to_try = [encoding]
            else:
                encodings_to_try = ['utf-8'] + SUPPORTED_ENCODINGS
            for enc in encodings_to_try:
                try:
                    lines = []
                    total = 0
                    with open(_path, 'r', encoding=enc, newline='') as f:
                        for line in f:
                            if total >= start_line and len(lines) < max_lines:
                                lines.append(line.rstrip('\n').rstrip('\r'))
                            total += 1
                    loaded = len(lines)
                    if total > start_line + loaded:
                        truncated = 1
                    else:
                        truncated = 0
                    return '\n'.join(lines), total, loaded, truncated, enc
                except (UnicodeDecodeError, UnicodeError):
                    if encoding:
                        with open(_path, 'r', encoding=encoding, errors='replace', newline='') as f:
                            lines = [line.rstrip('\n').rstrip('\r') for line in f]
                        total = len(lines)
                        return '\n'.join(lines), total, total, 0, encoding
                    continue
            lines = []
            total = 0
            with open(_path, 'r', encoding='utf-8', errors='replace', newline='') as f:
                for line in f:
                    if total >= start_line and len(lines) < max_lines:
                        lines.append(line.rstrip('\n').rstrip('\r'))
                    total += 1
            loaded = len(lines)
            truncated = 1 if total > start_line + loaded else 0
            return '\n'.join(lines), total, loaded, truncated, 'utf-8'
        except Exception:
            logger.exception("带限制读取文件失败")
            return "", 0, 0, -1, ""

    def write_file(self, file_path: str, content: str, encoding: str = 'utf-8') -> bool:
        """写入文件，自动创建父目录"""
        try:
            file_path = Path(file_path)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(file_path, 'w', encoding=encoding, newline='') as f:
                f.write(content)
            logger.info(f"文件写入成功: {file_path}")
            return True
        except Exception:
            logger.exception("文件写入失败")
            return False

    def create_backup(self, file_path: str, config=None) -> Optional[str]:
        """创建文件备份，返回备份路径；config 中 history_backup 为 False 时跳过"""
        if config is not None and not config.get("Edit.backup", True):
            return None
            
        file_path = Path(file_path)
        if not file_path.exists():
            return None
        
        backup_name = f"{file_path.stem}_{getTimestamp()}{file_path.suffix}"
        backup_path = self.backup_dir / backup_name
        
        try:
            shutil.copyfile(file_path, backup_path)
            logger.info(f"文件备份成功: {backup_path}")
            
            self._clean_old_backups(config)
            
            return str(backup_path)
        except Exception:
            logger.exception("备份创建失败")
            return None

    def _clean_old_backups(self, config=None) -> None:
        """清理超过指定天数的旧备份文件"""
        try:
            days_to_keep = 7
            
            if not self.backup_dir.exists():
                return
            
            current_time = time.time()
            cutoff_time = current_time - (days_to_keep * 86400)
            
            for backup_file in self.backup_dir.iterdir():
                if backup_file.is_file():
                    file_mtime = backup_file.stat().st_mtime
                    if file_mtime < cutoff_time:
                        backup_file.unlink()
                        logger.info(f"已删除过期备份: {backup_file.name}")
        except Exception:
            logger.exception("清理旧备份失败")

    def get_file_info(self, file_path: str) -> dict:
        """获取文件元信息：名称、路径、大小、修改/创建时间、扩展名"""
        file_path = Path(file_path)
        if not file_path.exists():
            return {}
        
        stat = file_path.stat()
        return {
            "name": file_path.name,
            "path": str(file_path.absolute()),
            "size": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            "created": datetime.fromtimestamp(stat.st_ctime).strftime("%Y-%m-%d %H:%M:%S"),
            "extension": file_path.suffix,
        }

    def render_markdown(self, content: str = None, file_path: str = None) -> Optional[str]:
        """渲染 Markdown 文本为 HTML（从文件读取或直接传内容）"""
        try:
            if file_path:
                content, encoding = readEncoding(file_path)
            elif content is None:
                return None
            
            return render_markdown(content, file_path)
        except Exception:
            logger.exception("Markdown渲染失败")
            return None

    def is_image_file(self, file_path: str) -> bool:
        """检查是否为图片文件"""
        if not file_path:
            return False
        path = file_path.lower()
        return any(path.endswith(ext) for ext in EXTENSION["IMAGE"])
    
    def is_tar_file(self, file_path: str) -> bool:
        """检查文件扩展名是否为 tar 压缩包格式"""
        path = file_path.lower()
        return any(path.endswith(ext) for ext in EXTENSION["TAR"])
    
    def is_zip_file(self, file_path: str) -> bool:
        """检查文件扩展名是否为 zip 压缩包格式"""
        path = file_path.lower()
        return any(path.endswith(ext) for ext in EXTENSION["ZIP"])

    def list_archive_contents(self, file_path: str) -> list:
        """列出压缩包内部的文件和文件夹"""
        file_path_obj = Path(file_path)
        if not file_path_obj.exists():
            return None
        
        file_path_lower = file_path.lower()
        if any(file_path_lower.endswith(ext) for ext in EXTENSION["ZIP"]):
            return self._list_zip_contents(file_path_obj)
        elif any(file_path_lower.endswith(ext) for ext in EXTENSION["TAR"]):
            return self._list_tar_contents(file_path_obj)
        return None

    def _list_zip_contents(self, file_path: Path) -> Optional[list]:
        """列出zip文件内容"""
        try:
            with zipfile.ZipFile(file_path, 'r') as zf:
                items = []
                for info in zf.infolist():
                    item = {
                        "name": info.filename,
                        "is_dir": info.is_dir(),
                        "size": info.file_size,
                        "compressed_size": info.compress_size,
                    }
                    items.append(item)
                return items
        except Exception:
            logger.exception("读取ZIP文件失败")
            return None

    def _list_tar_contents(self, file_path: Path) -> Optional[list]:
        """列出tar文件内容"""
        try:
            with tarfile.open(file_path, 'r:*') as tf:
                items = []
                for member in tf.getmembers():
                    item = {
                        "name": member.name,
                        "is_dir": member.isdir(),
                        "size": member.size,
                        "type": member.type,
                    }
                    items.append(item)
                return items
        except Exception:
            logger.exception("读取TAR文件失败")
            return None

    def read_archive_file(self, file_path: str, member_name: str) -> Optional[bytes]:
        """读取压缩包内指定文件的内容"""
        file_path_obj = Path(file_path)
        if not file_path_obj.exists():
            return None
        
        file_path_lower = file_path.lower()
        try:
            if any(file_path_lower.endswith(ext) for ext in EXTENSION["ZIP"]):
                with zipfile.ZipFile(file_path_obj, 'r') as zf:
                    return zf.read(member_name)
            elif any(file_path_lower.endswith(ext) for ext in EXTENSION["TAR"]):
                with tarfile.open(file_path_obj, 'r:*') as tf:
                    member = tf.getmember(member_name)
                    if member.isfile():
                        f = tf.extractfile(member)
                        return f.read()
        except Exception:
            logger.exception("读取压缩包内文件失败")
        return None


class FileControl:
    """文件操作控制器 - 管理文件的打开、保存、编码转换等操作"""

    def __init__(self, main_window):
        self.main = main_window

    def open_file(self):
        """打开文件对话框"""
        file_path = getFilePath(self.main, "打开文件", "所有文件 (*.*);;文本文件 (*.txt *.md)")
        if not file_path:
            return

        self.main.open_file_path(file_path)

    def open_file_path(self, file_path: str):
        """打开指定路径的文件"""
        if not file_path:
            return

        if os.path.isdir(file_path):
            self.main.load_folder(file_path)
            return

        # 避免循环依赖
        from src.gui.tab import EditorTab

        if not self.main.config.get("Edit.multi_tab", False):
            editor = self.main.get_current_editor()
            self._do_open_file(file_path)
            return

        if self.main.tab_widget:
            for i in range(self.main.tab_widget.count()):
                editor = self.main.tab_widget.widget(i)
                if isinstance(editor, EditorTab) and editor.get_file_path() == file_path:
                    self.main.tab_widget.setCurrentIndex(i)
                    self.main.statusBar().showMessage(f"文件已在标签页中打开: {file_path}", 3000)
                    return

        self._do_open_file(file_path)

    def _do_open_file(self, file_path: str):
        """实际打开文件的逻辑"""
        if not file_path or not os.path.isfile(file_path):
            messageBox(self.main, "打开失败", f"文件不存在或路径无效: {file_path}", 1)
            return

        for handler in self._file_handlers:
            if handler.can_handle(file_path):
                handler.open(file_path, self.main)
                self.main.config.add_recent_file(file_path)
                return

        for can_handle, open_file in getPluginManager(self.main).file_handlers:
            if can_handle(file_path):
                open_file(file_path, self.main)
                self.main.config.add_recent_file(file_path)
                return

        self._open_text_file(file_path)

    @property
    def _file_handlers(self):
        """获取文件处理器列表（惰性初始化）"""
        if not hasattr(self, '_handlers_cache'):
            self._handlers_cache = [
                _ImageFileHandler(self.main),
                _ArchiveFileHandler(self.main),
                _PdfFileHandler(self.main),
            ]
        return self._handlers_cache

    def _open_text_file(self, file_path: str):
        """打开文本文件（支持大文件翻页截断）"""
        content, encoding = None, None
        total_lines = 0
        loaded_lines = 0
        truncated = 0

        try:
            content, total_lines, loaded_lines, truncated, encoding = \
                self.main.file_op.readFileLimit(file_path, max_lines=50000, start_line=0)
        except (FileNotFoundError, UnicodeDecodeError, ValueError):
            pass
        except Exception as e:
            messageBox(self.main, "打开失败", f"读取文件时发生错误: {e}", 1)
            return

        if self._try_plugin_handling(file_path):
            return

        self._setup_text_editor(file_path, content, encoding, total_lines, loaded_lines, truncated)

    def _try_plugin_handling(self, file_path: str) -> bool:
        """尝试使用插件处理文件"""
        plugin_manager = getPluginManager(self.main)
        for plugin_name, plugin in plugin_manager.getAllPlugin().items():
            if hasattr(plugin, 'is_supported') and callable(plugin.is_supported) and plugin.is_supported(file_path):
                logger.info(f"使用插件 {plugin_name} 打开文件: {file_path}")
                if self._handle_plugin_file(plugin, file_path):
                    return True
        return False

    def _handle_plugin_file(self, plugin, file_path: str) -> bool:
        """未设置处理方式的文件尝试使用插件处理"""
        try:
            editor = None
            if self.main._use_tabs:
                editor = self.main.add_new_tab(file_path, "")
            else:
                editor = self.main.single_editor
                editor.set_content("")
                editor.set_file_path(file_path)
            return False
        except Exception:
            logger.exception("插件文件处理失败")
            return False

    def _setup_text_editor(self, file_path: str, content, encoding: str,
                           total_lines: int = 0, loaded_lines: int = 0, truncated: int = 0):
        """设置文本编辑器（支持大文件截断信息）"""
        try:
            if self.main._use_tabs:
                editor = self.main.add_new_tab(file_path, content)
            else:
                editor = self.main.single_editor
                editor.set_content(content)
                editor.set_file_path(file_path)
            editor.set_encoding(encoding)
            if truncated > 0:
                editor.set_truncated(total_lines, loaded_lines, file_path, encoding)
            self.main.encoding_label.setText(encodingName(encoding) if encoding else "")
            self.main._toc_panel.hide_panel()
            self.main.config.add_recent_file(file_path)
            if truncated > 0:
                self.main.statusBar().showMessage(
                    f"已打开: {os.path.abspath(file_path)} （显示 {loaded_lines}/{total_lines} 行）", 5000)
            else:
                self.main.statusBar().showMessage(f"已打开: {os.path.abspath(file_path)}", 3000)
        except Exception as e:
            logger.exception("设置编辑器内容时发生错误")
            messageBox(self.main, "打开失败", f"设置编辑器内容时发生错误: {e}", 1)

    def save_file(self) -> bool:
        """保存当前文件（支持大文件翻页合并保存）"""
        editor = self.main.get_current_editor()
        if not editor:
            return False

        file_path = editor.get_file_path()

        if not file_path:
            return self.save_file_as()

        encoding = editor.get_encoding()

        backup_path = None
        if file_path:
            backup_path = self.main.file_op.create_backup(file_path, self.main.config)

        try:
            # 大文件翻页模式下：合并各页内容再写出
            if editor._is_truncated:
                with open(file_path, 'w', encoding=encoding) as f:
                    f.write(editor._assemble_full_content())
            else:
                with open(file_path, 'w', encoding=encoding) as f:
                    f.write(editor.get_content())

            editor.mark_saved()
            if self.main.tab_widget:
                self.main.tab_widget.setTabText(self.main.tab_widget.currentIndex(), editor.get_title())

            if backup_path:
                self.main.statusBar().showMessage(f"已保存: {file_path} ({encoding}) [已备份]", 3000)
            else:
                self.main.statusBar().showMessage(f"已保存: {file_path} ({encoding})", 3000)
            return True

        except Exception as e:
            messageBox(self.main, "保存失败", f"保存文件时发生错误: {e}", 1)
            return False

    def save_file_as(self) -> bool:
        """另存为"""
        editor = self.main.get_current_editor()
        if not editor:
            return False

        file_path, _ = QFileDialog.getSaveFileName(self.main, "另存为", "", "所有文件 (*.*);;文本文件 (*.txt)")

        if not file_path:
            return False

        editor.set_file_path(file_path)
        return self.save_file()

    def open_with_encoding(self, encoding: str):
        """以指定编码打开当前文件"""
        editor = self.main.get_current_editor()
        if not editor:
            self.main.statusBar().showMessage("没有打开的文件", 2000)
            return

        file_path = editor.get_file_path()
        if not file_path:
            self.main.statusBar().showMessage("文件未保存，无法以指定编码打开", 2000)
            return

        actual_encoding = ENCODING_MAP.get(encoding, encoding.lower())

        try:
            content, total_lines, loaded_lines, truncated, _ = \
                self.main.file_op.readFileLimit(file_path, max_lines=50000, start_line=0, encoding=actual_encoding)
            editor.set_content(content)
            editor.set_encoding(actual_encoding)
            if truncated > 0 and hasattr(editor, 'set_truncated'):
                editor.set_truncated(total_lines, loaded_lines, file_path, actual_encoding)
            elif hasattr(editor, 'clear_truncated'):
                editor.clear_truncated()
            display = encodingName(actual_encoding)
            self.main.encoding_label.setText(display)
            self.main.statusBar().showMessage(f"已以 {display} 编码重新打开: {file_path}", 3000)
        except Exception as e:
            messageBox(self.main, "打开失败", f"以 {encoding} 编码读取文件失败: {e}", 1)

    def save_with_encoding(self, encoding: str):
        """以指定编码保存当前文件"""
        editor = self.main.get_current_editor()
        if not editor:
            self.main.statusBar().showMessage("没有打开的文件", 2000)
            return

        file_path = editor.get_file_path()
        if not file_path:
            self.main.statusBar().showMessage("文件未保存，请先保存文件", 2000)
            return

        actual_encoding = ENCODING_MAP.get(encoding, encoding.lower())

        try:
            with open(file_path, 'w', encoding=actual_encoding) as f:
                f.write(editor.get_content())
            editor.set_encoding(actual_encoding)
            display = encodingName(actual_encoding)
            self.main.encoding_label.setText(display)
            self.main.statusBar().showMessage(f"已以 {display} 编码保存: {file_path}", 3000)
        except Exception as e:
            messageBox(self.main, "保存失败", f"以 {encoding} 编码保存文件失败: {e}", 1)


class FileHandler:
    """文件处理器基类"""
    def __init__(self, main):
        self.main = main

    def can_handle(self, file_path: str) -> bool:
        raise NotImplementedError

    def open(self, file_path: str, main):
        raise NotImplementedError

    def _create_editor(self, file_path: str):
        """创建或复用编辑器"""
        if self.main._use_tabs:
            return self.main.add_new_tab(file_path, "")
        else:
            editor = self.main.single_editor
            editor.set_content("")
            editor.set_file_path(file_path)
            return editor


class _ImageFileHandler(FileHandler):
    """图片文件处理器"""
    def can_handle(self, file_path: str) -> bool:
        return self.main.file_op.is_image_file(file_path)

    def open(self, file_path: str, main):
        editor = self._create_editor(file_path)
        if editor.load_image(file_path):
            main.statusBar().showMessage(f"已打开图片: {os.path.abspath(file_path)}", 3000)
        else:
            messageBox(main, "打开失败", "无法读取图片文件", 1)


class _ArchiveFileHandler(FileHandler):
    """压缩文件处理器（zip/tar）"""
    def can_handle(self, file_path: str) -> bool:
        return self.main.file_op.is_zip_file(file_path) or self.main.file_op.is_tar_file(file_path)

    def open(self, file_path: str, main):
        self._create_editor(file_path)
        main.statusBar().showMessage(f"已打开: {os.path.abspath(file_path)} (右键进入图库模式)", 3000)


class _PdfFileHandler(FileHandler):
    """PDF文件处理器"""
    def can_handle(self, file_path: str) -> bool:
        return file_path.lower().endswith('.pdf')

    def open(self, file_path: str, main):
        editor = self._create_editor(file_path)
        if pdfView(editor, file_path):
            main.statusBar().showMessage(f"已打开PDF: {os.path.abspath(file_path)}", 3000)
        else:
            messageBox(main, "打开失败", "无法渲染PDF页面", 1)

class FileSelect(QDialog):
    """文件选择对话框，支持拖放和排除规则"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择文件夹")
        self.resize(500, 400)
        self.setAcceptDrops(True)

        self.label_path = QLabel("文件夹路径（每行一个，支持拖放）")
        self.path_edit = QTextEdit()
        self.path_edit.setStyleSheet("background: #eeeeee")
        self.label_exclude = QLabel(r"""排除规则（每行一项，支持通配符）
/file.txt - 排除单个 file.txt
*/file.txt - 排除所有 file.txt
/folder/ - 排除文件夹下的 folder 文件夹
*.pyc - 排除所有 .pyc 文件
*/.git/ - 排除所有 .git 文件夹""")
        self.label_exclude.setWordWrap(True)
        self.exclude_edit = QTextEdit()
        self.exclude_edit.setStyleSheet("background: #eeeeee")
        self.exclude_edit.setPlainText("*.pyc\n*/__pycache__/\n*/.git/")
        self.path_edit.setAcceptDrops(False)
        self.exclude_edit.setAcceptDrops(False)

        main_layout = QVBoxLayout()
        self.setLayout(main_layout)
        main_layout.addWidget(self.label_path)
        main_layout.addWidget(self.path_edit)
        main_layout.addWidget(self.label_exclude)
        main_layout.addWidget(self.exclude_edit)
        dialogBox(main_layout, self, show=False)

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

    def get_selected_paths(self) -> list[str]:
        """获取用户输入的路径列表"""
        text = self.path_edit.toPlainText()
        lines = text.splitlines()
        return [line.strip() for line in lines if line.strip()]

    def get_exclude_rules(self) -> list[str]:
        """获取用户输入的排除规则列表"""
        text = self.exclude_edit.toPlainText()
        lines = text.splitlines()
        return [line.strip() for line in lines if line.strip()]

    @staticmethod
    def select(parent=None, default_paths: list[str] = None, default_exclude_rules: list[str] = None) -> tuple:
        """显示文件选择对话框并返回选中的文件列表"""
        dialog = FileSelect(parent)
        if default_paths:
            dialog.path_edit.setPlainText('\n'.join(default_paths))
        if default_exclude_rules:
            dialog.exclude_edit.setPlainText('\n'.join(default_exclude_rules))
        result = dialog.exec()
        if result == QDialog.DialogCode.Accepted:
            paths = dialog.get_selected_paths()
            rules = dialog.get_exclude_rules()
            files = collect_files(paths, rules)
            return files, paths, rules
        return None


def _compile_single_rule(rule: str) -> tuple[re.Pattern, bool, bool]:
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
    fnmatch_pattern = fnmatch_pattern.replace('(?s:', '').rstrip(')\\Z')

    if is_dir:
        fnmatch_pattern = fnmatch_pattern.rstrip('$') + '/?$'

    try:
        return (re.compile(fnmatch_pattern, re.IGNORECASE), is_dir, root_only)
    except re.error:
        return None


def compile_rules(rules: list[str]) -> list[tuple[re.Pattern, bool, bool]]:
    """将排除规则列表预编译为正则表达式列表"""
    compiled = []
    for rule in rules:
        pattern = _compile_single_rule(rule)
        if pattern is not None:
            compiled.append(pattern)
    return compiled


def _match_relative_path(rel_path: str, pattern: re.Pattern, is_dir: bool, root_only: bool = False) -> bool:
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


def is_excluded(file_path: str, base_path: str, exclude_rules: list[tuple[re.Pattern, bool, bool]]) -> bool:
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

        if _match_relative_path(rel_path_normalized, pattern, rule_is_dir, root_only):
            return True

    return False


def filter_files(base_path: str, exclude_rules_raw: list[str]) -> list[str]:
    """过滤目录下符合规则的文件"""
    if not os.path.isdir(base_path):
        return []

    result = []
    base_path = os.path.normpath(base_path)

    exclude_rules = compile_rules(exclude_rules_raw)

    for root, dirs, files in os.walk(base_path, onerror=lambda _: None):  # 静默跳过不可访问的目录
        dirs_to_remove = []
        for d in dirs:
            dir_path = os.path.join(root, d)
            if is_excluded(dir_path, base_path, exclude_rules):
                dirs_to_remove.append(d)

        for d in dirs_to_remove:
            dirs.remove(d)

        for f in files:
            file_path = os.path.join(root, f)
            if not is_excluded(file_path, base_path, exclude_rules):
                result.append(file_path)

    return result


def collect_files(paths: list[str], rules: list[str]) -> list[str]:
    """从多个路径收集文件"""
    all_files = []
    for path in paths:
        if os.path.isdir(path):
            all_files.extend(filter_files(path, rules))
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
    
    def append_child(self, child):
        child.parent = self
        self.children.append(child)
    
    def child_count(self):
        return len(self.children)
    
    def child(self, row: int):
        return self.children[row] if 0 <= row < len(self.children) else None
    
    def row(self):
        return self.parent.children.index(self) if self.parent else 0


class ArchiveItemModel(QAbstractItemModel):
    """支持压缩包内容的自定义模型"""
    
    def __init__(self, file_op, parent=None):
        super().__init__(parent)
        self.file_op = file_op
        self.root_item = ArchiveFileItem("", True)
        self.archive_path = ""
    
    def load_archive(self, archive_path: str):
        """加载压缩包内容"""
        self.archive_path = archive_path
        self.beginResetModel()
        self.root_item = ArchiveFileItem("", True)
        
        items = self.file_op.list_archive_contents(archive_path)
        if not items:
            self.endResetModel()
            return
        
        name_to_item = {}
        
        for item in items:
            name = item["name"]
            is_dir = item["is_dir"]
            size = item.get("size", 0)
            
            if name.endswith("/") and not is_dir:
                is_dir = True
            
            parts = name.rstrip("/").split("/")
            
            if len(parts) == 1:
                new_item = ArchiveFileItem(parts[0], is_dir, size, "", archive_path)
                self.root_item.append_child(new_item)
                name_to_item[name.rstrip("/")] = new_item
            else:
                parent_name = "/".join(parts[:-1])
                parent_item = name_to_item.get(parent_name)
                if parent_item is None:
                    parent_item = self.root_item
                new_item = ArchiveFileItem(parts[-1], is_dir, size, parent_name + "/" if parent_name else "", archive_path)
                parent_item.append_child(new_item)
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
        return parent_item.child_count() if parent_item else 0


# 文件树（支持保存为文件）
def fileTree(directory: Path, prefix: str = "") -> list:
    """递归生成树状结构的文本行列表
    :param directory: 当前目录的 Path 对象
    :param prefix: 当前层的前缀字符串（用于绘制树形线）
    :return: 字符串列表，每一行是树的一行"""
    lines = []
    try:
        items = list(directory.iterdir())
    except PermissionError:
        lines.append(f"{prefix}[无法读取目录]")
        return lines

    dirs = sorted([item for item in items if item.is_dir()])
    files = sorted([item for item in items if item.is_file()])
    all_items = dirs + files

    for idx, item in enumerate(all_items):
        is_last = (idx == len(all_items) - 1)
        connector = "└── " if is_last else "├── "
        extension = "    " if is_last else "│   "

        lines.append(f"{prefix}{connector}{item.name}")

        if item.is_dir():
            sub_prefix = prefix + extension
            lines.extend(fileTree(item, sub_prefix))

    return lines

