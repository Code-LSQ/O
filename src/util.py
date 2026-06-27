# 不导入本地模块，防止循环依赖
import os
import sys
import json
import base64
import hashlib
import logging
import subprocess
import threading
if sys.platform == "win32":
    import winreg
    from ctypes import windll
from pathlib import Path
from datetime import datetime
from enum import Enum, auto
from email.utils import parsedate_to_datetime
from logging.handlers import RotatingFileHandler

import requests
from psutil import Process, cpu_count, disk_usage
from PySide6.QtWidgets import QApplication, QWidget, QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit, QTextEdit, QPushButton, QListWidget, QListWidgetItem, QMessageBox, QDialogButtonBox, QFileDialog, QLayout
from PySide6.QtGui import QDropEvent, QDragEnterEvent
from PySide6.QtCore import Qt, Signal, QObject, QLocale, QUrl, QTimer

APP_NAME = "O"

root = Path(__file__).resolve().parent.parent
plugin_dir = root / "plugin"
data_dir = root / "data"
config_file = data_dir / "config.json"

data_dir.mkdir(parents=True, exist_ok=True)

log_file = data_dir / "app.log"
log_handler = RotatingFileHandler(log_file, maxBytes=1024*1024, backupCount=1, encoding='utf-8')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[log_handler, logging.StreamHandler()]
)
logger = logging.getLogger()

lang_dir = root / "src" / "lang"
icon_dir = root / "src" / "icon"
theme_dir = root / "src" / "theme"

logo_ico = icon_dir / "Logo.ico"
logo_png = icon_dir / "Logo.png"
logo_icn = icon_dir / "Logo.icns"

BINARY_EXTENSIONS = {'.exe', '.dll', '.so', '.dylib', '.bin', '.msi',
    '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.ico', '.webp', '.tiff', '.tif', '.psd', '.ai', '.mp3', '.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.wav', '.flac', '.ogg', '.m4a', '.zip', '.jar', '.apk', '.cbz', '.hap', '.tar', '.gz', '.bz2', '.7z', '.rar', '.xz', '.zst', '.tgz', '.tbz2', '.txz', '.pyc', '.pyo', '.pyd', '.o', '.a', '.lib', '.obj', '.class', '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.epub', '.mobi', '.ttf', '.otf', '.woff', '.woff2', '.eot', '.db', '.sqlite', '.sqlite3', '.mdb', '.iso', '.dmg', '.vhd', '.img',
}

EXTENSION = {
    "TXET": {'.txt', '.md', '.markdown', '.py', '.js', '.ts', '.jsx', '.tsx', '.json', '.xml', '.html', '.css', '.scss', '.less', '.yaml', '.yml', '.toml', '.ini', '.cfg', '.conf', '.sh', '.bat', '.ps1', '.c', '.cpp', '.h', '.hpp', '.java', '.go', '.rs', '.rb', '.php', '.sql', '.gitignore', '.env'},
    "Markdown": {'.md', '.markdown', '.mkdn'},
    "IMAGE": {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.ico', '.webp', '.tiff', '.tif'},
    "ZIP": {'.zip', '.jar', '.apk', '.cbz', '.hap'},
    "TAR": {'.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tbz2', '.tar.xz', '.txz'}
}

EXCLUDE_PATTERNS = ["*.pyc", "*/__pycache__/", "*/.git/"]

ENCODING_MAP = {
    "UTF-8": "utf-8",
    "UTF-8 BOM": "utf-8-sig",
    "UTF-16": "utf-16",
    "UTF-16 BE": "utf-16-be",
    "UTF-16 LE": "utf-16-le",
    "GBK": "gb18030",
    "Shift-JIS": "shift_jis",
}

def encodingName(actual: str) -> str:
    """实际编码名 → 显示标签"""
    for label, name in ENCODING_MAP.items():
        if name == actual:
            return label
    return actual.upper()

def service(services: list, action, timeout):
    for service in services:
        try:
            if sys.platform == "win32":
                if action == "disable":
                    cmd = ['sc', 'config', service, 'start=', 'demand']
                else:
                    cmd = ['net', action, service]
            elif sys.platform == "linux":
                cmd = ['systemctl', action, service]
            elif sys.platform == "darwin":
                cmd = ['launchctl', action, service]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if result.returncode == 0:
                logger.info(f"{service} {action} 成功")
            else:
                logger.error(f"{service} {action} 失败: {result.stderr.strip()}")
        except Exception:
            logger.exception("执行命令错误")

class ExceptSignal(QObject):
    catchException = Signal(str)
    showMainWindow = Signal()

ExceptSign = ExceptSignal()


def dropFile(event: QDropEvent, file=None, folder=None):
    """文件拖放处理"""
    if not event.mimeData().hasUrls():
        return
    for url in event.mimeData().urls():
        path = urlToPath(url)
        if os.path.exists(path):
            if os.path.isfile(path) and file:
                file(path)
            elif folder:
                folder(path)
            event.acceptProposedAction()

def isWin11() -> bool:
    if sys.platform == "win32":
        return sys.getwindowsversion().build >= 22000
    return False

def isAdmin() -> bool:
    """检测当前进程是否具有管理员/root权限"""
    if sys.platform == "win32":
        try:
            return windll.shell32.IsUserAnAdmin() != 0
        except AttributeError:
            return False
    elif sys.platform in ("linux", "darwin"):
        return os.geteuid() == 0

def runAdmin() -> bool:
    """若当前非管理员，尝试提权并重启（Windows 使用 UAC）。提权成功后本进程会退出，不会返回；若失败则返回 False。"""
    if isAdmin():
        return True

    if sys.platform == "win32":
        try:
            script_path = os.path.abspath(sys.argv[0])
            params = subprocess.list2cmdline([script_path] + sys.argv[1:])
            result = windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
            if result > 32:
                sys.exit(0)
            else:
                return False
        except Exception:
            logger.exception("提权失败")
            return False

    elif sys.platform in ("linux", "darwin"):
        try:
            cmd = ["sudo", sys.executable] + sys.argv
            subprocess.run(cmd, check=True)
            sys.exit(0)
        except Exception:
            logger.exception("提权失败")
            return False

def restartApplication(parent=None):
    if not messageBox(parent, tr("重启"), tr("确定重启应用？") + "\n" + tr("未保存的更改将丢失")):
        return
    QTimer.singleShot(100, lambda: os.execv(sys.executable, [sys.executable] + sys.argv))

class Singleton:
    """单例基类（线程安全）"""
    _instance = None
    __lock = threading.Lock()
    
    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls.__lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self, *args, **kwargs):
        if self._initialized:
            return
        self._initialized = True
        self._init_impl(*args, **kwargs)
    
    def _init_impl(self, *args, **kwargs):
        pass


def showFile(path: str, parent=None):
    try:
        if sys.platform == "win32":
            path = os.path.abspath(path)
            subprocess.Popen(["explorer", "/select,", path])
        elif sys.platform == "linux":
            subprocess.Popen(["xdg-open", path])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        if parent:
            parent.statusBar().showMessage(f"已打开: {path}", 2000)
    except Exception:
        logger.exception("打开资源管理器失败")

def getDisk():
    disk = disk_usage("/")
    logger.info(f"磁盘信息\n总空间: {disk.total / (1024**3):.2f} GB\n已使用: {disk.used / (1024**3):.2f} GB\n使用率: {disk.percent}%")

def getNet():
    pass

def getScreen(app: QApplication, logic=False):
    """获取屏幕逻辑分辨率、缩放和像素密度(DPI)，logic 为 True 输出逻辑分辨率，为 False 输出物理分辨率"""
    try:
        screen = app.primaryScreen()
        size = screen.size()
        width = size.width()
        height = size.height()
        hz = screen.refreshRate()
        scale = screen.devicePixelRatio()
        dpi = screen.logicalDotsPerInchX()
        if not logic:
            width = int(width * scale)
            height = int(height * scale)
        logger.info(f"屏幕分辨率 {width}×{height}，刷新率 {hz}，缩放 {scale}，像素密度 {dpi}")
        return width, height, hz, scale, dpi
    except Exception:
        logger.exception("获取屏幕信息失败")
    return 1920, 1200, 60, 1.0, 96.0

def getDevice(app: QApplication):
    try:
        getDisk()
        getScreen(app)
    except Exception:
        logger.exception("获取设备信息失败")

def systemLanguage() -> str:
    """检测系统语言（简体中文返回"简体中文"，其他返回语言代码，同时也是文件名称）"""
    lang_code = QLocale.system().name()
    if lang_code == 'zh_CN':
        return "简体中文"
    if lang_code == 'en_GB':
        lang_code = 'en_US'
    lang_file = lang_dir / f"{lang_code}.json"
    if lang_file.exists():
        return lang_code
    return "简体中文"

class Translator(Singleton):
    """翻译，简体中文不做处理，其他语言加载 JSON 文件，通过字符串替换进行翻译"""
    
    def _init_impl(self):
        self._translations = {}
        self.lang = "简体中文"
    
    def loadTranslation(self, lang_code: str):
        """按需加载单个语言文件，简体中文不做处理"""
        if lang_code == "简体中文" or lang_code in self._translations:
            return
        file = lang_dir / f"{lang_code}.json"
        if file.exists():
            try:
                with open(file, 'r', encoding='utf-8') as f:
                    self._translations[lang_code] = json.load(f)
            except Exception:
                logger.exception(f"加载语言文件失败 {file}")
    
    def getLanguages(self) -> list:
        """扫描目录，从 .json 文件的"翻译"字段获取可用语言的列表"""
        languages = ["简体中文"]
        for file in sorted(lang_dir.glob("*.json")):
            try:
                data = json.loads(file.read_text(encoding='utf-8'))
                name = data.get("翻译")
                if name and name not in languages:
                    languages.append(name)
            except Exception:
                logger.exception(f"读取语言文件失败 {file}")
        return languages
    
    def setLanguage(self, lang_code: str):
        """通过语言代码设置当前语言"""
        if not lang_code or lang_code == "简体中文":
            self.lang = "简体中文"
            return
        self.lang = lang_code
        self.loadTranslation(lang_code)
    
    def tr(self, key: str) -> str:
        """翻译键值"""
        if self.lang == "简体中文":
            return key
        lang_dict = self._translations.get(self.lang)
        if lang_dict and lang_dict.get(key):
            return lang_dict.get(key)
        return key if key else ""
    
def tr(key: str) -> str:
    """
    翻译函数，使用字符串替换

    使用规范：
    程序原生使用中文，应尽可能追求简洁的中文表达并将变量单独放在首部或尾部。
    专有名词不用 tr() 包裹，也不在语言文件中，注意与其他需翻译文本加空格隔开。如  "API " + tr("接口")，tr("最大") + " Token"
    专有名词列表： Markdown、JSON、OpenList、AI、API Key、Token、Ctrl、OCR、1920x1080、
    注意文本中的英文与中文之间要有空格，

    语言文件中不包含 ":" "%" 等字符，此类特殊字符不用 tr() 包裹，如 tr("文件") + "(&F)"
    ": " 用 tr("文本")+": " 进行拼接，": " 后有变量则 tr("文本") + f": {var}"
    其余含变量情况用 tr("文本") + " " + var 或 var + " " + tr("文本") 拼接，将变量放在首部或尾部，中间需要空格
    "\n" 不用 tr() 包裹，换行使用  + "\n" +  进行拼接

    尽可能减少相似度较高的翻译，对其进行复用。统一中文表述。

    """
    return Translator().tr(key)


def getTimestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

def getAppPath():
    if getattr(sys, 'frozen', False):
        return sys.executable
    return os.path.abspath(sys.argv[0]) if sys.argv else ""

def setAutoStart(enabled: bool) -> bool:
    if sys.platform == "win32":
        return windowsAutoStart(enabled)
    elif sys.platform == "linux":
        return linuxAutoStart(enabled)
    elif sys.platform == "darwin":
        return macosAutoStart(enabled)
    return False

def windowsAutoStart(enabled: bool) -> bool:
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

def deleteRegistry(key_handle, sub_key):
    """递归删除注册表键及其所有子键"""
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
        logger.debug(f"已删除注册表键: {sub_key}")
    except OSError:
        logger.exception(f"删除注册表键失败: {sub_key}")

def isMenuRegister() -> bool:
    if sys.platform != "win32":
        return False
    shell_keys = [
        rf"Software\Classes\*\shell\{APP_NAME}",
        rf"Software\Classes\Directory\shell\{APP_NAME}"
    ]
    for shell_key in shell_keys:
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, shell_key, 0, winreg.KEY_READ)
            winreg.CloseKey(key)
        except FileNotFoundError:
            logger.debug(f"右键菜单注册表键缺失: {shell_key}")
            return False
        except Exception:
            return False
    logger.info("右键菜单注册表键已存在，跳过写入")
    return True

def setWindowsMenu(enabled: bool) -> bool:
    app_path = getAppPath()
    if not app_path:
        return False

    shell_keys = [
        rf"Software\Classes\*\shell\{APP_NAME}",
        rf"Software\Classes\Directory\shell\{APP_NAME}"
    ]

    for shell_key in shell_keys:
        try:
            if enabled:
                key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, shell_key, 0, winreg.KEY_SET_VALUE)
                try:
                    winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f"使用 {APP_NAME} 打开")
                    winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, f'"{app_path}",0')
                    cmd_key = winreg.CreateKeyEx(
                        winreg.HKEY_CURRENT_USER, shell_key + r"\command", 0, winreg.KEY_SET_VALUE
                    )
                    try:
                        if getattr(sys, 'frozen', False):
                            cmd = f'"{app_path}" "%1"'
                        else:
                            cmd = f'"{sys.executable}" "{app_path}" "%1"'
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


def macosAutoStart(enabled: bool) -> bool:
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


def autoStartDirLinux() -> Path:
    xdg_config = os.environ.get("XDG_CONFIG_HOME", "")
    if xdg_config:
        config_dir = Path(xdg_config)
    else:
        config_dir = Path.home() / ".config"
    return config_dir / "autostart"

def linuxAutostart(enabled: bool) -> bool:
    autostart_dir = autoStartDirLinux()
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


process = Process()
_cpu_count = cpu_count()

def monitor():
    # CPU、内存占用监视
    global process, _cpu_count

    try:
        cpu = process.cpu_percent(interval=None)
        if _cpu_count:
            cpu = cpu / _cpu_count
        mem = process.memory_info().rss / (1024 * 1024)
        usage = f"CPU {cpu:.1f}% | {tr('内存')} {mem:.0f} MB"
        return usage
    except Exception:
        logger.exception("获取资源占用异常")
        return False


def urlToPath(url: QUrl) -> str:
    """将 QUrl 转换为本地路径（处理 Windows file:///C:/... 格式）"""
    path = url.toLocalFile() or url.path()
    if sys.platform == 'win32' and len(path) > 2 and path[0] == '/' and path[2] == ':':
        path = path[1:]
    return os.path.normpath(path)


class FileDrop(QLabel):
    folderDropped = Signal(str)
    fileDropped = Signal(str)
    filesDropped = Signal(list)
    fileOrFolderDropped = Signal(str)

    def __init__(self, file_filter: list = None):
        super().__init__()
        self._file_filter = file_filter or []
        self.setAcceptDrops(True)
        self.setWordWrap(True)
        self.setFixedHeight(100)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("color: gray; border: 2px dashed gray;")
        self.setText("拖拽文件或文件夹到此处")

    def _filter_files(self, files: list) -> list:
        if not self._file_filter:
            return files
        return [f for f in files if any(f.lower().endswith(ext.lower()) for ext in self._file_filter)]

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setStyleSheet("color: black; border: 2px dashed blue;")
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent):
        self.reset_style()
        urls = event.mimeData().urls()
        files = []
        folder = None

        for url in urls:
            path = urlToPath(url)
            if not path:
                continue
            if os.path.isdir(path):
                folder = path
            elif os.path.isfile(path):
                if self._file_filter:
                    if any(path.lower().endswith(ext.lower()) for ext in self._file_filter):
                        files.append(path)
                else:
                    files.append(path)

        if folder:
            for root, _, fs in os.walk(folder):
                for f in fs:
                    if self._file_filter:
                        if any(f.lower().endswith(ext.lower()) for ext in self._file_filter):
                            files.append(os.path.normpath(os.path.join(root, f)))
                    else:
                        files.append(os.path.normpath(os.path.join(root, f)))
            self.folderDropped.emit(folder)
        elif files:
            self.filesDropped.emit(files)
            if len(files) == 1:
                self.fileDropped.emit(files[0])
            self.fileOrFolderDropped.emit(files[0] if len(files) == 1 else f"{len(files)} 个文件")

        event.acceptProposedAction()

    def reset_style(self):
        self.setStyleSheet("color: gray; border: 2px dashed gray;")

    def set_folder_path(self, path: str):
        self.setText(path)


class RuntimeManager:
    def __init__(self):
        self.Python = ""
        self.Java = ""
        self.Temp_Path = []

    def loadConfig(self, config):
        """从配置加载环境变量"""
        env_config = config.get("Launch.Runtime", {})
        self.Python = env_config.get("Python", "")
        self.Java = env_config.get("Java", "")
        self.Temp_Path = env_config.get("Temp_Path", [])

    def envTemp(self, current_path):
        """获取临时环境变量，将当前路径和配置中的临时路径加入PATH"""
        env = os.environ.copy()
        
        path_list = [current_path]
        
        if self.Python:
            python_dir = os.path.dirname(self.Python)
            if python_dir and os.path.isdir(python_dir):
                path_list.append(python_dir)
            script_dir = os.path.join(python_dir, "Scripts")
            if os.path.isdir(script_dir):
                path_list.append(script_dir)
        
        if self.Java:
            java_dir = os.path.dirname(self.Java)
            if java_dir and os.path.isdir(java_dir):
                path_list.append(java_dir)
        
        for p in self.Temp_Path:
            if p and os.path.isdir(p):
                path_list.append(p)
        
        current_path_env = env.get("PATH", "")
        path_separator = ";" if sys.platform == "win32" else ":"
        
        new_path = path_separator.join(path_list)
        if current_path_env:
            new_path += path_separator + current_path_env
        
        env["PATH"] = new_path
        
        return env


env_manager = RuntimeManager()

def openTerminal(path, config=None):
    """打开命令行并设置临时环境变量"""
    if not path:
        logger.warning(f"无效的路径: {path!r}")
        return False
    
    path = os.path.abspath(path)
    if os.path.isfile(path):
        path = os.path.dirname(path)
    
    if config:
        env_manager.loadConfig(config)
    
    env = env_manager.envTemp(path)
    
    try:
        if sys.platform == "win32":
            subprocess.Popen(["cmd", '/k', 'cd', '/d', path], env=env, cwd=path)
        elif sys.platform == "linux":
            subprocess.Popen(["xdg-terminal"], env=env, cwd=path, start_new_session=True)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-a", "Terminal", path], env=env, cwd=path)
        logger.info(f"已打开命令行: {path}")
        return True
    except Exception:
        logger.exception("打开命令行失败")
        return False


def fileHash(path: str, algorithm = "md5") -> str:
    """计算文件哈希值，建议使用 md5 或 sha256 算法"""
    ha = hashlib.new(algorithm)
    try:
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b''):
                ha.update(chunk)
        return ha.hexdigest()
    except Exception:
        logger.exception(f"计算哈希值失败: {path}")
        return ""

def checksum(path: str, algorithm="md5", _visited=None) -> str:
    """计算文件夹哈希值（哈希树）， _visited 用于检查是否形成环路（绑定挂载、循环硬链接目录等非符号链接导致的循环）"""

    if os.path.isfile(path):
        return fileHash(path, algorithm=algorithm)

    if os.path.isdir(path):
        if _visited is None:
            _visited = set()
        real_path = os.path.realpath(path)
        if real_path in _visited:
            logger.warning(f"检测到目录循环: {path}")
            return ""
        _visited.add(real_path)

        ha = hashlib.new(algorithm)
        entries = []
        try:
            names = os.listdir(path)
        except PermissionError:
            logger.exception(f"无法读取目录: {path}")
            return ""

        for name in names:
            child = os.path.join(path, name)
            if os.path.islink(child):
                link_target = os.readlink(child)
                child_hash = hashlib.new(algorithm)
                child_hash.update(link_target.encode("utf-8"))
                child_hash = child_hash.hexdigest()
                t = "L"
            elif os.path.isdir(child):
                child_hash = checksum(child, algorithm=algorithm, _visited=_visited)
                t = "D"
            elif os.path.isfile(child):
                child_hash = fileHash(child, algorithm=algorithm)
                t = "F"
            else:
                child_hash = ""
                t = "?"
                logger.debug(f"跳过非常规文件: {child}")

            entries.append((name, t, child_hash))

        for name, t, child_hash in sorted(entries, key=lambda x: x[0]):
            ha.update(f"{t}:{name}\0{child_hash}\0".encode("utf-8"))

        return ha.hexdigest()

    logger.warning(f"不支持的路径类型: {path}")
    return ""

def imageBase64(path: str) -> tuple[str, str]:
    """读取图片文件，返回 (base64_data, mime_type)"""
    ext = os.path.splitext(path)[1].lower().lstrip('.') or 'png'
    if ext == 'jpg':
        ext = 'jpeg'
    mime = f"image/{ext}"
    with open(path, 'rb') as f:
        data = base64.b64encode(f.read()).decode()
    return data, mime

def formatFileSize(size: int) -> str:
    """格式化文件大小"""
    if size < 1024:
        return f"{size} B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    elif size < 1024 ** 3:
        return f"{size / (1024 ** 2):.1f} MB"
    elif size < 1024 ** 4:
        return f"{size / (1024 ** 3):.2f} GB"
    else:
        return f"{size / (1024 ** 4):.2f} TB"

def lastModify(url):
    response = requests.get(url)
    last_modified = response.headers.get("Last-Modified")
    if last_modified:
        dt = parsedate_to_datetime(last_modified)
        return dt.timestamp()
    return 0

def folderLastModified(path):
    """返回文件夹本身及其文件和子文件夹最新的最后修改时间，Unix 时间戳"""
    # LastModified、mtime，都表示最后修改时间
    last_mtime = 0
    try:
        for root, dirs, files in os.walk(path):
            try:
                mtime = os.path.getmtime(root)
                if last_mtime is None or mtime > last_mtime:
                    last_mtime = mtime
            except OSError:
                continue

            for name in files:
                file_path = os.path.join(root, name)
                try:
                    mtime = os.path.getmtime(file_path)
                    if last_mtime is None or mtime > last_mtime:
                        last_mtime = mtime
                except OSError:
                    continue
    except Exception as e:
        raise RuntimeError(f"遍历目录失败: {e}")
    return int(last_mtime)

def parseMtime(mtime) -> float:
    """将远程 ISO 时间字符串转为 Unix 时间戳"""
    if isinstance(mtime, (int, float)):
        return float(mtime)
    try:
        return datetime.fromisoformat(str(mtime)).timestamp()
    except (ValueError, TypeError):
        return 0

def convertPath(path: str, mode: str) -> str:
    """绝对路径与相对路径转换"""
    try:
        if mode == "relative" and os.path.isabs(path):
            return os.path.relpath(path, str(root))
        elif mode == "absolute" and not os.path.isabs(path):
            return os.path.normpath(os.path.join(str(root), path))
    except ValueError:
        pass
    return path

def getFilePath(parent: QWidget, title="", filter="", mode="file", edit=None):
    """封装 QFileDialog 返回文件路径，可以与 QLineEdit 配合设置文本，用 lambda 连接到 选择、浏览 按钮"""
    if mode == "file":
        path, _ = QFileDialog.getOpenFileName(parent, title, "", filter)
    else:
        path = QFileDialog.getExistingDirectory(parent, title)
    if path:
        path = os.path.normpath(path)
        if edit:
            edit.setText(path)
        return path


class ClipboardMonitor(Singleton):
    """剪贴板监控器 - 使用 QClipboard 信号监听剪贴板变化"""
    def _init_impl(self):
        self._clipboard = QApplication.clipboard()
        self._last_content = ""
        self._callbacks = set()
        self.enabled = False
        self._init_clipboard()

    def _init_clipboard(self):
        try:
            self._last_content = self._clipboard.text() or ""
        except Exception:
            logger.exception("初始化剪贴板失败")

    def start(self):
        self.enabled = True
        self._clipboard.dataChanged.connect(self._on_clipboard_changed)

    def stop(self):
        self.enabled = False
        try:
            if self._clipboard.receivers(self._clipboard.dataChanged) > 0:
                self._clipboard.dataChanged.disconnect(self._on_clipboard_changed)
        except (TypeError, RuntimeError):
            pass

    def _on_clipboard_changed(self):
        if not self.enabled or not self._callbacks:
            return
        try:
            current = self._clipboard.text()
            if current and current != self._last_content:
                self._last_content = current
                for cb in self._callbacks:
                    try:
                        cb(current)
                    except Exception:
                        logger.exception("剪贴板回调执行失败")
        except Exception:
            logger.exception("处理剪贴板变化失败")


def inputDialog(parent, title, text="", default=""):
    """封装 QDialog ，输入单一输入，替换 QInputDialog """
    while True:
        dialog = QDialog(parent)
        dialog.setWindowTitle(title)

        vlayout = QVBoxLayout(dialog)
        layout = QFormLayout()
        edit = QLineEdit(default)
        layout.addRow(text, edit)
        vlayout.addLayout(layout)

        if dialogBox(vlayout, dialog):
            if edit.text().strip():
                return edit.text().strip()
            messageBox(parent, "警告", "不能为空", 1)
            continue

        return None

def dictDialog(parent, title, name="名称", value="值", name_text="", value_text="", textedit=False):
    """封装 QDialog ，输入名称与值，需要字典时使用，返回元组 (name, value) 或 (None, None) ，因为元组对调用比较方便"""
    while True:
        dialog = QDialog(parent)
        dialog.setWindowTitle(title)

        vlayout = QVBoxLayout(dialog)
        layout = QFormLayout()
        name_edit = QLineEdit(name_text)
        layout.addRow(name, name_edit)

        if textedit:
            value_edit = QTextEdit(value_text)
            value_edit.setAcceptRichText(False)
        else:
            value_edit = QLineEdit(value_text)
        layout.addRow(value, value_edit)
        vlayout.addLayout(layout)

        if dialogBox(vlayout, dialog):
            name_result = name_edit.text().strip()
            value_result = value_edit.toPlainText().strip() if textedit else value_edit.text().strip()
            if not name_result:
                messageBox(parent, "警告", f"{name}不能为空", 1)
                name_text, value_text = name_result, value_result
                continue
            return name_result, value_result
        return None, None

def dialogBox(layout: QLayout, dialog: QDialog, num: int=2, show=True):
    """封装 QDialog 的按钮
    差异仅为 StandardButton.Ok 的文本为确定，.Cancel 的文本为取消
    统一使用 Ok、Cancel ，不使用 Yes、No"""
    if num == 1:
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        box.accepted.connect(dialog.accept)
        box.button(QDialogButtonBox.StandardButton.Ok).setText(tr("确定"))
    elif num == 2:
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(dialog.accept)
        box.rejected.connect(dialog.reject)
        box.button(QDialogButtonBox.StandardButton.Ok).setText(tr("确定"))
        box.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("取消"))
    layout.addWidget(box)
    if show:
        return dialog.exec() == QDialog.DialogCode.Accepted
    else:
        return box

def messageBox(parent, title, text, num: int=2):
    """封装 QMessageBox
    差异仅为 StandardButton.Ok 的文本为确定，.Cancel 的文本为取消
    统一使用 Ok、Cancel ，不使用 Yes、No
    最好统一标题，信息、提示、完成、成功、警告、错误"""

    msg_box = QMessageBox(parent)
    msg_box.setWindowFlags(msg_box.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
    if parent:
        sheet = parent.styleSheet()
        if sheet:
            msg_box.setStyleSheet(sheet)
    msg_box.setWindowTitle(title)
    msg_box.setText(text)

    if num == 1:
        msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg_box.button(QMessageBox.StandardButton.Ok).setText(tr("确定"))
    if num == 2:
        msg_box.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        msg_box.button(QMessageBox.StandardButton.Ok).setText(tr("确定"))
        msg_box.button(QMessageBox.StandardButton.Cancel).setText(tr("取消"))
        return msg_box.exec() == QMessageBox.StandardButton.Ok
    if num == 3:
        msg_box.setStandardButtons(QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel)
        msg_box.button(QMessageBox.StandardButton.Save).setText(tr("保存"))
        msg_box.button(QMessageBox.StandardButton.Discard).setText(tr("不保存"))
        msg_box.button(QMessageBox.StandardButton.Cancel).setText(tr("取消"))

    return msg_box.exec()


class ManagePair(QDialog):
    """管理数据，每组数据有 name 和 value"""

    def __init__(self, parent=None, pairs=None, connect_signals: bool = True):
        super().__init__(parent)
        self.setMinimumSize(500, 300)

        # 创建控件
        self.pair_list = QListWidget()
        self.pair_list.setStyleSheet("QListWidget::item { height: 30px; }")
        self.add_btn = QPushButton("添加")
        self.edit_btn = QPushButton("编辑")
        self.delete_btn = QPushButton("删除")

        # 布局
        main_layout = QVBoxLayout(self)
        main_layout.addWidget(self.pair_list)

        button_layout = QHBoxLayout()
        button_layout.addWidget(self.add_btn)
        button_layout.addWidget(self.edit_btn)
        button_layout.addWidget(self.delete_btn)
        button_layout.addStretch()
        main_layout.addLayout(button_layout)

        # 连接信号
        if connect_signals:
            self.add_btn.clicked.connect(self.add)
            self.edit_btn.clicked.connect(self.edit)
            self.delete_btn.clicked.connect(self.delete)

        # 如果有初始数据，加载
        if pairs:
            self.set_pairs(pairs)

    def set_pairs(self, pairs):
        """设置配对数据，支持 dict {name: value} 或列表 [{"name":..., "value":...}]"""
        self.pair_list.clear()
        if isinstance(pairs, dict):
            items = pairs.items()
        elif isinstance(pairs, list):
            items = [(p.get("name",""), p.get("value","")) for p in pairs if isinstance(p, dict)]
        else:
            items = []
        for name, value in items:
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, value)
            self.pair_list.addItem(item)

    def get_pairs(self):
        """获取当前所有配对，返回 dict {name: value}"""
        pairs = {}
        for i in range(self.pair_list.count()):
            item = self.pair_list.item(i)
            name = item.text()
            value = item.data(Qt.ItemDataRole.UserRole)
            pairs[name] = value
        return pairs

    def pair_dialog(self, title, initial_name="", initial_value=""):
        """显示编辑对话框，返回 (name, value) 如果用户确认，否则 (None, None)"""
        return dictDialog(self, title, name_text=initial_name, value_text=initial_value)

    def add(self):
        """添加新配对"""
        name, value = self.pair_dialog("添加")
        if name:
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, value)
            self.pair_list.addItem(item)

    def edit(self):
        current_item = self.pair_list.currentItem()
        if not current_item:
            messageBox(self, "警告", "请先选择一个要编辑的项", 1)
            return

        old_name = current_item.text()
        old_value = current_item.data(Qt.ItemDataRole.UserRole)

        name, value = self.pair_dialog("编辑", old_name, old_value)
        if name:
            current_item.setText(name)
            current_item.setData(Qt.ItemDataRole.UserRole, value)

    def delete(self):
        current_item = self.pair_list.currentItem()
        if not current_item:
            messageBox(self, "警告", "请先选择一个要删除的项", 1)
            return

        if messageBox(self, "确认删除", f"确定要删除 '{current_item.text()}' 吗？"):
            row = self.pair_list.row(current_item)
            self.pair_list.takeItem(row)



# Windows 11 右键一级菜单
# def register_context_menu():
#     """注册一级菜单（需要 DLL + MSIX 在同目录）"""
#     dll = root / "OShellExt.dll"
#     subprocess.run(["rundll32.exe", f"{dll},RegisterPackage"], check=True)

# def unregister_context_menu():
#     """移除一级菜单"""
#     dll = root / "OShellExt.dll"
#     subprocess.run(["rundll32.exe", f"{dll},RemovePackage"], check=True)

