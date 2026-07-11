import os
import re

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QLabel, QPushButton
from PySide6.QtCore import Qt, Signal, QEvent
from PySide6.QtGui import QTextCursor, QTextDocument

from src.util import logger, EXTENSION, messageBox, urlToPath, tr
from src.core.syntax import createHighlighter
from src.core.timer import LRUCache
from src.gui.view import ViewMode, listArchive, readFileLimit


_LINE_ENDING_RE = re.compile(r'\r\n|\r')


class EditorTab(QWidget):
    """编辑器标签页：管理文件状态、视图模式（文本/图片/PDF/Markdown）、编码、高亮器"""

    file_changed = Signal(bool)
    file_opened = Signal(str)
    folder_opened = Signal(str)
    render_markdown = Signal()
    markdown_mode_changed = Signal(bool)
    image_loaded = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.file_path = None
        self.is_modified = False
        self.encoding = "utf-8"
        self.view_mode = ViewMode.TEXT
        self._current_mode = ViewMode.TEXT
        self._original_content = ""
        self.highlighter = None
        self.is_markdown = False
        self._markdown_cache = LRUCache(max_size=10)
        self.is_image = False
        self.setAcceptDrops(True)
        self._pending_file_path = None

        self.handlers = {}

        # 大文件翻页截断
        self._is_truncated = False
        self._page_size = 50000
        self._current_page = 0
        self._total_pages = 0
        self._total_lines = 0
        self._loaded_lines = 0
        self._page_buffer = {}         # {page_num: modified_content}
        self._truncated_file_path = ""
        self._truncated_encoding = "utf-8"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.text_edit = EditorTextEdit()
        self.text_edit.setPlaceholderText(tr("新建文件..."))
        self.text_edit.textChanged.connect(self._onTextChanged)
        self.text_edit._parent_tab = self
        self.text_edit.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)
        self.text_edit.installEventFilter(self)
        self.text_edit.setZoomCallback(lambda zf: self.window().statusBar().showMessage(tr("当前缩放") + f" {int(zf * 100)}%", 1500))
        self.text_edit.cursor_position_changed.connect(self._onCursorPos)
        self._cursor_position_callback = None

        layout.addWidget(self.text_edit)

        # 翻页栏
        self._pagination_bar = QWidget()
        self._pagination_bar.setVisible(False)
        self._pagination_bar.setFixedHeight(36)
        pag_layout = QHBoxLayout(self._pagination_bar)
        pag_layout.setContentsMargins(8, 2, 8, 2)
        pag_layout.setSpacing(6)

        self._prev_page_btn = QPushButton(tr("上一页"))
        self._prev_page_btn.setFixedWidth(80)
        self._prev_page_btn.clicked.connect(lambda: self._goToPage(self._current_page - 1) if self._current_page > 0 else None)
        pag_layout.addWidget(self._prev_page_btn)

        self._page_label = QLabel("")
        self._page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pag_layout.addWidget(self._page_label, 1)

        self._next_page_btn = QPushButton(tr("下一页"))
        self._next_page_btn.setFixedWidth(80)
        self._next_page_btn.clicked.connect(self._onNextPage)
        pag_layout.addWidget(self._next_page_btn)

        self._load_all_btn = QPushButton(tr("加载全部"))
        self._load_all_btn.setFixedWidth(100)
        self._load_all_btn.clicked.connect(lambda: self.loadAllContent())
        pag_layout.addWidget(self._load_all_btn)

        layout.addWidget(self._pagination_bar)
        
        self.image_scroll = QScrollArea()
        self.image_scroll.setWidgetResizable(True)
        self.image_scroll.hide()
        layout.addWidget(self.image_scroll)

    def dragEnterEvent(self, event):
        """拖拽进入事件"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        
    def dropEvent(self, event):
        """拖拽放下事件"""
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                file_path = urlToPath(url)
                if os.path.exists(file_path):
                    if os.path.isdir(file_path):
                        self.folder_opened.emit(file_path)
                    else:
                        self.file_opened.emit(file_path)
                    event.acceptProposedAction()

    def getHandler(self, mode):
        if mode not in self.handlers:
            cls = ViewMode._HANDLERS[mode]
            self.handlers[mode] = cls()
        return self.handlers[mode]

    @property
    def _archive_type(self):
        return self.getHandler(ViewMode.GALLERY).archive_type

    @_archive_type.setter
    def _archive_type(self, value):
        self.getHandler(ViewMode.GALLERY).archive_type = value

    @property
    def _zip_image_paths(self):
        return self.getHandler(ViewMode.GALLERY).zip_image_paths

    @_zip_image_paths.setter
    def _zip_image_paths(self, value):
        self.getHandler(ViewMode.GALLERY).zip_image_paths = value

    @property
    def _tar_image_paths(self):
        return self.getHandler(ViewMode.GALLERY).tar_image_paths

    @_tar_image_paths.setter
    def _tar_image_paths(self, value):
        self.getHandler(ViewMode.GALLERY).tar_image_paths = value

    @property
    def _archive_current_image(self):
        return self.getHandler(ViewMode.GALLERY).archive_current_image

    @_archive_current_image.setter
    def _archive_current_image(self, value):
        self.getHandler(ViewMode.GALLERY).archive_current_image = value

    @property
    def _is_viewing_archive_image(self):
        return self.getHandler(ViewMode.GALLERY).is_viewing_archive_image

    @_is_viewing_archive_image.setter
    def _is_viewing_archive_image(self, value):
        self.getHandler(ViewMode.GALLERY).is_viewing_archive_image = value

    @property
    def _pdf_widget(self):
        return self.getHandler(ViewMode.PDF).pdf_widget

    @_pdf_widget.setter
    def _pdf_widget(self, value):
        self.getHandler(ViewMode.PDF).pdf_widget = value

    @property
    def _pdf_document(self):
        return self.getHandler(ViewMode.PDF).pdf_document

    @_pdf_document.setter
    def _pdf_document(self, value):
        self.getHandler(ViewMode.PDF).pdf_document = value

    @property
    def _pdf_pixmaps(self):
        return self.getHandler(ViewMode.PDF).pdf_pixmaps

    @_pdf_pixmaps.setter
    def _pdf_pixmaps(self, value):
        self.getHandler(ViewMode.PDF).pdf_pixmaps = value

    def _onTextChanged(self):
        current_content = self.text_edit.toPlainText()
        current_normalized = _LINE_ENDING_RE.sub('\n', current_content)
        original_normalized = _LINE_ENDING_RE.sub('\n', self._original_content)
        if current_normalized != original_normalized:
            if not self.is_modified:
                self.is_modified = True
                self.file_changed.emit(True)
        else:
            if self.is_modified:
                self.is_modified = False
                self.file_changed.emit(False)

    def eventFilter(self, obj, event):
        if obj == self.text_edit and event.type() == QEvent.Type.MouseButtonDblClick and self._archive_type and self.file_path:
            return self._onArcDblClick(event)
        return super().eventFilter(obj, event)
    
    def _onArcDblClick(self, event):
        cursor = self.text_edit.textCursor()
        cursor.select(cursor.SelectionType.LineUnderCursor)
        line_text = cursor.selectedText().strip()
        if line_text:
            name_lower = line_text.lower()
            if any(name_lower.endswith(ext) for ext in EXTENSION["IMAGE"]):
                handler = self.getHandler(ViewMode.IMAGE)
                handler.openArchiveImage(self, self.file_path, line_text)
                return True
        return False
    
    def setFilePath(self, path: str):
        """设置文件路径：初始化高亮器，检测文件类型（Markdown/图片/压缩包）"""
        try:
            self.file_path = path
            self.setupHighlighter()
            
            if path:
                path_lower = path.lower()
                is_markdown = any(path_lower.endswith(ext) for ext in EXTENSION["Markdown"])
                is_image = self.isImgFile(path)
                is_zip = any(path_lower.endswith(ext) for ext in EXTENSION["ZIP"])
                is_tar = any(path_lower.endswith(ext) for ext in EXTENSION["TAR"])
                self._archive_type = 'zip' if is_zip else ('tar' if is_tar else None)
                if self._archive_type:
                    self._loadArcList(path)
            else:
                is_markdown = False
                is_image = False
            
            self.is_markdown = is_markdown
            self._markdown_html = None
            self.is_image = is_image
        except Exception:
            logger.exception("设置文件路径失败")
    
    def isImgFile(self, path: str) -> bool:
        """检查是否为图片文件"""
        if not path or not isinstance(path, str):
            return False
        try:
            ext = path.lower()
            return any(ext.endswith(img_ext) for img_ext in EXTENSION["IMAGE"])
        except (AttributeError, TypeError):
            return False
    
    def toPlainText(self) -> str:
        """获取纯文本内容"""
        return self.text_edit.toPlainText()
    
    def setPlainText(self, text: str):
        """设置纯文本内容"""
        self.text_edit.setPlainText(text)
        self.is_markdown = False
        self.text_edit.setReadOnly(False)
    
    def setupHighlighter(self):
        """设置语法高亮器"""
        if self.highlighter:
            try:
                self.highlighter.setDocument(None)
                if not self.highlighter.signalsBlocked():
                    self.highlighter.deleteLater()
            except RuntimeError:
                pass
            self.highlighter = None
        
        if not self.file_path:
            return
        
        try:
            doc = self.text_edit.document()
            if not doc:
                return
            highlighter = createHighlighter(self.file_path, doc)
            if highlighter:
                self.highlighter = highlighter
        except Exception:
            logger.exception("设置语法高亮器失败")

    def markSaved(self):
        """标记当前内容为已保存的干净状态，更新原始内容快照"""
        if self._is_truncated:
            self._original_content = self._assembleContent()
        else:
            self._original_content = self.text_edit.toPlainText()
        self.is_modified = False
        self.text_edit.document().setModified(False)

    def getTitle(self) -> str:
        """获取标签页标题（文件名 + 修改标记 *）"""
        if self.file_path:
            name = os.path.basename(self.file_path)
        else:
            name = tr("未命名")
        if self.is_modified:
            name += " *"
        return name

    def setLineSpacing(self, spacing: int):
        """设置行间距（0=禁用，>0=增量值）"""
        self._line_spacing = spacing
        if spacing == 0:
            return
        try:
            doc = self.text_edit.document()
            if doc:
                cursor = QTextCursor(doc)
                cursor.select(QTextCursor.SelectionType.Document)
                block_fmt = cursor.blockFormat()
                block_fmt.setLineHeight(100 + spacing, 1)
                cursor.setBlockFormat(block_fmt)
        except Exception:
            logger.exception("设置行距失败")

    def findText(self, text: str, forward: bool = True, 
                  case_sensitive: bool = False, regex: bool = False):
        """查找文本"""
        flags = QTextDocument.FindFlag(0)
        if not forward:
            flags |= QTextDocument.FindBackward
        if case_sensitive:
            flags |= QTextDocument.FindCaseSensitively
        if regex:
            flags |= QTextDocument.FindRegExp
        self.text_edit.find(text, flags)

    def replaceText(self, findText: str, replaceText: str,
                     case_sensitive: bool = False, regex: bool = False):
        """替换文本"""
        cursor = self.text_edit.textCursor()
        if cursor.hasSelection():
            cursor.insertText(replaceText)
        else:
            self.findText(findText, True, case_sensitive, regex)
            if self.text_edit.textCursor().hasSelection():
                cursor = self.text_edit.textCursor()
                cursor.insertText(replaceText)

    def _onCursorPos(self, line: int, col: int):
        if self._cursor_position_callback:
            self._cursor_position_callback(line, col)

    def setContent(self, content: str, emit_changed: bool = True):
        if content is None:
            content = ""
        try:
            content = _LINE_ENDING_RE.sub('\n', content)
        except Exception:
            logger.exception("替换换行符失败")

        if content == self._original_content and not self.is_modified:
            return
        
        self._markdown_cache.clear()
        self.is_modified = False
        self.clearTruncated(clear_buffer=False)

        # setPlainText 会触发 textChanged 信号，导致 _onTextChanged 误判
        # QPlainTextEdit 内部始终有至少一个段落，toPlainText() 返回的内容末尾会多出 \n，与 _original_content 比较不等，导致 is_modified 被错误设为 True
        self.text_edit.blockSignals(True)
        try:
            doc = self.text_edit.document()
            if doc:
                cursor = self.text_edit.textCursor()
                if cursor.position() > max(1, doc.characterCount()):
                    cursor.setPosition(0)
                    self.text_edit.setTextCursor(cursor)
        except Exception:
            logger.exception("恢复光标位置失败")

        try:
            self.text_edit.setPlainText(content)
            # Qt 内部会对某些 Unicode 字符做规范化（如 \xa0 → 空格），
            # 从 toPlainText() 回读 _original_content 确保后续比较与 Qt 实际内容一致
            self._original_content = self.text_edit.toPlainText()
            self.text_edit.document().setModified(False)
        except Exception:
            logger.exception("设置内容失败")
        finally:
            self.text_edit.blockSignals(False)

    def reloadFile(self) -> bool:
        """重新加载文件（相当于关闭再打开，保留光标滚动位置）"""
        if not self.file_path:
            return False

        if not os.path.exists(self.file_path):
            messageBox(self, tr("重新加载失败"), tr("文件不存在") + ": " + str(self.file_path), 1)
            return False

        if self.is_modified:
            if not messageBox(self, tr("未保存的修改"), tr("是否重新加载并丢弃修改")):
                return False

        old_cursor_pos = self.text_edit.textCursor().position()
        scrollbar = self.text_edit.verticalScrollBar()
        old_scroll_pos = scrollbar.value() if scrollbar else 0

        # 关闭当前视图
        old_handler = self.getHandler(self._current_mode)
        old_handler.deactivate(self)
        old_handler.close(self)

        self.text_edit.show()
        self.image_scroll.hide()

        self.view_mode = ViewMode.TEXT
        self._current_mode = ViewMode.TEXT
        self.is_markdown = False
        self._markdown_cache.clear()
        self._zip_image_paths = []
        self._tar_image_paths = []
        self._archive_current_image = None
        self._is_viewing_archive_image = False
        self._archive_type = None

        self.setFilePath(self.file_path)

        # 使用 ViewMode 重新加载
        ViewMode.openFile(self, self.file_path)
        new_handler = self.getHandler(self._current_mode)
        new_handler.activate(self)

        self.is_modified = False
        self.file_changed.emit(False)

        main_window = self.window()
        if hasattr(main_window, '_applyEditorSettings'):
            main_window._applyEditorSettings(self)

        try:
            cursor = self.text_edit.textCursor()
            doc = self.text_edit.document()
            if doc and old_cursor_pos <= doc.characterCount():
                cursor.setPosition(old_cursor_pos)
                self.text_edit.setTextCursor(cursor)
        except Exception:
            logger.exception("重载时恢复光标位置失败")

        try:
            if scrollbar:
                scrollbar.setValue(min(old_scroll_pos, scrollbar.maximum()))
        except Exception:
            logger.exception("重载时恢复滚动条位置失败")

        return True

    # ── 大文件翻页截断 ──────────────────────────────────────────────

    def setTruncated(self, total_lines: int, loaded_lines: int,
                      file_path: str, encoding: str):
        self._is_truncated = True
        self._total_lines = total_lines
        self._loaded_lines = loaded_lines
        self._total_pages = (total_lines + self._page_size - 1) // self._page_size
        self._current_page = 0
        self._page_buffer.clear()
        self._truncated_file_path = file_path
        self._truncated_encoding = encoding
        self.text_edit.setReadOnly(True)
        self._updatePages()
        self._pagination_bar.setVisible(True)

    def clearTruncated(self, clear_buffer: bool = True):
        self._is_truncated = False
        self._total_pages = 0
        self._current_page = 0
        self._total_lines = 0
        self._loaded_lines = 0
        if clear_buffer:
            self._page_buffer.clear()
        self._truncated_file_path = ""
        self.text_edit.setReadOnly(False)
        self._pagination_bar.setVisible(False)

    def _readPage(self, page: int) -> str:
        start_line = page * self._page_size
        content, total, loaded, truncated, _ = readFileLimit(
            self._truncated_file_path, max_lines=self._page_size, start_line=start_line)
        return content

    def _updatePages(self):
        if not self._is_truncated:
            self._page_label.setText("")
            return
        total = self._total_pages
        cur = self._current_page + 1
        self._page_label.setText(tr("第") + f" {cur} / {total} " + tr("页"))
        self._prev_page_btn.setEnabled(cur > 1)
        self._next_page_btn.setEnabled(cur < total)

    def _onNextPage(self):
        if self._current_page < self._total_pages - 1:
            self._goToPage(self._current_page + 1)

    def _goToPage(self, page: int):
        if page < 0 or page >= self._total_pages:
            return
        # 缓存当前页的编辑内容
        self._page_buffer[self._current_page] = self.text_edit.toPlainText()
        # 读取目标页
        if page in self._page_buffer:
            content = self._page_buffer[page]
        else:
            content = self._readPage(page)
        # 切换显示
        self.text_edit.blockSignals(True)
        self.text_edit.setPlainText(content)
        self.text_edit.blockSignals(False)
        self.text_edit.document().setModified(False)
        self._current_page = page
        self._updatePages()

    def loadAllContent(self):
        self._page_buffer[self._current_page] = self.text_edit.toPlainText()
        # 逐页读取未缓存的页面
        all_parts = []
        for p in range(self._total_pages):
            if p in self._page_buffer:
                all_parts.append(self._page_buffer[p])
            else:
                content = self._readPage(p)
                all_parts.append(content)
        full = '\n'.join(all_parts) if all_parts else ''
        self._original_content = full
        self.text_edit.blockSignals(True)
        self.text_edit.setPlainText(full)
        self.text_edit.blockSignals(False)
        self.text_edit.setReadOnly(False)
        self.text_edit.document().setModified(False)
        self._is_truncated = False
        self._page_buffer.clear()
        self._pagination_bar.setVisible(False)
        self.is_modified = False
        self.file_changed.emit(False)

    def _assembleContent(self) -> str:
        """合并各页内容为完整文件（用于保存时写出）"""
        if not self._is_truncated:
            return self.text_edit.toPlainText()
        # 保存当前页
        self._page_buffer[self._current_page] = self.text_edit.toPlainText()
        all_parts = []
        for p in range(self._total_pages):
            if p in self._page_buffer:
                all_parts.append(self._page_buffer[p])
            else:
                content = self._readPage(p)
                all_parts.append(content)
        return '\n'.join(all_parts) if all_parts else ''

    def stripEmptyLines(self):
        """去除空行"""
        content = self.text_edit.toPlainText()
        lines = content.split('\n')
        non_empty_lines = [line for line in lines if line.strip()]
        result = '\n'.join(non_empty_lines)
        self.text_edit.setPlainText(result)
        return len(lines) - len(non_empty_lines)

    def stripLeading(self):
        """去除行首空格"""
        content = self.text_edit.toPlainText()
        lines = content.split('\n')
        stripped_lines = [line.lstrip() for line in lines]
        self.text_edit.setPlainText('\n'.join(stripped_lines))

    def stripTrailing(self):
        """去除行尾空格"""
        content = self.text_edit.toPlainText()
        lines = content.split('\n')
        stripped_lines = [line.rstrip() for line in lines]
        self.text_edit.setPlainText('\n'.join(stripped_lines))

    def indentLines(self):
        """行首缩进（添加4个空格）"""
        content = self.text_edit.toPlainText()
        lines = content.split('\n')
        indented_lines = ['    ' + line for line in lines]
        self.text_edit.setPlainText('\n'.join(indented_lines))

    def _loadArcList(self, archive_path: str):
        """加载压缩包文件列表到编辑器"""
        items = listArchive(archive_path)
        if items:
            lines = [item["name"] for item in items]
            content = "\n".join(lines)
            self.setContent(content, emit_changed=False)


# 导入放在文件末尾避免循环依赖
from src.gui.widget import EditorTextEdit