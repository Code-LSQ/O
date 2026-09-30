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

from src.api import APP_NAME, app_path, logger, icon_dir, Interpret, tr, openTerminal

if sys.platform == "win32":
    # 除插件外，只在这个代码块中使用 ctypes 和 winreg
    import winreg
    from ctypes import windll, WINFUNCTYPE, Structure, c_int, c_int32, c_uint, c_uint16, c_uint32, c_byte, c_void_p, c_wchar, c_wchar_p, c_ulong, byref, cast, POINTER, sizeof, HRESULT, memset, string_at

    # 运行方式/终端/控制台   不适合 "Windows Terminal": (["wt"], 0), 去除兼容
    # 写全 .exe，避免 Popen 和 ShellExecuteW 按各自规则解析
    Terminal = {
        "cmd": (["cmd.exe", "/c"], subprocess.CREATE_NEW_CONSOLE),
        "PowerShell": (["powershell.exe", "-Command"], subprocess.CREATE_NEW_CONSOLE),
    }

    # CommandLineToArgvW 需要显式声明返回类型，否则 ctypes 默认按 32 位 int 处理，64 位下指针会被截断，后续 LocalFree 会失败
    windll.shell32.CommandLineToArgvW.restype = POINTER(c_wchar_p)

    def splitArgs(args):
        """按 Windows 自身的命令行规则把参数字符串拆成独立参数。

        整串 args 不能当作单个列表元素交给 Popen：list2cmdline 会给它整体加引号，
        目标程序只会收到一个参数，与 ShellExecuteW 的 lpParameters（由目标按空格拆分）语义不一致，
        换个运行方式就换了参数含义。故复用系统解析器，使两种运行方式拆分结果相同。
        argv[0] 的引号处理规则与其余参数不同，故加占位前缀后从下标 1 取起。

        已知限制，均为所选 shell 的固有行为，刻意不做处理（要绕开 per-shell 的引号逻辑，
        一旦写错只会静默传错参数）：
        1. cmd/PowerShell 仍会解释**无空格 token** 中的元字符，ShellExecuteW 不会。
           例如把 URL 查询串 ?a=1&b=2 当参数传给 cmd，会在 & 处截断并试着执行后半段；
           以 ^ 开头的正则参数会被吃掉 ^。含空格的 token 由引号保护，不受影响。
        2. %VAR%（PowerShell 的 $var）即使加了引号也会被展开，无法避免。
        3. PowerShell 会把空参数丢掉（实测 '' 传不进目标程序），cmd 不会，属 PS 5.1 自身缺陷。
        需要与 ShellExecuteW 完全一致时，把该工具的运行方式设为 None。"""
        if not args or not args.strip():
            return []
        count = c_int()
        argv = windll.shell32.CommandLineToArgvW("o " + args, byref(count))
        if not argv:
            # 解析失败（如引号未闭合）时退回单参数，宁可参数错也不要不启动
            logger.warning(f"参数解析失败，按单个参数处理: {args}")
            return [args]
        try:
            return [argv[i] for i in range(1, count.value)]
        finally:
            windll.kernel32.LocalFree(cast(argv, c_void_p))

    def buildTerminal(mode, argv, workdir=None):
        """把参数列表按运行方式拼成 (启动器, 参数串, 创建标志)，argv 已由 splitArgs 拆好。
        与 runTerminal 共用，提权分支没法复用它（提权只能走 ShellExecuteW）。
        两种 shell 都不能直接吃「前缀 + 参数列表」，故各自再包装一层，写法见下方注释。
        workdir 非空时在命令开头显式切换工作目录，原因见 openFile 的提权分支。
        cmd 会等待 GUI 程序（终端驻留，日志可见），PowerShell 不等，是 shell 固有差异，非 bug
        """
        prefix, flags = Terminal[mode]
        if mode == "PowerShell":
            # -Command 收到的是 PowerShell 代码而非命令行，必须用 & 显式调用，否则报 ParserError 或静默不启动。
            # 参数包单引号（内部翻倍转义），整串不含 " ，list2cmdline 包外层引号时就不会再做 \" 转义
            line = "& " + " ".join("'" + t.replace("'", "''") + "'" for t in argv)
            if workdir:
                # 转义规则与 cmd 完全不同，不能套用下面的写法
                line = "Set-Location -LiteralPath '" + workdir.replace("'", "''") + "'; " + line
            params = subprocess.list2cmdline(prefix[1:] + [line])
        else:
            # cmd 在引号数达到 4 个时走「剥掉首尾引号」规则，含空格的程序路径会被截断在第一个空格处，
            # 故给整条命令再包一层引号，cmd 剥掉外层后内层结构保持完整；
            # 外层引号由手拼，所以 list2cmdline 的结果与 workdir 都必须放在引号内部
            inner = subprocess.list2cmdline(argv)
            if workdir:
                # Windows 路径不含 " ，cd 的路径包引号即可防空格与元字符，不会破坏外层引号配对
                inner = f'cd /d "{workdir}" && {inner}'
            params = " ".join(prefix[1:]) + f' "{inner}"'
        return prefix[0], params, flags

    def runTerminal(mode, cmd, workdir, args=""):
        """按运行方式启动命令。mode 为 None 表示无控制台，否则为 Terminal 字典键。
        结束即关窗口的参数已固化在 Terminal 的前缀中，交互式滞留终端由 openTerminal 的 cmd /k 承担。
        args 经 splitArgs 拆分，但元字符与环境变量仍由所选 shell 解释，限制见 splitArgs。
        需要提权时不要用这里，见 openFile 的提权分支。
        """
        argv = cmd + splitArgs(args)
        if not mode:
            return subprocess.Popen(argv, cwd=workdir or None, creationflags=subprocess.CREATE_NO_WINDOW)
        launcher, params, flags = buildTerminal(mode, argv)
        # 必须传字符串而非列表，列表会再走一次 list2cmdline 把手拼的引号转义成 \" ，cmd 不认
        return subprocess.Popen(f"{launcher} {params}", cwd=workdir or None, creationflags=flags)

    # 命令提示符特殊处理，CLSID 统一使用 shell::: 的形式，更规范，兼容性好。已确认 ::{...} 格式有小部分不兼容
    SYSTEM_ACT = {
        "命令提示符": "Terminal",
        "回收站": "shell:::{645FF040-5081-101B-9F08-00AA002F954E}",
        "此电脑": "shell:::{20D04FE0-3AEA-1069-A2D8-08002B30309D}",
        "库": "shell:::{031E4825-7B94-4dc3-B131-E946B44C8DD5}",
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

    def openFile(path: str, cwd=None, args=None, mode=None, operation="open"):
        """打开文件或文件夹，不检查文件存在性。
        mode 为终端时：文件夹在自身目录打开终端（忽略 cwd），文件在所在文件夹执行该文件；
        operation 为 runas 且指定 mode 时，提权启动终端执行，见下方注释；
        其余情况用 ShellExecuteW，失败时弹 Windows 原生错误框提示，不抛异常。
        os.startfile(path) 不支持参数，所以使用 windll。"""
        path = os.path.expandvars(path)
        if mode:
            if os.path.isdir(path):
                openTerminal(path)
                return
            # 未配置工作目录时用文件所在目录；再展开一次环境变量，因为直接调用本函数的路径不经过 main.py
            workdir = os.path.expandvars(cwd or os.path.dirname(path))
            if operation != "runas":
                runTerminal(mode, [path], workdir, args)
                return
            # 提权只能走 ShellExecuteW（CreateProcess 无提权能力），且提权对象必须是启动器本身，否则只有程序没有终端。
            # 提权进程由 AppInfo 服务创建，实测 cmd 与 PowerShell 都无视 lpDirectory（一律落在 System32），
            # 所以 workdir 必须由 buildTerminal 在命令里显式切换，lpDirectory 只作兜底。
            launcher, params, _ = buildTerminal(mode, [path] + splitArgs(args), workdir)
            result = windll.shell32.ShellExecuteW(None, "runas", launcher, params, workdir or None, 1)
        else:
            # lpDirectory 传 None 时使用调用方当前目录（MSDN 明文定义），空字符串行为未定义，故空值统一转 None
            result = windll.shell32.ShellExecuteW(None, operation, path, args, cwd or None, 1)
        if result <= 32:
            # UAC 提权被用户取消时返回 5（SE_ERR_ACCESSDENIED），静默处理
            if operation == "runas" and result == 5:
                return
            if result == 2:
                text = "Windows " + tr("找不到文件，请确认名称是否正确，然后重试")
            elif result == 3:
                text = "Windows " + tr("找不到路径，请确认路径是否正确，然后重试")
            elif result == 5:
                text = tr("拒绝访问")
            else:
                text = tr("打开文件失败") + " (" + str(result) + ")"
            # 0x40010 = MB_OK | MB_ICONERROR | MB_TOPMOST，标题用路径，与资源管理器"找不到文件"框一致
            windll.user32.MessageBoxW(None, text + "\n\n" + path, path, 0x40010)
            logger.error(f"打开文件失败: {path}, 错误码 {result}")

    # 需要能够与 Explorer 交互，之后看看怎么做
    def explorer():
        pass

    def activateWindow(name):
        windll.user32.SetForegroundWindow(int(name))

    def isAdmin() -> bool:
        try:
            return windll.shell32.IsUserAnAdmin() != 0
        except AttributeError:
            return False

    def runAdmin() -> bool:
        """若当前非管理员，尝试提权并重启（Windows 使用 UAC）。提权成功后本进程会退出，不会返回；若失败则返回 False。"""
        if isAdmin():
            return True

        try:
            if Interpret:
                params = subprocess.list2cmdline([app_path] + sys.argv[1:])
                result = windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
            else:
                params = subprocess.list2cmdline(sys.argv[1:])
                result = windll.shell32.ShellExecuteW(None, "runas", app_path, params, None, 1)

            if result > 32:
                sys.exit(0)
            else:
                return False
        except Exception:
            logger.exception("提权失败")
            return False

    def stdConsole(mode=False):
        if mode:
            windll.kernel32.AllocConsole()
        sys.stdin = open("CONIN$", "r")
        sys.stdout = open("CONOUT$", "w")
        sys.stderr = open("CONOUT$", "w")

    def isKeyDown(vk: int) -> bool:
        """查询虚拟键码是否处于物理按下状态"""
        return bool(windll.user32.GetAsyncKeyState(vk) & 0x8000)

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

    Terminal = {
        "": "",
    }

    def runTerminal(mode, cmd, workdir, args=""):
        """非 Windows 无终端选择，仅直接运行命令。
        args 未做拆分，整串作为一个参数传入（与原有行为一致，待支持 Linux/macOS 时再处理）"""
        return subprocess.Popen(cmd + ([args] if args else []), cwd=workdir or None)

    SYSTEM_ACT = {
        "终端": "Terminal",
        "回收站": "trash://",
    }

    def openFile(path: str, cwd=None, args=None, mode=None, operation="open"):
        if args:
            logger.warning("xdg-open 不支持参数，已忽略")
        try:
            subprocess.run(["xdg-open", path], cwd=cwd, check=True)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"打开文件失败: {e}")

    def isAdmin() -> bool:
        return os.geteuid() == 0

    def runAdmin() -> bool:
        """若当前非管理员，尝试提权并重启。提权成功后本进程会退出，不会返回；若失败则返回 False。"""
        try:
            if Interpret:
                cmd = ["sudo", sys.executable] + sys.argv
            else:
                cmd = ["sudo", app_path] + sys.argv[1:]
            subprocess.run(cmd, check=True)
            sys.exit(0)
        except Exception:
            logger.exception("提权失败")
            return False

    def activateWindow(name):
        # Linux 下 Qt 的 activateWindow() 已足够，无需原生调用；
        # Wayland 上没有任何 API 能让应用未经用户交互强制抢焦点，故留空
        pass

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
            exec_cmd = f'"{sys.executable}" "{app_path}"' if Interpret else f'"{app_path}"'
            desktop_content = f"""[Desktop Entry]
    Type=Application
    Name={APP_NAME}
    Exec={exec_cmd}
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
    from ctypes import CDLL, util, c_void_p

    Terminal = {
        "": "",
    }

    def runTerminal(mode, cmd, workdir, args=""):
        """非 Windows 无终端选择，仅直接运行命令。
        args 未做拆分，整串作为一个参数传入（与原有行为一致，待支持 Linux/macOS 时再处理）"""
        return subprocess.Popen(cmd + ([args] if args else []), cwd=workdir or None)

    SYSTEM_ACT = {
        "终端": "Terminal",
        "回收站": os.path.expanduser("~/.Trash"),
    }

    def openFile(path: str, cwd=None, args=None, mode=None, operation="open"):
        if args:
            logger.warning("macOS open 命令不支持参数，已忽略")
        try:
            subprocess.run(["open", path], cwd=cwd, check=True)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"打开文件失败: {e}")

    def isAdmin() -> bool:
        return os.geteuid() == 0

    def runAdmin() -> bool:
        """若当前非管理员，尝试提权并重启。提权成功后本进程会退出，不会返回；若失败则返回 False。"""
        try:
            if Interpret:
                cmd = ["sudo", sys.executable] + sys.argv
            else:
                cmd = ["sudo", app_path] + sys.argv[1:]
            subprocess.run(cmd, check=True)
            sys.exit(0)
        except Exception:
            logger.exception("提权失败")
            return False

    def activateWindow(name):
        """macOS 下通过 AppKit 强制激活前台，等价于 Windows 的 SetForegroundWindow。
        当应用处于后台时，Qt 自身的 activateWindow() 抢不到前台，故需原生调用。"""
        try:
            objc = CDLL(util.find_library("objc"))
            objc.objc_getClass.restype = c_void_p
            objc.objc_msgSend.restype = c_void_p
            objc.sel_registerName.restype = c_void_p
            NSApp = objc.objc_getClass(b"NSApplication")
            shared = objc.objc_msgSend(NSApp, objc.sel_registerName(b"sharedApplication"))
            objc.objc_msgSend(shared, objc.sel_registerName(b"activateIgnoringOtherApps:"), True)
        except Exception:
            logger.exception("macOS 激活应用失败")

    def isKeyDown(vk: int) -> bool:
        """查询虚拟键码是否处于物理按下状态（非 Windows 恒返回 False）"""
        return False

    def setAutoStart(enabled: bool) -> bool:
        plist_path = Path.home() / "Library" / "LaunchAgents" / f"com.{APP_NAME.lower()}.plist"

        if enabled:
            plist_path.parent.mkdir(parents=True, exist_ok=True)
            if Interpret:
                program_args = f"<string>{sys.executable}</string>\n            <string>{app_path}</string>"
            else:
                program_args = f"<string>{app_path}</string>"
            plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
    <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
    <plist version="1.0">
    <dict>
        <key>Label</key>
        <string>com.{APP_NAME.lower()}</string>
        <key>ProgramArguments</key>
        <array>
            {program_args}
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
