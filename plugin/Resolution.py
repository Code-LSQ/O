from ctypes import WinDLL, wintypes, byref, sizeof, Structure
from typing import Optional, Tuple

from PySide6.QtWidgets import QVBoxLayout, QLabel, QWidget, QTextEdit, QMenu
from PySide6.QtGui import QAction

from src.plugin import PluginBase
from src.util import logger, messageBox

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

    def __init__(self, main=None):
        super().__init__(main=main)
        self.resolutions = ["1280×720", "1920×1080", "1920×1200", "2560×1440", "2560×1600", "3200×2000"]
        self._original_devmode = None
        self._res_edit = None

    def loadConfig(self):
        super().loadConfig()
        data = self.settings.get("value", [])
        if data:
            self.resolutions = data

    def initialize(self):
        if not super().initialize():
            return

    def configWidget(self, parent=None):
        w = QWidget(parent)
        layout = QVBoxLayout(w)
        layout.addWidget(QLabel("每行一个分辨率，格式如 1920×1080"))
        self._res_edit = QTextEdit()
        self._res_edit.setPlainText("\n".join(self.resolutions))
        w.destroyed.connect(lambda: setattr(self, '_res_edit', None))
        layout.addWidget(self._res_edit)
        return w

    def saveConfig(self, save=True):
        if self._res_edit is not None:
            lines = self._res_edit.toPlainText().strip().split("\n")
            resolutions = []
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                if parseResolution(line) is None:
                    messageBox(self._res_edit, "格式错误", f"无效格式：{line}\n应为 1920×1080", 1)
                    return False
                resolutions.append(line)
            if not resolutions:
                messageBox(self._res_edit, "警告", "至少需要一条分辨率", 1)
                return False
            self.resolutions = resolutions
            self.settings["value"] = resolutions
        # 校验失败返回 False，SettingsDialog.accept() 遇到 False 会放弃关闭，用户修正后重新提交。不要 raise，会打乱 accept 的流程。
        return super().saveConfig(save=save)

    def getAction(self):
        menu = QMenu()
        for res_str in self.resolutions:
            parsed = parseResolution(res_str)
            if parsed is None:
                continue
            w, h = parsed
            action = QAction(res_str, self.main)
            action.triggered.connect(lambda checked, w=w, h=h: self._switchResolution(w, h))
            menu.addAction(action)

        return menu

    def _switchResolution(self, w, h):
        self.initialize()
        ok, err = testResolution(w, h)
        if not ok:
            messageBox(self.main, "不支持", f"分辨率 {w}×{h} 不可用：{err}", 1)
            return

        try:
            self._original_devmode = getDevmode()
        except RuntimeError as e:
            messageBox(self.main, "错误", f"备份当前分辨率失败：{e}", 1)
            return

        if not applyResolution(w, h):
            messageBox(self.main, "错误", f"应用分辨率 {w}×{h} 失败", 1)
            return

        self._confirmResolution(w, h)

    def _confirmResolution(self, w, h):
        msg = f"分辨率已临时更改为 {w}×{h}\n是否保留此分辨率？"
        if messageBox(self.main, "分辨率已更改", msg, 2):
            applyResolution(w, h, permanent=True)
            logger.info(f"分辨率已永久更改为 {w}×{h}")
        else:
            self._restoreResolution()

    def _restoreResolution(self):
        if self._original_devmode is None:
            return
        try:
            devmode = getDevmode()
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

def getDevmode():
    devmode = DEVMODE()
    devmode.dmSize = sizeof(DEVMODE)
    if not user32.EnumDisplaySettingsW(None, ENUM_CURRENT_SETTINGS, byref(devmode)):
        raise RuntimeError("无法获取当前显示设置")
    return devmode

def testResolution(width, height):
    try:
        devmode = getDevmode()
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

def applyResolution(width, height, permanent=False):
    devmode = getDevmode()
    devmode.dmPelsWidth = width
    devmode.dmPelsHeight = height
    devmode.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT
    flags = CDS_UPDATEREGISTRY if permanent else 0
    return user32.ChangeDisplaySettingsW(byref(devmode), flags) == DISP_CHANGE_SUCCESSFUL

def parseResolution(text: str) -> Optional[Tuple[int, int]]:
    try:
        parts = text.strip().split("×")
        if len(parts) != 2:
            return None
        w, h = int(parts[0]), int(parts[1])
        if w > 0 and h > 0:
            return w, h
    except (ValueError, IndexError):
        logger.exception("分辨率字符串解析失败")
    return None
