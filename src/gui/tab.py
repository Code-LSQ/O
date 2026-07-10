import os
import re
import tarfile
import zipfile
import hashlib

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QLabel, QPushButton, QTextEdit, QMenu
from PySide6.QtCore import Qt, Signal, QEvent
from PySide6.QtGui import QPixmap, QTextCursor, QTextDocument, QAction, QImage

from src.file import FileOperation, pdfView, readEncoding
from src.util import logger, EXTENSION, messageBox, urlToPath, tr
from src.core.syntax import createHighlighter
from src.core.md import renderForView
from src.core.timer import LRUCache
from src.gui.view import ViewMode


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
        self._original_content = ""
        self.highlighter = None
        self.is_markdown = False
        self._markdown_cache = LRUCache(max_size=10)
        self.is_image = False
        self.setAcceptDrops(True)
        self._pending_file_path = None
        self._is_zip_gallery = False
        self._zip_image_paths = []
        self._tar_image_paths = []
        self._archive_type = None
        self._archive_current_image = None
        self._is_viewing_archive_image = False

        self._comic_view_enabled = False
        self._comic_container = None
        self._comic_layout = None
        self._comic_base_width = 800
        self._comic_zoom_factor = 1.0
        self._comic_images_data = []

        self._is_pdf = False
        self._pdf_page_count = 0
        self._pdf_pixmaps = []
        self._pdf_scroll = None
        self._pdf_gallery_container = None
        self._pdf_file_path = None
        self._pdf_plugin = None
        self._pdf_view = None
        self._pdf_document = None

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
        self.image_label = ImageLabel()
        self.image_label.setZoomCallback(lambda zf: self.window().statusBar().showMessage(tr("当前缩放") + f" {int(zf * 100)}%", 1500))
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_scroll.setWidget(self.image_label)
        self.image_scroll.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.image_scroll.customContextMenuRequested.connect(self._showCtxMenu)
        self.image_label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.image_label.customContextMenuRequested.connect(self._showLabelMenu)
        self.image_scroll.hide()
        layout.addWidget(self.image_scroll)

        self._gallery_widget = QWidget()
        gallery_layout = QVBoxLayout(self._gallery_widget)
        gallery_layout.setContentsMargins(0, 0, 0, 0)
        gallery_layout.setSpacing(0)
        
        self._gallery_toolbar = QWidget()
        self._gallery_toolbar.setFixedHeight(32)
        self._gallery_toolbar.setStyleSheet("background-color: #f0f0f0; border-bottom: 1px solid #cccccc;")
        gallery_toolbar_layout = QHBoxLayout(self._gallery_toolbar)
        gallery_toolbar_layout.setContentsMargins(5, 0, 5, 0)
        
        self._gallery_back_btn = QPushButton(tr("← 返回编辑器"))
        self._gallery_back_btn.setFixedWidth(120)
        self._gallery_back_btn.clicked.connect(self._exitGallery)
        self._gallery_back_btn.setStyleSheet("border: none; padding: 5px;")
        
        self._gallery_title_label = QLabel("ZIP " + tr("图库"))
        self._gallery_title_label.setStyleSheet("font-weight: bold; color: #333333;")
        
        gallery_toolbar_layout.addWidget(self._gallery_back_btn)
        gallery_toolbar_layout.addWidget(self._gallery_title_label)
        gallery_toolbar_layout.addStretch()
        
        gallery_layout.addWidget(self._gallery_toolbar)
        
        self._gallery_scroll = QScrollArea()
        self._gallery_scroll.setWidgetResizable(True)
        self._gallery_scroll.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._gallery_container = QWidget()
        self._gallery_container.setStyleSheet("background-color: #fafafa;")
        self._gallery_layout = QVBoxLayout(self._gallery_container)
        self._gallery_layout.setSpacing(10)
        self._gallery_layout.setContentsMargins(10, 10, 10, 10)
        self._gallery_scroll.setWidget(self._gallery_container)
        gallery_layout.addWidget(self._gallery_scroll)
        
        self._gallery_widget.hide()
        layout.addWidget(self._gallery_widget)

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
        logger.info(f">>> 双击选中: '{line_text}'")
        if line_text:
            name_lower = line_text.lower()
            if any(name_lower.endswith(ext) for ext in EXTENSION["IMAGE"]):
                logger.info(f">>> 触发加载图片: {line_text}")
                self._loadSingleImg(line_text)
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
                try:
                    file_size = os.path.getsize(path)
                except Exception:
                    file_size = 0
                
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
    
    def _isZipAllImg(self, zip_path: str) -> tuple:
        """检查ZIP文件是否只包含图片，返回(是否全是图片, 图片路径列表)"""
        try:
            image_paths = []
            with zipfile.ZipFile(zip_path, "r") as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    name = info.filename.lower()
                    is_image = any(name.endswith(ext) for ext in EXTENSION["IMAGE"])
                    if not is_image:
                        return False, []
                    image_paths.append(info.filename)
            return len(image_paths) > 0, sorted(image_paths, key=lambda x: self._sortKey(x))
        except Exception:
            logger.exception("检查ZIP文件失败")
            return False, []
    
    @staticmethod
    def _sortKey(path):
        """自然排序key：提取文件名中的数字用于排序"""
        basename = os.path.basename(path)
        parts = re.split(r'(\d+)', basename)
        return [int(p) if p.isdigit() else p for p in parts]
    
    def _loadGallery(self, image_paths: list, read_image: callable) -> bool:
        """加载图片图库（通用）"""
        for i in reversed(range(self._gallery_layout.count())):
            widget = self._gallery_layout.itemAt(i).widget()
            if widget:
                widget.deleteLater()

        for idx, img_name in enumerate(image_paths):
            try:
                img_data = read_image(img_name)
                image = QImage.fromData(img_data)
                if image.isNull():
                    continue
                pixmap = QPixmap.fromImage(image)

                img_widget = QLabel()
                img_widget.setPixmap(pixmap)
                img_widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
                img_widget.setStyleSheet("border: 1px solid #cccccc; padding: 5px; background-color: white;")
                img_widget.setMinimumHeight(50)
                img_widget.setMinimumWidth(100)
                img_widget.mousePressEvent = lambda e, path=img_name: self._onGalleryClick(path)
                img_widget.setCursor(Qt.CursorShape.PointingHandCursor)

                name_label = QLabel(f"{idx + 1}. {img_name}")
                name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                name_label.setStyleSheet("color: #666666; font-size: 11px; padding: 2px;")

                container = QWidget()
                container_layout = QVBoxLayout(container)
                container_layout.setContentsMargins(5, 5, 5, 5)
                container_layout.addWidget(name_label)
                container_layout.addWidget(img_widget)

                self._gallery_layout.addWidget(container)
            except Exception:
                logger.exception(f"加载图片失败 {img_name}")
                continue

        self.text_edit.hide()
        self.image_scroll.hide()
        self._gallery_widget.show()
        self._gallery_toolbar.setVisible(True)

        logger.info(f"图库已加载: {len(image_paths)} 张图片")
        return True

    def _loadZipGallery(self, zip_path: str):
        """加载ZIP文件为图片图库"""
        try:
            is_all_images, image_paths = self._isZipAllImg(zip_path)
            logger.info(f"ZIP检查结果: is_all_images={is_all_images}, image_count={len(image_paths)}")
            if not is_all_images:
                logger.warning(f"ZIP文件不是全部图片: {zip_path}")
                return False

            self._zip_image_paths = image_paths
            self._is_zip_gallery = True

            with zipfile.ZipFile(zip_path, "r") as zf:
                return self._loadGallery(image_paths, zf.read)
        except Exception:
            logger.exception("加载ZIP图库失败")
            return False
    
    def _exitGallery(self):
        """退出压缩包图库视图，返回文本编辑器"""
        self._gallery_widget.hide()
        self.image_scroll.hide()
        self.text_edit.show()
        self._is_zip_gallery = False
        
        self._zip_image_paths = []
        self._tar_image_paths = []
        self._archive_type = None
        self._comic_images_data.clear()
        
        for i in reversed(range(self._gallery_layout.count())):
            widget = self._gallery_layout.itemAt(i).widget()
            if widget:
                widget.deleteLater()
    
    def _loadTarGallery(self, tar_path: str):
        """加载TAR文件为图片图库"""
        try:
            image_paths = self._listTarImgs(tar_path)
            if not image_paths:
                logger.warning(f"TAR文件没有图片: {tar_path}")
                return False

            self._tar_image_paths = image_paths
            self._is_zip_gallery = True

            with tarfile.open(tar_path, 'r:*') as tf:
                return self._loadGallery(image_paths, lambda n: tf.extractfile(tf.getmember(n)).read())
        except Exception:
            logger.exception("加载TAR图库失败")
            return False
    
    def _listTarImgs(self, tar_path: str) -> list:
        """列出TAR文件中的图片"""
        try:
            image_paths = []
            with tarfile.open(tar_path, 'r:*') as tf:
                for member in tf.getmembers():
                    if member.isfile() and any(member.name.lower().endswith(ext) for ext in EXTENSION["IMAGE"]):
                        image_paths.append(member.name)
            return sorted(image_paths, key=lambda x: self._sortKey(x))
        except Exception:
            logger.exception("列出TAR图片失败")
            return []
    
    def _exitPdf(self):
        """清理 PDF 视图资源"""
        if not self._is_pdf:
            return

        self._is_pdf = False
        self._pdf_page_count = 0
        self._pdf_pixmaps = []
        self._pdf_file_path = None
        self._pdf_plugin = None

        if hasattr(self, '_pdf_view') and self._pdf_view:
            self._pdf_view.hide()
            self._pdf_view.deleteLater()
            self._pdf_view = None

        if hasattr(self, '_pdf_document') and self._pdf_document:
            self._pdf_document.close()
            self._pdf_document = None

        if self._pdf_scroll:
            self._pdf_scroll.hide()
            if self._pdf_scroll.widget():
                self._pdf_scroll.widget().deleteLater()
            self._pdf_scroll.deleteLater()
            self._pdf_scroll = None

        if self._pdf_gallery_container:
            self._pdf_gallery_container.deleteLater()
            self._pdf_gallery_container = None

        self.text_edit.show()
        self.image_scroll.hide()
        self.is_image = False
    
    def _onGalleryClick(self, img_name: str):
        """图库中点击图片，在中央放大显示"""
        self._archive_current_image = img_name
        self._is_viewing_archive_image = True

        if not self._zip_image_paths:
            is_all_images, image_paths = self._isZipAllImg(self.file_path)
            if is_all_images:
                self._zip_image_paths = image_paths

        try:
            with zipfile.ZipFile(self.file_path, "r") as zf:
                img_data = zf.read(img_name)
                image = QImage.fromData(img_data)
                if not image.isNull():
                    pixmap = QPixmap.fromImage(image)
                    self.image_label.setPixmap(pixmap)
                    self._gallery_widget.hide()
                    self.text_edit.hide()
                    self.image_scroll.show()
        except Exception:
            logger.exception("显示图片失败")

    def isImgFile(self, path: str) -> bool:
        """检查是否为图片文件"""
        if not path or not isinstance(path, str):
            return False
        try:
            ext = path.lower()
            return any(ext.endswith(img_ext) for img_ext in EXTENSION["IMAGE"])
        except (AttributeError, TypeError):
            return False
    
    def loadImage(self, file_path: str) -> bool:
        """加载并显示图片"""
        image = QImage(file_path)
        if image.isNull():
            return False
        pixmap = QPixmap.fromImage(image)
        if pixmap.isNull():
            return False

        self.image_label.setFilePath(file_path, self.image_scroll)
        self.image_label.setPixmap(pixmap)

        self.text_edit.hide()
        self.image_scroll.show()
        self.is_image = True
        self.text_edit.setLineNumbersVisible(False)
        return True

    
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

    def setViewMode(self, mode: str, emit_changed: bool = True):
        """设置查看模式"""
        if self.view_mode == mode:
            return
        
        old_mode = self.view_mode
        self.view_mode = mode
        
        if mode == ViewMode.TEXT:
            if self.is_image and self.file_path:
                self.loadImage(self.file_path)
                self._pagination_bar.setVisible(False)
            else:
                was_truncated = self._is_truncated
                self.setContent(self._original_content, emit_changed=emit_changed)
                if was_truncated:
                    self._is_truncated = True
                self.text_edit.show()
                self.image_scroll.hide()
                self._pagination_bar.setVisible(was_truncated)
                if was_truncated:
                    self.text_edit.setReadOnly(True)
            if not self._is_truncated:
                self.text_edit.setReadOnly(False)
            self.is_markdown = False
            self.markdown_mode_changed.emit(False)
        elif mode == ViewMode.MARKDOWN:
            if self.is_image:
                self.text_edit.show()
                self.image_scroll.hide()
            
            content_to_render = self._original_content
            
            mtime = ""
            if self.file_path:
                try:
                    mtime = str(os.path.getmtime(self.file_path))
                except OSError:
                    pass
            cache_key = hashlib.md5((content_to_render + (self.file_path or "") + mtime).encode()).hexdigest()
            
            html = self._markdown_cache.get(cache_key)
            if html is None:
                html, success = renderForView(content_to_render, self.file_path)
                if success:
                    self._markdown_cache.set(cache_key, html)
            else:
                success = True
            
            if success:
                try:
                    self.text_edit.setMarkdownHtml(html)
                except Exception:
                    self.setContent(content_to_render, emit_changed=emit_changed)
            else:
                self.setContent(content_to_render, emit_changed=emit_changed)
            self.text_edit.setReadOnly(True)
            self.is_markdown = True
            self.markdown_mode_changed.emit(True)
            self._pagination_bar.setVisible(False)
        elif mode == ViewMode.HEX:
            self.hexView()
            self.markdown_mode_changed.emit(False)
            self._pagination_bar.setVisible(False)
    
    def hexView(self):
        if not self.file_path:
            return
        try:
            self.image_scroll.hide()
            self.text_edit.show()
            with open(self.file_path, "rb") as f:
                data = f.read()
            display = ""
            for i in range(0, len(data), 16):
                chunk = data[i:i+16]
                hex_part = " ".join(f'{b:02X}' for b in chunk).ljust(48)
                ascii_part = "".join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
                display += f'{i:08X}  {hex_part}  {ascii_part}\n'
            self.text_edit.setPlainText(display)
            self.text_edit.setReadOnly(True)
        except Exception:
            logger.exception("显示十六进制失败")

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

        if self._is_zip_gallery:
            self._exitGallery()
        if self._is_viewing_archive_image:
            self._is_viewing_archive_image = False
            self._archive_current_image = None
            self.text_edit.show()
            self.image_scroll.hide()
        if self._is_pdf:
            self._exitPdf()
        if hasattr(self.image_label, '_comic_view_enabled') and self.image_label._comic_view_enabled:
            self.image_label._exitComicView()

        self.text_edit.show()
        self.image_scroll.hide()
        self._gallery_widget.hide()
        self.view_mode = ViewMode.TEXT

        self.is_markdown = False
        self._markdown_cache.clear()
        self._zip_image_paths = []
        self._tar_image_paths = []
        self._archive_current_image = None
        self._is_viewing_archive_image = False
        self._is_zip_gallery = False
        self._comic_images_data.clear()
        self._archive_type = None

        self.setFilePath(self.file_path)

        path_lower = self.file_path.lower()
        is_image = self.isImgFile(self.file_path)
        is_archive = any(path_lower.endswith(ext) for ext in (*EXTENSION["ZIP"], *EXTENSION["TAR"]))
        is_pdf = path_lower.endswith('.pdf')

        try:
            if is_image:
                self.loadImage(self.file_path)
            elif is_pdf:
                pdfView(self, self.file_path)
            elif is_archive:
                pass
            else:
                content, total_lines, loaded_lines, truncated, encoding = \
                    FileOperation().readFileLimit(
                        self.file_path, max_lines=50000, start_line=0)
                if content:
                    self._original_content = None
                    self.setContent(content, emit_changed=False)
                    self.encoding = encoding
                    self.clearTruncated()
                    if truncated > 0:
                        self.setTruncated(total_lines, loaded_lines,
                                           self.file_path, encoding)
                else:
                    content, encoding = readEncoding(self.file_path)
                    self._original_content = None
                    self.setContent(content, emit_changed=False)
                    self.encoding = encoding
        except Exception as e:
            messageBox(self, tr("重新加载失败"), tr("无法重新加载文件") + ": " + str(e), 1)
            return False

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
        content, total, loaded, truncated, _ = FileOperation().readFileLimit(
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

    def _loadArcImg(self, pos):
            """根据鼠标位置加载压缩包中的图片"""
            if not self._archive_type or not self.file_path:
                return
            cursor = self.text_edit.textCursor()
            cursor.select(cursor.SelectionType.BlockUnderCursor)
            line_text = cursor.selectedText().strip()
            if line_text:
                name_lower = line_text.lower()
                if any(name_lower.endswith(ext) for ext in EXTENSION["IMAGE"]):
                    self._loadSingleImg(line_text)
        
    def _loadSingleImg(self, member_name: str):
        """从压缩包中加载单张图片并显示在图片标签中"""
        if not member_name or not self._archive_type or not self.file_path:
            return
        content = FileOperation().readArchive(self.file_path, member_name)
        if content is None:
            return
        image = QImage.fromData(content)
        if image.isNull():
            return
        pixmap = QPixmap.fromImage(image)

        self.is_image = True
        self._archive_current_image = member_name

        self.image_label.setFilePath(member_name, self.image_scroll)
        self.image_label.setPixmap(pixmap)

        self.text_edit.setVisible(False)
        self.image_scroll.setVisible(True)
        self._gallery_toolbar.setVisible(True)
        self._gallery_title_label.setText(self._archive_type.upper() + " - " + member_name)
        self.update()

    def _loadArcList(self, archive_path: str):
        """加载压缩包文件列表到编辑器"""
        items = FileOperation().listArchive(archive_path)
        if items:
            lines = [item["name"] for item in items]
            content = "\n".join(lines)
            self.setContent(content, emit_changed=False)

    def _showImgMenu(self, pos, widget):
        """显示图片右键菜单"""
        menu = QMenu(self)
        action = QAction(tr("图库模式"), menu)
        if self._archive_type:
            action.triggered.connect(self._enterArcGallery)
        else:
            action.triggered.connect(self._enterFolderComic)
        menu.addAction(action)
        menu.exec(widget.mapToGlobal(pos))
    
    def _showLabelMenu(self, pos):
        self._showImgMenu(pos, self.image_label)
    
    def _showCtxMenu(self, pos):
        self._showImgMenu(pos, self.image_scroll)
    
    def _enterFolderComic(self):
        """进入普通文件夹图库模式"""
        self.image_label._enterComicView()

    def _enterArcGallery(self):
        """进入压缩包图库模式"""
        logger.info(f"=== _enterArcGallery called, archive_type={self._archive_type}")
        
        if not self._zip_image_paths and self._archive_type == 'zip':
            is_all_images, image_paths = self._isZipAllImg(self.file_path)
            if is_all_images:
                self._zip_image_paths = image_paths
        
        if not self._tar_image_paths and self._archive_type == 'tar':
            self._tar_image_paths = self._listTarImgs(self.file_path)
        
        images_data = []
        
        if self._archive_type == 'zip':
            try:
                with zipfile.ZipFile(self.file_path, "r") as zf:
                    for img_name in self._zip_image_paths:
                        try:
                            img_data = zf.read(img_name)
                            images_data.append((img_name, img_data))
                        except Exception:
                            logger.exception(f"读取图片失败 {img_name}")
            except Exception:
                logger.exception("打开 ZIP 失败")
                return

        elif self._archive_type == 'tar':
            try:
                with tarfile.open(self.file_path, 'r:*') as tf:
                    for img_name in self._tar_image_paths:
                        try:
                            member = tf.getmember(img_name)
                            if member.isfile():
                                f = tf.extractfile(member)
                                img_data = f.read()
                                images_data.append((img_name, img_data))
                        except Exception:
                            logger.exception(f"读取图片失败 {img_name}")
            except Exception:
                logger.exception("打开 TAR 失败")
        
        logger.info(f"=== loaded {len(images_data)} images")
        
        self.text_edit.hide()
        self._gallery_widget.hide()
        
        self.image_scroll.show()
        
        logger.info(f"=== calling set_archive_images, image_label={self.image_label}")
        self.image_label.setArchiveImages(images_data)
        self.image_label._toggleComicView()


# 导入放在文件末尾避免循环依赖
from src.gui.widget import EditorTextEdit, ImageLabel