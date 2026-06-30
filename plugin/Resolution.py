from ctypes import WinDLL, wintypes, byref, sizeof, Structure
from typing import Optional, List, Tuple

from PySide6.QtWidgets import QVBoxLayout, QLabel, QDialog, QTextEdit, QMenu
from PySide6.QtGui import QAction

from src.plugin import PluginBase
from src.util import logger, messageBox, dialogBox

CDS_UPDATEREGISTRY = 0x01
CDS_TEST = 0x00000002
ENUM_CURRENT_SETTINGS = 0xFFFFFFFF
DM_PELSWIDTH = 0x00080000
DM_PELSHEIGHT = 0x00100000
DISP_CHANGE_SUCCESSFUL = 0

class ResolutionPlugin(PluginBase):
    """快速修改分辨率，仅适用于 Windows 。不用管缩放，Windows 会自动切换，只要改分辨率"""

    version = "1.0.0"
    description = "修改分辨率"

    def __init__(self, main_window):
        super().__init__(main_window)
        self.resolutions = ["1280×720", "1920×1080", "1920×1200", "2560×1440", "2560×1600", "3200×2000"]
        self._original_devmode = None

    def loadConfig(self):
        super().loadConfig()
        data = self.settings.get("Resolution", [])
        if data:
            self.resolutions = data

    def initialize(self):
        if not super().initialize():
            return

    def _save_settings(self):
        self.settings["Resolution"] = self.resolutions
        self.saveConfig()

    def getAction(self):
        menu = QMenu()

        settings_action = QAction("设置", self.main_window)
        settings_action.triggered.connect(self._show_settings)
        menu.addAction(settings_action)
        menu.addSeparator()

        for res_str in self.resolutions:
            parsed = parse_resolution(res_str)
            if parsed is None:
                continue
            w, h = parsed
            action = QAction(res_str, self.main_window)
            action.triggered.connect(lambda checked, w=w, h=h: self._switch_resolution(w, h))
            menu.addAction(action)

        return menu

    def _show_settings(self):
        self.initialize()
        dialog = ResolutionSettingsDialog(self.main_window, self.resolutions)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.resolutions = dialog.resolutions
            self._save_settings()
            logger.info(f"分辨率列表已更新: {self.resolutions}")

    def _switch_resolution(self, w, h):
        self.initialize()
        ok, err = test_resolution(w, h)
        if not ok:
            messageBox(self.main_window, "不支持", f"分辨率 {w}×{h} 不可用：{err}", 1)
            return

        try:
            self._original_devmode = get_current_devmode()
        except RuntimeError as e:
            messageBox(self.main_window, "错误", f"备份当前分辨率失败：{e}", 1)
            return

        if not apply_resolution(w, h):
            messageBox(self.main_window, "错误", f"应用分辨率 {w}×{h} 失败", 1)
            return

        self._confirm_keep_resolution(w, h)

    def _confirm_keep_resolution(self, w, h):
        msg = f"分辨率已临时更改为 {w}×{h}\n是否保留此分辨率？"
        if messageBox(self.main_window, "分辨率已更改", msg, 2):
            apply_resolution(w, h, permanent=True)
            logger.info(f"分辨率已永久更改为 {w}×{h}")
        else:
            self._restore_resolution()

    def _restore_resolution(self):
        if self._original_devmode is None:
            return
        try:
            devmode = get_current_devmode()
        except RuntimeError:
            return
        devmode.dmPelsWidth = self._original_devmode.dmPelsWidth
        devmode.dmPelsHeight = self._original_devmode.dmPelsHeight
        devmode.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT
        user32.ChangeDisplaySettingsW(byref(devmode), 0)
        self._original_devmode = None
        logger.info("分辨率已恢复")


class DEVMODE(Structure):
    _fields_ = [
        ("dmDeviceName", wintypes.WCHAR * 32),
        ("dmSpecVersion", wintypes.WORD),
        ("dmDriverVersion", wintypes.WORD),
        ("dmSize", wintypes.WORD),
        ("dmDriverExtra", wintypes.WORD),
        ("dmFields", wintypes.DWORD),
        ("dmOrientation", wintypes.WORD),
        ("dmPaperSize", wintypes.WORD),
        ("dmPaperLength", wintypes.WORD),
        ("dmPaperWidth", wintypes.WORD),
        ("dmScale", wintypes.WORD),
        ("dmCopies", wintypes.WORD),
        ("dmDefaultSource", wintypes.WORD),
        ("dmPrintQuality", wintypes.WORD),
        ("dmColor", wintypes.WORD),
        ("dmDuplex", wintypes.WORD),
        ("dmYResolution", wintypes.WORD),
        ("dmTTOption", wintypes.WORD),
        ("dmCollate", wintypes.WORD),
        ("dmFormName", wintypes.WCHAR * 32),
        ("dmLogPixels", wintypes.WORD),
        ("dmBitsPerPel", wintypes.DWORD),
        ("dmPelsWidth", wintypes.DWORD),
        ("dmPelsHeight", wintypes.DWORD),
        ("dmDisplayFlags", wintypes.DWORD),
        ("dmDisplayFrequency", wintypes.DWORD),
        ("dmICMMethod", wintypes.DWORD),
        ("dmICMIntent", wintypes.DWORD),
        ("dmMediaType", wintypes.DWORD),
        ("dmDitherType", wintypes.DWORD),
        ("dmReserved1", wintypes.DWORD),
        ("dmReserved2", wintypes.DWORD),
        ("dmPanningWidth", wintypes.DWORD),
        ("dmPanningHeight", wintypes.DWORD),
    ]

user32 = WinDLL('user32', use_last_error=True)

def get_current_devmode():
    devmode = DEVMODE()
    devmode.dmSize = sizeof(DEVMODE)
    if not user32.EnumDisplaySettingsW(None, ENUM_CURRENT_SETTINGS, byref(devmode)):
        raise RuntimeError("无法获取当前显示设置")
    return devmode

def test_resolution(width, height):
    try:
        devmode = get_current_devmode()
    except RuntimeError as e:
        return False, str(e)
    devmode.dmPelsWidth = width
    devmode.dmPelsHeight = height
    devmode.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT
    result = user32.ChangeDisplaySettingsW(byref(devmode), CDS_TEST)
    if result == DISP_CHANGE_SUCCESSFUL:
        return True, None
    error_map = {
        -2: "模式不支持",
        -1: "更改失败",
        -3: "无法更新注册表",
        -4: "无效标志",
        -5: "无效参数",
        -6: "双视图模式不支持",
    }
    return False, error_map.get(result, f"未知错误 {result}")

def apply_resolution(width, height, permanent=False):
    devmode = get_current_devmode()
    devmode.dmPelsWidth = width
    devmode.dmPelsHeight = height
    devmode.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT
    flags = CDS_UPDATEREGISTRY if permanent else 0
    return user32.ChangeDisplaySettingsW(byref(devmode), flags) == DISP_CHANGE_SUCCESSFUL

def parse_resolution(text: str) -> Optional[Tuple[int, int]]:
    try:
        parts = text.strip().split("×")
        if len(parts) != 2:
            return None
        w, h = int(parts[0]), int(parts[1])
        if w > 0 and h > 0:
            return w, h
    except (ValueError, IndexError):
        pass
    return None


class ResolutionSettingsDialog(QDialog):
    def __init__(self, parent, resolutions: List[str]):
        super().__init__(parent)
        self.setWindowTitle("分辨率设置")
        self.setMinimumSize(350, 300)
        self.resolutions = resolutions[:]
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        label = QLabel("每行一个分辨率，格式如 1920×1080")
        layout.addWidget(label)

        self.edit = QTextEdit()
        layout.addWidget(self.edit)

        dialogBox(layout, self, show=False)

        self._load_data()

    def _load_data(self):
        self.edit.setPlainText("\n".join(self.resolutions))

    def accept(self):
        lines = self.edit.toPlainText().strip().split("\n")
        resolutions = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if parse_resolution(line) is None:
                messageBox(self, "格式错误", f"无效格式：{line}\n应为 1920×1080", 1)
                return
            resolutions.append(line)
        if not resolutions:
            messageBox(self, "警告", "至少需要一条分辨率", 1)
            return
        self.resolutions = resolutions
        super().accept()
