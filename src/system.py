"""跨平台适配模块，谨慎导入本地模块"""
import os
import sys
import subprocess
from pathlib import Path
from functools import lru_cache

from PySide6.QtGui import QPixmap, QImage, QIcon

from src.util import logger, getAppPath, APP_NAME

if sys.platform == "win32":
    import winreg
    from ctypes import WINFUNCTYPE, Structure, windll, c_int, c_int32, c_uint, c_uint16, c_uint32, c_byte, c_void_p, cast, c_wchar, byref, POINTER, sizeof, HRESULT, c_ulong, memset, string_at

    SYSTEM_ACT = {
        "命令提示符": "Terminal",
        "回收站": "shell:::{645FF040-5081-101B-9F08-00AA002F954E}",
        "所有任务": "shell:::{ED7BA470-8E54-465E-825C-99712043E01C}",
        "网络连接": "shell:::{7007ACC7-3202-11D1-AAD2-00805FC1270E}",
        "系统信息": "msinfo32",
        }
    
    def setAutoStart(enabled: bool) -> bool:
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE
            )
            if enabled:
                app_path = getAppPath()
                if not app_path:
                    logger.error("开机自启失败：无法获取程序路径")
                    return False
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, f'"{app_path}"')
                logger.info("开机自启已启用")
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                    logger.info("开机自启已禁用")
                except FileNotFoundError:
                    pass
            winreg.CloseKey(key)
            return True
        except Exception:
            logger.exception("设置开机自启失败")
            return False

    class GUID(Structure):
        _fields_ = [
            ("Data1", c_uint32),
            ("Data2", c_uint16),
            ("Data3", c_uint16),
            ("Data4", c_byte * 8)
        ]

    class SHFILEINFO(Structure):
        _fields_ = [
            ("hIcon", c_void_p),
            ("iIcon", c_int),
            ("dwAttributes", c_uint),
            ("szDisplayName", c_wchar * 260),
            ("szTypeName", c_wchar * 80)
        ]

    class BITMAPINFOHEADER(Structure):
        _fields_ = [
            ("biSize", c_uint32),
            ("biWidth", c_int32),
            ("biHeight", c_int32),
            ("biPlanes", c_uint16),
            ("biBitCount", c_uint16),
            ("biCompression", c_uint32),
            ("biSizeImage", c_uint32),
            ("biXPelsPerMeter", c_int32),
            ("biYPelsPerMeter", c_int32),
            ("biClrUsed", c_uint32),
            ("biClrImportant", c_uint32)
        ]

    SHIL_JUMBO = 0x4
    SHGFI_SYSICONINDEX = 0x4000

    IID_IImageList = GUID(
        0x46EB5926, 0x582E, 0x4017,
        (0x9F, 0xDF, 0xE8, 0x99, 0x8D, 0xAA, 0x09, 0x50)
    )

    @lru_cache(maxsize=256)
    def getFileIcon(file_path: str, size: int = 128) -> QIcon:
        """获取文件的高分辨率图标（带缓存）"""
        if not os.path.exists(file_path):
            return QIcon()
        shinfo = SHFILEINFO()
        result = windll.shell32.SHGetFileInfoW(
            file_path, 0, byref(shinfo), sizeof(shinfo), SHGFI_SYSICONINDEX
        )
        if result == 0:
            return QIcon()
        index = shinfo.iIcon

        ppv = c_void_p()
        SHGetImageList = windll.shell32.SHGetImageList
        SHGetImageList.argtypes = [c_int, POINTER(GUID), POINTER(c_void_p)]
        SHGetImageList.restype = HRESULT
        hr = SHGetImageList(SHIL_JUMBO, byref(IID_IImageList), byref(ppv))
        if hr != 0 or not ppv:
            return QIcon()
        image_list = ppv

        vtable_ptr = cast(image_list, POINTER(c_void_p))
        vtable = cast(vtable_ptr.contents, POINTER(c_void_p))

        Release = cast(vtable[2], WINFUNCTYPE(c_ulong, c_void_p))
        GetIconFunc = WINFUNCTYPE(HRESULT, c_void_p, c_int, c_uint, POINTER(c_void_p))
        get_icon_func = cast(vtable[10], GetIconFunc)

        hicon = c_void_p()
        hr = get_icon_func(image_list, index, 0, byref(hicon))
        if hr != 0 or not hicon:
            Release(image_list)
            return QIcon()

        screen_dc = windll.user32.GetDC(0)
        if not screen_dc:
            windll.user32.DestroyIcon(hicon)
            Release(image_list)
            return QIcon()

        mem_dc = windll.gdi32.CreateCompatibleDC(screen_dc)
        if not mem_dc:
            windll.user32.ReleaseDC(0, screen_dc)
            windll.user32.DestroyIcon(hicon)
            Release(image_list)
            return QIcon()

        bmi = BITMAPINFOHEADER()
        bmi.biSize = sizeof(BITMAPINFOHEADER)
        bmi.biWidth = size
        bmi.biHeight = -size
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = 0

        bits = c_void_p()
        hbitmap = windll.gdi32.CreateDIBSection(screen_dc, byref(bmi), 0, byref(bits), 0, 0)
        if not hbitmap or not bits:
            windll.gdi32.DeleteDC(mem_dc)
            windll.user32.ReleaseDC(0, screen_dc)
            windll.user32.DestroyIcon(hicon)
            Release(image_list)
            return QIcon()

        old_bmp = windll.gdi32.SelectObject(mem_dc, hbitmap)
        memset(bits, 0, size * size * 4)
        windll.user32.DrawIconEx(mem_dc, 0, 0, hicon, size, size, 0, None, 0x0003)
        windll.gdi32.SelectObject(mem_dc, old_bmp)

        pixel_bytes = string_at(bits, size * size * 4)
        img = QImage(pixel_bytes, size, size, QImage.Format_ARGB32)

        icon = QIcon(QPixmap.fromImage(img))
        if icon.isNull():
            icon = QIcon()

        windll.gdi32.DeleteObject(hbitmap)
        windll.gdi32.DeleteDC(mem_dc)
        windll.user32.ReleaseDC(0, screen_dc)
        windll.user32.DestroyIcon(hicon)
        Release(image_list)

        return icon


elif sys.platform == "linux":

    SYSTEM_ACT = {
        "回收站": "trash://",
    }

    def autoStartDir() -> Path:
        xdg_config = os.environ.get("XDG_CONFIG_HOME", "")
        if xdg_config:
            config_dir = Path(xdg_config)
        else:
            config_dir = Path.home() / ".config"
        return config_dir / "autostart"

    def setAutoStart(enabled: bool) -> bool:
        autostart_dir = autoStartDir()
        desktop_file = autostart_dir / f"{APP_NAME}.desktop"
        app_path = getAppPath()
        if not app_path:
            return False
        
        if enabled:
            autostart_dir.mkdir(parents=True, exist_ok=True)
            desktop_content = f"""[Desktop Entry]
    Type=Application
    Name={APP_NAME}
    Exec={app_path}
    Hidden=false
    NoDisplay=false
    X-GNOME-Autostart-enabled=true
    """
            try:
                desktop_file.write_text(desktop_content, encoding='utf-8')
                return True
            except Exception:
                logger.exception("设置 Linux 开机启动失败")
                return False
        else:
            if desktop_file.exists():
                try:
                    desktop_file.unlink()
                    return True
                except Exception:
                    logger.exception("关闭 Linux 开机启动失败")
                    return False
            return True
        
    def getFileIcon():
        pass


elif sys.platform == "darwin":

    SYSTEM_ACT = {
        "回收站": os.path.expanduser("~/.Trash"),
    }

    def setAutoStart(enabled: bool) -> bool:
        plist_path = Path.home() / "Library" / "LaunchAgents" / f"com.{APP_NAME.lower()}.plist"
        app_path = getAppPath()
        if not app_path:
            return False
        
        if enabled:
            plist_path.parent.mkdir(parents=True, exist_ok=True)
            plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
    <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
    <plist version="1.0">
    <dict>
        <key>Label</key>
        <string>com.{APP_NAME.lower()}</string>
        <key>ProgramArguments</key>
        <array>
            <string>{app_path}</string>
        </array>
        <key>RunAtLoad</key>
        <true/>
    </dict>
    </plist>"""
            try:
                plist_path.write_text(plist_content, encoding='utf-8')
                subprocess.run(["launchctl", "load", str(plist_path)], check=False)
                return True
            except Exception:
                logger.exception("设置 macOS 开机启动失败")
                return False
        else:
            if plist_path.exists():
                try:
                    subprocess.run(["launchctl", "unload", str(plist_path)], check=False)
                    plist_path.unlink()
                    return True
                except Exception:
                    logger.exception("关闭 macOS 开机启动失败")
                    return False
            return True

    def getFileIcon():
        pass
