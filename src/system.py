"""跨平台适配模块，谨慎导入本地模块"""
import os
import sys
import shutil
import subprocess
from pathlib import Path
from functools import lru_cache
from datetime import datetime

from PySide6.QtWidgets import QFileIconProvider
from PySide6.QtCore import QFileInfo
from PySide6.QtGui import QPixmap, QImage, QIcon

from src.util import APP_NAME, app_path, logger, icon_dir, Interpret

if sys.platform == "win32":
    import winreg
    from ctypes import WINFUNCTYPE, Structure, windll, c_int, c_int32, c_uint, c_uint16, c_uint32, c_byte, c_void_p, cast, c_wchar, byref, POINTER, sizeof, HRESULT, c_ulong, memset, string_at, c_wchar_p

    # 命令提示符特殊处理
    # CLSID 统一使用 shell::: 的形式，更规范，兼容性好。已确认 ::{...} 格式有小部分不兼容
    SYSTEM_ACT = {
        "命令提示符": "Terminal",
        "回收站": "shell:::{645FF040-5081-101B-9F08-00AA002F954E}",
        "此电脑": "shell:::{20D04FE0-3AEA-1069-A2D8-08002B30309D}",
        "所有任务": "shell:::{ED7BA470-8E54-465E-825C-99712043E01C}",
        "网络": "shell:::{F02C1A0D-BE21-4350-88B0-7367FC96EF3C}",
        "网络连接": "shell:::{7007ACC7-3202-11D1-AAD2-00805FC1270E}",
        "网络和共享中心": "shell:::{8E908FC9-BECC-40f6-915B-F4CA0E70D03D}",
        "所有控制面板项": "shell:::{21EC2020-3AEA-1069-A2DD-08002B30309D}",
        "设备和打印机": "shell:::{A8A91A66-3A7D-4424-8D24-04E180695C7A}",
        "Windows 工具": "shell:::{D20EA4E1-3957-11d2-A40B-0C5020524153}",
        "文件历史记录": "shell:::{F6B6E965-E9B2-444B-9286-10C9152EDBC5}",
        "添加网络位置": "shell:::{D4480A50-BA28-11d1-8E75-00C04FA31A86}",
        "屏幕设置": "ms-settings:display",
        }
    
    def setAutoStart(enabled: bool) -> bool:
        key = None
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE
            )
            if enabled:
                if Interpret:
                    reg_cmd = f'"{sys.executable}" "{app_path}"'
                else:
                    reg_cmd = f'"{app_path}"'
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, reg_cmd)
                logger.info("开机自启已启用")
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                    logger.info("开机自启已禁用")
                except FileNotFoundError:
                    pass
            return True
        except Exception:
            logger.exception("设置开机自启失败")
            return False
        finally:
            if key:
                winreg.CloseKey(key)

    def isKeyDown(vk: int) -> bool:
        """查询虚拟键码是否处于物理按下状态"""
        return bool(windll.user32.GetAsyncKeyState(vk) & 0x8000)

    def deleteRegistry(key_handle, sub_key):
        try:
            sub_handle = winreg.OpenKey(key_handle, sub_key, 0, winreg.KEY_ALL_ACCESS)
        except FileNotFoundError:
            return
        try:
            while True:
                try:
                    child = winreg.EnumKey(sub_handle, 0)
                    deleteRegistry(sub_handle, child)
                except OSError:
                    break
        finally:
            winreg.CloseKey(sub_handle)
        try:
            winreg.DeleteKey(key_handle, sub_key)
            logger.info(f"已删除注册表键: {sub_key}")
        except OSError:
            logger.exception(f"删除注册表键失败: {sub_key}")

    def isMenuRegister() -> bool:
        shell_keys = [
            rf"Software\Classes\*\shell\{APP_NAME}",
            rf"Software\Classes\Directory\shell\{APP_NAME}",
        ]
        for shell_key in shell_keys:
            try:
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, shell_key, 0, winreg.KEY_READ)
                winreg.CloseKey(key)
            except FileNotFoundError:
                logger.info(f"右键菜单注册表键缺失: {shell_key}")
                return False
            except Exception:
                return False
        logger.info("右键菜单注册表键已存在，跳过写入")
        return True

    def setMenu(enabled: bool) -> bool:
        shell_keys = [
            rf"Software\Classes\*\shell\{APP_NAME}",
            rf"Software\Classes\Directory\shell\{APP_NAME}",
        ]
        for shell_key in shell_keys:
            try:
                if enabled:
                    key = winreg.CreateKeyEx(
                        winreg.HKEY_CURRENT_USER, shell_key, 0, winreg.KEY_SET_VALUE
                    )
                    try:
                        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f"使用 {APP_NAME} 打开")
                        winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, f'"{app_path}",0')
                        cmd_key = winreg.CreateKeyEx(
                            winreg.HKEY_CURRENT_USER, shell_key + r"\command", 0, winreg.KEY_SET_VALUE
                        )
                        try:
                            if Interpret:
                                cmd = f'"{sys.executable}" "{app_path}" "%1"'
                            else:
                                cmd = f'"{app_path}" "%1"'
                            winreg.SetValueEx(cmd_key, "", 0, winreg.REG_SZ, cmd)
                        finally:
                            winreg.CloseKey(cmd_key)
                    finally:
                        winreg.CloseKey(key)
                else:
                    deleteRegistry(winreg.HKEY_CURRENT_USER, shell_key)
            except Exception:
                logger.exception(f"设置右键菜单失败: {shell_key}")
                return False
        logger.info(f"右键菜单已{'注册' if enabled else '移除'}")
        return True

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

    def _renderHICON(hicon, size: int) -> QIcon:
        """将 HICON 绘制到 DIB Section 并返回 QIcon"""
        if not hicon:
            return QIcon()
        screen_dc = windll.user32.GetDC(0)
        if not screen_dc:
            return QIcon()
        mem_dc = windll.gdi32.CreateCompatibleDC(screen_dc)
        if not mem_dc:
            windll.user32.ReleaseDC(0, screen_dc)
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
            return QIcon()
        old_bmp = windll.gdi32.SelectObject(mem_dc, hbitmap)
        memset(bits, 0, size * size * 4)
        windll.user32.DrawIconEx(mem_dc, 0, 0, hicon, size, size, 0, None, 0x0003)
        windll.gdi32.SelectObject(mem_dc, old_bmp)
        pixel_bytes = string_at(bits, size * size * 4)
        img = QImage(pixel_bytes, size, size, QImage.Format_ARGB32)
        icon = QIcon(QPixmap.fromImage(img))
        windll.gdi32.DeleteObject(hbitmap)
        windll.gdi32.DeleteDC(mem_dc)
        windll.user32.ReleaseDC(0, screen_dc)
        return icon

    def _getIconFromList(index: int, size: int) -> QIcon:
        """从系统图片列表中获取指定索引的高分辨率图标"""
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

        icon = _renderHICON(hicon, size)
        Release(image_list)
        windll.user32.DestroyIcon(hicon)
        return icon

    @lru_cache(maxsize=256)
    def getFileIcon(file_path: str, size: int = 128) -> QIcon:
        """获取文件或 CLSID 的图标（带缓存）"""
        if file_path.startswith("shell:::"):
            if file_path == "shell:::{645FF040-5081-101B-9F08-00AA002F954E}":
                return QIcon(str(icon_dir / "Recycle.png"))
            return getCLSIDIcon(file_path, size * 4)
        if file_path == "Terminal":
            file_path = "C:\\Windows\\System32\\cmd.exe"
        if not os.path.exists(file_path):
            return QIcon()
        if Path(file_path).suffix.lower() == ".msc":
            shinfo = SHFILEINFO()
            result = windll.shell32.SHGetFileInfoW(
                file_path, 0, byref(shinfo), sizeof(shinfo), SHGFI_SYSICONINDEX
            )
            if result != 0:
                return _getIconFromList(shinfo.iIcon, size * 2)
        return QFileIconProvider().icon(QFileInfo(file_path))

    @lru_cache(maxsize=256)
    def getCLSIDIcon(clsid: str, size: int = 128) -> QIcon:
        """获取 CLSID 的高分辨率图标（带缓存），接受 shell:::{...} 格式"""
        # 通过 SHParseDisplayName 将 CLSID 路径解析为 PIDL
        SHParseDisplayName = windll.shell32.SHParseDisplayName
        SHParseDisplayName.argtypes = [c_wchar_p, c_void_p, POINTER(c_void_p), c_uint, POINTER(c_uint)]
        SHParseDisplayName.restype = HRESULT
        pidl = c_void_p()
        try:
            hr = SHParseDisplayName(clsid, None, byref(pidl), 0, None)
        except OSError:
            hr = -1
        if hr != 0 or not pidl:
            return QIcon()
        try:
            # 用单独的 DLL 句柄避免 argtypes 冲突
            _shell32 = windll.LoadLibrary("shell32.dll")
            _SHGetInfo = _shell32.SHGetFileInfoW
            _SHGetInfo.argtypes = [c_void_p, c_uint, POINTER(SHFILEINFO), c_uint, c_uint]
            _SHGetInfo.restype = c_void_p
            SHGFI_PIDL = 0x8
            shinfo = SHFILEINFO()
            result = _SHGetInfo(pidl, 0, byref(shinfo), sizeof(shinfo), SHGFI_PIDL | SHGFI_SYSICONINDEX)
            if result == 0:
                return QIcon()
            icon = _getIconFromList(shinfo.iIcon, size)
        finally:
            ILFree = windll.shell32.ILFree
            ILFree.argtypes = [c_void_p]
            ILFree.restype = None
            ILFree(pidl)
        return icon

    def moveTrash(path):
        try:
            path = os.path.abspath(path)
            escaped = path.replace("'", "''")
            result = subprocess.run(
                ["powershell", "-Command",
                    f"Add-Type -AssemblyName Microsoft.VisualBasic; [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile('{escaped}', 'OnlyErrorDialogs', 'SendToRecycleBin')"],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                logger.info(f"{path} 成功移动到回收站")
                return True
            logger.error(f"{path} 移动到回收站失败: {result.stderr}")
        except Exception:
            logger.exception(f"{path} 移动到回收站失败")
        return False


elif sys.platform == "linux":

    SYSTEM_ACT = {
        "命令提示符": "Terminal",
        "回收站": "trash://",
    }

    def isKeyDown(vk: int) -> bool:
        """查询虚拟键码是否处于物理按下状态（非 Windows 恒返回 False）"""
        return False

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
                desktop_file.write_text(desktop_content, encoding="utf-8")
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
        
    @lru_cache(maxsize=256)
    def getFileIcon(file_path: str, size: int = 128) -> QIcon:
        if file_path == "Terminal":
            for p in ["/usr/bin/gnome-terminal", "/usr/bin/xterm", "/usr/bin/konsole"]:
                if os.path.exists(p):
                    return QFileIconProvider().icon(QFileInfo(p))
            return QIcon()
        if os.path.exists(file_path):
            return QFileIconProvider().icon(QFileInfo(file_path))
        return QIcon()

    def moveTrash(path):
        try:
            p = Path(path).resolve()
            trash_dir = Path.home() / ".local/share/Trash"
            files_dir = trash_dir / "files"
            info_dir = trash_dir / "info"
            files_dir.mkdir(parents=True, exist_ok=True)
            info_dir.mkdir(parents=True, exist_ok=True)
            basename = p.name
            dest = files_dir / basename
            info_path = info_dir / f"{basename}.trashinfo"
            if dest.exists() or info_path.exists():
                stem = p.stem
                ext = p.suffix
                counter = 1
                while True:
                    new_name = f"{stem}.{counter}{ext}"
                    dest = files_dir / new_name
                    info_path = info_dir / f"{new_name}.trashinfo"
                    if not dest.exists() and not info_path.exists():
                        logger.info(f"回收站中已存在 {basename}，重命名为 {dest.name}")
                        break
                    counter += 1
            shutil.move(p, dest)
            with open(info_path, "w", encoding="utf-8") as f:
                f.write("[Trash Info]\n")
                f.write(f"Path={p}\n")
                f.write(f"DeletionDate={datetime.now().isoformat()}\n")
            logger.info(f"{p} 成功移动到回收站")
            return True
        except Exception:
            logger.exception(f"{path} 移动到回收站失败")
        return False

    def setMenu(enabled: bool) -> bool:
        return True

    def isMenuRegister() -> bool:
        return True


elif sys.platform == "darwin":

    SYSTEM_ACT = {
        "命令提示符": "Terminal",
        "回收站": os.path.expanduser("~/.Trash"),
    }

    def isKeyDown(vk: int) -> bool:
        """查询虚拟键码是否处于物理按下状态（非 Windows 恒返回 False）"""
        return False

    def setAutoStart(enabled: bool) -> bool:
        plist_path = Path.home() / "Library" / "LaunchAgents" / f"com.{APP_NAME.lower()}.plist"

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
                plist_path.write_text(plist_content, encoding="utf-8")
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

    @lru_cache(maxsize=256)
    def getFileIcon(file_path: str, size: int = 128) -> QIcon:
        if file_path == "Terminal":
            for p in ["/System/Applications/Utilities/Terminal.app", "/Applications/iTerm.app"]:
                if os.path.exists(p):
                    return QFileIconProvider().icon(QFileInfo(p))
            return QIcon()
        if os.path.exists(file_path):
            return QFileIconProvider().icon(QFileInfo(file_path))
        return QIcon()

    def moveTrash(path):
        try:
            path = os.path.abspath(path)
            result = subprocess.run(
                ["osascript", "-e",
                 f'tell application "Finder" to delete POSIX file "{path}"'],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                logger.info(f"{path} 成功移动到回收站")
                return True
            logger.error(f"{path} 移动到回收站失败: {result.stderr}")
        except Exception:
            logger.exception(f"{path} 移动到回收站失败")
        return False

    def setMenu(enabled: bool) -> bool:
        return True

    def isMenuRegister() -> bool:
        return True
