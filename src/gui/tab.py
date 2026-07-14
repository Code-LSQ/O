import os
import re
import base64
import hashlib
import webbrowser
from urllib import parse

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QLabel, QPushButton, QTextEdit, QMenu
from PySide6.QtCore import Qt, Signal, QEvent, QRect, QByteArray
from PySide6.QtGui import QTextCursor, QTextDocument, QPainter, QColor, QTextCharFormat, QAction, QKeySequence, QPixmap, QImage, QPalette, qGray

from src.config import getConfig, DEFAULT_CONFIG
from src.util import logger, EXTENSION, messageBox, urlToPath, tr, inputDialog
from src.core.syntax import createHighlighter
from src.core.timer import LRUCache
from src.gui.view import ViewMode, listArchive, readFileLimit


_LINE_ENDING_RE = re.compile(r'\r\n|\r')


class EditorTextEdit(QTextEdit):
    """带行号显示的文本编辑器"""
    cursor_position_changed = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.line_number_area = self.LineNumberArea(self)
        self.line_number_area.setObjectName("line_number_area")
        self.line_number_area.setAutoFillBackground(True)
        self.line_numbers_visible = False
        self._cached_line_number_width = 0
        self._cached_block_count = 0

        doc = self.document()
        doc.blockCountChanged.connect(self._onBlockChange)
        doc.contentsChanged.connect(self._onContentsChanged)
        self.cursorPositionChanged.connect(self._onCursorChanged)

        self._updateLineNumWidth(0)
        self.highlightLine()
        
        self._zoom_factor = 1.0
        self._min_zoom = 0.5
        self._max_zoom = 3.0
        self._base_font_size = 10
        self._zoom_callback = None

        self._multi_cursors = []
        self._multi_cursor_active = False
        self._multi_base_text = ""
        self._auto_indent_enabled = True

        self._shortcut_actions = {}
        self._reloadShortcuts()

    def setZoomCallback(self, callback):
        self._zoom_callback = callback
    
    def _onBlockChange(self, new_count: int):
        """块数量变化时更新缓存"""
        if new_count != self._cached_block_count:
            self._cached_block_count = new_count
            self._updateLineNumWidth(0)
            self.line_number_area.update()

    def _onContentsChanged(self):
        """内容改变时更新行号区域（仅边距，不触发行号重绘）"""
        if self.line_numbers_visible:
            self._updateLineNumWidth(0)

    def wheelEvent(self, event):
        modifiers = event.modifiers()
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                self.zoomIn()
            elif delta < 0:
                self.zoomOut()
            event.accept()
            return
        super().wheelEvent(event)
    
    def _scrollToAnchor(self, anchor: str):
        cursor = self.textCursor()
        doc = self.document()
        if not doc:
            return

        cursor.movePosition(QTextCursor.MoveOperation.Start)
        pattern = f'id="{anchor}"'
        found = doc.find(pattern, cursor)
        if not found.isNull():
            self.setTextCursor(found)
            self.ensureCursorVisible()
            return

        cursor.movePosition(QTextCursor.MoveOperation.Start)
        pattern = f'name="{anchor}"'
        found = doc.find(pattern, cursor)
        if not found.isNull():
            self.setTextCursor(found)
            self.ensureCursorVisible()
    
    def zoomIn(self):
        """放大字体"""
        if self._zoom_factor < self._max_zoom:
            self._zoom_factor += 0.1
            self._applyZoom()
            if self._zoom_callback:
                self._zoom_callback(self._zoom_factor)

    def zoomOut(self):
        """缩小字体"""
        if self._zoom_factor > self._min_zoom:
            self._zoom_factor -= 0.1
            self._applyZoom()
            if self._zoom_callback:
                self._zoom_callback(self._zoom_factor)

    def _applyZoom(self):
        """应用缩放"""
        font = self.font()
        current_size = font.pointSize()
        if current_size <= 0:
            current_size = font.pixelSize()
        if current_size <= 0:
            current_size = 10
        if self._base_font_size == 10:
            self._base_font_size = current_size
        new_size = int(self._base_font_size * self._zoom_factor)
        new_size = max(1, new_size)
        font.setPointSize(new_size)
        self.setFont(font)
        self._updateLineNumWidth(0)

    def setHtml(self, text: str):
        """重写setHtml以支持markdown渲染"""
        super().setHtml(text)
    
    def setMarkdownHtml(self, html: str):
        """设置markdown渲染的HTML内容"""
        if not html:
            return
        try:
            doc = self.document()
            if doc:
                cursor = self.textCursor()
                cursor.setPosition(0)
                self.setTextCursor(cursor)
            
            img_pattern = r'<img\s+([^>]*?)>'
            _MAX_IMAGE_PIXELS = 2000
            _MAX_BASE64_LEN = 3 * 1024 * 1024

            def addImageResource(match):
                attrs = match.group(1)
                src_match = re.search(r'src\s*=\s*(["\'])(data:image/[^;]+;base64,[^"\'>]+)\1', attrs)
                if not src_match:
                    return match.group(0)
                src = src_match.group(2)
                mime_match = re.match(r'data:(image/[^;]+);base64,', src)
                mime_type = mime_match.group(1) if mime_match else 'image/png'
                b64_data = src.split(',')[1]

                if len(b64_data) > _MAX_BASE64_LEN:
                    logger.warning(f"跳过超大base64图片({len(b64_data)} bytes)")
                    return match.group(0)

                resource_name = f"image:{hashlib.md5(src.encode()).hexdigest()}"
                qt_resource_type = getattr(QTextDocument.ResourceType, 'ImageResource', 3)

                if doc.resource(qt_resource_type, resource_name):
                    new_attrs = re.sub(r'src\s*=\s*(["\'])[^"\']+\1', '', attrs).strip()
                    return f'<img src="{resource_name}"' + (f' {new_attrs}' if new_attrs else '')

                try:
                    image = self._base64ToImage(b64_data, mime_type)
                    if image.isNull():
                        return match.group(0)
                    if image.width() > _MAX_IMAGE_PIXELS or image.height() > _MAX_IMAGE_PIXELS:
                        image = image.scaled(
                            _MAX_IMAGE_PIXELS, _MAX_IMAGE_PIXELS,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation
                        )
                    doc.addResource(qt_resource_type, resource_name, QPixmap.fromImage(image))
                except Exception:
                    return match.group(0)
                new_attrs = re.sub(r'src\s*=\s*(["\'])[^"\']+\1', '', attrs).strip()
                return f'<img src="{resource_name}"' + (f' {new_attrs}' if new_attrs else '')

            html = re.sub(img_pattern, addImageResource, html)
            super().setHtml(html)
            
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        except Exception:
            logger.exception("设置Markdown HTML失败")
    
    def _base64ToImage(self, base64_data: str, mime_type: str) -> QImage:
        """将base64数据转换为QImage"""
        try:
            data = base64.b64decode(base64_data)
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
    
    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Tab:
            cursor = self.textCursor()
            if cursor.hasSelection():
                start = cursor.selectionStart()
                cursor.setPosition(start)
                cursor.movePosition(cursor.MoveOperation.StartOfLine)
                new_start = cursor.position()
                end = cursor.selectionEnd()
                cursor.setPosition(end, cursor.MoveMode.KeepAnchor)
                cursor.movePosition(cursor.MoveOperation.EndOfLine, cursor.MoveMode.KeepAnchor)
                end = cursor.position()
                lines_text = self.document().toPlainText()[new_start:end]
                lines = lines_text.split('\n')
                cursor.setPosition(new_start)
                cursor.setPosition(end, cursor.MoveMode.KeepAnchor)
                cursor.insertText('\n'.join('    ' + line for line in lines))
            else:
                cursor.insertText("    ")
            event.accept()
            return

        key = event.key()
        mods = event.modifiers()
        # logger.info(f"keyPressEvent: key={key} mods={mods} text='{event.text()}' multi_active={self._multi_cursor_active}")

        mods_val = mods.value if hasattr(mods, 'value') else (mods if isinstance(mods, int) else 0)
        event_seq = QKeySequence(key | mods_val)
        for name, seq in self._shortcut_seqs.items():
            if seq == event_seq:
                # logger.info(f"匹配快捷键 '{name}'")
                handler = self._shortcut_handlers.get(name)
                if handler:
                    handler()
                event.accept()
                return

        if self._multi_cursor_active and event.key() in (
            Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Backspace, Qt.Key.Key_Delete,
            Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down,
            Qt.Key.Key_Home, Qt.Key.Key_End, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown,
        ):
            self._cancelMultiCursor()

        if self._multi_cursor_active and event.text():
            mods = event.modifiers()
            if mods in (Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.ShiftModifier):
                self._applyKeyToCursors(event.text())
                event.accept()
                return

        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._handleEnterKey()
            event.accept()
            return

        if not self._shortcut_actions:
            main = self.window()
            if main:
                if hasattr(main, 'action_undo'):
                    self._shortcut_actions[main.action_undo] = self.undo

        for action, handler in self._shortcut_actions.items():
            for sc in action.shortcuts():
                if sc == event_seq:
                    handler()
                    event.accept()
                    return

        super().keyPressEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            cursor = self.cursorForPosition(event.pos())
            if cursor:
                fmt = cursor.charFormat()
                if fmt.isAnchor():
                    href = fmt.anchorHref()
                    if href and href.startswith('#'):
                        self._scrollToAnchor(href[1:])
                        event.accept()
                        return
            if event.modifiers() & Qt.KeyboardModifier.AltModifier:
                self._addAltClickCursor(event.pos())
                event.accept()
                return
            if self._multi_cursor_active:
                self._cancelMultiCursor()
                self.viewport().update()
            super().mousePressEvent(event)
        else:
            super().mousePressEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.ignore()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            event.ignore()
        else:
            super().dropEvent(event)

    def _onCursorChanged(self):
        if self._multi_cursor_active:
            self._updateCursors()
        else:
            self.highlightLine()
        self._checkBracket()
        self._emitCursorPos()

    def _emitCursorPos(self):
        cursor = self.textCursor()
        line = cursor.blockNumber() + 1
        col = cursor.columnNumber() + 1
        self.cursor_position_changed.emit(line, col)

    def _reloadShortcuts(self):
        config = getConfig()
        saved = config.get("Edit.shortcuts", {})
        default_shortcuts = DEFAULT_CONFIG["Edit"]["shortcuts"]
        self._shortcut_seqs = {}
        self._shortcut_handlers = {
            "goToLine": self.goToLine,
            "jump_next": self._jumpNext,
        }
        for name in self._shortcut_handlers:
            s = saved.get(name, default_shortcuts.get(name, ""))
            if s:
                self._shortcut_seqs[name] = QKeySequence(s)

        self._shortcut_actions.clear()
        main = self.window()
        if main:
            if hasattr(main, 'action_undo'):
                self._shortcut_actions[main.action_undo] = self.undo

    def goToLine(self):
        text = inputDialog(self, tr("跳转到行"), tr("行号"), default="1")
        if text:
            cursor = self.textCursor()
            block = self.document().findBlockByNumber(int(text) - 1)
            if block:
                cursor.setPosition(block.position())
                self.setTextCursor(cursor)
                self.ensureCursorVisible()

    def _handleEnterKey(self):
        cursor = self.textCursor()
        if cursor.hasSelection():
            cursor.removeSelectedText()
        if self._auto_indent_enabled:
            block = cursor.block()
            text = block.text()
            indent = re.match(r'^(\s*)', text).group(1)
            cursor.insertText('\n' + indent)
        else:
            cursor.insertText('\n')

    def setAutoIndent(self, enabled: bool):
        self._auto_indent_enabled = enabled

    def _jumpNext(self):
        cursor = self.textCursor()
        if not self._multi_cursor_active and not cursor.hasSelection():
            cursor.select(QTextCursor.SelectionType.WordUnderCursor)
            self.setTextCursor(cursor)
            self._multi_base_text = cursor.selectedText()
            if not self._multi_base_text:
                return
            self._multi_cursors = [(cursor.selectionStart(), cursor.selectionEnd())]
            self._multi_cursor_active = True
        else:
            if cursor.hasSelection() and cursor.selectedText() != self._multi_base_text:
                self._cancelMultiCursor()
                self._multi_base_text = cursor.selectedText()
                self._multi_cursors = [(cursor.selectionStart(), cursor.selectionEnd())]
                self._multi_cursor_active = True
                self._updateCursors()
                return
            if not self._multi_base_text:
                return
            last_end = self._multi_cursors[-1][1]
            fc = QTextCursor(self.document())
            fc.setPosition(last_end)
            result = self.document().find(self._multi_base_text, fc)
            if not result.selectedText() or result.selectionStart() <= last_end:
                fc2 = QTextCursor(self.document())
                fc2.movePosition(QTextCursor.MoveOperation.Start)
                result = self.document().find(self._multi_base_text, fc2)
                if not result.selectedText():
                    return
                if result.selectionStart() == self._multi_cursors[0][0]:
                    self.setTextCursor(result)
                    return
            self._multi_cursors.append((result.selectionStart(), result.selectionEnd()))
            self.setTextCursor(result)
        self._updateCursors()

    def _addAltClickCursor(self, pos):
        click_pos = self.cursorForPosition(pos).position()
        if not self._multi_cursor_active:
            cur = self.textCursor()
            if cur.hasSelection():
                self._multi_cursors = [(cur.selectionStart(), cur.selectionEnd())]
            else:
                self._multi_cursors = [(cur.position(), cur.position())]
            self._multi_cursor_active = True
        self._multi_cursors.append((click_pos, click_pos))
        c = QTextCursor(self.document())
        c.setPosition(click_pos)
        self.setTextCursor(c)
        self._updateCursors()

    def _applyKeyToCursors(self, text):
        doc = self.document()
        sorted_cursors = sorted(self._multi_cursors, key=lambda x: x[0], reverse=True)
        for start, end in sorted_cursors:
            c = QTextCursor(doc)
            c.setPosition(start)
            c.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            c.insertText(text)
        offset = 0
        new_cursors = []
        for s, _ in sorted(self._multi_cursors, key=lambda x: x[0]):
            offset += len(text)
            new_cursors.append((s + offset, s + offset))
        self._multi_cursors = new_cursors
        self._multi_base_text = ""
        self._updateCursors()

    def _updateCursors(self):
        self.viewport().update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._multi_cursor_active:
            p = QPainter(self.viewport())
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            color = QColor(0, 0, 0)
            doc = self.document()
            for start, end in self._multi_cursors:
                if start == end:
                    c = QTextCursor(doc)
                    c.setPosition(start)
                    rect = self.cursorRect(c)
                    rect.setWidth(2)
                    p.fillRect(rect, color)
            p.end()

    def _cancelMultiCursor(self):
        self._multi_cursors = []
        self._multi_cursor_active = False
        self._multi_base_text = ""
        self.highlightLine()

    _BRACKET_PAIRS = {'(': ')', '[': ']', '{': '}', ')': '(', ']': '[', '}': '{'}

    def _checkBracket(self):
        cursor = self.textCursor()
        pos = cursor.position()
        doc = self.document()
        if not doc:
            return
        if pos > 0:
            char = doc.characterAt(pos - 1)
            if char in self._BRACKET_PAIRS:
                self._highlightBracket(pos - 1, char)
                return
        if pos < doc.characterCount():
            char = doc.characterAt(pos)
            if char in self._BRACKET_PAIRS:
                self._highlightBracket(pos, char)
                return
        return

    def _highlightBracket(self, pos, char):
        rev = {')': '(', ']': '[', '}': '{'}
        fwd = {'(': ')', '[': ']', '{': '}'}
        if char in fwd:
            open_ch, close_ch = char, fwd[char]
            direction, start = 1, pos + 1
        else:
            open_ch, close_ch = rev[char], char
            direction, start = -1, pos - 1
        depth = 1
        d = self.document()
        text = d.toPlainText()
        i = start
        while 0 <= i < len(text):
            if text[i] == open_ch:
                depth += 1 if direction == 1 else -1
            elif text[i] == close_ch:
                depth -= 1 if direction == 1 else -1
            if depth == 0:
                extras = []
                for p in (pos, i):
                    sel = QTextEdit.ExtraSelection()
                    sel.format.setBackground(QColor(128, 128, 128, 150))
                    c = QTextCursor(d)
                    c.setPosition(p)
                    c.setPosition(p + 1, QTextCursor.MoveMode.KeepAnchor)
                    sel.cursor = c
                    extras.append(sel)
                self.setExtraSelections(extras)
                return
            i += direction
        sel = QTextEdit.ExtraSelection()
        sel.format.setBackground(QColor(255, 100, 100, 120))
        c = QTextCursor(d)
        c.setPosition(pos)
        c.setPosition(pos + 1, QTextCursor.MoveMode.KeepAnchor)
        sel.cursor = c
        self.setExtraSelections([sel])

    def setPlainText(self, text: str):
        """重写setPlainText以避免光标位置错误"""
        try:
            self.blockSignals(True)
            doc = self.document()
            if doc:
                cursor = self.textCursor()
                if cursor.position() > doc.characterCount():
                    cursor.setPosition(0)
                    self.setTextCursor(cursor)
            super().setPlainText(text or "")
            self.blockSignals(False)
        except Exception:
            self.blockSignals(False)

    def contextMenuEvent(self, event):
        """自定义右键菜单（中文）"""
        menu = QMenu(self)
        
        # 撤销
        action_undo = QAction(tr("撤销"), self)
        action_undo.setShortcut(QKeySequence.StandardKey.Undo)
        action_undo.triggered.connect(self.undo)
        menu.addAction(action_undo)
        
        # 重做
        action_redo = QAction(tr("重做"), self)
        action_redo.setShortcut(QKeySequence.StandardKey.Redo)
        action_redo.triggered.connect(self.redo)
        menu.addAction(action_redo)
        
        menu.addSeparator()
        
        # 剪切
        action_cut = QAction(tr("剪切"), self)
        action_cut.setShortcut(QKeySequence.StandardKey.Cut)
        action_cut.triggered.connect(self.cut)
        action_cut.setEnabled(self.textCursor().hasSelection())
        menu.addAction(action_cut)
        
        # 复制
        action_copy = QAction(tr("复制"), self)
        action_copy.setShortcut(QKeySequence.StandardKey.Copy)
        action_copy.triggered.connect(self.copy)
        action_copy.setEnabled(self.textCursor().hasSelection())
        menu.addAction(action_copy)
        
        # 粘贴
        action_paste = QAction(tr("粘贴"), self)
        action_paste.setShortcut(QKeySequence.StandardKey.Paste)
        action_paste.triggered.connect(self.paste)
        menu.addAction(action_paste)
        
        # 删除
        action_delete = QAction(tr("删除"), self)
        action_delete.setShortcut(QKeySequence("Del"))
        action_delete.triggered.connect(self.deleteSelected)
        action_delete.setEnabled(self.textCursor().hasSelection())
        menu.addAction(action_delete)
        
        menu.addSeparator()
        
        # 打开网址
        selected_text = self.textCursor().selectedText().strip()
        if selected_text:
            urls = self._extractUrls(selected_text)
            if urls:
                action_openUrl = QAction(str(len(urls)) + " " + tr("个网址"), self)
                action_openUrl.triggered.connect(lambda checked, urls=urls: self._openUrls(urls))
                menu.addAction(action_openUrl)
                menu.addSeparator()
        
        # 搜索引擎功能
        if selected_text:
            try:
                config = getConfig()
                search_engines = config.get("Edit.engine", {})
                if search_engines:
                    for name, url in search_engines.items():
                        action = QAction(name + " " + tr("搜索"), self)
                        action.triggered.connect(lambda checked, u=url, t=selected_text: self._searchWith(u, t))
                        menu.addAction(action)
                    menu.addSeparator()
            except Exception:
                logger.exception("搜索错误")

        # 全选
        action_select_all = QAction(tr("全选"), self)
        action_select_all.setShortcut(QKeySequence.StandardKey.SelectAll)
        action_select_all.triggered.connect(self.selectAll)
        menu.addAction(action_select_all)
        
        # 重新加载
        if hasattr(self, '_parent_tab') and self._parent_tab and self._parent_tab.file_path:
            menu.addSeparator()
            action_reload = QAction(tr("重新加载"), self)
            action_reload.triggered.connect(self._reloadFile)
            menu.addAction(action_reload)
        
        menu.exec(event.globalPos())

    def deleteSelected(self):
        """删除选中的文本"""
        cursor = self.textCursor()
        if cursor.hasSelection():
            cursor.removeSelectedText()
            self.setTextCursor(cursor)

    def undo(self):
        super().undo()
        tab = self._parent_tab
        if tab and tab.is_modified:
            content = self.toPlainText()
            if content == tab._original_content:
                tab.is_modified = False
                tab.file_changed.emit(False)
                self.document().setModified(False)

    def _reloadFile(self):
        """重新加载当前文件（委托给父标签页的完整重载逻辑）"""
        if not hasattr(self, '_parent_tab') or not self._parent_tab:
            return
        self._parent_tab.reloadFile()

    def _extractUrls(self, text: str) -> list:
        """从文本中提取所有网址"""
        urls = []
        lines = text.split('\n')
        url_pattern = re.compile(r'https?://[^\s]+|www\.[^\s]+')
        for line in lines:
            line = line.strip()
            if line:
                matches = url_pattern.findall(line)
                for url in matches:
                    if url.endswith('.') or url.endswith(','):
                        url = url[:-1]
                    if url.startswith('www.'):
                        url = 'https://' + url
                    urls.append(url)
        return urls

    def _openUrls(self, urls: list):
        """用浏览器打开多个URL"""
        for url in urls:
            webbrowser.open(url)

    def _searchWith(self, url_template: str, query: str):
        """使用搜索引擎搜索"""
        encoded_query = parse.quote_plus(query)
        search_url = url_template.replace("{query}", encoded_query)
        webbrowser.open(search_url)

    def _openUrl(self, url: str):
        self._openUrls([url])

    def setLineNumbersVisible(self, visible: bool):
        """设置行号是否可见"""
        self.line_numbers_visible = visible
        self.line_number_area.setVisible(visible)
        self._updateLineNumWidth(0)

    def isLineNumbersVisible(self) -> bool:
        return self.line_numbers_visible

    def _lineNumWidth(self):
        """计算行号区域宽度（带缓存）"""
        if not self.line_numbers_visible:
            return 0
        doc = self.document()
        current_block_count = doc.blockCount()
        if current_block_count != self._cached_block_count or self._cached_line_number_width == 0:
            digits = 1
            max_num = max(1, current_block_count)
            while max_num >= 10:
                max_num //= 10
                digits += 1
            w = self.fontMetrics().horizontalAdvance('9')
            self._cached_line_number_width = 2 + w * (digits + 1)
            self._cached_block_count = current_block_count
        return self._cached_line_number_width

    def _updateLineNumWidth(self, _):
        """更新行号区域宽度"""
        w = self._lineNumWidth()
        if self.viewportMargins().left() != w:
            self.setViewportMargins(w, 0, 0, 0)

    def _updateLineNumArea(self, rect, dy):
        """更新行号区域"""
        if not self.line_numbers_visible:
            return
        if dy:
            self.line_number_area.scroll(0, dy)
        else:
            self.line_number_area.update(0, rect.y(), self.line_number_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._updateLineNumWidth(0)

    def scrollContentsBy(self, dx, dy):
        """滚动内容时同步行号区域"""
        super().scrollContentsBy(dx, dy)
        if self.line_numbers_visible:
            self._updateLineNumArea(self.viewport().rect(), dy)

    def resizeEvent(self, event):
        """窗口大小改变事件"""
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.line_number_area.setGeometry(QRect(cr.left(), cr.top(), self._lineNumWidth(), cr.height()))

    def highlightLine(self):
        """高亮当前行"""
        extra_selections = []
        if not self.isReadOnly():
            try:
                selection = QTextEdit.ExtraSelection()
                bg = self.palette().color(QPalette.ColorRole.Base)
                g = qGray(bg.rgb())
                offset = 24
                v = g + offset if g <= 128 else g - offset
                line_color = QColor(v, v, v)
                selection.format.setBackground(line_color)
                selection.format.setProperty(QTextCharFormat.Property.FullWidthSelection, True)
                selection.cursor = self.textCursor()
                selection.cursor.clearSelection()
                extra_selections.append(selection)
            except Exception:
                logger.exception("设置额外选区失败")
        self.setExtraSelections(extra_selections)

    class LineNumberArea(QWidget):
        def __init__(self, editor):
            super().__init__(editor)
            self.editor = editor

        def paintEvent(self, event):
            editor = self.editor
            if not editor.line_numbers_visible:
                return
            painter = QPainter(self)
            try:
                doc = editor.document()
                if not doc or doc.blockCount() == 0:
                    return
                text_color = editor.palette().color(QPalette.ColorRole.WindowText)
                gray = qGray(text_color.rgb())
                pen_color = QColor(gray, gray, gray, 150)
                scroll_offset = editor.verticalScrollBar().value()
                block = doc.begin()
                block_number = block.blockNumber()
                while block.isValid():
                    try:
                        block_geo = doc.documentLayout().blockBoundingRect(block)
                        top = int(block_geo.top() - scroll_offset)
                        bottom = top + int(block_geo.height())
                        if bottom >= event.rect().top() and top <= event.rect().bottom() and block.isVisible():
                            painter.setPen(pen_color)
                            painter.drawText(0, top, self.width() - 2,
                                            editor.fontMetrics().height(),
                                            Qt.AlignmentFlag.AlignRight, str(block_number + 1))
                        if top > event.rect().bottom():
                            break
                    except Exception:
                        break
                    block = block.next()
                    block_number += 1
            finally:
                painter.end()


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
        self._archive_type = None
        self.highlighter = None
        self.is_markdown = False
        self._markdown_cache = LRUCache(max_size=10)
        self.is_image = False
        self.setAcceptDrops(True)


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

    def _onTextChanged(self):
        if self.is_modified:
            return
        if self.text_edit.document().isModified():
            if self.text_edit.toPlainText() != self._original_content:
                self.is_modified = True
                self.file_changed.emit(True)

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
                self._archive_type = "zip" if is_zip else ("tar" if is_tar else None)
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

        ViewMode.reloadFile(self)

        self.is_modified = False
        self.file_changed.emit(False)

        main_window = self.window()
        if hasattr(main_window, '_applyEditorSettings'):
            main_window._applyEditorSettings(self)

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
