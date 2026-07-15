import os
import re
import tarfile
import zipfile
import hashlib
import base64
from pathlib import Path

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget, QTextBrowser
from PySide6.QtCore import Qt, QTimer, QObject, QByteArray, QBuffer, QSize
from PySide6.QtGui import QPixmap, QImage, QImageReader, QTextCursor, QTextDocument

from src.util import logger, EXTENSION, tr, fileType, sortKey, ENCODING_MAP
from src.core.md import renderForView


class ViewMode:
    TEXT = "文本"
    MARKDOWN = "Markdown"
    HEX = "十六进制"
    IMAGE = "图像"
    GALLERY = "图库"
    PDF = "PDF"

    ALL = [TEXT, MARKDOWN, HEX, IMAGE, GALLERY, PDF]

    EXT_KEYS = {
        MARKDOWN: ("Markdown",),
        IMAGE: ("IMAGE",),
        GALLERY: ("ZIP", "TAR", "ARCHIVE", "IMAGE"),
        HEX: ("EXECUTE", "DISK"),
    }

    _HANDLERS = {}

    @classmethod
    def register(cls, mode, handler_cls):
        cls._HANDLERS[mode] = handler_cls

    @classmethod
    def openFile(cls, tab, file_path):
        mode = cls.detectInitial(file_path)
        tab.view_mode = mode
        tab._current_mode = mode
        tab.is_image = mode in (cls.IMAGE, cls.GALLERY, cls.PDF)
        tab.is_markdown = (mode == cls.MARKDOWN)
        handler = tab.getHandler(mode)
        if mode in (cls.TEXT, cls.MARKDOWN):
            content, total_lines, loaded_lines, truncated, encoding = \
                readFileLimit(file_path, max_lines=50000, start_line=0)
            tab.encoding = encoding
            if truncated:
                tab.setTruncated(total_lines, loaded_lines, file_path, encoding)
            handler.open(tab, content=content)
        elif mode == cls.HEX:
            handler.open(tab, file_path=file_path)
        else:
            handler.open(tab, file_path=file_path)
        return mode

    @classmethod
    def detectInitial(cls, file_path):
        path_lower = file_path.lower()
        if path_lower.endswith(".pdf"):
            return cls.PDF
        for ext in EXTENSION["IMAGE"]:
            if path_lower.endswith(ext):
                return cls.IMAGE
        for key in ("ZIP", "TAR"):
            if any(path_lower.endswith(ext) for ext in EXTENSION[key]):
                return cls.HEX
        for mode, keys in cls.EXT_KEYS.items():
            if mode in (cls.GALLERY, cls.MARKDOWN):
                continue
            for k in keys:
                if any(path_lower.endswith(ext) for ext in EXTENSION[k]):
                    return mode
        return cls.TEXT

    @classmethod
    def supportedModes(cls, file_path=None):
        modes = [cls.TEXT, cls.HEX]
        if not file_path:
            return modes
        path_lower = file_path.lower()
        for mode, keys in cls.EXT_KEYS.items():
            for k in keys:
                if any(path_lower.endswith(ext) for ext in EXTENSION[k]):
                    modes.append(mode)
                    break
        if path_lower.endswith(".pdf"):
            modes.append(cls.PDF)
        return modes

    @classmethod
    def getCurrent(cls, tab):
        return getattr(tab, "_current_mode", tab.view_mode)

    @classmethod
    def switchMode(cls, tab, mode):
        old = tab.view_mode
        if old == mode:
            return
        old_handler = tab.getHandler(old)
        new_handler = tab.getHandler(mode)
        old_handler.deactivate(tab)
        old_handler.close(tab)
        tab._current_mode = mode
        tab.view_mode = mode
        tab.is_image = mode in (cls.IMAGE, cls.GALLERY, cls.PDF)
        tab.is_markdown = (mode == cls.MARKDOWN)
        if mode in (cls.TEXT, cls.MARKDOWN) and not tab._original_content and tab.file_path:
            archive_type = tab._archive_type
            if archive_type:
                items = listArchive(tab.file_path)
                content = "\n".join(item["name"] for item in items) if items else ""
                tab.encoding = "UTF-8"
            else:
                content, total_lines, loaded_lines, truncated, encoding = \
                    readFileLimit(tab.file_path, max_lines=50000, start_line=0)
                tab.encoding = encoding
                if truncated:
                    tab.setTruncated(total_lines, loaded_lines, tab.file_path, encoding)
            new_handler.open(tab, content=content)
        else:
            new_handler.open(tab, tab.file_path, None)
        new_handler.activate(tab)
        tab.markdown_mode_changed.emit(mode == cls.MARKDOWN)

    @classmethod
    def reloadFile(cls, tab):
        old_cursor_pos = tab.text_edit.textCursor().position()
        scrollbar = tab.text_edit.verticalScrollBar()
        old_scroll_pos = scrollbar.value() if scrollbar else 0

        old_handler = tab.getHandler(tab._current_mode)
        old_handler.deactivate(tab)
        old_handler.close(tab)

        tab.text_edit.show()
        tab.image_scroll.hide()

        tab.view_mode = cls.TEXT
        tab._current_mode = cls.TEXT
        tab.is_markdown = False
        tab._markdown_cache.clear()
        tab._page_buffer.clear()
        gal = tab.getHandler(cls.GALLERY)
        gal.zip_image_paths = []
        gal.tar_image_paths = []
        gal.archive_current_image = None
        gal.is_viewing_archive_image = False
        tab._archive_type = None

        tab.setFilePath(tab.file_path)
        cls.openFile(tab, tab.file_path)

        new_handler = tab.getHandler(tab._current_mode)
        new_handler.activate(tab)

        try:
            cursor = tab.text_edit.textCursor()
            doc = tab.text_edit.document()
            if doc and old_cursor_pos <= doc.characterCount():
                cursor.setPosition(old_cursor_pos)
                tab.text_edit.setTextCursor(cursor)
        except Exception:
            logger.exception("重载时恢复光标位置失败")

        try:
            if scrollbar:
                scrollbar.setValue(min(old_scroll_pos, scrollbar.maximum()))
        except Exception:
            logger.exception("重载时恢复滚动条位置失败")


class TextMode:
    def open(self, tab, file_path=None, content=None):
        tab.image_scroll.hide()
        pdf_widget = tab.getHandler(ViewMode.PDF).pdf_widget
        if pdf_widget:
            pdf_widget.hide()
        tab.text_edit.show()
        tab._pagination_bar.setVisible(bool(getattr(tab, "_is_truncated", False)))
        if content is not None:
            tab.setContent(content)
        elif tab._original_content:
            original = tab._original_content
            tab._original_content = ""
            tab.setContent(original)
        tab.text_edit.setReadOnly(bool(getattr(tab, "_is_truncated", False)))

    def close(self, tab):
        pass

    def activate(self, tab):
        pass

    def deactivate(self, tab):
        pass


class MarkdownMode:
    def open(self, tab, file_path=None, content=None):
        tab.image_scroll.hide()
        tab.text_edit.show()
        tab._pagination_bar.setVisible(False)
        content_to_render = content if content is not None else tab._original_content
        tab.setContent(content_to_render)
        tab.text_edit.setReadOnly(False)

    def close(self, tab):
        tab._markdown_cache.clear()
        html_view = getattr(tab, '_html_view', None)
        if html_view:
            tab.layout().removeWidget(html_view)
            html_view.deleteLater()
            tab._html_view = None
        tab.text_edit.clear()

    def activate(self, tab):
        content_to_render = tab._original_content
        if not content_to_render:
            return
        mtime = ""
        if tab.file_path:
            try:
                mtime = str(os.path.getmtime(tab.file_path))
            except OSError:
                pass
        cache_key = hashlib.md5(
            (content_to_render + (tab.file_path or "") + mtime).encode()
        ).hexdigest()
        md_cache = tab._markdown_cache
        html = md_cache.get(cache_key)
        if html is None:
            html, success = renderForView(content_to_render, tab.file_path)
            if success:
                md_cache.set(cache_key, html)
        if html is not None:
            html_view = QTextBrowser()
            html_view.setFont(tab.text_edit.font())
            doc = html_view.document()
            processed = addImageResource(doc, html)
            doc.setHtml(processed)
            # setHtml 后光标在末尾，手动移到开头避免自动滚到底部
            cursor = html_view.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            html_view.setTextCursor(cursor)

            tab.text_edit.hide()
            tab.layout().insertWidget(0, html_view)
            tab._html_view = html_view
        else:
            tab.text_edit.setPlainText(content_to_render)
            tab.text_edit.setReadOnly(False)

    def deactivate(self, tab):
        tab._markdown_cache.clear()
        html_view = getattr(tab, '_html_view', None)
        if html_view:
            tab.layout().removeWidget(html_view)
            html_view.deleteLater()
            tab._html_view = None
        tab.text_edit.show()
        tab.text_edit.setReadOnly(False)


class HexMode:
    def open(self, tab, file_path=None, content=None):
        target = file_path or tab.file_path
        if not target:
            return
        tab.image_scroll.hide()
        tab.text_edit.show()
        tab._pagination_bar.setVisible(False)
        try:
            with open(target, "rb") as f:
                data = f.read()
            display = ""
            for i in range(0, len(data), 16):
                chunk = data[i:i+16]
                hex_part = " ".join(f"{b:02X}" for b in chunk).ljust(48)
                ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
                display += f"{i:08X}  {hex_part}  {ascii_part}\n"
            tab.text_edit.setPlainText(display)
            tab.text_edit.setReadOnly(True)
        except Exception:
            logger.exception("显示十六进制失败")

    def close(self, tab):
        pass

    def activate(self, tab):
        pass

    def deactivate(self, tab):
        pass


class ImageMode(QObject):
    def __init__(self):
        super().__init__()
        self._pixmap = None
        self._zoom_factor = 1.0
        self._min_zoom = 0.1
        self._max_zoom = 10.0
        self._label = None
        self._viewport_installed = False
        self._filter_tab = None

    def open(self, tab, file_path=None, content=None):
        target = file_path or tab.file_path
        if not target:
            return
        if self._label is None:
            self._label = QLabel()
            self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        image = QImage(target)
        if image.isNull():
            return
        pixmap = QPixmap.fromImage(image)
        if pixmap.isNull():
            return
        self._pixmap = pixmap
        self._zoom_factor = 1.0
        self._label.setPixmap(pixmap)
        tab.image_scroll.setWidget(self._label)
        if not self._viewport_installed:
            self._filter_tab = tab
            tab.image_scroll.viewport().installEventFilter(self)
            self._viewport_installed = True
        tab.text_edit.hide()
        tab.image_scroll.show()
        tab.text_edit.setLineNumbersVisible(False)
        tab.is_image = True

    def openArchiveImage(self, tab, archive_path, member_name):
        content = readArchive(archive_path, member_name)
        if content is None:
            return
        image = QImage.fromData(content)
        if image.isNull():
            return
        pixmap = QPixmap.fromImage(image)
        if pixmap.isNull():
            return
        if self._label is None:
            self._label = QLabel()
            self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pixmap = pixmap
        self._zoom_factor = 1.0
        self._label.setPixmap(pixmap)
        tab.image_scroll.setWidget(self._label)
        if not self._viewport_installed:
            self._filter_tab = tab
            tab.image_scroll.viewport().installEventFilter(self)
            self._viewport_installed = True
        tab.text_edit.hide()
        tab.image_scroll.show()

    def eventFilter(self, obj, event):
        if event.type() == event.Type.Wheel:
            modifiers = event.modifiers()
            if modifiers & Qt.KeyboardModifier.ControlModifier:
                delta = event.angleDelta().y()
                if delta > 0:
                    self._zoom_factor += 0.1
                elif delta < 0:
                    self._zoom_factor -= 0.1
                self._zoom_factor = max(self._min_zoom, min(self._max_zoom, self._zoom_factor))
                tab = self._filter_tab
                if tab:
                    self._applyZoom(tab)
                event.accept()
                return True
        return False

    def _applyZoom(self, tab):
        if self._pixmap and not self._pixmap.isNull():
            new_width = int(self._pixmap.width() * self._zoom_factor)
            new_height = int(self._pixmap.height() * self._zoom_factor)
            if new_width > 0 and new_height > 0:
                scaled = self._pixmap.scaled(
                    new_width, new_height,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
                self._label.setPixmap(scaled)
                w = tab.window()
                if hasattr(w, 'statusBar'):
                    w.statusBar().showMessage(tr("当前缩放") + f" {int(self._zoom_factor * 100)}%", 1500)

    def close(self, tab):
        if self._viewport_installed:
            tab.image_scroll.viewport().removeEventFilter(self)
            self._viewport_installed = False
            self._filter_tab = None
        self._pixmap = None
        self._zoom_factor = 1.0
        if self._label:
            self._label.clear()
            self._label.deleteLater()
            self._label = None

    def activate(self, tab):
        pass

    def deactivate(self, tab):
        pass


class GalleryMode(QObject):
    def __init__(self):
        super().__init__()
        self.archive_type = None
        self.zip_image_paths = []
        self.tar_image_paths = []
        self.archive_current_image = None
        self.is_viewing_archive_image = False

        self._gallery_view_enabled = False
        self._gallery_container = None
        self._gallery_layout = None
        self._gallery_base_width = 800
        self._archive_gallery = False
        self._archive_image_names = []
        self._archive_image_cache = {}
        self._archive_image_sizes = []
        self._archive_path = ""
        self._archive_gallery_layout = None
        self._archive_gallery_container = None
        self._archive_gallery_base_width = 800
        self._zoom_factor = 1.0
        self._gallery_items = []
        self._gallery_item_count = 0
        self._gallery_window_size = 2
        self._gallery_loaded = set()
        self._gallery_current_center = -1
        self._gallery_image_heights = []
        self._gallery_label_tops = []
        self._gallery_total_height = 0
        self._gallery_scroll_value = 0
        self._gallery_scrollbar = None
        self._filter_tab = None

    def open(self, tab, file_path=None, content=None):
        if self.archive_type:
            self._enterArcGallery(tab)
        else:
            self._enterFolderGallery(tab)

    def openFolderGallery(self, tab):
        self._enterFolderGallery(tab)

    def openArchiveGallery(self, tab, archive_path):
        self.archive_type = None
        if fileType(archive_path, "ZIP"):
            self.archive_type = "zip"
        elif fileType(archive_path, "TAR"):
            self.archive_type = "tar"
        self._enterArcGallery(tab)

    def _enterFolderGallery(self, tab):
        file_path = tab.file_path
        if not file_path or not os.path.exists(file_path):
            return
        folder = os.path.dirname(file_path)
        if not folder:
            return
        image_files = self._getFolderImages(folder)
        if not image_files:
            return
        scroll_area = tab.image_scroll
        avail_width = scroll_area.viewport().width() - 20
        if avail_width <= 0:
            avail_width = 800
        self._gallery_view_enabled = True
        self._gallery_base_width = avail_width
        self._zoom_factor = 1.0
        self._gallery_items = image_files
        self._gallery_item_count = len(image_files)
        self._gallery_loaded.clear()
        self._gallery_current_center = -1
        self._gallery_image_heights = []
        for img_path in image_files:
            reader = QImageReader(img_path)
            size = reader.size()
            if not size.isValid():
                size = QSize(800, 600)
            self._gallery_image_heights.append(self._calcHeight(size))
        self._gallery_label_tops = []
        cum_y = 0
        for h in self._gallery_image_heights:
            self._gallery_label_tops.append(cum_y)
            cum_y += h
        self._gallery_total_height = cum_y
        self._gallery_container = QWidget()
        self._gallery_container.setStyleSheet("background-color: white;")
        self._gallery_layout = QVBoxLayout(self._gallery_container)
        self._gallery_layout.setSpacing(0)
        self._gallery_layout.setContentsMargins(0, 0, 0, 0)
        self._gallery_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for i, img_path in enumerate(image_files):
            label = QLabel()
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setMinimumHeight(self._gallery_image_heights[i])
            label.mousePressEvent = lambda e, p=img_path: None
            self._gallery_layout.addWidget(label)
        self._gallery_container.setMinimumHeight(self._gallery_total_height)
        self._gallery_container.setCursor(Qt.CursorShape.ArrowCursor)
        scroll_area.setCursor(Qt.CursorShape.ArrowCursor)
        scroll_area.takeWidget()
        scroll_area.setWidget(self._gallery_container)
        self._gallery_container.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        scroll_area.viewport().installEventFilter(self)
        self._filter_tab = tab
        self._gallery_scrollbar = scroll_area.verticalScrollBar()
        self._gallery_scrollbar.valueChanged.connect(self._onGalleryScroll)
        QTimer.singleShot(0, self._updateGallery)
        tab.text_edit.hide()
        tab.image_scroll.show()

    def _enterArcGallery(self, tab):
        if self.archive_type == "zip" and not self.zip_image_paths:
            self.zip_image_paths = listArchiveImages(tab.file_path)
        if self.archive_type == "tar" and not self.tar_image_paths:
            self.tar_image_paths = listArchiveImages(tab.file_path)

        image_names = self.zip_image_paths if self.archive_type == "zip" else self.tar_image_paths
        if not image_names:
            return
        self._archive_path = tab.file_path
        self._archive_image_names = image_names
        self._archive_image_cache = {}
        self._archive_image_sizes = []
        for img_name in image_names:
            img_data = readArchive(tab.file_path, img_name)
            if img_data is not None:
                try:
                    ba = QByteArray(img_data)
                    buf = QBuffer(ba)
                    reader = QImageReader(buf)
                    size = reader.size()
                    buf.close()
                    if not size.isValid():
                        size = QSize(800, 600)
                except Exception:
                    size = QSize(800, 600)
                self._archive_image_sizes.append(size)
        if not self._archive_image_sizes:
            return
        self._archive_gallery = True
        scroll_area = tab.image_scroll
        avail_width = scroll_area.viewport().width() - 20
        if avail_width <= 0:
            avail_width = 800
        self._gallery_view_enabled = True
        self._gallery_base_width = avail_width
        self._zoom_factor = 1.0
        self._gallery_items = image_names
        self._gallery_item_count = len(image_names)
        self._gallery_loaded.clear()
        self._gallery_current_center = -1
        self._gallery_image_heights = []
        for size in self._archive_image_sizes:
            self._gallery_image_heights.append(self._calcHeight(size))
        self._gallery_label_tops = []
        cum_y = 0
        for h in self._gallery_image_heights:
            self._gallery_label_tops.append(cum_y)
            cum_y += h
        self._gallery_total_height = cum_y
        container = QWidget()
        container.setStyleSheet("background-color: white;")
        layout = QVBoxLayout(container)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for i, img_name in enumerate(image_names):
            label = QLabel()
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setMinimumHeight(self._gallery_image_heights[i])
            label.mousePressEvent = lambda e, p=img_name: None
            layout.addWidget(label)
        container.setMinimumHeight(self._gallery_total_height)
        self._gallery_container = container
        self._gallery_layout = layout
        self._archive_gallery_layout = layout
        self._archive_gallery_container = container
        self._archive_gallery_base_width = self._gallery_base_width
        QTimer.singleShot(0, lambda: self._setupGalleryContainer(tab, container))
        self._gallery_scrollbar = scroll_area.verticalScrollBar()
        self._gallery_scrollbar.valueChanged.connect(self._onGalleryScroll)
        QTimer.singleShot(0, self._updateGallery)
        tab.text_edit.hide()
        tab.image_scroll.show()

    def _setupGalleryContainer(self, tab, container):
        scroll_area = tab.image_scroll
        scroll_area.takeWidget()
        scroll_area.setWidget(container)
        container.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)

    def eventFilter(self, obj, event):
        if event.type() == event.Type.Wheel:
            modifiers = event.modifiers()
            if modifiers & Qt.KeyboardModifier.ControlModifier:
                delta = event.angleDelta().y()
                if delta > 0:
                    self._zoom_factor += 0.1
                elif delta < 0:
                    self._zoom_factor -= 0.1
                self._zoom_factor = max(0.1, min(10.0, self._zoom_factor))
                tab = self._filter_tab
                if tab:
                    self._refreshGallery(tab)
                    w = tab.window()
                    if hasattr(w, 'statusBar'):
                        w.statusBar().showMessage(tr("当前缩放") + f" {int(self._zoom_factor * 100)}%", 1500)
                event.accept()
                return True
        return False

    def _refreshGallery(self, tab):
        if not self._gallery_view_enabled:
            return
        if self._archive_gallery:
            self._refreshArchive(tab)
        elif self._gallery_container:
            self._refreshFolder(tab)

    def _refreshArchive(self, tab):
        if not self._archive_gallery or not self._archive_gallery_container:
            return
        layout = self._archive_gallery_layout
        if not layout:
            return
        base_width = self._gallery_base_width
        if base_width <= 0:
            base_width = 800
        for idx in list(self._gallery_loaded):
            self._unloadGallery(idx)
        self._gallery_current_center = -1
        self._gallery_image_heights = []
        for size in self._archive_image_sizes:
            self._gallery_image_heights.append(self._calcHeight(size))
        self._gallery_label_tops = []
        cum_y = 0
        for h in self._gallery_image_heights:
            self._gallery_label_tops.append(cum_y)
            cum_y += h
        self._gallery_total_height = cum_y
        for i in range(layout.count()):
            widget = layout.itemAt(i).widget()
            if widget and i < len(self._gallery_image_heights):
                widget.setMinimumHeight(self._gallery_image_heights[i])
        self._archive_gallery_container.setMinimumHeight(self._gallery_total_height)
        self._updateGallery()

    def _refreshFolder(self, tab):
        file_path = tab.file_path
        if not file_path or not os.path.exists(file_path):
            return
        folder = os.path.dirname(file_path)
        if not folder:
            return
        image_files = self._getFolderImages(folder)
        if not image_files:
            return
        base_width = self._gallery_base_width
        if base_width <= 0:
            base_width = 800
        for idx in list(self._gallery_loaded):
            self._unloadGallery(idx)
        self._gallery_current_center = -1
        self._gallery_image_heights = []
        for img_path in image_files:
            reader = QImageReader(img_path)
            size = reader.size()
            if not size.isValid():
                size = QSize(800, 600)
            self._gallery_image_heights.append(self._calcHeight(size))
        self._gallery_label_tops = []
        cum_y = 0
        for h in self._gallery_image_heights:
            self._gallery_label_tops.append(cum_y)
            cum_y += h
        self._gallery_total_height = cum_y
        for i in range(self._gallery_layout.count()):
            widget = self._gallery_layout.itemAt(i).widget()
            if widget and i < len(self._gallery_image_heights):
                widget.setMinimumHeight(self._gallery_image_heights[i])
        self._gallery_container.setMinimumHeight(self._gallery_total_height)
        self._updateGallery()

    def _exitGalleryView(self, tab):
        self._gallery_view_enabled = False
        self._archive_gallery = False
        if self._gallery_scrollbar:
            try:
                self._gallery_scrollbar.valueChanged.disconnect(self._onGalleryScroll)
            except (TypeError, RuntimeError):
                pass
            self._gallery_scrollbar = None
        self._gallery_items = []
        self._gallery_item_count = 0
        self._gallery_loaded.clear()
        self._gallery_current_center = -1
        self._gallery_image_heights.clear()
        self._gallery_label_tops.clear()
        self._gallery_total_height = 0
        self._gallery_scroll_value = 0
        if self._gallery_layout:
            while self._gallery_layout.count() > 0:
                item = self._gallery_layout.takeAt(0)
                if item and item.widget():
                    item.widget().deleteLater()
        if self._gallery_container:
            scroll_area = tab.image_scroll
            scroll_area.takeWidget()
            scroll_area.setCursor(Qt.CursorShape.ArrowCursor)
            self._gallery_container.deleteLater()
            self._gallery_container = None
            self._gallery_layout = None
            self._archive_gallery_container = None
            self._archive_gallery_layout = None
        self._archive_image_names = []
        self._archive_image_cache = {}
        self._archive_image_sizes = []
        self._archive_path = ""

    def _calcHeight(self, img_size):
        if img_size.width() <= 0:
            return 100
        base_width = self._gallery_base_width
        scale = base_width * self._zoom_factor / img_size.width()
        return max(1, int(img_size.height() * scale))

    def _loadGallery(self, idx):
        try:
            label = self._gallery_layout.itemAt(idx).widget()
            if not label:
                return
            item = self._gallery_items[idx]
            if self._archive_gallery:
                img_data = self._archive_image_cache.get(idx)
                if img_data is None:
                    img_data = readArchive(self._archive_path, item)
                    if img_data is None:
                        return
                    self._archive_image_cache[idx] = img_data
                image = QImage.fromData(img_data)
            elif isinstance(item, str):
                image = QImage(item)
            else:
                image = QImage.fromData(item[1])
            if image.isNull():
                return
            pixmap = QPixmap.fromImage(image)
            base_width = self._gallery_base_width
            width = int(base_width * self._zoom_factor)
            if pixmap.width() > width:
                scaled = pixmap.scaled(
                    width,
                    int(pixmap.height() * width / pixmap.width()),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
            else:
                scaled = pixmap
            label.setPixmap(scaled)
            self._gallery_loaded.add(idx)
        except Exception:
            logger.exception(f"懒加载图片失败 index={idx}")

    def _unloadGallery(self, idx):
        try:
            label = self._gallery_layout.itemAt(idx).widget()
            if label:
                label.clear()
            self._gallery_loaded.discard(idx)
        except Exception:
            logger.exception(f"卸载图片失败 index={idx}")

    def _updateGallery(self):
        if not self._gallery_view_enabled or self._gallery_item_count == 0:
            return
        scrollbar = self._gallery_scrollbar
        if not scrollbar:
            return
        scroll_value = scrollbar.value()
        viewport_height = scrollbar.parent().height() if scrollbar.parent() else 600
        center_y = scroll_value + viewport_height // 2
        tops = self._gallery_label_tops
        lo, hi = 0, len(tops)
        while lo < hi:
            mid = (lo + hi) // 2
            if tops[mid] <= center_y:
                lo = mid + 1
            else:
                hi = mid
        center_idx = max(0, lo - 1)
        if center_idx == self._gallery_current_center and self._gallery_loaded:
            return
        self._gallery_current_center = center_idx
        window_start = max(0, center_idx - self._gallery_window_size)
        window_end = min(self._gallery_item_count - 1, center_idx + self._gallery_window_size)
        for idx in list(self._gallery_loaded):
            if idx < window_start or idx > window_end:
                self._unloadGallery(idx)
                if self._archive_gallery:
                    self._archive_image_cache.pop(idx, None)
        for idx in range(window_start, window_end + 1):
            if idx not in self._gallery_loaded:
                self._loadGallery(idx)

    def _onGalleryScroll(self, value):
        self._gallery_scroll_value = value
        self._updateGallery()

    def _getFolderImages(self, folder):
        try:
            image_files = []
            for f in os.listdir(folder):
                fpath = os.path.join(folder, f)
                if os.path.isfile(fpath) and fileType(fpath, "IMAGE"):
                    image_files.append(fpath)
            return sorted(image_files, key=lambda x: sortKey(x))
        except Exception:
            logger.exception("获取文件夹图片失败")
            return []

    def close(self, tab):
        if self._filter_tab:
            tab.image_scroll.viewport().removeEventFilter(self)
            self._filter_tab = None
        if self._gallery_view_enabled:
            self._exitGalleryView(tab)
        self.archive_type = None
        self.zip_image_paths = []
        self.tar_image_paths = []
        self.archive_current_image = None
        self.is_viewing_archive_image = False

    def activate(self, tab):
        pass

    def deactivate(self, tab):
        pass


class PdfMode:
    def __init__(self):
        self.pdf_widget = None
        self.pdf_document = None
        self.pdf_pixmaps = []

    def open(self, tab, file_path=None, content=None):
        target = file_path or tab.file_path
        if not target:
            return

        from PySide6.QtPdf import QPdfDocument
        from PySide6.QtPdfWidgets import QPdfView

        class _PdfViewer(QPdfView):
            def __init__(self, parent=None):
                super().__init__(parent)
                self._zoom_factor = 1.0
                self.setPageMode(QPdfView.PageMode.MultiPage)
                self.setZoomMode(QPdfView.ZoomMode.FitInView)
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

        pdf_document = QPdfDocument()
        pdf_document.load(target)
        if pdf_document.error() != QPdfDocument.Error.None_:
            return

        pdf_view = _PdfViewer()
        pdf_view.setDocument(pdf_document)

        tab.text_edit.hide()
        tab.image_scroll.hide()

        self.pdf_widget = pdf_view
        self.pdf_document = pdf_document
        tab.layout().addWidget(pdf_view)
        tab.text_edit.setLineNumbersVisible(False)

    def close(self, tab):
        if self.pdf_widget:
            self.pdf_widget.deleteLater()
            self.pdf_widget = None
        if self.pdf_document:
            self.pdf_document.close()
            self.pdf_document = None
        self.pdf_pixmaps.clear()

    def activate(self, tab):
        pass

    def deactivate(self, tab):
        if self.pdf_widget:
            self.pdf_widget.hide()


ViewMode.register(ViewMode.TEXT, TextMode)
ViewMode.register(ViewMode.MARKDOWN, MarkdownMode)
ViewMode.register(ViewMode.HEX, HexMode)
ViewMode.register(ViewMode.IMAGE, ImageMode)
ViewMode.register(ViewMode.GALLERY, GalleryMode)
ViewMode.register(ViewMode.PDF, PdfMode)


def _listZipEntries(file_path) -> list:
    """列出 zip 文件内部条目"""
    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            items = []
            for info in zf.infolist():
                items.append({
                    "name": info.filename,
                    "is_dir": info.is_dir(),
                    "size": info.file_size,
                })
            return items
    except Exception:
        logger.exception("读取 ZIP 文件失败")
        return None


def _listTarEntries(file_path) -> list:
    """列出 tar 文件内部条目"""
    try:
        with tarfile.open(file_path, 'r:*') as tf:
            items = []
            for member in tf.getmembers():
                items.append({
                    "name": member.name,
                    "is_dir": member.isdir(),
                    "size": member.size,
                })
            return items
    except Exception:
        logger.exception("读取 TAR 文件失败")
        return None


def listArchive(file_path: str) -> list:
    """列出压缩包内部的文件和文件夹"""
    file_path_obj = Path(file_path)
    if not file_path_obj.exists():
        return None
    if fileType(file_path, "ZIP"):
        return _listZipEntries(file_path_obj)
    elif fileType(file_path, "TAR"):
        return _listTarEntries(file_path_obj)
    return None


def readArchive(file_path: str, member_name: str) -> bytes:
    """读取压缩包内指定文件的内容"""
    file_path_obj = Path(file_path)
    if not file_path_obj.exists():
        return None
    try:
        if fileType(file_path, "ZIP"):
            with zipfile.ZipFile(file_path_obj, "r") as zf:
                return zf.read(member_name)
        elif fileType(file_path, "TAR"):
            with tarfile.open(file_path_obj, 'r:*') as tf:
                member = tf.getmember(member_name)
                if member.isfile():
                    f = tf.extractfile(member)
                    return f.read()
    except Exception:
        logger.exception("读取压缩包内文件失败")
    return None


def listArchiveImages(file_path: str) -> list:
    """列出压缩包中的所有图片路径（按文件名自然排序）"""
    try:
        if fileType(file_path, "ZIP"):
            image_paths = []
            with zipfile.ZipFile(file_path, "r") as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    name = info.filename
                    if fileType(name, "IMAGE"):
                        image_paths.append(name)
            return sorted(image_paths, key=sortKey)
        elif fileType(file_path, "TAR"):
            image_paths = []
            with tarfile.open(file_path, 'r:*') as tf:
                for member in tf.getmembers():
                    if member.isfile() and fileType(member.name, "IMAGE"):
                        image_paths.append(member.name)
            return sorted(image_paths, key=sortKey)
    except Exception:
        logger.exception("列出压缩包图片失败")
    return []



def readFileLimit(file_path: str, max_lines: int = 50000, start_line: int = 0, encoding: str = None):
    """读取文件，带行数限制，支持跳过行数（用于翻页）
    Args:
        file_path: 文件路径
        max_lines: 最多读取行数
        start_line: 起始行号（0-based，跳过前 start_line 行）
        encoding: 指定编码（不为 None 时跳过自动检测）
    
    Returns:
            形式为 [str, int, int, int, str]
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
            SUPPORTED_ENCODINGS = list(ENCODING_MAP.values())
            encodings_to_try = ["utf-8"] + SUPPORTED_ENCODINGS
        for enc in encodings_to_try:
            try:
                lines = []
                total = 0
                with open(_path, "r", encoding=enc, newline="") as f:
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
                    with open(_path, "r", encoding=encoding, errors="replace", newline="") as f:
                        lines = [line.rstrip('\n').rstrip('\r') for line in f]
                    total = len(lines)
                    return '\n'.join(lines), total, total, 0, encoding
                continue
        lines = []
        total = 0
        with open(_path, "r", encoding="utf-8", errors="replace", newline="") as f:
            for line in f:
                if total >= start_line and len(lines) < max_lines:
                    lines.append(line.rstrip('\n').rstrip('\r'))
                total += 1
        loaded = len(lines)
        truncated = 1 if total > start_line + loaded else 0
        return '\n'.join(lines), total, loaded, truncated, "utf-8"
    except Exception:
        logger.exception("带限制读取文件失败")
        return "", 0, 0, -1, ""


def _base64ToImage(b64_data, mime_type):
    try:
        data = base64.b64decode(b64_data)
        byte_array = QByteArray(data)
        image = QImage()
        if image.loadFromData(byte_array):
            return image
        format_str = mime_type.replace('image/', '').upper()
        if format_str == 'SVG+XML':
            format_str = 'SVG'
        if format_str and image.loadFromData(byte_array, format_str):
            return image
        logger.warning(f"QImage 加载失败，mime_type: {mime_type}, data_len: {len(data)}")
        return QImage()
    except Exception:
        logger.exception("图片转换失败")
        return QImage()


def addImageResource(doc, html):
    """处理HTML中的base64图片，添加为QTextDocument资源，返回替换后的HTML"""
    max_pixels = 2000
    max_b64_len = 16 * 1024 * 1024
    qt_resource_type = getattr(QTextDocument.ResourceType, 'ImageResource', 3)
    img_pattern = r'<img\s+([^>]*?)>'
    result = []
    last_end = 0

    for m in re.finditer(img_pattern, html):
        result.append(html[last_end:m.start()])
        attrs = m.group(1)

        src_match = re.search(r'src\s*=\s*(["\'])(data:image/[^;]+;base64,[^"\'>]+)\1', attrs)
        if not src_match:
            result.append(m.group(0))
            last_end = m.end()
            continue

        src = src_match.group(2)
        mime_match = re.match(r'data:(image/[^;]+);base64,', src)
        mime_type = mime_match.group(1) if mime_match else 'image/png'
        b64_data = src.split(',')[1]

        if len(b64_data) > max_b64_len:
            logger.warning(f"跳过超大base64图片({len(b64_data)} bytes)")
            result.append(m.group(0))
            last_end = m.end()
            continue

        resource_name = f"image:{hashlib.md5(src.encode()).hexdigest()}"

        if doc.resource(qt_resource_type, resource_name):
            new_attrs = re.sub(r'src\s*=\s*(["\'])[^"\']+\1', '', attrs).strip()
            result.append(f'<img src="{resource_name}"' + (f' {new_attrs}' if new_attrs else ''))
            last_end = m.end()
            continue

        try:
            image = _base64ToImage(b64_data, mime_type)
            if image.isNull():
                result.append(m.group(0))
                last_end = m.end()
                continue
            if image.width() > max_pixels or image.height() > max_pixels:
                image = image.scaled(
                    max_pixels, max_pixels,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
            doc.addResource(qt_resource_type, resource_name, QPixmap.fromImage(image))
        except Exception:
            result.append(m.group(0))
            last_end = m.end()
            continue

        new_attrs = re.sub(r'src\s*=\s*(["\'])[^"\']+\1', '', attrs).strip()
        result.append(f'<img src="{resource_name}"' + (f' {new_attrs}' if new_attrs else ''))
        last_end = m.end()

    result.append(html[last_end:])
    return ''.join(result)

