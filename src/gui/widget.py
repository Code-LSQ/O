import os
import re
import base64
import hashlib
import webbrowser
from urllib import parse

from PySide6.QtWidgets import QWidget, QVBoxLayout, QTextEdit, QMenu, QScrollArea, QLabel
from PySide6.QtCore import Qt, Signal, QRect, QByteArray, QTimer, QSize, QBuffer
from PySide6.QtGui import QPainter, QColor, QTextCursor, QTextCharFormat, QAction, QKeySequence, QPixmap, QTextDocument, QImage, QImageReader, QPalette, qGray

from src.util import logger, EXTENSION, inputDialog, tr
from src.config import getConfig, DEFAULT_CONFIG

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
        self.textChanged.connect(self._updateLineNum)
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
        """内容改变时更新行号区域"""
        if self.line_numbers_visible:
            self._updateLineNumWidth(0)
            self.line_number_area.update()

    def _updateLineNum(self):
        """文本改变时更新行号区域"""
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
        self.highlightLine()

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
                action_openUrl = QAction(len(urls) + tr("个网址"), self)
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
                        action = QAction(tr("使用") + " " + name + " " + tr("搜索"), self)
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
            self._cached_line_number_width = 3 + self.fontMetrics().horizontalAdvance('9') * digits
            self._cached_block_count = current_block_count
        return self._cached_line_number_width

    def _updateLineNumWidth(self, _):
        """更新行号区域宽度"""
        self.setViewportMargins(self._lineNumWidth(), 0, 0, 0)

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
            self.line_number_area.update()

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
                block = doc.begin()
                block_number = block.blockNumber()
                text_color = editor.palette().color(QPalette.ColorRole.WindowText)
                gray = qGray(text_color.rgb())
                pen_color = QColor(gray, gray, gray, 150)
                scroll_offset = editor.verticalScrollBar().value()
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


class ImageLabel(QLabel):
    """支持滚轮缩放的图片标签"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap = None
        self._zoom_factor = 1.0
        self._min_zoom = 0.1
        self._max_zoom = 10.0
        self._zoom_callback = None
        self.setScaledContents(False)
        self._current_file_path = None
        self._comic_view_enabled = False
        self._comic_container = None
        self._comic_layout = None
        self._comic_base_width = 800
        self._viewport = None
        self._viewport_installed = False
        self._archive_comic = False
        self._archive_images_data = []
        self._archive_comic_layout = None
        self._archive_comic_container = None
        self._archive_comic_base_width = 800
        # 懒加载图库属性
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
    
    def setFilePath(self, path: str, scroll_area=None):
        """设置当前文件路径"""
        self._current_file_path = path
        self._scroll_area_ref = scroll_area
        
        if scroll_area and not self._viewport_installed:
            viewport = scroll_area.viewport()
            viewport.installEventFilter(self)
            self._viewport_installed = True
    
    def eventFilter(self, obj, event):
        """事件过滤器捕获滚轮事件"""
        if event.type() == event.Type.Wheel:
            modifiers = event.modifiers()
            if modifiers & Qt.KeyboardModifier.ControlModifier:
                delta = event.angleDelta().y()
                if delta > 0:
                    self._zoom_factor += 0.1
                elif delta < 0:
                    self._zoom_factor -= 0.1

                self._zoom_factor = max(self._min_zoom, min(self._max_zoom, self._zoom_factor))

                if self._comic_view_enabled:
                    self._refreshComic()
                else:
                    self._applyZoom()

                if self._zoom_callback:
                    self._zoom_callback(self._zoom_factor)
                event.accept()
                return True
        return super().eventFilter(obj, event)
    
    def contextMenuEvent(self, event):
        """自定义右键菜单"""
        menu = QMenu(self)
        
        action_comic_view = QAction(tr("图库模式"), self)
        action_comic_view.triggered.connect(self._toggleComicView)
        menu.addAction(action_comic_view)
        
        menu.exec(event.globalPos())
    
    def _toggleComicView(self):
        """切换图库模式"""
        if self._comic_view_enabled:
            self._exitComicView()
        elif self._archive_comic:
            self._enterArchiveView()
        else:
            self._enterComicView()
    
    def setArchiveImages(self, images_data: list):
        """设置压缩包图片数据"""
        self._archive_comic = True
        self._archive_images_data = images_data
    
    def _enterArchiveView(self):
        """进入压缩包图库模式 - 懒加载"""
        if not self._archive_images_data:
            return
        
        scroll_area = getattr(self, '_scroll_area_ref', None) or self.parent()
        if not scroll_area or not isinstance(scroll_area, QScrollArea):
            return
        
        avail_width = scroll_area.viewport().width() - 20
        if avail_width <= 0:
            avail_width = 800
        
        self._comic_view_enabled = True
        self._comic_base_width = avail_width
        self._zoom_factor = 1.0
        
        # 预计算所有图片高度
        self._gallery_items = self._archive_images_data
        self._gallery_item_count = len(self._archive_images_data)
        self._gallery_loaded.clear()
        self._gallery_current_center = -1
        self._gallery_image_heights = []
        for img_name, img_data in self._archive_images_data:
            try:
                byte_array = QByteArray(img_data)
                buffer = QBuffer(byte_array)
                reader = QImageReader(buffer)
                size = reader.size()
                buffer.close()
                if not size.isValid():
                    size = QSize(800, 600)
            except Exception:
                size = QSize(800, 600)
            self._gallery_image_heights.append(self._calcHeight(size))
        
        self._gallery_label_tops = []
        cum_y = 0
        for h in self._gallery_image_heights:
            self._gallery_label_tops.append(cum_y)
            cum_y += h
        self._gallery_total_height = cum_y
        
        # 创建占位标签
        container = QWidget()
        container.setStyleSheet("background-color: white;")
        layout = QVBoxLayout(container)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        for i, (img_name, _) in enumerate(self._archive_images_data):
            label = QLabel()
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setMinimumHeight(self._gallery_image_heights[i])
            label.mousePressEvent = lambda e, p=img_name: self.comicClick(p)
            layout.addWidget(label)
        
        container.setMinimumHeight(self._gallery_total_height)
        
        self._comic_container = container
        self._comic_layout = layout
        self._archive_comic_layout = layout
        self._archive_comic_container = container
        self._archive_comic_base_width = self._comic_base_width
        
        if scroll_area and isinstance(scroll_area, QScrollArea):
            QTimer.singleShot(0, lambda: self._setupComicContainer(container))
        
        # 安装滚动监听实现懒加载
        self._gallery_scrollbar = scroll_area.verticalScrollBar()
        self._gallery_scrollbar.valueChanged.connect(self._onGalleryScroll)
        
        QTimer.singleShot(0, self._updateGallery)
    
    def _setupComicContainer(self, container):
        scroll_area = getattr(self, '_scroll_area_ref', None) or self.parent()
        if not scroll_area or not isinstance(scroll_area, QScrollArea):
            return
        scroll_area.takeWidget()
        scroll_area.setWidget(container)
        container.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        container.customContextMenuRequested.connect(self._showComicMenu)
    
    def _enterComicView(self):
        """进入图库模式 - 懒加载，只加载当前窗口周围图片"""
        if not self._current_file_path or not os.path.exists(self._current_file_path):
            return
        
        folder = os.path.dirname(self._current_file_path)
        if not folder:
            return
        
        image_files = self._getFolderImages(folder)
        if not image_files:
            return
        
        scroll_area = getattr(self, '_scroll_area_ref', None) or self.parent()
        if not scroll_area or not isinstance(scroll_area, QScrollArea):
            return
        
        avail_width = scroll_area.viewport().width() - 20
        if avail_width <= 0:
            avail_width = 800
        
        self._comic_view_enabled = True
        self._comic_base_width = avail_width
        self._zoom_factor = 1.0
        
        # 预计算所有图片高度（只读头部，不解码像素）
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
        
        # 创建占位标签
        self._comic_container = QWidget()
        self._comic_container.setStyleSheet("background-color: white;")
        self._comic_layout = QVBoxLayout(self._comic_container)
        self._comic_layout.setSpacing(0)
        self._comic_layout.setContentsMargins(0, 0, 0, 0)
        self._comic_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        for i, img_path in enumerate(image_files):
            label = QLabel()
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setMinimumHeight(self._gallery_image_heights[i])
            label.mousePressEvent = lambda e, p=img_path: self.comicClick(p)
            self._comic_layout.addWidget(label)
        
        self._comic_container.setMinimumHeight(self._gallery_total_height)
        self._comic_container.setCursor(Qt.CursorShape.ArrowCursor)
        scroll_area.setCursor(Qt.CursorShape.ArrowCursor)
        scroll_area.takeWidget()
        scroll_area.setWidget(self._comic_container)
        
        self._comic_container.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._comic_container.customContextMenuRequested.connect(lambda pos: self._showComicMenu(pos))
        
        scroll_area.viewport().installEventFilter(self)
        
        # 安装滚动监听实现懒加载
        self._gallery_scrollbar = scroll_area.verticalScrollBar()
        self._gallery_scrollbar.valueChanged.connect(self._onGalleryScroll)
        
        QTimer.singleShot(0, self._updateGallery)
    
    def _showComicMenu(self, pos):
        """图库模式右键菜单"""
        menu = QMenu(self._comic_container)
        
        action_comic_view = QAction(tr("图库模式"), self)
        action_comic_view.triggered.connect(self._toggleComicView)
        menu.addAction(action_comic_view)
        
        global_pos = self._comic_container.mapToGlobal(pos)
        menu.exec(global_pos)
    
    def _handleComicWheel(self, event):
        """图库模式滚轮缩放"""
        modifiers = event.modifiers()
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                self._zoom_factor += 0.1
            elif delta < 0:
                self._zoom_factor -= 0.1

            self._zoom_factor = max(self._min_zoom, min(self._max_zoom, self._zoom_factor))
            self._refreshComic()
            if self._zoom_callback:
                self._zoom_callback(self._zoom_factor)
            event.accept()
            return True
        return False
    
    def _refreshComic(self):
        """刷新图库显示"""
        if not self._comic_view_enabled:
            return
        
        if self._archive_comic:
            self._refreshArchive()
        elif self._comic_container:
            self._refreshFolder()
    
    def _refreshArchive(self):
        """缩放后刷新压缩包图库"""
        if not self._archive_comic or not self._archive_comic_container:
            return

        layout = self._archive_comic_layout
        if not layout:
            return

        base_width = getattr(self, '_comic_base_width', 800)
        if base_width <= 0:
            base_width = 800

        # 清除已加载图片
        for idx in list(self._gallery_loaded):
            self._unloadGallery(idx)
        self._gallery_current_center = -1

        # 重新计算高度
        self._gallery_image_heights = []
        for img_name, img_data in self._archive_images_data:
            try:
                byte_array = QByteArray(img_data)
                buffer = QBuffer(byte_array)
                reader = QImageReader(buffer)
                size = reader.size()
                buffer.close()
                if not size.isValid():
                    size = QSize(800, 600)
            except Exception:
                size = QSize(800, 600)
            self._gallery_image_heights.append(self._calcHeight(size))

        self._gallery_label_tops = []
        cum_y = 0
        for h in self._gallery_image_heights:
            self._gallery_label_tops.append(cum_y)
            cum_y += h
        self._gallery_total_height = cum_y

        # 更新占位标签高度
        for i in range(layout.count()):
            widget = layout.itemAt(i).widget()
            if widget and i < len(self._gallery_image_heights):
                widget.setMinimumHeight(self._gallery_image_heights[i])

        self._archive_comic_container.setMinimumHeight(self._gallery_total_height)

        self._updateGallery()

    def _refreshFolder(self):
        """缩放后刷新文件夹图库"""
        if not self._current_file_path or not os.path.exists(self._current_file_path):
            return

        folder = os.path.dirname(self._current_file_path)
        if not folder:
            return

        image_files = self._getFolderImages(folder)
        if not image_files:
            return

        base_width = getattr(self, '_comic_base_width', 800)
        if base_width <= 0:
            base_width = 800

        # 清除已加载图片
        for idx in list(self._gallery_loaded):
            self._unloadGallery(idx)
        self._gallery_current_center = -1

        # 重新计算高度
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

        # 更新占位标签高度
        for i in range(self._comic_layout.count()):
            widget = self._comic_layout.itemAt(i).widget()
            if widget and i < len(self._gallery_image_heights):
                widget.setMinimumHeight(self._gallery_image_heights[i])

        self._comic_container.setMinimumHeight(self._gallery_total_height)

        self._updateGallery()
    
    def _exitComicView(self):
        """退出图库模式"""
        self._comic_view_enabled = False
        self._archive_comic = False
        
        # 断开滚动监听
        if self._gallery_scrollbar:
            try:
                self._gallery_scrollbar.valueChanged.disconnect(self._onGalleryScroll)
            except (TypeError, RuntimeError):
                pass
            self._gallery_scrollbar = None
        
        # 清理懒加载状态
        self._gallery_items = []
        self._gallery_item_count = 0
        self._gallery_loaded.clear()
        self._gallery_current_center = -1
        self._gallery_image_heights.clear()
        self._gallery_label_tops.clear()
        self._gallery_total_height = 0
        self._gallery_scroll_value = 0
        
        # 清理现有小部件
        if self._comic_layout:
            while self._comic_layout.count() > 0:
                item = self._comic_layout.takeAt(0)
                if item and item.widget():
                    item.widget().deleteLater()
        
        if self._comic_container:
            scroll_area = getattr(self, '_scroll_area_ref', None) or self.parent()
            if scroll_area and isinstance(scroll_area, QScrollArea):
                viewport = scroll_area.viewport()
                if self._viewport_installed:
                    viewport.removeEventFilter(self)
                
                scroll_area.takeWidget()
                scroll_area.setWidget(self)
                scroll_area.setCursor(Qt.CursorShape.ArrowCursor)
            self._comic_container.deleteLater()
            self._comic_container = None
            self._comic_layout = None
            self._archive_comic_container = None
            self._archive_comic_layout = None
        
        self._archive_images_data = []
        self._pixmap = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
    
    def _calcHeight(self, img_size):
        """计算图片缩放后的显示高度"""
        if img_size.width() <= 0:
            return 100
        base_width = getattr(self, '_comic_base_width', 800)
        scale = base_width * self._zoom_factor / img_size.width()
        return max(1, int(img_size.height() * scale))

    def _loadGallery(self, idx: int):
        """懒加载指定索引的图片"""
        try:
            label = self._comic_layout.itemAt(idx).widget()
            if not label:
                return
            item = self._gallery_items[idx]

            if isinstance(item, str):
                image = QImage(item)
            else:
                image = QImage.fromData(item[1])

            if image.isNull():
                return

            pixmap = QPixmap.fromImage(image)
            base_width = getattr(self, '_comic_base_width', 800)
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

    def _unloadGallery(self, idx: int):
        """卸载指定索引的图片（释放内存）"""
        try:
            label = self._comic_layout.itemAt(idx).widget()
            if label:
                label.clear()
            self._gallery_loaded.discard(idx)
        except Exception:
            logger.exception(f"卸载图片失败 index={idx}")

    def _updateGallery(self):
        """根据当前滚动位置更新加载窗口"""
        if not self._comic_view_enabled or self._gallery_item_count == 0:
            return

        scrollbar = self._gallery_scrollbar
        if not scrollbar:
            return

        scroll_value = scrollbar.value()
        viewport_height = scrollbar.parent().height() if scrollbar.parent() else 600
        center_y = scroll_value + viewport_height // 2

        # 二分查找中心图片索引
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

        # 卸载窗口外的图片
        for idx in list(self._gallery_loaded):
            if idx < window_start or idx > window_end:
                self._unloadGallery(idx)

        # 加载窗口内的图片
        for idx in range(window_start, window_end + 1):
            if idx not in self._gallery_loaded:
                self._loadGallery(idx)

    def _onGalleryScroll(self, value):
        """滚动时更新懒加载窗口"""
        self._gallery_scroll_value = value
        self._updateGallery()

    def comicClick(self, img_path: str):
        """图库模式中点击图片，无行为，防止崩溃"""
        pass
    
    def _getFolderImages(self, folder: str) -> list:
        """获取文件夹中的所有图片，按名字排序"""
        try:
            image_files = []
            for f in os.listdir(folder):
                fpath = os.path.join(folder, f)
                if os.path.isfile(fpath):
                    ext = f.lower()
                    if any(ext.endswith(img_ext) for img_ext in EXTENSION["IMAGE"]):
                        image_files.append(fpath)
            return sorted(image_files, key=lambda x: self._sortKey(x))
        except Exception:
            logger.exception("获取文件夹图片失败")
            return []
    
    @staticmethod
    def _sortKey(path):
        """自然排序key：提取文件名中的数字用于排序"""
        basename = os.path.basename(path)
        parts = re.split(r'(\d+)', basename)
        return [int(p) if p.isdigit() else p for p in parts]
    
    def setZoomCallback(self, callback):
        """设置缩放回调"""
        self._zoom_callback = callback
    
    def setPixmap(self, pixmap):
        """设置图片"""
        self._pixmap = pixmap
        self._zoom_factor = 1.0
        super().setPixmap(pixmap)
    
    def wheelEvent(self, event):
        """滚轮事件 - Ctrl+滚轮缩放"""
        modifiers = event.modifiers()
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                self._zoom_factor += 0.1
            elif delta < 0:
                self._zoom_factor -= 0.1

            self._zoom_factor = max(self._min_zoom, min(self._max_zoom, self._zoom_factor))
            self._applyZoom()
            if self._zoom_callback:
                self._zoom_callback(self._zoom_factor)
            event.accept()
            return
        super().wheelEvent(event)
    
    def _applyZoom(self):
        """应用缩放"""
        if self._pixmap and not self._pixmap.isNull():
            new_width = int(self._pixmap.width() * self._zoom_factor)
            new_height = int(self._pixmap.height() * self._zoom_factor)
            
            if new_width > 0 and new_height > 0:
                scaled_pixmap = self._pixmap.scaled(
                    new_width, new_height,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
                super().setPixmap(scaled_pixmap)
                
                if self._zoom_callback:
                    self._zoom_callback(self._zoom_factor)
    
    def resetZoom(self):
        """重置缩放"""
        self._zoom_factor = 1.0
        if self._pixmap:
            super().setPixmap(self._pixmap)
            if self._zoom_callback:
                self._zoom_callback(1.0)
