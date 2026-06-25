import os
import re
import base64
import hashlib
import webbrowser
from urllib import parse

from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QMenu, QPushButton, QDialog, QScrollArea, QLabel
from PySide6.QtCore import Qt, Signal, QRect, QThread, QByteArray, QTimer
from PySide6.QtGui import QPainter, QColor, QTextCursor, QTextCharFormat, QAction, QKeySequence, QPixmap, QTextDocument, QImage, QPalette, qGray

from src.util import logger, EXTENSION, inputDialog, tr
from src.config import getConfig, DEFAULT_CONFIG
from src.core.AI import getAIClient

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
        doc.blockCountChanged.connect(self._on_block_count_changed)
        doc.contentsChanged.connect(self._on_contents_changed)
        self.textChanged.connect(self._on_text_changed_for_line_numbers)
        self.textChanged.connect(self._on_text_changed_autocomplete)
        self.cursorPositionChanged.connect(self._on_cursor_changed)

        self.update_line_number_area_width(0)
        self.highlight_current_line()
        
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
        self._reload_shortcuts()

        self._ai_context_buffer: list[str] = []

        self._ghost_text = ""
        self._ghost_tab_label = QLabel(self)
        self._ghost_tab_label.setVisible(False)
        self._ghost_tab_label.setTextFormat(Qt.RichText)
        self._ghost_tab_label.setStyleSheet("""
            QLabel {
                background: transparent;
                border: none;
                padding: 0;
                font-size: 13px;
            }
        """)
        self._ghost_text_label = QLabel(self)
        self._ghost_text_label.setVisible(False)
        self._ghost_text_label.setTextFormat(Qt.RichText)
        self._ghost_text_label.setStyleSheet("""
            QLabel {
                background: transparent;
                border: none;
                padding: 0;
                font-size: 13px;
            }
        """)
        self._autocomplete_timer = QTimer(self)
        self._autocomplete_timer.setSingleShot(True)
        self._autocomplete_timer.timeout.connect(self._trigger_autocomplete)
        self._autocomplete_worker = None

        self.verticalScrollBar().valueChanged.connect(self._reposition_ghost)
        self.horizontalScrollBar().valueChanged.connect(self._reposition_ghost)

    def set_zoom_callback(self, callback):
        self._zoom_callback = callback
    
    def _on_block_count_changed(self, new_count: int):
        """块数量变化时更新缓存"""
        if new_count != self._cached_block_count:
            self._cached_block_count = new_count
            self.update_line_number_area_width(0)
            self.line_number_area.update()

    def _on_contents_changed(self):
        """内容改变时更新行号区域"""
        if self.line_numbers_visible:
            self.update_line_number_area_width(0)
            self.line_number_area.update()

    def _on_text_changed_for_line_numbers(self):
        """文本改变时更新行号区域"""
        if self.line_numbers_visible:
            self.update_line_number_area_width(0)

    def wheelEvent(self, event):
        modifiers = event.modifiers()
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                self.zoom_in()
            elif delta < 0:
                self.zoom_out()
            event.accept()
            return
        super().wheelEvent(event)
    
    def _scroll_to_anchor(self, anchor: str):
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
    
    def zoom_in(self):
        """放大字体"""
        if self._zoom_factor < self._max_zoom:
            self._zoom_factor += 0.1
            self._apply_zoom()
            if self._zoom_callback:
                self._zoom_callback(self._zoom_factor)

    def zoom_out(self):
        """缩小字体"""
        if self._zoom_factor > self._min_zoom:
            self._zoom_factor -= 0.1
            self._apply_zoom()
            if self._zoom_callback:
                self._zoom_callback(self._zoom_factor)

    def _apply_zoom(self):
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
        self.update_line_number_area_width(0)

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

            def add_image_resource(match):
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
                    image = self._base64_to_image(b64_data, mime_type)
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

            html = re.sub(img_pattern, add_image_resource, html)
            super().setHtml(html)
            
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        except Exception:
            logger.exception("设置Markdown HTML失败")
    
    def _base64_to_image(self, base64_data: str, mime_type: str) -> QImage:
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
        if self._ghost_tab_label.isVisible() and event.key() == Qt.Key.Key_Tab:
            self._accept_ghost()
            event.accept()
            return

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
            self._cancel_multi_cursor()

        if self._multi_cursor_active and event.text():
            mods = event.modifiers()
            if mods in (Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.ShiftModifier):
                self._apply_key_to_multi_cursors(event.text())
                event.accept()
                return

        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._handle_enter_key()
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
                        self._scroll_to_anchor(href[1:])
                        event.accept()
                        return
            if event.modifiers() & Qt.KeyboardModifier.AltModifier:
                self._add_alt_click_cursor(event.pos())
                event.accept()
                return
            if self._multi_cursor_active:
                self._cancel_multi_cursor()
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

    def _on_cursor_changed(self):
        if self._multi_cursor_active:
            self._update_multi_cursor_highlight()
        else:
            self.highlight_current_line()
        self._check_bracket_match()
        self._emit_cursor_position()
        self._hide_ghost()

    def _emit_cursor_position(self):
        cursor = self.textCursor()
        line = cursor.blockNumber() + 1
        col = cursor.columnNumber() + 1
        self.cursor_position_changed.emit(line, col)

    def _reload_shortcuts(self):
        config = getConfig()
        saved = config.get("Edit.shortcuts", {})
        default_shortcuts = DEFAULT_CONFIG["Edit"]["shortcuts"]
        self._shortcut_seqs = {}
        self._shortcut_handlers = {
            "go_to_line": self.go_to_line,
            "jump_next": self._jump_next,
        }
        for name in self._shortcut_handlers:
            s = saved.get(name, default_shortcuts.get(name, ""))
            if s:
                self._shortcut_seqs[name] = QKeySequence(s)

    def go_to_line(self):
        text = inputDialog(self, tr("跳转到行"), tr("行号"), default="1")
        if text:
            cursor = self.textCursor()
            block = self.document().findBlockByNumber(int(text) - 1)
            if block:
                cursor.setPosition(block.position())
                self.setTextCursor(cursor)
                self.ensureCursorVisible()

    def _handle_enter_key(self):
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

    def set_auto_indent(self, enabled: bool):
        self._auto_indent_enabled = enabled

    def _jump_next(self):
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
                self._cancel_multi_cursor()
                self._multi_base_text = cursor.selectedText()
                self._multi_cursors = [(cursor.selectionStart(), cursor.selectionEnd())]
                self._multi_cursor_active = True
                self._update_multi_cursor_highlight()
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
        self._update_multi_cursor_highlight()

    def _add_alt_click_cursor(self, pos):
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
        self._update_multi_cursor_highlight()

    def _apply_key_to_multi_cursors(self, text):
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
        self._update_multi_cursor_highlight()

    def _update_multi_cursor_highlight(self):
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

    def _cancel_multi_cursor(self):
        self._multi_cursors = []
        self._multi_cursor_active = False
        self._multi_base_text = ""
        self.highlight_current_line()

    _BRACKET_PAIRS = {'(': ')', '[': ']', '{': '}', ')': '(', ']': '[', '}': '{'}

    def _check_bracket_match(self):
        cursor = self.textCursor()
        pos = cursor.position()
        doc = self.document()
        if not doc:
            return
        if pos > 0:
            char = doc.characterAt(pos - 1)
            if char in self._BRACKET_PAIRS:
                self._highlight_matching_bracket(pos - 1, char)
                return
        if pos < doc.characterCount():
            char = doc.characterAt(pos)
            if char in self._BRACKET_PAIRS:
                self._highlight_matching_bracket(pos, char)
                return
        self.highlight_current_line()

    def _highlight_matching_bracket(self, pos, char):
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
        action_delete.triggered.connect(self.delete_selected)
        action_delete.setEnabled(self.textCursor().hasSelection())
        menu.addAction(action_delete)
        
        menu.addSeparator()
        
        # 打开网址
        selected_text = self.textCursor().selectedText().strip()
        if selected_text:
            urls = self._extract_urls(selected_text)
            if urls:
                action_open_url = QAction(len(urls) + tr("个网址"), self)
                action_open_url.triggered.connect(lambda checked, urls=urls: self._open_urls(urls))
                menu.addAction(action_open_url)
                menu.addSeparator()
        
        # AI功能
        try:
            config = getConfig()
            if config.get("AI.enabled", False):
                ai_menu = QMenu("AI", self)
                
                # 获取提示词列表
                prompts = config.get("AI.prompts", {})
                if prompts:
                    for name in prompts:
                        if name not in ("系统提示词", "自动补全"):
                            action = QAction(name, self)
                            action.triggered.connect(lambda checked, n=name: self._handle_ai_request(n))
                            ai_menu.addAction(action)
                    ai_menu.addSeparator()

                if config.get("AI.autocomplete", False):
                    action_autocomplete = QAction("AI " + tr("自动补全"), self)
                    action_autocomplete.triggered.connect(self._handle_ai_autocomplete)
                    ai_menu.addAction(action_autocomplete)
                    ai_menu.addSeparator()


                # 加入上下文
                if selected_text:
                    action_add_context = QAction(tr("加入上下文"), self)
                    action_add_context.triggered.connect(self._add_ai_context)
                    ai_menu.addAction(action_add_context)

                    if self._ai_context_buffer:
                        action_clear_context = QAction(tr("清空上下文") + f" ({len(self._ai_context_buffer)}" + tr("条") + ")", self)
                        action_clear_context.triggered.connect(self._clear_ai_context)
                        ai_menu.addAction(action_clear_context)
                    ai_menu.addSeparator()

                action_ask = QAction(tr("询问") + " AI", self)
                action_ask.triggered.connect(lambda checked=False: self._handle_ai_request(None))
                ai_menu.addAction(action_ask)

                menu.addMenu(ai_menu)
                menu.addSeparator()
        except Exception:
            logger.exception("AI 右键错误")

        # 搜索引擎功能
        if selected_text:
            try:
                config = getConfig()
                search_engines = config.get("Edit.engine", {})
                if search_engines:
                    for name, url in search_engines.items():
                        action = QAction(tr("使用") + " " + name + " " + tr("搜索"), self)
                        action.triggered.connect(lambda checked, u=url, t=selected_text: self._search_with_engine(u, t))
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
            action_reload.triggered.connect(self._reload_file)
            menu.addAction(action_reload)
        
        # Markdown渲染选项
        if hasattr(self, '_parent_tab') and self._parent_tab:
            parent_tab = self._parent_tab
            if parent_tab.file_path and any(parent_tab.file_path.lower().endswith(ext) for ext in EXTENSION["Markdown"]):
                menu.addSeparator()
                action_render_md = QAction(("Markdown" + tr("渲染")), self)
                action_render_md.triggered.connect(parent_tab._toggle_markdown_view)
                menu.addAction(action_render_md)
        
        menu.exec(event.globalPos())

    def delete_selected(self):
        """删除选中的文本"""
        cursor = self.textCursor()
        if cursor.hasSelection():
            cursor.removeSelectedText()
            self.setTextCursor(cursor)

    def _reload_file(self):
        """重新加载当前文件（委托给父标签页的完整重载逻辑）"""
        if not hasattr(self, '_parent_tab') or not self._parent_tab:
            return
        self._parent_tab.reload_file()

    def _extract_urls(self, text: str) -> list:
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

    def _open_urls(self, urls: list):
        """用浏览器打开多个URL"""
        for url in urls:
            webbrowser.open(url)

    def _search_with_engine(self, url_template: str, query: str):
        """使用搜索引擎搜索"""
        encoded_query = parse.quote_plus(query)
        search_url = url_template.replace("{query}", encoded_query)
        webbrowser.open(search_url)

    def _open_url(self, url: str):
        self._open_urls([url])

    def setLineNumbersVisible(self, visible: bool):
        """设置行号是否可见"""
        self.line_numbers_visible = visible
        self.line_number_area.setVisible(visible)
        self.update_line_number_area_width(0)

    def isLineNumbersVisible(self) -> bool:
        return self.line_numbers_visible

    def line_number_area_width(self):
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

    def update_line_number_area_width(self, _):
        """更新行号区域宽度"""
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def update_line_number_area(self, rect, dy):
        """更新行号区域"""
        if not self.line_numbers_visible:
            return
        if dy:
            self.line_number_area.scroll(0, dy)
        else:
            self.line_number_area.update(0, rect.y(), self.line_number_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self.update_line_number_area_width(0)

    def scrollContentsBy(self, dx, dy):
        """滚动内容时同步行号区域"""
        super().scrollContentsBy(dx, dy)
        if self.line_numbers_visible:
            self.line_number_area.update()

    def resizeEvent(self, event):
        """窗口大小改变事件"""
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.line_number_area.setGeometry(QRect(cr.left(), cr.top(), self.line_number_area_width(), cr.height()))

    def highlight_current_line(self):
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

    def _add_ai_context(self):
        """将选中文本加入AI上下文缓冲区"""
        cursor = self.textCursor()
        if cursor.hasSelection():
            text = cursor.selectedText()
            self._ai_context_buffer.append(text)
            self.window().statusBar().showMessage(tr("已加入上下文") + f" (共{len(self._ai_context_buffer)}" + tr("条") + ")", 2000)

    def _clear_ai_context(self):
        """清空AI上下文缓冲区"""
        self._ai_context_buffer.clear()
        self.window().statusBar().showMessage(tr("上下文已清空"), 2000)

    def _handle_ai_request(self, prompt_name: str = None):
        """处理AI请求（流式+上下文）"""
        cursor = self.textCursor()
        if cursor.hasSelection():
            selected_text = cursor.selectedText()
        else:
            selected_text = tr("请帮我处理文本")

        messages = [{"role": "user", "content": selected_text}]

        if self._ai_context_buffer:
            context = "\n---\n".join(self._ai_context_buffer)
            messages.insert(0, {"role": "system", "content": f"以下是用户提供的上下文信息：\n{context}"})
            self._ai_context_buffer.clear()

        self._ai_dialog = AIDialog(messages, prompt_name, main_window=self.window())
        self._ai_dialog.setStyleSheet(self.window().styleSheet())
        self._ai_dialog.show()

    def _handle_ai_autocomplete(self):
        self._trigger_autocomplete()

    def _on_text_changed_autocomplete(self):
        self._hide_ghost()
        cfg = getConfig()
        if not cfg.get("AI.enabled", False) or not cfg.get("AI.autocomplete", False):
            return
        self._autocomplete_timer.start(500)

    def _trigger_autocomplete(self):
        cursor = self.textCursor()
        if cursor.hasSelection():
            return
        text = self.toPlainText()[:cursor.position()]
        if not text.strip():
            return

        cfg = getConfig()
        if not cfg.get("AI.enabled", False) or not cfg.get("AI.autocomplete", False):
            return

        if self._autocomplete_worker and self._autocomplete_worker.isRunning():
            self._autocomplete_worker.requestInterruption()
            self._autocomplete_worker.wait(500)

        text = text[-2000:]

        prompt_template = None
        try:
            client = getAIClient()
            tpl = client.get_prompt_by_name("自动补全")
            if tpl:
                prompt_template = tpl
        except Exception:
            logger.exception("加载AI提示模板失败")
        if not prompt_template:
            prompt_template = DEFAULT_CONFIG["AI"]["prompts"].get("自动补全", "")
        if not prompt_template:
            prompt_template = "请根据以下内容补全后续内容，输出不要超过100个字符，只输出补全的部分：\n\n{request}"
        user_content = prompt_template.replace("{request}", text)
        messages = [{"role": "user", "content": user_content}]
        self._autocomplete_worker = AIAutocompleteWorker(messages)
        self._autocomplete_worker.finished.connect(self._on_autocomplete_result)
        self._autocomplete_worker.start()

    def _on_autocomplete_result(self, result: str):
        result = result.strip()
        if not result:
            return
        self._ghost_text = result
        self._show_ghost()

    def _show_ghost(self):
        if not self._ghost_text:
            return
        gray = "#999"
        blue = "#2196F3"
        self._ghost_text_label.setText(
            f'<span style="color: {gray};">{self._ghost_text}</span>'
        )
        self._ghost_text_label.adjustSize()
        self._ghost_text_label.raise_()
        self._ghost_text_label.setVisible(True)
        self._ghost_tab_label.setText(
            f'<span style="color: {blue}; font-weight: bold;">[Tab]</span>'
        )
        self._ghost_tab_label.adjustSize()
        self._ghost_tab_label.raise_()
        self._ghost_tab_label.setVisible(True)
        self._reposition_ghost()

    def _reposition_ghost(self):
        cr = self.cursorRect()
        pos = self.viewport().mapTo(self, cr.topLeft())
        if self._ghost_tab_label.isVisible():
            self._ghost_tab_label.move(pos.x(), pos.y() - self._ghost_tab_label.height() - 2)
        if self._ghost_text_label.isVisible():
            self._ghost_text_label.move(pos.x(), pos.y() + cr.height() + 2)

    def _hide_ghost(self):
        self._ghost_tab_label.setVisible(False)
        self._ghost_text_label.setVisible(False)
        self._ghost_text = ""

    def _accept_ghost(self):
        if not self._ghost_text:
            return
        self._autocomplete_timer.stop()
        cursor = self.textCursor()
        cursor.insertText(self._ghost_text)
        self._hide_ghost()

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


class AIAutocompleteWorker(QThread):
    """AI自动补全工作线程"""
    finished = Signal(str)

    _alive: set = set()
    _TIMEOUT_MS = 15000

    def __init__(self, messages):
        super().__init__()
        self.messages = messages
        AIAutocompleteWorker._alive.add(self)
        self.finished.connect(self._cleanup)
        self._timeout_timer = QTimer(self)
        self._timeout_timer.setSingleShot(True)
        self._timeout_timer.timeout.connect(self._on_timeout)

    def start(self, priority=QThread.Priority.InheritPriority):
        self._timeout_timer.start(self._TIMEOUT_MS)
        super().start(priority)

    def _on_timeout(self):
        self.requestInterruption()
        self.wait(1000)
        self.finished.emit("")

    def _cleanup(self):
        self._timeout_timer.stop()
        AIAutocompleteWorker._alive.discard(self)

    def run(self):
        try:
            client = getAIClient()
            result, _, _ = client.chat(messages=self.messages)
            if not self.isInterruptionRequested():
                self.finished.emit(result)
        except Exception:
            logger.exception("AI工作线程执行失败")


class AIWorker(QThread):
    """AI工作线程（支持流式和非流式）"""
    chunk_received = Signal(str)
    finished = Signal(str)
    error = Signal(str)

    _alive: set = set()

    def __init__(self, messages, prompt_name=None, stream=True):
        super().__init__()
        self.messages = messages
        self.prompt_name = prompt_name
        self.stream = stream
        AIWorker._alive.add(self)
        self.finished.connect(self._cleanup)
        self.error.connect(self._cleanup)

    def _cleanup(self):
        AIWorker._alive.discard(self)

    def run(self):
        try:
            client = getAIClient()
            if self.stream:
                full_response = []
                def on_chunk(chunk):
                    if self.isInterruptionRequested():
                        return
                    full_response.append(chunk)
                    self.chunk_received.emit(chunk)
                client.stream_chat(
                    messages=self.messages,
                    callback=on_chunk,
                    prompt_name=self.prompt_name
                )
                if not self.isInterruptionRequested():
                    self.finished.emit(''.join(full_response))
            else:
                text, _, _ = client.chat(messages=self.messages, prompt_name=self.prompt_name)
                if not self.isInterruptionRequested():
                    self.finished.emit(text)
        except Exception as e:
            if not self.isInterruptionRequested():
                self.error.emit(str(e))


class AIDialog(QDialog):
    """AI回复对话框（支持流式和非流式，可编辑后粘贴）"""

    def __init__(self, messages, prompt_name, main_window=None):
        super().__init__()
        self._main_window = main_window
        self.setWindowTitle("AI " + tr("回复"))
        self.setMinimumSize(300, 200)
        self.resize(420, 280)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)

        config = getConfig()
        geometry = config.get("AI.dialog")
        if geometry:
            self.restoreGeometry(QByteArray.fromBase64(geometry.encode()))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(False)
        self.text_edit.setPlaceholderText(tr("连接中..."))
        layout.addWidget(self.text_edit)

        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 0, 0, 0)

        copy_btn = QPushButton(tr("复制"))
        copy_btn.clicked.connect(self._copy)
        btn_layout.addWidget(copy_btn)

        self.paste_btn = QPushButton(tr("粘贴"))
        self.paste_btn.setEnabled(False)
        self.paste_btn.clicked.connect(self._paste)
        btn_layout.addWidget(self.paste_btn)

        self.apply_btn = QPushButton(tr("编辑器"))
        self.apply_btn.setEnabled(False)
        self.apply_btn.clicked.connect(self._apply)
        btn_layout.addWidget(self.apply_btn)

        btn_layout.addStretch()

        close_btn = QPushButton(tr("关闭"))
        close_btn.clicked.connect(self.close)
        btn_layout.addWidget(close_btn)

        layout.addLayout(btn_layout)

        use_stream = config.get("AI.stream", True)
        self.worker = AIWorker(messages, prompt_name, stream=use_stream)
        if use_stream:
            self.worker.chunk_received.connect(self._on_chunk)
        self.worker.finished.connect(self._on_finished)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    def closeEvent(self, event):
        if self.worker.isRunning():
            self.worker.requestInterruption()
            self.worker.wait(3000)
        config = getConfig()
        config.set("AI.dialog", self.saveGeometry().toBase64().data().decode())
        config.save()
        super().closeEvent(event)

    def _on_chunk(self, chunk):
        self.text_edit.setPlaceholderText("")
        cursor = self.text_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(chunk)
        self.text_edit.setTextCursor(cursor)

    def _on_finished(self, response):
        self.text_edit.setPlaceholderText("")
        self.text_edit.setPlainText(response)
        self.apply_btn.setEnabled(True)
        self.paste_btn.setEnabled(True)

    def _on_error(self, error):
        self.text_edit.setPlaceholderText("")
        self.text_edit.setPlainText(tr("请求失败") + f": {error}")
        self.apply_btn.setEnabled(False)
        self.paste_btn.setEnabled(False)

    def _copy(self):
        text = self.text_edit.toPlainText()
        if text:
            QApplication.clipboard().setText(text)
        self.close()

    def _paste(self):
        text = self.text_edit.toPlainText()
        if not text:
            return
        if self._main_window:
            editor = self._main_window.get_current_editor()
            if editor:
                editor.text_edit.textCursor().insertText(text)
        self.close()

    def _apply(self):
        text = self.text_edit.toPlainText()
        if not text:
            return
        if self._main_window:
            self._main_window.activateWindow()
            self._main_window.raise_()
            editor = self._main_window.get_current_editor()
            if editor:
                editor.text_edit.setFocus()
                editor.text_edit.textCursor().insertText(text)
        self.close()


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
    
    def set_file_path(self, path: str, scroll_area=None):
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
                    self._comic_refresh()
                else:
                    self._apply_zoom()

                if self._zoom_callback:
                    self._zoom_callback(self._zoom_factor)
                event.accept()
                return True
        return super().eventFilter(obj, event)
    
    def contextMenuEvent(self, event):
        """自定义右键菜单"""
        menu = QMenu(self)
        
        action_comic_view = QAction(tr("漫画视图"), self)
        action_comic_view.triggered.connect(self._toggle_comic_view)
        menu.addAction(action_comic_view)
        
        menu.exec(event.globalPos())
    
    def _toggle_comic_view(self):
        """切换漫画视图"""
        if self._comic_view_enabled:
            self._exit_comic_view()
        elif self._archive_comic:
            self._enter_archive_comic_view()
        else:
            self._enter_comic_view()
    
    def set_archive_images(self, images_data: list):
        """设置压缩包图片数据"""
        self._archive_comic = True
        self._archive_images_data = images_data
    
    def _enter_archive_comic_view(self):
        """进入压缩包漫画视图"""
        logger.info(f"=== _enter_archive_comic_view, images_data count={len(self._archive_images_data)}")
        
        if not self._archive_images_data:
            logger.warning("=== no archive images data!")
            return
        
        self._comic_view_enabled = True
        
        scroll_area = getattr(self, '_scroll_area_ref', None) or self.parent()
        logger.info(f"=== scroll_area={scroll_area}, type={type(scroll_area)}")
        if not scroll_area or not isinstance(scroll_area, QScrollArea):
            logger.warning(f"=== scroll_area not QScrollArea!")
            return
        
        avail_width = scroll_area.viewport().width() - 20
        if avail_width <= 0:
            avail_width = 800
        
        self._comic_base_width = avail_width
        self._zoom_factor = 1.0
        
        container = QWidget()
        container.setStyleSheet("background-color: white;")
        layout = QVBoxLayout(container)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        total_height = 0
        loaded_count = 0
        for img_name, img_data in self._archive_images_data:
            try:
                image = QImage.fromData(img_data)
                if image.isNull():
                    logger.warning(f"=== 图片解码失败: {img_name}")
                    continue
                pixmap = QPixmap.fromImage(image)

                loaded_count += 1

                label = QLabel()
                label.setPixmap(pixmap)
                label.setAlignment(Qt.AlignmentFlag.AlignCenter)

                layout.addWidget(label)
                total_height += pixmap.height()
            except Exception:
                logger.exception(f"加载图片失败 {img_name}")
                continue
        
        container.setMinimumHeight(total_height)
        
        logger.info(f"=== comic: {total_height}px, {loaded_count}/{len(self._archive_images_data)} images loaded")
        
        self._comic_view_enabled = True
        self._comic_container = container
        self._comic_layout = layout
        self._archive_comic_layout = layout
        self._archive_comic_container = container
        self._archive_comic_base_width = self._comic_base_width
        
        scroll_area = getattr(self, '_scroll_area_ref', None) or self.parent()
        if scroll_area and isinstance(scroll_area, QScrollArea):
            QTimer.singleShot(0, lambda: self._setup_comic_container(container))
        else:
            container.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            container.customContextMenuRequested.connect(self._show_comic_context_menu)
    
    def _setup_comic_container(self, container):
        scroll_area = getattr(self, '_scroll_area_ref', None) or self.parent()
        if not scroll_area or not isinstance(scroll_area, QScrollArea):
            return
        scroll_area.takeWidget()
        scroll_area.setWidget(container)
        container.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        container.customContextMenuRequested.connect(self._show_comic_context_menu)
    
    def _enter_comic_view(self):
        """进入漫画视图模式"""
        if not self._current_file_path or not os.path.exists(self._current_file_path):
            return
        
        folder = os.path.dirname(self._current_file_path)
        if not folder:
            return
        
        image_files = self._get_folder_images(folder)
        if not image_files:
            return
        
        self._comic_view_enabled = True
        
        scroll_area = getattr(self, '_scroll_area_ref', None) or self.parent()
        if not scroll_area or not isinstance(scroll_area, QScrollArea):
            return
        
        avail_width = scroll_area.viewport().width() - 20
        if avail_width <= 0:
            avail_width = 800
        
        self._comic_base_width = avail_width
        self._zoom_factor = 1.0
        
        self._comic_container = QWidget()
        self._comic_container.setStyleSheet("background-color: white;")
        self._comic_layout = QVBoxLayout(self._comic_container)
        self._comic_layout.setSpacing(0)
        self._comic_layout.setContentsMargins(0, 0, 0, 0)
        self._comic_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        for img_path in image_files:
            try:
                image = QImage(img_path)
                if image.isNull():
                    continue
                pixmap = QPixmap.fromImage(image)

                label = QLabel()
                label.setPixmap(pixmap)
                label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                label.mousePressEvent = lambda e, p=img_path: self.comicClick(p)

                self._comic_layout.addWidget(label)
            except Exception:
                logger.exception(f"加载图片失败 {img_path}")
                continue
        
        self._comic_container.setCursor(Qt.CursorShape.ArrowCursor)
        scroll_area.setCursor(Qt.CursorShape.ArrowCursor)
        scroll_area.takeWidget()
        scroll_area.setWidget(self._comic_container)
        
        self._comic_container.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._comic_container.customContextMenuRequested.connect(lambda pos: self._show_comic_context_menu(pos))
        
        scroll_area.viewport().installEventFilter(self)
    
    def _show_comic_context_menu(self, pos):
        """漫画视图右键菜单"""
        menu = QMenu(self._comic_container)
        
        action_comic_view = QAction(tr("漫画视图"), self)
        action_comic_view.triggered.connect(self._toggle_comic_view)
        menu.addAction(action_comic_view)
        
        global_pos = self._comic_container.mapToGlobal(pos)
        menu.exec(global_pos)
    
    def _handle_comic_wheel(self, event):
        """漫画视图滚轮缩放"""
        modifiers = event.modifiers()
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                self._zoom_factor += 0.1
            elif delta < 0:
                self._zoom_factor -= 0.1

            self._zoom_factor = max(self._min_zoom, min(self._max_zoom, self._zoom_factor))
            self._comic_refresh()
            if self._zoom_callback:
                self._zoom_callback(self._zoom_factor)
            event.accept()
            return True
        return False
    
    def _comic_refresh(self):
        """刷新漫画视图显示"""
        if not self._comic_view_enabled:
            return
        
        if self._archive_comic:
            self._archive_comic_refresh()
        elif self._comic_container:
            self._comic_folder_refresh()
    
    def _archive_comic_refresh(self):
        """刷新压缩包漫画视图"""
        if not self._archive_comic or not self._archive_comic_container:
            return

        layout = self._archive_comic_layout
        if not layout:
            return

        base_width = getattr(self, '_comic_base_width', 800)
        if base_width <= 0:
            base_width = 800

        while layout.count() > 0:
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        total_height = 0
        for img_name, img_data in self._archive_images_data:
            try:
                image = QImage.fromData(img_data)
                if image.isNull():
                    continue
                pixmap = QPixmap.fromImage(image)

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

                label = QLabel()
                label.setPixmap(scaled)
                label.setAlignment(Qt.AlignmentFlag.AlignCenter)

                layout.addWidget(label)
                total_height += scaled.height()
            except Exception:
                logger.exception(f"刷新图片失败 {img_name}")
                continue
        
        self._archive_comic_container.setMinimumHeight(total_height)

    def _comic_folder_refresh(self):
        if not self._current_file_path or not os.path.exists(self._current_file_path):
            return

        folder = os.path.dirname(self._current_file_path)
        if not folder:
            return

        image_files = self._get_folder_images(folder)
        if not image_files:
            return

        while self._comic_layout.count() > 0:
            item = self._comic_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        base_width = getattr(self, '_comic_base_width', 800)
        if base_width <= 0:
            base_width = 800

        for img_path in image_files:
            try:
                image = QImage(img_path)
                if image.isNull():
                    continue
                pixmap = QPixmap.fromImage(image)

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

                label = QLabel()
                label.setPixmap(scaled)
                label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                label.mousePressEvent = lambda e, p=img_path: self.comicClick(p)

                self._comic_layout.addWidget(label)
            except Exception:
                logger.exception(f"加载图片失败 {img_path}")
                continue
    
    def _exit_comic_view(self):
        """退出漫画视图"""
        self._comic_view_enabled = False
        self._archive_comic = False
        
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
        
        self.setCursor(Qt.CursorShape.ArrowCursor)
    
    def comicClick(self, img_path: str):
        """漫画视图中点击图片，无行为，防止崩溃"""
        pass
    
    def _get_folder_images(self, folder: str) -> list:
        """获取文件夹中的所有图片，按名字排序"""
        try:
            image_files = []
            for f in os.listdir(folder):
                fpath = os.path.join(folder, f)
                if os.path.isfile(fpath):
                    ext = f.lower()
                    if any(ext.endswith(img_ext) for img_ext in EXTENSION["IMAGE"]):
                        image_files.append(fpath)
            return sorted(image_files, key=lambda x: self._natural_sort_key(x))
        except Exception:
            logger.exception("获取文件夹图片失败")
            return []
    
    @staticmethod
    def _natural_sort_key(path):
        """自然排序key：提取文件名中的数字用于排序"""
        basename = os.path.basename(path)
        parts = re.split(r'(\d+)', basename)
        return [int(p) if p.isdigit() else p for p in parts]
    
    def set_zoom_callback(self, callback):
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
            self._apply_zoom()
            if self._zoom_callback:
                self._zoom_callback(self._zoom_factor)
            event.accept()
            return
        super().wheelEvent(event)
    
    def _apply_zoom(self):
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
