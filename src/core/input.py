import time
import threading
import signal

from pynput import keyboard, mouse
from PySide6.QtCore import Qt, QMetaObject, QEvent, QObject, Signal, QTimer
from PySide6.QtWidgets import QApplication

from src.util import logger, Singleton

# 需要注意，PySide6 6.10.3 与 pynput 1.8.0 冲突。
# 如果需要全局快捷键并抑制其传给系统和其他程序， Windows 使用 win32_event_filter ，macOS 使用 darwin_intercept 。 Linux 似乎暂时没有好办法。


def _doCopy():
    old = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        GlobalHotkeyListener._is_pasting = True
        time.sleep(0.1)
        kb = keyboard.Controller()
        kb.press(keyboard.Key.ctrl)
        kb.press('c')
        kb.release('c')
        kb.release(keyboard.Key.ctrl)
    finally:
        GlobalHotkeyListener._is_pasting = False
        signal.signal(signal.SIGINT, old)

def _pollClipboard(old, callback, timeout_ms, interval_ms, elapsed):
    current = QApplication.clipboard().text() or ""
    if current != old:
        callback(current)
        return
    if elapsed >= timeout_ms:
        callback("")
        return
    QTimer.singleShot(interval_ms, lambda: _pollClipboard(
        old, callback, timeout_ms, interval_ms, elapsed + interval_ms))

def copyWait(callback, timeout_ms=3000, interval_ms=100):
    """模拟 Ctrl+C 后轮询剪贴板直到内容变化或超时，通过 callback(text) 异步返回"""
    old = QApplication.clipboard().text() or ""
    _doCopy()
    QTimer.singleShot(interval_ms, lambda: _pollClipboard(
        old, callback, timeout_ms, interval_ms, interval_ms))


class GlobalHotkeyListener(Singleton):
    """全局快捷键和鼠标侧键监听器"""
    
    _keyboard_listener = None
    _mouse_listener = None
    _main_window = None
    _tool_hotkeys = {}
    _tool_hotkeys_cache = {}
    _pending_tool = None
    _pending_hotkey = None
    _pressed_keys = set()
    _vk_pressed = set()
    _hotkey_triggered = False
    _tool_hotkeys_fired = set()
    _is_pasting = False
    _placeholders = {"Select": ""}
    _last_tool_hotkey_time = 0
    _min_hotkey_interval = 0.3

    _VK_TO_NAME = {
        0x41: 'a', 0x42: 'b', 0x43: 'c', 0x44: 'd', 0x45: 'e',
        0x46: 'f', 0x47: 'g', 0x48: 'h', 0x49: 'i', 0x4A: 'j',
        0x4B: 'k', 0x4C: 'l', 0x4D: 'm', 0x4E: 'n', 0x4F: 'o',
        0x50: 'p', 0x51: 'q', 0x52: 'r', 0x53: 's', 0x54: 't',
        0x55: 'u', 0x56: 'v', 0x57: 'w', 0x58: 'x', 0x59: 'y', 0x5A: 'z',
        0x30: '0', 0x31: '1', 0x32: '2', 0x33: '3', 0x34: '4',
        0x35: '5', 0x36: '6', 0x37: '7', 0x38: '8', 0x39: '9',
        0x10: 'shift_l', 0x11: 'ctrl_l', 0x12: 'alt_l',
        0xA0: 'shift_l', 0xA1: 'shift_r',0xA2: 'ctrl_l', 0xA3: 'ctrl_r',
        0xA4: 'alt_l', 0xA5: 'alt_r',
        0x5B: 'cmd', 0x5C: 'cmd',
        0x70: 'f1', 0x71: 'f2', 0x72: 'f3', 0x73: 'f4',
        0x74: 'f5', 0x75: 'f6', 0x76: 'f7', 0x77: 'f8',
        0x78: 'f9', 0x79: 'f10', 0x7A: 'f11', 0x7B: 'f12',
        0x25: 'left', 0x26: 'up', 0x27: 'right', 0x28: 'down',
        0x24: 'home', 0x23: 'end', 0x21: 'page_up', 0x22: 'page_down',
        0x2D: 'insert', 0x2E: 'delete',
        0x20: 'space', 0x0D: 'enter', 0x09: 'tab',
        0x08: 'backspace', 0x1B: 'esc',
    }

    def _init(self):
        self._lock = threading.Lock()
        self._last_tool_hotkey_time = 0
        self._tool_hotkeys_fired = set()
    
    @staticmethod
    def _getKeyName(key):
        key_name = None
        key_char = getattr(key, 'char', None)
        key_name_attr = getattr(key, "name", None)
        key_vk = getattr(key, 'vk', None)

        if key_char is not None and isinstance(key_char, str) and len(key_char) == 1 and ord(key_char) >= 32:
            key_name = key_char.lower()
        elif key_name_attr is not None:
            key_name = key_name_attr.lower()
        elif key_vk is not None:
            key_name = GlobalHotkeyListener._VK_TO_NAME.get(key_vk)

        if not key_name:
            key_name = str(key).lower()
        if key_name.startswith('key.'):
            key_name = key_name[4:]
        elif key_name.startswith('keycode.'):
            key_name = key_name[8:]

        return key_name

    def start(self, main_window, hotkey_str=None, mouse_side_enabled=False, double_ctrl_enabled=False):
        """启动全局监听器"""
        self._stop()
        time.sleep(0.1)
        self._main_window = main_window
        
        self._vk_pressed.clear()
        self._tool_hotkeys_fired.clear()
        with self._lock:
            self._pressed_keys = set()
            self._hotkey_triggered = False
            self._modifier_press_order = []
        last_ctrl_press_time = 0
        ctrl_was_held = False
        
        hotkey_config = self._parseHotkey(hotkey_str) if hotkey_str else None
        
        def onPress(key):
            nonlocal last_ctrl_press_time, ctrl_was_held
            try:
                if self._is_pasting:
                    return
                key_name = self._getKeyName(key)
                if not key_name:
                    return
                
                with self._lock:
                    self._pressed_keys.add(key_name)
                    pressed_keys_copy = self._pressed_keys.copy()
                    self._modifier_press_order.append(key_name)
                
                if double_ctrl_enabled and key_name in ('ctrl_l', 'ctrl_r', 'ctrl'):
                    current_time = time.time()
                    time_since_last_press = current_time - last_ctrl_press_time
                    
                    if not ctrl_was_held and time_since_last_press < 0.5:
                        logger.info("连按Ctrl键触发启动器")
                        QMetaObject.invokeMethod(main_window, "_toggleWindow", Qt.ConnectionType.QueuedConnection)
                    
                    last_ctrl_press_time = current_time
                    ctrl_was_held = True
                
                if hotkey_config and not self._hotkey_triggered:
                    with self._lock:
                        if self._checkHotkey(pressed_keys_copy, hotkey_config):
                            self._hotkey_triggered = True
                            QMetaObject.invokeMethod(main_window, "_toggleWindow", Qt.ConnectionType.QueuedConnection)
                
                self._checkToolHotkeys(pressed_keys_copy)
                
            except Exception:
                logger.exception("按键处理错误")
        
        def onRelease(key):
            nonlocal ctrl_was_held
            try:
                key_name = self._getKeyName(key)
                if not key_name:
                    return
                
                with self._lock:
                    self._pressed_keys.discard(key_name)
                    self._hotkey_triggered = False
                    if key_name in self._modifier_press_order:
                        self._modifier_press_order = [k for k in self._modifier_press_order if k != key_name]
                
                if self._is_pasting:
                    return
                
                if double_ctrl_enabled and key_name in ('ctrl_l', 'ctrl_r', 'ctrl'):
                    ctrl_was_held = False
            except Exception:
                logger.exception("按键释放错误")
        
        def onClick(x, y, button, pressed):
            if pressed and button in (mouse.Button.x1, mouse.Button.x2) and mouse_side_enabled:
                QMetaObject.invokeMethod(main_window, "_toggleWindow", Qt.ConnectionType.QueuedConnection)
        
        def keyboardWinFilter(msg, data):
            WM_KEYDOWN = 0x0100
            WM_KEYUP = 0x0101
            WM_SYSKEYDOWN = 0x0104
            WM_SYSKEYUP = 0x0105
            if self._is_pasting:
                if msg in (WM_KEYUP, WM_SYSKEYUP):
                    self._vk_pressed.discard(data.vkCode)
                return
            vk = data.vkCode
            if msg in (WM_KEYDOWN, WM_SYSKEYDOWN):
                self._vk_pressed.add(vk)
                pressed_names = set()
                for vk_code in self._vk_pressed:
                    name = self._VK_TO_NAME.get(vk_code)
                    pressed_names.add(name if name else str(vk_code))
                for hotkey_str, tool in self._tool_hotkeys.items():
                    hotkey_keys = self._tool_hotkeys_cache.get(hotkey_str)
                    if hotkey_keys and self._checkHotkey(pressed_names, hotkey_keys):
                        if hotkey_str in self._tool_hotkeys_fired:
                            self._keyboard_listener.suppress_event()
                            return
                        current_time = time.time()
                        if current_time - self._last_tool_hotkey_time < self._min_hotkey_interval:
                            self._keyboard_listener.suppress_event()
                            return
                        self._last_tool_hotkey_time = current_time
                        self._tool_hotkeys_fired.add(hotkey_str)
                        self._pending_tool = tool
                        self._pending_hotkey = hotkey_str
                        QMetaObject.invokeMethod(main_window, "runHotkey", Qt.ConnectionType.QueuedConnection)
                        self._keyboard_listener.suppress_event()
            elif msg in (WM_KEYUP, WM_SYSKEYUP):
                self._vk_pressed.discard(vk)
                if self._tool_hotkeys_fired:
                    pressed_up = set()
                    for vk_code in self._vk_pressed:
                        name = self._VK_TO_NAME.get(vk_code)
                        pressed_up.add(name if name else str(vk_code))
                    stale = {s for s in self._tool_hotkeys_fired
                             if not self._checkHotkey(pressed_up, self._tool_hotkeys_cache.get(s, set()))}
                    self._tool_hotkeys_fired -= stale

        try:
            self._keyboard_listener = keyboard.Listener(
                on_press=onPress,
                on_release=onRelease,
                suppress=False,
                win32_event_filter=keyboardWinFilter
            )
            self._keyboard_listener.start()
            if hotkey_config or self._tool_hotkeys or double_ctrl_enabled:
                logger.info(f"全局快捷键监听已启动: {hotkey_str}")
            
            if mouse_side_enabled:
                self._mouse_listener = None

                def mouseWinFilter(msg, data):
                    WM_XBUTTONDOWN = 0x020B
                    WM_XBUTTONUP = 0x020C
                    if msg in (WM_XBUTTONDOWN, WM_XBUTTONUP):
                        button = data.mouseData >> 16
                        if button in (1, 2):
                            if msg == WM_XBUTTONDOWN:
                                QMetaObject.invokeMethod(main_window, "_toggleWindow", Qt.ConnectionType.QueuedConnection)
                            self._mouse_listener.suppress_event()

                self._mouse_listener = mouse.Listener(
                    on_click=onClick,
                    suppress=False,
                    win32_event_filter=mouseWinFilter
                    )
                self._mouse_listener.start()
                logger.info("鼠标侧键监听已启动")
        except Exception:
            logger.exception("启动全局监听失败")
    
    def _stop(self):
        """停止监听器"""
        if self._keyboard_listener is not None:
            try:
                self._keyboard_listener.stop()
            except Exception:
                logger.exception("键盘监听错误")
            self._keyboard_listener = None
        
        if self._mouse_listener is not None:
            try:
                self._mouse_listener.stop()
            except Exception:
                logger.exception("鼠标监听错误")
            self._mouse_listener = None
        
        self._vk_pressed.clear()
        self._tool_hotkeys_fired.clear()
        with self._lock:
            self._pressed_keys = set()
            self._hotkey_triggered = False
            self._modifier_press_order = []
    
    def restart(self, main_window, hotkey_str=None, mouse_side_enabled=False, double_ctrl_enabled=False):
        """重启监听器"""
        self._stop()
        self.start(main_window, hotkey_str, mouse_side_enabled, double_ctrl_enabled)
    
    def _parseHotkey(self, hotkey_str: str) -> set:
        """解析快捷键字符串为按键集合"""
        if not hotkey_str:
            return None
        
        keys = set()
        parts = [p.strip().lower() for p in hotkey_str.split('+')]
        
        for part in parts:
            if part == 'ctrl':
                keys.add('ctrl_l')
            elif part == 'alt':
                keys.add('alt_l')
            elif part == 'shift':
                keys.add('shift_l')
            elif part in ('win', 'super', 'meta'):
                keys.add('cmd')
            else:
                keys.add(part)
        
        logger.info(f"解析快捷键: '{hotkey_str}' -> {keys}")
        return keys if keys else None
    
    def _checkHotkey(self, pressed_keys: set, hotkey_keys: set) -> bool:
        """检查是否匹配快捷键"""
        if not hotkey_keys:
            return False
        
        modifier_keys = {'ctrl_l', 'ctrl_r', 'ctrl', 'alt_l', 'alt_r', 'alt', 
                         'shift_l', 'shift_r', 'shift', 'cmd_l', 'cmd_r', 'cmd'}
        
        required_modifiers = hotkey_keys & modifier_keys
        required_normal = hotkey_keys - modifier_keys
        
        if required_modifiers:
            for k in required_modifiers:
                if k == 'ctrl_l':
                    if not ('ctrl_l' in pressed_keys or 'ctrl_r' in pressed_keys or 'ctrl' in pressed_keys):
                        return False
                elif k == 'alt_l':
                    if not ('alt_l' in pressed_keys or 'alt_r' in pressed_keys or 'alt' in pressed_keys):
                        return False
                elif k == 'shift_l':
                    if not ('shift_l' in pressed_keys or 'shift_r' in pressed_keys or 'shift' in pressed_keys):
                        return False
                elif k == 'cmd':
                    if not ('cmd_l' in pressed_keys or 'cmd_r' in pressed_keys or 'cmd' in pressed_keys):
                        return False
        
        for k in required_normal:
            if k not in pressed_keys:
                return False
        
        if required_modifiers and required_normal:
            modifier_key_map = {'ctrl_l': {'ctrl_l', 'ctrl_r', 'ctrl'},
                                'ctrl_r': {'ctrl_l', 'ctrl_r', 'ctrl'},
                                'ctrl': {'ctrl_l', 'ctrl_r', 'ctrl'},
                                'alt_l': {'alt_l', 'alt_r', 'alt'},
                                'alt_r': {'alt_l', 'alt_r', 'alt'},
                                'alt': {'alt_l', 'alt_r', 'alt'},
                                'shift_l': {'shift_l', 'shift_r', 'shift'},
                                'shift_r': {'shift_l', 'shift_r', 'shift'},
                                'shift': {'shift_l', 'shift_r', 'shift'},
                                'cmd': {'cmd_l', 'cmd_r', 'cmd'},
                                'cmd_l': {'cmd_l', 'cmd_r', 'cmd'},
                                'cmd_r': {'cmd_l', 'cmd_r', 'cmd'}}
            normal_key_indices = []
            for key in required_normal:
                if key in self._modifier_press_order:
                    normal_key_indices.append(self._modifier_press_order.index(key))
            for mod in required_modifiers:
                normalized_mods = modifier_key_map.get(mod, {mod})
                mod_index = -1
                for i, key in enumerate(self._modifier_press_order):
                    if key in normalized_mods:
                        mod_index = i
                        break
                if mod_index == -1:
                    return False
                if normal_key_indices and min(normal_key_indices) < mod_index:
                    return False
        
        return True
    
    def _checkToolHotkeys(self, pressed_keys=None):
        """检查工具快捷键"""
        if not self._tool_hotkeys or not self._main_window:
            return
        if self._is_pasting:
            return
        
        if pressed_keys is None:
            with self._lock:
                pressed_keys = self._pressed_keys.copy()

        for hotkey_str, tool in self._tool_hotkeys.items():
            hotkey_keys = self._tool_hotkeys_cache.get(hotkey_str)
            if hotkey_keys and hotkey_str not in self._tool_hotkeys_fired and self._checkHotkey(pressed_keys, hotkey_keys):
                current_time = time.time()
                if current_time - self._last_tool_hotkey_time < self._min_hotkey_interval:
                    return
                self._last_tool_hotkey_time = current_time
                self._tool_hotkeys_fired.add(hotkey_str)
                logger.info(f"触发工具快捷键: {hotkey_str} -> {tool.get("name", "")}")
                self._pending_tool = tool
                self._pending_hotkey = hotkey_str
                
                QMetaObject.invokeMethod(self._main_window, "runHotkey", Qt.ConnectionType.QueuedConnection)
                break
    
    def registerHotkey(self, hotkey_str: str, tool: dict):
        """注册工具快捷键"""
        if not hotkey_str:
            return
        with self._lock:
            self._tool_hotkeys[hotkey_str] = tool
            self._tool_hotkeys_cache[hotkey_str] = self._parseHotkey(hotkey_str)
        logger.info(f"注册工具快捷键: {hotkey_str} -> {tool.get("name", "")}")
    
    def clearToolHotkeys(self):
        """清除所有工具快捷键"""
        with self._lock:
            self._tool_hotkeys.clear()
            self._tool_hotkeys_cache.clear()
        logger.info("清除所有工具快捷键")


def eventToKey(event):
    """将 QKeyEvent 转换为快捷键字符串"""
    key = event.key()
    if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Tab, Qt.Key.Key_Space, Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
        return None
    
    modifiers = event.modifiers()
    parts = []
    if modifiers & Qt.KeyboardModifier.ControlModifier:
        parts.append("Ctrl")
    if modifiers & Qt.KeyboardModifier.ShiftModifier:
        parts.append("Shift")
    if modifiers & Qt.KeyboardModifier.AltModifier:
        parts.append("Alt")
    if modifiers & Qt.KeyboardModifier.MetaModifier:
        parts.append("Meta")
    
    key_name = codeToKey(key)
    if not key_name:
        return None
    parts.append(key_name)
    
    return "+".join(parts) if parts else None


def codeToKey(key):
    """将键值映射为可读字符串"""
    key_map = {
        Qt.Key.Key_A: "A", Qt.Key.Key_B: "B", Qt.Key.Key_C: "C", Qt.Key.Key_D: "D",
        Qt.Key.Key_E: "E", Qt.Key.Key_F: "F", Qt.Key.Key_G: "G", Qt.Key.Key_H: "H",
        Qt.Key.Key_I: "I", Qt.Key.Key_J: "J", Qt.Key.Key_K: "K", Qt.Key.Key_L: "L",
        Qt.Key.Key_M: "M", Qt.Key.Key_N: "N", Qt.Key.Key_O: "O", Qt.Key.Key_P: "P",
        Qt.Key.Key_Q: "Q", Qt.Key.Key_R: "R", Qt.Key.Key_S: "S", Qt.Key.Key_T: "T",
        Qt.Key.Key_U: "U", Qt.Key.Key_V: "V", Qt.Key.Key_W: "W", Qt.Key.Key_X: "X",
        Qt.Key.Key_Y: "Y", Qt.Key.Key_Z: "Z",
        Qt.Key.Key_0: "0", Qt.Key.Key_1: "1", Qt.Key.Key_2: "2", Qt.Key.Key_3: "3", Qt.Key.Key_4: "4", Qt.Key.Key_5: "5", Qt.Key.Key_6: "6", Qt.Key.Key_7: "7", Qt.Key.Key_8: "8", Qt.Key.Key_9: "9",
        Qt.Key.Key_F1: "F1", Qt.Key.Key_F2: "F2", Qt.Key.Key_F3: "F3", Qt.Key.Key_F4: "F4", Qt.Key.Key_F5: "F5", Qt.Key.Key_F6: "F6", Qt.Key.Key_F7: "F7", Qt.Key.Key_F8: "F8", Qt.Key.Key_F9: "F9", Qt.Key.Key_F10: "F10", Qt.Key.Key_F11: "F11", Qt.Key.Key_F12: "F12",
        Qt.Key.Key_Return: "Return", Qt.Key.Key_Enter: "Enter", Qt.Key.Key_Tab: "Tab", Qt.Key.Key_Space: "Space",
        Qt.Key.Key_Backspace: "Backspace", Qt.Key.Key_Delete: "Delete",
        Qt.Key.Key_Left: "Left", Qt.Key.Key_Right: "Right", Qt.Key.Key_Up: "Up", Qt.Key.Key_Down: "Down",
        Qt.Key.Key_Home: "Home", Qt.Key.Key_End: "End", Qt.Key.Key_PageUp: "PageUp", Qt.Key.Key_PageDown: "PageDown",
        Qt.Key.Key_Insert: "Insert", Qt.Key.Key_Help: "Help",
        Qt.Key.Key_Pause: "Pause", Qt.Key.Key_Print: "Print",
    }
    return key_map.get(key)


class KeyCaptureFilter(QObject):
    """键盘事件过滤器，用于捕获快捷键设置时的按键"""

    key_captured = Signal(str)
    capture_cancelled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pressed_keys = set()
        self._max_keys = set()
        self._last_seq = None

    def reset(self):
        """重置捕获状态"""
        self._pressed_keys = set()
        self._max_keys = set()
        self._last_seq = None

    def eventFilter(self, obj, event):
        event_type = event.type()
        is_key_press = event_type == QEvent.Type.KeyPress
        is_key_release = event_type == QEvent.Type.KeyRelease

        if is_key_press or is_key_release:
            key = event.key()

            if key == Qt.Key.Key_Escape:
                self.capture_cancelled.emit()
                self.reset()
                self.key_captured.emit("")
                return True

            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Tab, Qt.Key.Key_Space, Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
                return True

            modifiers = event.modifiers()
            key_name = codeToKey(key)

            if is_key_press:
                if key_name:
                    self._pressed_keys.add(key_name)
                else:
                    if modifiers & Qt.KeyboardModifier.ControlModifier:
                        self._pressed_keys.add('ctrl')
                    if modifiers & Qt.KeyboardModifier.ShiftModifier:
                        self._pressed_keys.add('shift')
                    if modifiers & Qt.KeyboardModifier.AltModifier:
                        self._pressed_keys.add('alt')
                    if modifiers & Qt.KeyboardModifier.MetaModifier:
                        self._pressed_keys.add('meta')
                self._max_keys = self._max_keys | self._pressed_keys

                seq = self._buildSeq(self._max_keys)
                if seq and seq != self._last_seq:
                    self._last_seq = seq
                    self.key_captured.emit(seq)
            else:
                if key_name:
                    self._pressed_keys.discard(key_name)
                else:
                    if not (modifiers & Qt.KeyboardModifier.ControlModifier):
                        self._pressed_keys.discard('ctrl')
                    if not (modifiers & Qt.KeyboardModifier.ShiftModifier):
                        self._pressed_keys.discard('shift')
                    if not (modifiers & Qt.KeyboardModifier.AltModifier):
                        self._pressed_keys.discard('alt')
                    if not (modifiers & Qt.KeyboardModifier.MetaModifier):
                        self._pressed_keys.discard('meta')

                if not self._pressed_keys and self._max_keys:
                    seq = self._buildSeq(self._max_keys)
                    if seq and seq != self._last_seq:
                        self._last_seq = seq
                        self.key_captured.emit(seq)
                    self._max_keys = set()
                    self._pressed_keys = set()
            return True

        return super().eventFilter(obj, event)

    def _buildSeq(self, keys):
        other_keys = keys - {'ctrl', 'shift', 'alt', 'meta'}
        if not other_keys:
            return None
        parts = []
        if 'ctrl' in keys:
            parts.append("Ctrl")
        if 'shift' in keys:
            parts.append("Shift")
        if 'alt' in keys:
            parts.append("Alt")
        if 'meta' in keys:
            parts.append("Meta")
        other_keys = keys - {'ctrl', 'shift', 'alt', 'meta'}
        for key in sorted(other_keys):
            parts.append(key)
        return "+".join(parts) if parts else None

