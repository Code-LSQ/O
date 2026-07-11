"""GUI 组件模块，谨慎导入本地模块"""
import re
import base64
import hashlib
import webbrowser
import subprocess
from urllib import parse

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QMenu, QLabel, QPushButton, QProgressBar, QDialog, QApplication
from PySide6.QtCore import Qt, Signal, QRect, QByteArray, QTimer, QObject, QThread
from PySide6.QtGui import QPainter, QColor, QTextCursor, QTextCharFormat, QAction, QKeySequence, QPixmap, QTextDocument, QImage, QPalette, qGray

from src.util import logger, EXTENSION, inputDialog, tr, messageBox, arch, root, VERSION, download, compareVersions
from src.config import getConfig, DEFAULT_CONFIG
from src.core.update import getReleaseInfo, extractUpdate, writeUpdateScript, cleanTemp, UPDATE_ZIP, UPDATE_DIR

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


class _CheckWorker(QObject):
    finished = Signal(object)

    def run(self):
        info = getReleaseInfo()
        self.finished.emit(info)


class _DownloadWorker(QObject):
    progress = Signal(int, int)
    finished = Signal(bool)

    def __init__(self, url):
        super().__init__()
        self._url = url

    def run(self):
        success = download(self._url, UPDATE_ZIP, self.progress.emit)
        self.finished.emit(success)


class UpdateDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._release_info = None
        self._downloading = False
        self._installing = False
        self._checking = False
        self._download_url = None
        self._initUI()

    def _initUI(self):
        self.setWindowTitle(tr("检查更新"))
        self.setMinimumWidth(500)
        self.setAttribute(Qt.WA_DeleteOnClose)

        layout = QVBoxLayout(self)

        self._version_label = QLabel()
        layout.addWidget(self._version_label)

        self._notes_label = QLabel(tr("更新说明"))
        self._notes_label.hide()
        layout.addWidget(self._notes_label)

        self._notes_text = QTextEdit()
        self._notes_text.setReadOnly(True)
        self._notes_text.setMaximumHeight(250)
        self._notes_text.hide()
        layout.addWidget(self._notes_text)

        self._progress = QProgressBar()
        self._progress.hide()
        layout.addWidget(self._progress)

        btn_layout = QHBoxLayout()

        self._check_btn = QPushButton(tr("检查更新"))
        self._check_btn.clicked.connect(self._check)
        btn_layout.addWidget(self._check_btn)

        self._download_btn = QPushButton(tr("下载更新"))
        self._download_btn.clicked.connect(self._download)
        self._download_btn.setEnabled(False)
        btn_layout.addWidget(self._download_btn)

        self._close_btn = QPushButton(tr("取消"))
        self._close_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self._close_btn)

        layout.addLayout(btn_layout)

    def closeEvent(self, event):
        if self._downloading:
            messageBox(self, tr("提示"), tr("正在下载更新，请等待下载完成"), 1)
            event.ignore()
            return
        if self._installing:
            event.ignore()
            return
        cleanTemp()
        event.accept()

    @staticmethod
    def checkAndUpdate(parent):
        dialog = UpdateDialog(parent)
        dialog._check()
        dialog.exec()

    def _setCheckEnabled(self, enabled):
        self._check_btn.setEnabled(enabled)

    def _check(self):
        if self._checking:
            return
        self._checking = True
        self._release_info = None
        self._download_btn.setEnabled(False)
        self._notes_text.hide()
        self._notes_label.hide()
        self._version_label.setText(tr("检查中..."))
        self._setCheckEnabled(False)

        self._thread = QThread()
        self._worker = _CheckWorker()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._onCheckResult)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _onCheckResult(self, info):
        self._checking = False
        self._setCheckEnabled(True)
        if info is None:
            messageBox(self, tr("错误"), tr("检查更新失败"), 1)
            self._version_label.setText(tr("检查更新失败"))
            return

        version = info["version"]
        if compareVersions(version, VERSION) <= 0:
            self._version_label.setText(tr("当前已是最新版本"))
            return

        self._release_info = info
        self._version_label.setText(tr("发现新版本") + f" {version}")
        body = info.get("body", "").strip()
        if body:
            self._notes_text.setText(body)
            self._notes_label.show()
            self._notes_text.show()

        asset_name = f"Windows_{arch}.zip"
        for asset in info["assets"]:
            if asset["name"] == asset_name:
                self._download_url = asset["browser_download_url"]
                self._download_btn.setEnabled(True)
                return

        messageBox(self, tr("警告"), tr("没有找到适用于当前平台的更新包"), 1)

    def _download(self):
        if not self._download_url:
            return
        self._downloading = True
        self._download_btn.setEnabled(False)
        self._check_btn.setEnabled(False)

        self._progress.setValue(0)
        self._progress.show()

        self._dl_thread = QThread()
        self._dl_worker = _DownloadWorker(self._download_url)
        self._dl_worker.moveToThread(self._dl_thread)
        self._dl_thread.started.connect(self._dl_worker.run)
        self._dl_worker.progress.connect(self._onProgress)
        self._dl_worker.finished.connect(self._onDownloadFinished)
        self._dl_worker.finished.connect(self._dl_thread.quit)
        self._dl_worker.finished.connect(self._dl_worker.deleteLater)
        self._dl_thread.finished.connect(self._dl_thread.deleteLater)
        self._dl_thread.start()

    def _onProgress(self, current, total):
        if total > 0:
            self._progress.setMaximum(total)
            self._progress.setValue(current)

    def _onDownloadFinished(self, success):
        self._downloading = False
        self._progress.hide()
        if success:
            self._download_btn.setText(tr("重启更新"))
            self._download_btn.setEnabled(True)
            self._download_btn.clicked.disconnect()
            self._download_btn.clicked.connect(self._install)
        else:
            cleanTemp()
            messageBox(self, tr("错误"), tr("下载失败"), 1)
            self._download_btn.setText(tr("下载更新"))
            self._download_btn.setEnabled(True)
            self._check_btn.setEnabled(True)

    def _install(self):
        if not messageBox(self, tr("提示"), tr("确认重启并安装更新？")):
            return

        self._installing = True
        self._setCheckEnabled(False)
        self._close_btn.setEnabled(False)
        self._version_label.setText(tr("准备更新..."))

        if not extractUpdate(UPDATE_ZIP, UPDATE_DIR):
            messageBox(self, tr("错误"), tr("解压更新包失败"), 1)
            self._installing = False
            self._close_btn.setEnabled(True)
            self._setCheckEnabled(True)
            return

        try:
            writeUpdateScript()
            script = str(root / "update.cmd")
            subprocess.Popen(
                [script],
                creationflags=subprocess.CREATE_NO_WINDOW,
                cwd=str(root),
            )
        except Exception:
            logger.exception("启动更新脚本失败")
            messageBox(self, tr("错误"), tr("启动更新脚本失败"), 1)
            self._installing = False
            self._close_btn.setEnabled(True)
            self._setCheckEnabled(True)
            return

        QTimer.singleShot(500, QApplication.quit)
