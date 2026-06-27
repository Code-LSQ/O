from PySide6.QtCore import Qt, Signal, QPoint
from PySide6.QtGui import QMouseEvent

class WindowMouse:
    """窗口拖拽和调整大小"""

    isDragging = Signal(bool)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setMouseTracking(True)
        self._resize_edge = None
        self._dragging = False
        self._start_global_pos = QPoint()
        self._start_window_pos = QPoint()
        self._start_size = None
        self._title_bar_height = 32
        self._is_maximized = False
        self._pre_maximize_geometry = None

    def _get_edge(self, pos: QPoint):
        """获取鼠标位置的边缘方向"""
        w, h = self.width(), self.height()
        x, y = pos.x(), pos.y()
        edge_size = 8

        if x < edge_size:
            return 'left'
        if x > w - edge_size:
            return 'right'
        if y < edge_size:
            return 'top'
        if y > h - edge_size:
            return 'bottom'
        return None

    def _is_on_title_bar(self, pos: QPoint):
        """检查是否在标题栏区域（用于拖拽，不包含顶部边缘区域）"""
        edge_size = 8
        return 0 <= pos.x() < self.width() and edge_size <= pos.y() <= self._title_bar_height

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() != Qt.MouseButton.LeftButton:
            return

        pos = event.position().toPoint()
        self._start_global_pos = event.globalPosition().toPoint()
        self._start_window_pos = self.pos()
        self._start_size = self.size()

        if self._is_on_title_bar(pos):
            self._dragging = True
            self.grabMouse()
            self.isDragging.emit(True)
        else:
            edge = self._get_edge(pos)
            if edge:
                self._resize_edge = edge
                self._dragging = True
                self.grabMouse()
                self.isDragging.emit(True)

    def mouseMoveEvent(self, event: QMouseEvent):
        if not self._dragging:
            return

        gpos = event.globalPosition().toPoint()

        if self._resize_edge:
            self._do_resize(gpos)
        else:
            self._do_drag(gpos)

    def _do_drag(self, gpos: QPoint):
        """执行窗口拖拽"""
        dx = gpos.x() - self._start_global_pos.x()
        dy = gpos.y() - self._start_global_pos.y()
        x = self._start_window_pos.x() + dx
        y = self._start_window_pos.y() + dy
        self.move(x, y)

    def _do_resize(self, gpos: QPoint):
        """执行窗口大小调整"""
        dx = gpos.x() - self._start_global_pos.x()
        dy = gpos.y() - self._start_global_pos.y()

        x = self._start_window_pos.x()
        y = self._start_window_pos.y()
        w = self._start_size.width()
        h = self._start_size.height()
        edge = self._resize_edge

        if edge == 'left':
            x += dx
            w -= dx
        elif edge == 'right':
            w += dx
        elif edge == 'top':
            y += dy
            h -= dy
        elif edge == 'bottom':
            h += dy

        min_w = self.minimumWidth() or 200
        min_h = self.minimumHeight() or 150

        if w < min_w:
            if edge == 'left':
                x = self._start_window_pos.x() + self._start_size.width() - min_w
            w = min_w
        if h < min_h:
            if edge == 'top':
                y = self._start_window_pos.y() + self._start_size.height() - min_h
            h = min_h

        if edge in ('left', 'top'):
            self.setGeometry(x, y, w, h)
        else:
            self.resize(w, h)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._dragging:
            self.releaseMouse()
            self._resize_edge = None
            self._dragging = False
            self.isDragging.emit(False)

    def _toggle_maximize(self):
        if self._is_maximized:
            self._is_maximized = False
            if self._pre_maximize_geometry:
                self.setGeometry(self._pre_maximize_geometry)
            elif hasattr(self, '_fallback_size'):
                self.resize(*self._fallback_size)
            self.window_control.update_max_button(False)
        else:
            self._pre_maximize_geometry = self.geometry()
            self._is_maximized = True
            screen = self.screen()
            if screen:
                self.setGeometry(screen.availableGeometry())
            self.window_control.update_max_button(True)