"""工具模块，不导入本地模块，防止循环依赖"""
import os
import sys
import re
import json
import base64
import hashlib
import logging
import subprocess
import threading
if sys.platform == "win32":
    from ctypes import windll
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse
from logging.handlers import RotatingFileHandler

import requests
from psutil import Process, cpu_count, disk_usage
from PySide6.QtWidgets import QApplication, QWidget, QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit, QTextEdit, QPushButton, QListWidget, QListWidgetItem, QMessageBox, QDialogButtonBox, QFileDialog, QLayout
from PySide6.QtGui import QDropEvent, QDragEnterEvent
from PySide6.QtCore import Qt, Signal, QObject, QLocale, QUrl, QTimer, QSysInfo, QThread

AUTHOR = "Code-LSQ"
APP_NAME = "O"
VERSION = "0.5.0"
REPOSITORY = f"https://github.com/{AUTHOR}/{APP_NAME}"
UPDATE = f"https://api.github.com/repos/{AUTHOR}/{APP_NAME}/releases/latest"

if getattr(sys, "frozen", False) or "__compiled__" in globals():
    Interpret = False
    app_path = sys.executable
    root = Path(sys.executable).parent
else:
    Interpret = True
    app_path = os.path.abspath(sys.argv[0])
    root = Path(__file__).resolve().parent.parent

plugin_dir = root / "plugin"
data_dir = root / "data"
config_file = data_dir / "config.json"

data_dir.mkdir(parents=True, exist_ok=True)

log_file = data_dir / "app.log"
log_handler = RotatingFileHandler(log_file, maxBytes=1024 * 1024, backupCount=1, encoding="utf-8")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[log_handler, logging.StreamHandler()],
)
logger = logging.getLogger()

lang_dir = root / "src" / "lang"
icon_dir = root / "src" / "icon"
theme_dir = root / "src" / "theme"

logo_ico = icon_dir / "Logo.ico"
logo_png = icon_dir / "Logo.png"
logo_icn = icon_dir / "Logo.icns"

backup_dir = data_dir / "backup"

arch = QSysInfo.currentCpuArchitecture()
if arch in ("x86_64", "amd64"):
    arch = "x64"
elif arch in ("arm64", "aarch64"):
    arch = "arm64"


def execPython():
    """以 --exec 模式在嵌入式解释器中执行 Python 脚本，返回退出码"""
    script_path = sys.argv[2]
    extra_args = sys.argv[3:]
    logger.info(f"以 --exec 模式执行脚本: {script_path}")
    logger.info(f"额外参数: {extra_args}")

    if sys.stdin is None and sys.platform == "win32":
        try:
            sys.stdin = open("CONIN$", "r")
            sys.stdout = open("CONOUT$", "w")
            sys.stderr = open("CONOUT$", "w")
        except OSError:
            windll.kernel32.AllocConsole()
            sys.stdin = open("CONIN$", "r")
            sys.stdout = open("CONOUT$", "w")
            sys.stderr = open("CONOUT$", "w")
    try:
        sys.argv = [script_path] + extra_args
        sys.path.insert(0, os.path.dirname(os.path.abspath(script_path)))
        with open(script_path, "r", encoding="utf-8") as f:
            source = f.read()
        code = compile(source, script_path, "exec")
        exec(code, {
            "__name__": "__main__",
            "__file__": script_path,
            "__builtins__": __builtins__,
        })
        return 0
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        logger.info(f"脚本通过 sys.exit({code}) 退出: {script_path}")
        return code
    except Exception:
        logger.exception(f"执行脚本失败: {script_path}")
        return 1
    finally:
        if sys.stdin is not None:
            os.system("pause")


def compareVersions(v1: str, v2: str) -> int:
    """语义化版本比较，返回 -1 (v1<v2) / 0 (相等) / 1 (v1>v2)"""
    parts1 = [int(x) for x in v1.split(".")]
    parts2 = [int(x) for x in v2.split(".")]
    max_len = max(len(parts1), len(parts2))
    parts1 += [0] * (max_len - len(parts1))
    parts2 += [0] * (max_len - len(parts2))
    for a, b in zip(parts1, parts2):
        if a < b:
            return -1
        if a > b:
            return 1
    return 0


EXTENSION = {
    "TXET": {".txt", ".py", ".js", ".ts", ".jsx", ".tsx", ".json", ".xml", ".html", ".css", ".scss", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".sh", ".bat", ".ps1", ".c", ".cpp", ".h", ".hpp", ".java", ".go", ".rs", ".rb", ".php", ".sql", ".gitignore", ".env"},
    "Markdown": {".md", ".markdown", ".mkdn"},
    "IMAGE": {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".tiff", ".tif", ".psd", ".ai", ".heic", ".avif"},
    "ZIP": {".zip", ".jar", ".apk", ".cbz", ".hap"},
    "TAR": {".tar", ".tgz", ".tbz2", ".txz"},
    "ARCHIVE": {".gz", ".bz2", ".xz", ".zst", ".7z", ".rar"},
    "AUDIO": {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus"},
    "VIDEO": {".mp4", ".avi", ".mkv", ".mov", ".webm"},
    "EXECUTE": {".exe", ".dll", ".so", ".dylib", ".bin", ".msi", ".wasm", ".pyc", ".pyd", ".o", ".a", ".lib", ".obj", ".class"},
    "DOCUMENT": {".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".epub", ".mobi", ".odt", ".ods", ".odp"},
    "FONT": {".ttf", ".otf", ".woff", ".woff2", ".ttc"},
    "DATABASE": {".db", ".sqlite", ".sqlite3", ".accdb"},
    "DISK": {".iso", ".dmg", ".vhd", ".img"},
}

BINARY_EXTENSIONS = (
    EXTENSION["IMAGE"]
    | EXTENSION["ZIP"]
    | EXTENSION["TAR"]
    | EXTENSION["ARCHIVE"]
    | EXTENSION["AUDIO"]
    | EXTENSION["VIDEO"]
    | EXTENSION["EXECUTE"]
    | EXTENSION["DOCUMENT"]
    | EXTENSION["FONT"]
    | EXTENSION["DATABASE"]
    | EXTENSION["DISK"]
)

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
                    cmd = ["sc", "config", service, "start=", "demand"]
                else:
                    cmd = ["net", action, service]
            elif sys.platform == "linux":
                cmd = ["systemctl", action, service]
            elif sys.platform == "darwin":
                cmd = ["launchctl", action, service]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            if result.returncode == 0:
                logger.info(f"{service} {action} 成功")
            else:
                logger.error(f"{service} {action} 失败: {result.stderr.strip()}")
        except Exception:
            logger.exception("执行命令错误")


class OSignal(QObject):
    catchException = Signal(str)

OSign = OSignal()


def runAsync(fn, on_done=None, on_error=None, on_progress=None):
    """在线程中执行 fn，结果在主线程回调

    用法:
        runAsync(getReleaseInfo, on_done=self._onCheckResult)
        runAsync(
            lambda report: download(url, path, report),
            on_done=lambda ok: ...,
            on_progress=lambda cur, total: bar.setValue(cur),
        )
    """

    class _Worker(QObject):
        done = Signal(object)
        err = Signal(str)
        progress = Signal(int, int)

        def run(self):
            try:
                cb = self.progress.emit if on_progress else None
                result = fn(cb) if on_progress else fn()
                self.done.emit(result)
            except Exception as e:
                self.err.emit(str(e))

    t = QThread()
    w = _Worker()
    w.moveToThread(t)
    t.started.connect(w.run)
    if on_done:
        w.done.connect(on_done)
    if on_error:
        w.err.connect(on_error)
    if on_progress:
        w.progress.connect(on_progress)
    w.done.connect(t.quit)
    w.err.connect(t.quit)
    w.done.connect(w.deleteLater)
    w.err.connect(w.deleteLater)
    t.finished.connect(t.deleteLater)
    t.start()


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
            if Interpret:
                cmd = ["sudo", sys.executable] + sys.argv
            else:
                cmd = ["sudo", sys.executable] + sys.argv[1:]
            subprocess.run(cmd, check=True)
            sys.exit(0)
        except Exception:
            logger.exception("提权失败")
            return False


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
        with self.__lock:
            if self._initialized:
                return
            self._initialized = True
        self._init(*args, **kwargs)

    def _init(self, *args, **kwargs):
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
            parent.statusBar().showMessage(tr("已打开") + ": " + path, 2000)
    except Exception:
        logger.exception("打开资源管理器失败")


def getDevice(app: QApplication, logic=False):
    """输出磁盘信息到日志，获取屏幕逻辑分辨率、缩放和像素密度(DPI)，logic 为 True 输出逻辑分辨率，为 False 输出物理分辨率"""

    disk = disk_usage("/")
    logger.info(f"磁盘信息 - 总空间 {disk.total / (1024**3):.2f} GB，已使用 {disk.used / (1024**3):.2f} GB，使用率 {disk.percent}%")

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
        logger.info(f"屏幕信息 - 分辨率 {width}×{height}，刷新率 {hz}，缩放 {scale}，像素密度 {dpi}")
        return width, height, hz, scale, dpi
    except Exception:
        logger.exception("获取屏幕信息失败")
    return 1920, 1200, 60, 1.0, 96.0


def systemLanguage() -> str:
    """检测系统语言（简体中文返回"简体中文"，其他返回语言代码，同时也是文件名称）"""
    lang_code = QLocale.system().name()
    if lang_code == "zh_CN":
        return "简体中文"
    if lang_code == "en_GB":
        lang_code = "en_US"
    lang_file = lang_dir / f"{lang_code}.json"
    if lang_file.exists():
        return lang_code
    return "简体中文"


class Translator(Singleton):
    """翻译，简体中文不做处理，其他语言加载 JSON 文件，通过字符串替换进行翻译"""

    def _init(self):
        self._translations = {}
        self.lang = "简体中文"

    def loadTranslation(self, lang_code: str):
        """按需加载单个语言文件，简体中文不做处理"""
        if lang_code == "简体中文" or lang_code in self._translations:
            return
        file = lang_dir / f"{lang_code}.json"
        if file.exists():
            try:
                with open(file, "r", encoding="utf-8") as f:
                    self._translations[lang_code] = json.load(f)
            except Exception:
                logger.exception(f"加载语言文件失败 {file}")

    def getLanguages(self) -> list:
        """扫描目录，从 .json 文件的"翻译"字段获取可用语言的列表"""
        languages = ["简体中文"]
        for file in sorted(lang_dir.glob("*.json")):
            try:
                data = json.loads(file.read_text(encoding="utf-8"))
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
    程序原生使用中文，应尽可能追求简洁的中文表达，并将变量单独放在首部或尾部，不要在中间位置。tr() 不要在 f"" 内部。如有类似情况发生，应考虑更改中文表达。

    语言文件中不包含 " "、": "、"%"、"\n" 等字符，以及专有名词（Python、Java、Markdown、JSON、OpenList、AI、API Key、Token、Ctrl、OCR、1920x1080 等），此类内容不用 tr() 包裹，以 tr("文本") + "\n" 的形式拼接。

    文本中的英文与中文之间、专有名词与其他字符之间、字符串与变量之间，要有空格，不要在翻译中加，用 " " 拼接。如 tr("文本") + " " + var 或 var + " " + tr("文本") 拼接。专有名词与特殊字符相邻，可以合并。如 "API " + tr("接口")，tr("最大") + " Token"。

    统一中文表述，尽可能减少相似度较高的翻译，对其进行复用。

    """
    return Translator().tr(key)


def getTimestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def restartApplication(parent=None):
    if not messageBox(parent, tr("重启"), tr("确定重启应用？")):
        return
    if Interpret:
        QTimer.singleShot(100, lambda: os.execv(sys.executable, [sys.executable] + sys.argv))
    else:
        QTimer.singleShot(100, lambda: os.execv(sys.executable, [sys.executable] + sys.argv[1:]))


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
        usage = f"CPU {cpu:.1f}%" + " | " + tr("内存") + f" {mem:.0f} MB"
        return usage
    except Exception:
        logger.exception("获取资源占用异常")
        return False


class UsageMonitor:
    """资源占用监视器，封装定时器与标签更新"""

    def __init__(self, parent, label, config, interval=3000):
        self._parent = parent
        self._label = label
        self._config = config
        self._interval = interval
        self._timer = None
        self._active = False

    def start(self):
        """启动定时器"""
        if self._timer:
            return
        self._timer = QTimer(self._parent)
        self._timer.timeout.connect(self._update)
        self._timer.start(self._interval)
        self._update()
        self._active = True

    def stop(self):
        """停止定时器"""
        if self._timer:
            self._timer.stop()
            self._timer.deleteLater()
            self._timer = None
        self._active = False

    def pause(self):
        """暂停（最小化时调用），返回是否之前是活跃的"""
        was_active = self._active
        self.stop()
        return was_active

    def resume(self, was_active):
        """恢复（从最小化恢复时调用）"""
        if was_active:
            self.start()

    def sync(self):
        """根据当前配置同步定时器和标签可见性"""
        enabled = self._config.get("usage", True)
        self._label.setVisible(enabled)
        if enabled:
            self.start()
        else:
            self.stop()

    def _update(self):
        usage = monitor()
        if usage:
            self._label.setText(usage)


def urlToPath(url: QUrl) -> str:
    """将 QUrl 转换为本地路径（处理 Windows file:///C:/... 格式）"""
    path = url.toLocalFile() or url.path()
    if sys.platform == "win32" and len(path) > 2 and path[0] == "/" and path[2] == ":":
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
        self.setText(tr("拖拽文件或文件夹到此处"))

    def _filterFiles(self, files: list) -> list:
        if not self._file_filter:
            return files
        return [
            f for f in files if any(f.lower().endswith(ext.lower()) for ext in self._file_filter)
        ]

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setStyleSheet("color: black; border: 2px dashed blue;")
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent):
        self.resetStyle()
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
            self.fileOrFolderDropped.emit(files[0] if len(files) == 1 else str(len(files)) + " " + tr("个文件"))

        event.acceptProposedAction()

    def resetStyle(self):
        self.setStyleSheet("color: gray; border: 2px dashed gray;")

    def setFolderPath(self, path: str):
        self.setText(path)


class RuntimeManager:
    def __init__(self):
        self.Python = ""
        self.Java = ""
        self.Temp_Path = []
        self.Env_Vars = {}
        self._managed_keys = set()
        self._injected_paths = []

    def loadConfig(self, config):
        """从配置加载环境变量"""
        env_config = config.get("Launch.Runtime", {})
        self.Python = env_config.get("Python", "")
        self.Java = env_config.get("Java", "")
        self.Env_Vars = {}
        self.Temp_Path = []
        for item in env_config.get("Temp_Path", []):
            if "=" in item:
                key, _, val = item.partition("=")
                k, v = key.strip(), val.strip()
                if k and v:
                    self.Env_Vars[k] = v
            else:
                self.Temp_Path.append(item)

    def inject(self):
        """全局注入运行时环境变量到 os.environ"""
        # 先清理上次注入的 key
        for key in self._managed_keys:
            os.environ.pop(key, None)

        self._managed_keys = set()

        for key, val in self.Env_Vars.items():
            if val:
                os.environ[key] = val
                self._managed_keys.add(key)

        path_list = []
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

        # 注入 PATH：移除旧注入路径，再追加新路径（去重）
        parts = os.environ.get("PATH", "").split(os.pathsep)
        if self._injected_paths:
            removed = {os.path.normpath(p).lower() for p in self._injected_paths}
            parts = [p for p in parts if p and os.path.normpath(p).lower() not in removed]
        if path_list:
            seen = {os.path.normpath(p).lower() for p in parts if p}
            to_add = [p for p in path_list if os.path.normpath(p).lower() not in seen]
            if to_add:
                parts = to_add + parts
            self._injected_paths = path_list
        else:
            self._injected_paths = []
        os.environ["PATH"] = os.pathsep.join(parts)

        logger.info(f"环境变量注入完成，PATH 长度: {len(os.environ.get('PATH', ''))}")

env = RuntimeManager()


def openTerminal(path):
    """打开终端"""
    if not path:
        logger.warning(f"无效的路径: {path!r}")
        return False

    path = os.path.abspath(path)
    if os.path.isfile(path):
        path = os.path.dirname(path)

    try:
        if sys.platform == "win32":
            subprocess.Popen(["cmd", "/k", "cd", "/d", path], cwd=path)
        elif sys.platform == "linux":
            subprocess.Popen(["xdg-terminal"], cwd=path, start_new_session=True)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-a", "Terminal", path], cwd=path)
        logger.info(f"已打开终端: {path}")
        return True
    except Exception:
        logger.exception("打开终端失败")
        return False


def fileHash(path: str, algorithm="md5") -> str:
    """计算文件哈希值，建议使用 md5 或 sha256 算法"""
    ha = hashlib.new(algorithm)
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                ha.update(chunk)
        return ha.hexdigest()
    except Exception:
        logger.exception(f"计算哈希值失败: {path}")
        return ""

def checksum(path: str, algorithm="md5", _visited=None) -> str:
    """计算文件夹哈希值（哈希树），调用时请用 info 级别的日志记录路径和计算哈希值的结果， _visited 用于检查是否形成环路（绑定挂载、循环硬链接目录等非符号链接导致的循环）"""

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
                logger.info(f"跳过非常规文件: {child}")

            entries.append((name, t, child_hash))

        for name, t, child_hash in sorted(entries, key=lambda x: x[0]):
            ha.update(f"{t}:{name}\0{child_hash}\0".encode("utf-8"))

        return ha.hexdigest()

    logger.warning(f"不支持的路径类型: {path}")
    return ""


def imageBase64(path: str) -> tuple[str, str]:
    """读取图片文件，返回 (base64_data, mime_type)"""
    ext = os.path.splitext(path)[1].lower().lstrip(".") or "png"
    if ext == "jpg":
        ext = "jpeg"
    mime = f"image/{ext}"
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode()
    return data, mime


def formatFileSize(size: int) -> str:
    """格式化文件大小"""
    if size < 1024:
        return f"{size} B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    elif size < 1024**3:
        return f"{size / (1024 ** 2):.1f} MB"
    elif size < 1024**4:
        return f"{size / (1024 ** 3):.2f} GB"
    else:
        return f"{size / (1024 ** 4):.2f} TB"


def download(url: str, path: Path, report=None):
    """下载文件，path 若为文件夹路径，则尝试从服务器获取文件名，若为指定的文件路径，则直接下载为该路径，若已存在，则需重命名为 name(1).zip 类似。
    从 HTTP 响应获取文件的最后修改时间，作为 os.utime() 的参数，格式为 Unix 时间戳"""
    try:
        response = requests.get(url, timeout=10, stream=True)
        response.raise_for_status()
        disposition = response.headers.get("Content-Disposition")
        size_str = response.headers.get("Content-Length")
        size = int(size_str) if size_str else 0
        last_modified = response.headers.get("Last-Modified")
        downloaded = 0

        if path.is_dir():
            name = None
            if disposition and "filename=" in disposition:
                name = disposition.split("filename=")[1].strip("'\"")
            if not name:
                name = Path(urlparse(url).path).name or "download"
            path = path / name

        # 到这里则 path 一定是文件了
        if path.exists():
            stem = path.stem
            suffix = path.suffix
            counter = 1
            while path.exists():
                path = path.with_name(f"{stem}({counter}){suffix}")
                counter += 1

        with open(path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if report:
                        report(downloaded, size)

        if last_modified:
            dt = datetime.strptime(last_modified, "%a, %d %b %Y %H:%M:%S %Z")
            timestamp = dt.timestamp()
            os.utime(path, [timestamp, timestamp])
            time_info = dt.strftime("%Y-%m-%d %H:%M:%S %Z")
            logger.info(f"{path} 的最后修改时间为 {time_info}")
        else:
            logger.info("服务器未返回最后修改时间")

        return True
    except requests.exceptions.RequestException:
        logger.exception("网络错误")
    except Exception:
        logger.exception("未知下载错误")
    return False


def urlConvert(url: str):
    """把 https://github.com/{author}/{repo}  https://github.com/{author}/{repo}/releases  一类的网址，转化成 https://api.github.com/repos/{author}/{repo}/releases/latest """
    if not url:
        return ""
    if '://' not in url:
        url = 'https://' + url
    parsed = urlparse(url)
    hostname = (parsed.hostname or '').removeprefix('www.')
    if hostname != 'github.com':
        return url
    path = parsed.path.strip('/')
    if path.endswith('.git'):
        path = path[:-4]
    parts = path.split('/')
    if len(parts) < 2:
        return url
    author, repo = parts[0], parts[1]
    if len(parts) >= 4 and parts[2] == 'releases' and parts[3] == 'tag':
        tag = parts[4] if len(parts) > 4 else 'latest'
        return f"https://api.github.com/repos/{author}/{repo}/releases/tags/{tag}"
    return f"https://api.github.com/repos/{author}/{repo}/releases/latest"


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
    """绝对路径与相对路径转换，不处理有环境变量的路径"""
    if "%" in path or "$" in path:
        return path
    try:
        if mode == "relative" and os.path.isabs(path):
            return os.path.relpath(path, str(root))
        elif mode == "absolute" and not os.path.isabs(path):
            return os.path.normpath(os.path.join(str(root), path))
    except ValueError:
        logger.exception("路径模式转换失败")
    return path


def getFilePath(parent: QWidget, title="", filter="", mode="file", edit=None):
    """封装 QFileDialog 返回文件路径，可以与 QLineEdit 配合设置文本，用 lambda 连接到 选择 按钮"""
    if mode == "file":
        path, _ = QFileDialog.getOpenFileName(parent, title, "", filter)
    else:
        path = QFileDialog.getExistingDirectory(parent, title)
    if path:
        path = os.path.normpath(path)
        if edit:
            edit.setText(path)
        return path


def filePathWidget(parent, form: QFormLayout, name, title="", filter="", mode="file"):
    """封装 QLabel + QLineEdit + '选择' QPushButton 并直接添加到 form
    返回 (QLineEdit, QPushButton)"""
    hbox = QHBoxLayout()
    edit = QLineEdit()
    hbox.addWidget(edit)

    btn = QPushButton(tr("选择"))
    btn.setMinimumWidth(50)
    btn.clicked.connect(lambda: getFilePath(parent, title, filter, mode, edit))
    hbox.addWidget(btn)

    form.addRow(name, hbox)
    return edit, btn


def labelEdit(parent, name):
    """封装 QLabel + QLineEdit，水平布局，独立宽度，不按 QFormLayout 的方式对齐"""
    layout = QHBoxLayout()
    label = QLabel(name)
    edit = QLineEdit()
    layout.addWidget(label)
    layout.addWidget(edit)
    layout.setStretch(1, 1)
    return layout, edit


class ClipboardMonitor(Singleton):
    """剪贴板监控器 - 使用 QClipboard 信号监听剪贴板变化"""

    def _init(self):
        self._clipboard = QApplication.clipboard()
        self._callbacks = set()
        self.enabled = False
        try:
            self._last_content = self._clipboard.text() or ""
        except Exception:
            logger.exception("初始化剪贴板失败")
            self._last_content = ""

    def start(self):
        self.enabled = True
        self._clipboard.dataChanged.connect(self._onClipboardChanged)

    def stop(self):
        self.enabled = False
        try:
            if self._clipboard.receivers(self._clipboard.dataChanged) > 0:
                self._clipboard.dataChanged.disconnect(self._onClipboardChanged)
        except (TypeError, RuntimeError):
            logger.exception("剪贴板信号断开失败")

    def _onClipboardChanged(self):
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
    """封装 QDialog ，输入单一输入，替换 QInputDialog"""
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
            messageBox(parent, tr("警告"), tr("不能为空"), 1)
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
        layout.addRow(tr(name), name_edit)

        if textedit:
            value_edit = QTextEdit(value_text)
            value_edit.setAcceptRichText(False)
        else:
            value_edit = QLineEdit(value_text)
        layout.addRow(tr(value), value_edit)
        vlayout.addLayout(layout)

        if dialogBox(vlayout, dialog):
            name_result = name_edit.text().strip()
            value_result = (
                value_edit.toPlainText().strip() if textedit else value_edit.text().strip()
            )
            if not name_result:
                messageBox(parent, tr("警告"), name + " " + tr("不能为空"), 1)
                name_text, value_text = name_result, value_result
                continue
            return name_result, value_result
        return None, None


def dialogBox(layout: QLayout, dialog: QDialog, num: int = 2, show=True):
    """封装 QDialog 的按钮
    差异仅为 StandardButton.Ok 的文本为确定，.Cancel 的文本为取消
    统一使用 Ok、Cancel ，不使用 Yes、No"""
    if num == 1:
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        box.accepted.connect(dialog.accept)
        box.button(QDialogButtonBox.StandardButton.Ok).setText(tr("确定"))
    elif num == 2:
        box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        box.accepted.connect(dialog.accept)
        box.rejected.connect(dialog.reject)
        box.button(QDialogButtonBox.StandardButton.Ok).setText(tr("确定"))
        box.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("取消"))
    layout.addWidget(box)
    if show:
        return dialog.exec() == QDialog.DialogCode.Accepted
    else:
        return box


def messageBox(parent, title, text, num: int = 2):
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
        msg_box.setStandardButtons(
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel
        )
        msg_box.button(QMessageBox.StandardButton.Ok).setText(tr("确定"))
        msg_box.button(QMessageBox.StandardButton.Cancel).setText(tr("取消"))
        return msg_box.exec() == QMessageBox.StandardButton.Ok
    if num == 3:
        msg_box.setStandardButtons(
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel
        )
        msg_box.button(QMessageBox.StandardButton.Save).setText(tr("保存"))
        msg_box.button(QMessageBox.StandardButton.Discard).setText(tr("不保存"))
        msg_box.button(QMessageBox.StandardButton.Cancel).setText(tr("取消"))

    return msg_box.exec()


def activateWidget(cls):
    """防重复，已有对话框实例时显示而不创建，避免嵌套 exec()，返回 True ，否则 False"""
    for widget in QApplication.topLevelWidgets():
        if isinstance(widget, cls) and widget.isVisible():
            widget.raise_()
            widget.activateWindow()
            return True
    return False


class ManagePair(QDialog):
    """管理数据，每组数据有 name 和 value"""

    def __init__(self, parent=None, pairs=None, connect_signals: bool = True):
        super().__init__(parent)
        self.setMinimumSize(500, 300)

        # 创建控件
        self.pair_list = QListWidget()
        self.pair_list.setStyleSheet("QListWidget::item { height: 30px; }")
        self.add_btn = QPushButton(tr("添加"))
        self.edit_btn = QPushButton(tr("编辑"))
        self.delete_btn = QPushButton(tr("删除"))

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
            self.setPairs(pairs)

    def setPairs(self, pairs):
        """设置配对数据，支持 dict {name: value} 或列表 [{"name":..., "value":...}]"""
        self.pair_list.clear()
        if isinstance(pairs, dict):
            items = pairs.items()
        elif isinstance(pairs, list):
            items = [(p.get("name", ""), p.get("value", "")) for p in pairs if isinstance(p, dict)]
        else:
            items = []
        for name, value in items:
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, value)
            self.pair_list.addItem(item)

    def getPairs(self):
        """获取当前所有配对，返回 dict {name: value}"""
        pairs = {}
        for i in range(self.pair_list.count()):
            item = self.pair_list.item(i)
            name = item.text()
            value = item.data(Qt.ItemDataRole.UserRole)
            pairs[name] = value
        return pairs

    def pairDialog(self, title, initial_name="", initial_value=""):
        """显示编辑对话框，返回 (name, value) 如果用户确认，否则 (None, None)"""
        return dictDialog(self, title, name_text=initial_name, value_text=initial_value)

    def add(self):
        """添加新配对"""
        name, value = self.pairDialog(tr("添加"))
        if name:
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, value)
            self.pair_list.addItem(item)

    def edit(self):
        current_item = self.pair_list.currentItem()
        if not current_item:
            messageBox(self, tr("警告"), tr("请先选择一项"), 1)
            return

        old_name = current_item.text()
        old_value = current_item.data(Qt.ItemDataRole.UserRole)

        name, value = self.pairDialog(tr("编辑"), old_name, old_value)
        if name:
            current_item.setText(name)
            current_item.setData(Qt.ItemDataRole.UserRole, value)

    def delete(self):
        current_item = self.pair_list.currentItem()
        if not current_item:
            messageBox(self, tr("警告"), tr("请先选择一项"), 1)
            return

        if messageBox(self, tr("确认删除"), tr("是否确认删除") + " '" + current_item.text() + "'"):
            row = self.pair_list.row(current_item)
            self.pair_list.takeItem(row)


def fetchWebTitle(url):
    """获取网页标题，失败返回 None"""
    try:
        if not url.startswith("http"):
            url = "https://" + url
        resp = requests.get(url, timeout=5, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        resp.encoding = resp.apparent_encoding
        m = re.search(r"<title[^>]*>(.*?)</title>", resp.text, re.I | re.S)
        if m:
            return m.group(1).strip()
    except Exception:
        logger.exception("获取网页标题失败")
    return None


def fetchWebIcon(url):
    """获取网站图标，保存到 data/url/ 并返回路径，失败返回 None"""
    try:
        if not url.startswith("http"):
            url = "https://" + url
        parsed = urlparse(url)

        # 从 HTML 中解析 favicon 链接
        icon_url = None
        resp = requests.get(url, timeout=5, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        m = re.search(
            r'<link[^>]*rel=["\'](?:shortcut )?icon["\'][^>]*href=["\']([^"\']+)["\']',
            resp.text, re.I,
        )
        if m:
            icon_href = m.group(1)
            if icon_href.startswith("//"):
                icon_url = parsed.scheme + ":" + icon_href
            elif icon_href.startswith("/"):
                icon_url = f"{parsed.scheme}://{parsed.netloc}{icon_href}"
            elif not icon_href.startswith("http"):
                icon_url = f"{parsed.scheme}://{parsed.netloc}/{icon_href}"
            else:
                icon_url = icon_href
        else:
            icon_url = f"{parsed.scheme}://{parsed.netloc}/favicon.ico"

        icon_data = requests.get(icon_url, timeout=5, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }).content
        if len(icon_data) < 100:
            return None

        save_dir = data_dir / "url"
        save_dir.mkdir(parents=True, exist_ok=True)
        hostname = parsed.netloc.replace(":", "_")
        ext = Path(urlparse(icon_url).path).suffix or ".png"
        save_path = save_dir / f"{hostname}{ext}"
        save_path.write_bytes(icon_data)
        return str(save_path)
    except Exception:
        logger.exception("获取网站图标失败")
    return None


def fileType(path: str, mode: str) -> bool:
    """判断文件类型，无法判断 .tar.gz 这样的类型"""
    return Path(path).suffix.lower() in EXTENSION[mode]


def sortKey(path):
    """自然排序 key：提取文件名中的数字用于排序"""
    basename = os.path.basename(path)
    parts = re.split(r'(\d+)', basename)
    return [int(p) if p.isdigit() else p for p in parts]


# 文件树（支持保存为文件）
def fileTree(directory: Path, prefix: str = "") -> list:
    """递归生成树状结构的文本行列表
    :param directory: 当前目录的 Path 对象
    :param prefix: 当前层的前缀字符串（用于绘制树形线）
    :return: 字符串列表，每一行是树的一行"""
    lines = []
    try:
        items = list(directory.iterdir())
    except PermissionError:
        lines.append(f"{prefix}[" + tr("无法读取目录") + "]")
        return lines

    dirs = sorted([item for item in items if item.is_dir()])
    files = sorted([item for item in items if item.is_file()])
    all_items = dirs + files

    for idx, item in enumerate(all_items):
        is_last = (idx == len(all_items) - 1)
        connector = "└── " if is_last else "├── "
        extension = "    " if is_last else "│   "

        lines.append(f"{prefix}{connector}{item.name}")

        if item.is_dir():
            sub_prefix = prefix + extension
            lines.extend(fileTree(item, sub_prefix))

    return lines


# Windows 11 右键一级菜单
# def register_context_menu():
#     """注册一级菜单（需要 DLL + MSIX 在同目录）"""
#     dll = root / "OShellExt.dll"
#     subprocess.run(["rundll32.exe", f"{dll},RegisterPackage"], check=True)

# def unregister_context_menu():
#     """移除一级菜单"""
#     dll = root / "OShellExt.dll"
#     subprocess.run(["rundll32.exe", f"{dll},RemovePackage"], check=True)
