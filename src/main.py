import os
import sys
import re
import copy
import subprocess
import webbrowser
if sys.platform == "win32":
    from ctypes import windll
from pathlib import Path

from psutil import Process, process_iter, NoSuchProcess, AccessDenied
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget, QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QScrollArea, QLabel, QPushButton, QToolButton, QLineEdit, QComboBox, QMenu, QFormLayout, QFrame, QFileIconProvider, QCheckBox, QSystemTrayIcon
from PySide6.QtGui import QAction, QFont, QIcon, QKeySequence, QShortcut, QCursor, QDragEnterEvent, QDropEvent, QDrag
from PySide6.QtCore import Qt, QSize, Signal, Slot, QEvent, QFileInfo, QTimer, QPoint, QMimeData

from src.util import logger, theme_dir, logo_ico, logo_png, logo_icn, isAdmin, runAdmin, openTerminal, convertPath, getFilePath, filePathWidget, Translator, tr, APP_NAME, restartApplication, showFile, dialogBox, messageBox, service, inputDialog, log_file, config_file, UsageMonitor, env, fetchWebTitle, fetchWebIcon
from src.config import SettingsDialog, getConfig
from src.gui.control import WindowMouse, WindowControl, show_plugin_dialog
from src.core.input import GlobalHotkeyListener, KeyCaptureFilter, copy_selection
from src.core.timer import TimerManager
from src.plugin import getPluginManager, pluginActionMenu
from src.system import SYSTEM_ACT, getFileIcon

# 全局快捷键是 hotkey，编辑器快捷键是 shortcut。在程序中只提供一种全局快捷键，即通过启动器的快捷键间接调用，减少复杂性。提供的快捷键页面后续也分成两种。


def setApp(app: QApplication):
    config = getConfig()
    font = QFont(config.get("font_family"), config.get("font_size"))
    if sys.platform == "win32":
        font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
        icon = QIcon(str(logo_ico))
    elif sys.platform == "linux":
        icon = QIcon(str(logo_png))
    elif sys.platform == "darwin":
        icon = QIcon(str(logo_icn))
    app.setFont(font)
    app.setWindowIcon(icon)
    env.loadConfig(getConfig())
    env.inject()

class SystemTray:
    """系统托盘"""
    
    def __init__(self, parent, app: QApplication = None):
        self.parent = parent
        self.app = app
        self.tray_icon: QSystemTrayIcon = None
        self.tray_menu: QMenu = None
    
    def init_tray(self):
        """初始化系统托盘"""
        tray = getConfig().get("tray", False)
        if tray:
            self._create_tray_icon()
    
    def update_tray(self):
        """更新托盘图标状态（根据当前配置）"""
        tray = getConfig().get("tray", False)
        if tray and self.tray_icon is None:
            self._create_tray_icon()
        elif not tray and self.tray_icon:
            self._remove_tray_icon()
    
    def _create_tray_icon(self):
        """创建托盘图标"""
        if self.tray_icon:
            return

        icon = QIcon(str(logo_ico))
        self.tray_icon = QSystemTrayIcon(icon, self.parent)
        self.tray_icon.setToolTip(f"{APP_NAME}")
        
        self.tray_menu = QMenu(self.parent)
        self.tray_menu.setObjectName("tray")
        self.tray_menu.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.tray_menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.tray_menu.setFixedWidth(80)
        
        show_action = self.tray_menu.addAction(tr("主窗口"))
        show_action.triggered.connect(self.show_from_tray)
        
        quit_action = self.tray_menu.addAction(tr("退出"))
        quit_action.triggered.connect(self.quit_from_tray)
        
        self.tray_icon.show()
        
        self.tray_icon.activated.connect(self.on_tray_activated)
    
    def _remove_tray_icon(self):
        """移除托盘图标"""
        if self.tray_icon:
            self.tray_icon.hide()
            self.tray_icon.deleteLater()
            self.tray_icon = None
        if self.tray_menu:
            self.tray_menu.deleteLater()
            self.tray_menu = None
    
    def show_from_tray(self):
        """从托盘显示窗口"""
        self.parent.show()
        self.parent.activateWindow()
    
    def quit_from_tray(self):
        """从托盘退出程序"""
        self._remove_tray_icon()
        if self.app:
            self.app.quit()
    
    def on_tray_activated(self, reason):
        """托盘图标被点击"""
        if reason == QSystemTrayIcon.ActivationReason.Context:
            if self.tray_menu is None:
                return
            sz = self.tray_menu.sizeHint()
            icon_rect = self.tray_icon.geometry()
            if icon_rect and icon_rect.isValid():
                c = icon_rect.center()
                x = c.x()
                y = c.y() - sz.height()
            else:
                cursor = QCursor.pos()
                x = cursor.x() - sz.width()
                y = cursor.y() - sz.height()
                screen = QApplication.screenAt(cursor)
                if screen:
                    g = screen.availableGeometry()
                    x = max(g.left(), min(x, g.right() - sz.width() - 4))
                    y = max(g.top(), min(y, g.bottom() - sz.height()))
            self.tray_menu.popup(QPoint(x, y))
        elif reason == QSystemTrayIcon.ActivationReason.DoubleClick or reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show_from_tray()

class ServiceProcess:
    """监控主进程退出后自动清理附属进程和服务"""

    _active_pids: set = set()

    def __init__(self, process_names: list, service_names: list):
        self.process_names = process_names
        self.service_names = service_names
        self.monitored_pids: set = set()
        self._initial_pids: set = set()
        self._timer = None
        self._cleaned_up = False

    def startMonitor(self, pids: set):
        """开始监控指定 PID"""
        dupes = pids & ServiceProcess._active_pids
        if dupes:
            logger.warning(f"PID {dupes} 已在监控中，跳过")
            return
        ServiceProcess._active_pids |= pids
        self._initial_pids = pids.copy()
        self.monitored_pids = pids.copy()
        self._timer = TimerManager().create_timer()
        self._timer.setInterval(10000)
        self._timer.timeout.connect(self._poll)
        self._timer.start()
        logger.info(f"开始监控 {len(self.monitored_pids)} 个进程: {self.monitored_pids}")

    def _poll(self):
        """10s 定时检查主进程是否存活"""
        alive = set()
        for pid in self.monitored_pids:
            try:
                if Process(pid).is_running():
                    alive.add(pid)
            except (NoSuchProcess, AccessDenied):
                pass
        self.monitored_pids = alive
        if not alive:
            logger.info("主进程已退出，开始清理")
            self._cleanup()
        else:
            logger.info(f"监控中，进程仍存活: {alive}")

    def _cleanup(self):
        """清理附属进程和服务"""
        if self._cleaned_up:
            return
        self._cleaned_up = True

        if self._timer:
            self._timer.stop()
            self._timer.deleteLater()
            self._timer = None

        ServiceProcess._active_pids.difference_update(self._initial_pids)

        target_names = [n.strip().strip('"').lower() for n in self.process_names]
        targets = {}
        try:
            for p in process_iter(['pid', 'name']):
                try:
                    if p.info['name'] and p.info['name'].lower() in target_names:
                        targets.setdefault(p.info['name'].lower(), []).append(p.info['pid'])
                except (NoSuchProcess, AccessDenied):
                    pass
        except Exception:
            logger.exception("遍历进程时出错")

        for name, pids in targets.items():
            for pid in pids:
                try:
                    proc = Process(pid)
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except Exception:
                        proc.kill()
                    logger.info(f"已终止进程: {name} (PID {pid})")
                except (NoSuchProcess, AccessDenied):
                    pass

        service(self.service_names, "stop", 10)
        logger.info(f"附属清理完成: 进程={self.process_names}, 服务={self.service_names}")

# 系统关键进程名单（小写），禁止终止
PROCESS_LIST = {
    "system", "smss.exe", "csrss.exe", "wininit.exe", "services.exe",
    "lsass.exe", "svchost.exe", "winlogon.exe", "spoolsv.exe",
    "conhost.exe", "dwm.exe", "explorer.exe", "taskhostw.exe",
    "runtimebroker.exe", "securityhealthservice.exe",
    "msmpeng.exe", "nissrv.exe",
}

# 系统关键服务名单（小写），禁止停止/修改
SERVICE_LIST = {
    "rpcss", "rpceptmapper", "dcomlaunch", "plugplay",
    "power", "gpsvc", "profsvc", "wpnsystem",
    "wdnissvc", "windefend", "sense",
}

def filterList(items: list, whitelist: set, kind: str) -> list:
    safe = []
    for item in items:
        key = item.strip().strip('"').lower()
        if key in whitelist:
            logger.warning(f"{kind} \"{item}\" 在系统白名单中，已跳过")
        else:
            safe.append(item)
    return safe


def validateServiceName(text: str) -> tuple:
    """校验服务名格式，返回 (是否合法, 错误信息)"""
    if not text.strip():
        return True, ""
    parts = [s.strip() for s in text.split("|") if s.strip()]
    for part in parts:
        if part.startswith('"'):
            if not part.endswith('"'):
                return False, f"服务 {part} 的引号未闭合"
            inner = part[1:-1]
            if not inner:
                return False, f"服务 {part} 引号内容为空"
            if not re.match(r"^[a-zA-Z0-9 _.\-]+$", inner):
                return False, f"服务 {inner} 包含不合法字符"
        else:
            if '"' in part:
                return False, f"服务 {part} 引号位置不正确"
            if not re.match(r"^[a-zA-Z0-9.\-_]+$", part):
                return False, f"服务 {part} 包含不合法字符"
    return True, ""


def validateProcessName(text: str) -> tuple:
    """校验进程名格式，返回 (是否合法, 错误信息)"""
    if not text.strip():
        return True, ""
    parts = [s.strip() for s in text.split("|") if s.strip()]
    for part in parts:
        if part.startswith('"'):
            if not part.endswith('"'):
                return False, f"进程 {part} 的引号未闭合"
            inner = part[1:-1]
            if not inner:
                return False, f"进程 {part} 引号内容为空"
            if not re.match(r"^[a-zA-Z0-9 _.\-]+$", inner):
                return False, f"进程 {inner} 包含不合法字符"
            if "." not in inner:
                return False, f"进程 {inner} 缺少扩展名"
        else:
            if '"' in part:
                return False, f"进程 {part} 引号位置不正确"
            if not re.match(r"^[a-zA-Z0-9.\-_]+$", part):
                return False, f"进程 {part} 包含不合法字符"
            if "." not in part:
                return False, f"进程 {part} 缺少扩展名"
    return True, ""

# 打开文件或文件夹，不用检查文件存在性，runItem 统一检查并抛异常
def openFile(path: str, cwd=None, args=None, operation="open"):
    if sys.platform == "win32":
        # os.startfile(path) 不支持参数，所以使用 windll
        path = os.path.expandvars(path)
        result = windll.shell32.ShellExecuteW(None, operation, path, args, cwd, 1)
        if result <= 32:
            raise RuntimeError(f"打开文件失败 {result}")
    elif sys.platform == "linux":
        if args:
            logger.warning("xdg-open 不支持参数，已忽略")
        try:
            subprocess.run(["xdg-open", path], cwd=cwd, check=True)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"打开文件失败: {e}")
    elif sys.platform == "darwin":
        if args:
            logger.warning("macOS open 命令不支持参数，已忽略")
        try:
            subprocess.run(["open", path], cwd=cwd, check=True)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"打开文件失败: {e}")

# 命令行应用，工作目录为空时，打开软件所在目录。不为空时，打开cmd并跳转到工作目录。
def cmdApp(path: str, cwd=None, *args, **kwargs):
    if cwd:
        openTerminal(cwd)
    else:
        openTerminal(Path(path).resolve().parent)

def runPython(path: str, cwd, args, operation):
    run_path = getConfig().get("Launch.Runtime.Python", "")
    if not run_path:
        raise RuntimeError("未配置 Python 路径")
    if Path(path).suffix.lower() == '.py':
        cmd = [run_path, path]
        if args:
            cmd.append(args)
        subprocess.Popen(cmd, shell=True, cwd=cwd or None)
    else:
        raise TypeError("仅支持 .py 文件")

def runJava(path: str, cwd, args, operation):
    run_path = getConfig().get("Launch.Runtime.Java", "")
    if not run_path:
        raise RuntimeError("未配置 Java 路径")
    if Path(path).suffix.lower() == '.jar':
        cmd = [run_path, '-jar', path]
        if args:
            cmd.append(args)
        subprocess.Popen(cmd, shell=True, cwd=cwd or None)
    else:
        raise TypeError("仅支持 .jar 文件")

def open_url(url, *args, **kwargs):
    webbrowser.open(url)

# 类型映射，对于 .py，.jar，.ps1，设置 shell=True 是为了方便在关闭 cmd 窗口时结束脚本或程序，否则不好结束
TYPES = {
    "文件": openFile,
    "命令行": cmdApp,
    "Python": runPython,
    "Java": runJava,
    "网址": open_url,
    "预设": None  # 预设类型由 runPreset 方法处理
}


class DragToolButton(QToolButton):
    """支持拖拽排序的工具按钮"""
    
    drag_started = Signal(QWidget, QPoint)  # 拖拽开始信号，传递按钮和起始位置
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_start_pos = None
        self._is_dragging = False
    
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.position().toPoint()
            self._is_dragging = False
        super().mousePressEvent(event)
    
    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and self._drag_start_pos:
            distance = (event.position().toPoint() - self._drag_start_pos).manhattanLength()
            if distance >= QApplication.startDragDistance():
                if not self._is_dragging:
                    self._is_dragging = True
                    self.drag_started.emit(self, event.position().toPoint())
        super().mouseMoveEvent(event)
    
    def mouseReleaseEvent(self, event):
        self._drag_start_pos = None
        self._is_dragging = False
        super().mouseReleaseEvent(event)

class EditTool(QDialog):
    """编辑工具对话框"""
    
    def __init__(self, tool_data: dict=None, parent=None):
        super().__init__(parent)
        self.tool_data = tool_data or {}
        self.setMinimumWidth(450)
        self.setWindowTitle(tr("添加"))
        self.setWindowFlags(Qt.WindowType.Window)
        self._setup_ui()
        self._load_data()
        QTimer.singleShot(0, self._do_center)
    
    def _do_center(self):
        screen = self.screen()
        if screen:
            screen_geom = screen.geometry()
            x = screen_geom.x() + (screen_geom.width() - self.width()) // 2
            y = screen_geom.y() + (screen_geom.height() - self.height()) // 2
            self.move(x, y)
    
    def _setup_ui(self):
        """初始化UI"""
        layout = QVBoxLayout(self)
        
        form_layout = QFormLayout()
        
        self.name_edit = QLineEdit()
        form_layout.addRow("名称", self.name_edit)
        
        self.type_combo = QComboBox()
        # 排除"预设"类型，因为预设工具不能通过对话框添加
        available_types = [t for t in TYPES.keys() if t != "预设"]
        self.type_combo.addItems(available_types)
        self.type_combo.currentTextChanged.connect(self._on_type_changed)
        form_layout.addRow("类型", self.type_combo)
        
        # 路径/URL
        def _on_browse():
            if self.type_combo.currentText() == "网址":
                self._fetch_url_info()
                return
            t = self.type_combo.currentText()
            choices = {
                "文件": ("选择文件", ""),
                "命令行": ("选择可执行文件", "可执行文件 (*.exe);;所有文件 (*)"),
                "Python": ("Python 脚本", "Python 文件 (*.py);;所有文件 (*)"),
                "Java": ("JAR 文件", "JAR 文件 (*.jar);;所有文件 (*)"),
            }
            title, filt = choices.get(t, ("选择文件", ""))
            path = getFilePath(self, title, filt, edit=self.path_edit)
            if path and not self.name_edit.text():
                self.name_edit.setText(Path(path).stem)

        self.path_edit, self.browse_btn = filePathWidget(self, form_layout, "路径", "选择文件", "")
        self._path_label = form_layout.itemAt(form_layout.rowCount() - 1, QFormLayout.ItemRole.LabelRole).widget()
        self.browse_btn.clicked.disconnect()
        self.browse_btn.clicked.connect(_on_browse)
        
        # 工作目录
        self.cwd_edit, self.cwd_browse_btn = filePathWidget(self, form_layout, "工作目录", "", "", "dir")
        
        # 参数
        self.args_edit = QLineEdit()
        form_layout.addRow("启动参数", self.args_edit)
        
        # 附属服务（仅文件类型显示）
        self.service_edit = QLineEdit()
        self._service_label = QLabel("服务")
        form_layout.addRow(self._service_label, self.service_edit)
        
        # 附属进程（仅文件类型显示）
        self.process_edit = QLineEdit()
        self._process_label = QLabel("进程")
        form_layout.addRow(self._process_label, self.process_edit)
        
        # 备注
        self.note_edit = QLineEdit()
        self.note_edit.setMaximumHeight(60)
        form_layout.addRow("备注", self.note_edit)
        
        # 快捷键
        hotkey_layout = QHBoxLayout()
        self.hotkey_edit = QLineEdit()
        self.hotkey_edit.setPlaceholderText("点击输入快捷键")
        self.hotkey_key_capture_filter = KeyCaptureFilter(self)
        self.hotkey_key_capture_filter.key_captured.connect(lambda seq: self.hotkey_edit.setText(seq))
        self.hotkey_edit.installEventFilter(self.hotkey_key_capture_filter)
        hotkey_layout.addWidget(self.hotkey_edit)
        self.run_as_admin_cb = QCheckBox("管理员运行")
        hotkey_layout.addWidget(self.run_as_admin_cb)
        form_layout.addRow("快捷键", hotkey_layout)
        
        # 图标
        self.icon_edit, _ = filePathWidget(self, form_layout, "图标", "选择图标", "图片文件 (*.png *.jpg *.jpeg *.bmp *.ico *.gif);;所有文件 (*)")

        form_layout.setLabelAlignment(Qt.AlignmentFlag.AlignCenter)
        
        layout.addLayout(form_layout)
        
        dialogBox(layout, self, show=False)
    
    def _load_data(self):
        """加载数据"""
        if not self.tool_data:
            return

        self.name_edit.setText(self.tool_data.get("name", ""))

        tool_type = self.tool_data.get("type", "文件")

        # 预设类型不能修改
        if tool_type == "预设":
            self.type_combo.addItem("预设")
            self.type_combo.setCurrentText("预设")
            self.type_combo.setEnabled(False)
            self.type_combo.setStyleSheet("QComboBox::down-arrow { width: 0px; } QComboBox::drop-down { width: 0px; }")
            self.path_edit.setEnabled(False)
            self.browse_btn.setEnabled(False)
        else:
            index = self.type_combo.findText(tool_type)
            if index >= 0:
                self.type_combo.setCurrentIndex(index)

        # URL类型使用url字段，其他类型使用path字段
        if tool_type == "网址":
            self.path_edit.setText(self.tool_data.get("url", ""))
        else:
            self.path_edit.setText(self.tool_data.get("path", ""))

        self.cwd_edit.setText(self.tool_data.get("cwd", ""))
        self.args_edit.setText(self.tool_data.get("args", ""))
        self.service_edit.setText(self.tool_data.get("service", ""))
        self.process_edit.setText(self.tool_data.get("process", ""))
        self.run_as_admin_cb.setChecked(self.tool_data.get("run_as_admin", False))
        self.note_edit.setText(self.tool_data.get("note", ""))
        self.hotkey_edit.setText(self.tool_data.get("hotkey", ""))
        self.icon_edit.setText(self.tool_data.get("icon", ""))
    
    def _on_type_changed(self, tool_type: str):
        """类型改变时更新UI"""
        is_file = tool_type == "文件"
        self._service_label.setVisible(is_file)
        self.service_edit.setVisible(is_file)
        self._process_label.setVisible(is_file)
        self.process_edit.setVisible(is_file)

        if tool_type == "网址":
            self._path_label.setText("URL")
            self.browse_btn.setText("获取")
            self.browse_btn.setVisible(True)
            self.cwd_edit.setEnabled(False)
            self.cwd_browse_btn.setEnabled(False)
        elif tool_type == "预设":
            self._path_label.setText("路径")
            self.browse_btn.setVisible(False)
            self.cwd_edit.setEnabled(False)
            self.cwd_browse_btn.setEnabled(False)
        else:
            self._path_label.setText("路径")
            self.browse_btn.setText("选择")
            self.browse_btn.setVisible(True)
            self.cwd_edit.setEnabled(True)
            self.cwd_browse_btn.setEnabled(True)
    
    def _fetch_url_info(self):
        """获取网址信息并填充名称和图标"""
        url = self.path_edit.text().strip()
        if not url:
            messageBox(self, "提示", "请先输入网址", 1)
            return

        title = fetchWebTitle(url)
        if title and not self.name_edit.text().strip():
            self.name_edit.setText(title)

        icon_path = fetchWebIcon(url)
        if icon_path and not self.icon_edit.text().strip():
            self.icon_edit.setText(icon_path)

    def get_data(self) -> dict:
        """获取工具数据"""
        tool_type = self.type_combo.currentText()
        path_text = self.path_edit.text().strip().strip('"')
        cwd_text = self.cwd_edit.text().strip().strip('"')
        icon_text = self.icon_edit.text().strip().strip('"')
        
        path_mode = getConfig().get("Launch.path_mode", "absolute")
        
        if path_mode == "relative" and tool_type != "预设":
            path_text = convertPath(path_text, "relative")
            cwd_text = convertPath(cwd_text, "relative")
            icon_text = convertPath(icon_text, "relative")

        data = {
            "type": tool_type,
            "name": self.name_edit.text().strip(),
            "path": "" if tool_type == "网址" else path_text,
            "cwd": cwd_text,
            "args": self.args_edit.text().strip(),
            "service": self.service_edit.text().strip(),
            "process": self.process_edit.text().strip(),
            "note": self.note_edit.text().strip(),
            "hotkey": self.hotkey_edit.text().strip(),
            "icon": icon_text,
            "run_as_admin": self.run_as_admin_cb.isChecked()
        }
        
        # URL类型使用url字段替代path字段
        if tool_type == "网址":
            del data["path"]
            data["url"] = path_text
        
        # 过滤空值
        data = {k: v for k, v in data.items() if v}
        
        return data
    
    def accept(self):
        """确认"""
        if not self.name_edit.text().strip():
            messageBox(self, "警告", "名称不能为空", 1)
            return

        if not self.path_edit.text().strip():
            messageBox(self, "警告", "路径不能为空", 1)
            return

        if not self._check_whitelist():
            return

        super().accept()
        self.deleteLater()

    def _check_whitelist(self) -> bool:
        svc_text = self.service_edit.text()
        proc_text = self.process_edit.text()

        valid, msg = validateServiceName(svc_text)
        if not valid:
            messageBox(self, "格式错误", msg, 1)
            return False

        valid, msg = validateProcessName(proc_text)
        if not valid:
            messageBox(self, "格式错误", msg, 1)
            return False

        for text, whitelist, kind in [
            (svc_text, SERVICE_LIST, "服务"),
            (proc_text, PROCESS_LIST, "进程"),
        ]:
            items = [s.strip().strip('"') for s in text.split("|") if s.strip()]
            blocked = [item for item in items if item.lower().strip('"') in whitelist]
            if blocked:
                if not messageBox(self, "系统保护", f"{kind} 白名单项: {', '.join(blocked)}\n这些是系统关键项目，不建议管理。\n是否仍要保存？"):
                    return False
        return True

    def reject(self):
        """取消"""
        super().reject()
        self.deleteLater()

def argsPlaceholder(args: str) -> str:
    if not args:
        return args
    for key, value in GlobalHotkeyListener._placeholders.items():
        placeholder = f"{{{key}}}"
        if placeholder in args and value:
            args = args.replace(placeholder, value)
    return args

def _get_groups(tools: dict) -> list:
    return list(tools.keys()) if tools else []


def _get_group_tools(tools: dict, group: str) -> list:
    return tools.get(group, [])

class MainWindow(WindowMouse, QMainWindow):
    """启动器主窗口"""
    
    def __init__(self, app: QApplication=None, file_path=None):
        super().__init__()

        self.app = app
        self._editor_window = None
        self.config = getConfig()
        self._tools = getConfig().get("Launch.tools", {})
        self._path_mode = getConfig().get("Launch.path_mode", "absolute")
        self._on_top = getConfig().get("Launch.on_top", False)
        self.setStatusBar(None)
        self.system_tray = SystemTray(self, self.app)
        Translator().setLanguage(self.config.get("language", "简体中文"))
        self._current_group = getConfig().get("Launch.active_group", "")
        self._fallback_size = (600, 400)
        self.window_control = WindowControl(self)
        self._plugin_shortcuts = []
        self._service_processes = []
        self._tools_initialized = False
        self.setAcceptDrops(True)
        self._setup_ui()
        self.applyTheme()
        self.system_tray.init_tray()
        self._load_geometry()

        if file_path:
            QTimer.singleShot(0, lambda: self._open_editor(file_path))
        
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self.refreshTool)

        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.timeout.connect(self._on_hover_timeout)

        QTimer.singleShot(0, self._lazy_init)

        if self.app:
            self.app.aboutToQuit.connect(self._onQuit)

    def _onQuit(self):
        """应用退出时保存状态"""
        self._save_geometry()
        getConfig().save()

    def _sync_tools(self):
        """同步 _tools 到配置"""
        getConfig().set("Launch.tools", self._tools)

    def _save_tools(self):
        """同步 _tools 到配置并保存"""
        self._sync_tools()
        getConfig().save()

    def _open_editor(self, file_path=None):
        """打开/激活编辑器窗口"""
        from src.edit import EditorWindow
        # 按需导入降低内存占用
        if self._editor_window is None:
            self._editor_window = EditorWindow(self.app, file_path, main_window=self)
            self.applyTheme(self._editor_window)
            self._editor_window.destroyed.connect(lambda: setattr(self, '_editor_window', None))
            getPluginManager().setMainWindow(self._editor_window)
        elif file_path:
            self._editor_window.open_file_path(file_path)
        self._editor_window.show()
        self._editor_window.raise_()
        self._editor_window.activateWindow()
        return self._editor_window

    def applyTheme(self, window=None):
        """应用主题到窗口"""
        if not window:
            window = self
        try:
            theme = self.config.get("theme", "Light")
            theme_name = theme.capitalize()
            theme_file = theme_dir / f"{theme_name}.qss"
            if not theme_file.exists():
                return
            with open(theme_file, 'r', encoding='utf-8') as f:
                style = f.read()
            current = window.styleSheet()
            if style != current:
                window.setStyleSheet(style)
                if hasattr(window, 'window_control'):
                    window.window_control.update_icons_for_theme(theme)
        except Exception:
            logger.exception("应用主题失败")

    def _lazy_init(self):
        """延迟初始化"""
        self.startGlobalListener()
        self._init_plugins()

    def _init_plugins(self):
        """初始化插件系统"""
        try:
            pm = getPluginManager(self)
            pm.initConfig(getConfig())
        except Exception:
            logger.exception("插件初始化失败")

    def startGlobalListener(self):
        """启动全局监听器"""
        self._register_editor_hotkeys()
        self.registerHotkeys()
        config = getConfig()
        hotkey_str = config.get("Launch.hotkey", "Ctrl+L")
        mouse_side_enabled = config.get("Launch.mouse_side", False)
        double_ctrl_enabled = config.get("Launch.double_ctrl", False)
        GlobalHotkeyListener().start(self, hotkey_str, mouse_side_enabled, double_ctrl_enabled)

    def _register_editor_hotkeys(self):
        """注册编辑器快捷键（插件）"""
        listener = GlobalHotkeyListener()
        config = getConfig()

        plugin_config = config.get("Plugin", {})
        self._plugin_shortcuts = []
        for pname, pcfg in plugin_config.items():
            hotkey = pcfg.get("hotkey", "")
            if hotkey:
                listener.registerHotkey(hotkey, {"type": "plugin_toggle", "name": pname})
                sc = QShortcut(QKeySequence(hotkey), self, context=Qt.ShortcutContext.ApplicationShortcut)
                sc.activated.connect(lambda: None)
                self._plugin_shortcuts.append(sc)

    @Slot()
    def runHotkey(self):
        """通过快捷键运行工具"""
        listener = GlobalHotkeyListener()
        tool = listener._pending_tool
        if not tool:
            return
        tool_type = tool.get("type", "文件")

        if tool_type == "plugin_toggle":
            name = tool.get("name", "")
            editor = self._editor_window
            if editor:
                plugin = getPluginManager().plugins.get(name)
                if plugin and plugin.getAction():
                    plugin.getAction().trigger()
            return

        # 普通工具（文件/脚本/预设/网址）
        self.runItem(tool)

    def _setup_ui(self):
        """初始化UI"""
        # 无边框窗口，可独立移动
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window
        
        # 根据配置设置置顶
        if self._on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        
        self.setWindowFlags(flags)
        
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # 标题栏
        self._create_title_bar(main_layout)

        # 内容区域（分组 + 启动项）
        layout_mode = getConfig().get("Launch.layout", "horizontal")
        
        if layout_mode == "horizontal":
            # 横向排列
            self._create_group_bar(main_layout)
            
            separator = QFrame()
            separator.setObjectName("sep_h")
            separator.setFrameShape(QFrame.Shape.HLine)
            main_layout.addWidget(separator)
            
            # 启动项区域
            tools_widget = QWidget()
            tools_widget.setObjectName("launcher_tools")
            self._tools_widget = tools_widget
            tools_layout = QVBoxLayout(tools_widget)
            tools_layout.setContentsMargins(0, 0, 0, 0)
            self._create_tools_area(tools_layout)
            main_layout.addWidget(tools_widget, 1)

        else:
            # 纵向排列
            content_widget = QWidget()
            content_widget.setObjectName("launcher_content")
            content_layout = QHBoxLayout(content_widget)
            content_layout.setContentsMargins(0, 0, 0, 0)
            content_layout.setSpacing(0)
            
            # 分组区域
            self._create_group_bar(content_layout)

            separator = QFrame()
            separator.setObjectName("sep_v")
            separator.setFrameShape(QFrame.Shape.VLine)
            content_layout.addWidget(separator)
            
            # 启动项区域
            tools_widget = QWidget()
            tools_widget.setObjectName("launcher_tools")
            self._tools_widget = tools_widget
            tools_layout = QVBoxLayout(tools_widget)
            tools_layout.setContentsMargins(0, 0, 0, 0)
            self._create_tools_area(tools_layout)
            content_layout.addWidget(tools_widget, 1)
            
            main_layout.addWidget(content_widget, 1)

    def _create_title_bar(self, parent_layout: QVBoxLayout):
        """创建标题栏"""
        title_bar = QWidget()
        title_bar.setObjectName("title_bar")
        title_bar.setFixedHeight(32)
        
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(0)
        title_layout.addStretch(1)

        self.cpu_label = QLabel(self)
        self.cpu_label.setObjectName("cpu_label")
        title_layout.addWidget(self.cpu_label)
        self._usage_monitor = UsageMonitor(title_bar, self.cpu_label, self.config)
        self._usage_monitor.sync()

        self.settings_btn = QPushButton("设置")
        self.settings_btn.setObjectName("settings_btn")
        self.settings_btn.setFixedSize(70, 32)
        self.settings_btn.clicked.connect(self._configDialog)
        title_layout.addWidget(self.settings_btn)
        
        self.window_control.createWindowButton(title_layout)
        
        parent_layout.addWidget(title_bar)

        self.separate = QFrame()
        self.separate.setObjectName("sep_h")
        self.separate.setFrameShape(QFrame.Shape.HLine)
        parent_layout.addWidget(self.separate)
        
        # 标题栏双击最大化
        title_bar.mouseDoubleClickEvent = self._title_double_click

    def _title_double_click(self, event):
        """标题栏双击"""
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle_maximize()

    def _create_group_bar(self, parent_layout):
        """创建分组按钮栏"""
        self.group_frame = QFrame()
        self.group_frame.setObjectName("group_frame")
        self.group_frame.setFrameShape(QFrame.Shape.NoFrame)
        self.group_frame.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.group_frame.customContextMenuRequested.connect(self._show_group_frame_menu)

        self._setup_group_layout()
        self.refreshGroup()
        
        parent_layout.addWidget(self.group_frame)
    
    def _setup_group_layout(self):
        """设置分组布局"""
        # 清除旧布局
        if hasattr(self, 'group_layout') and self.group_layout:
            while self.group_layout.count():
                item = self.group_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            self.group_layout.deleteLater()
        
        layout_mode = getConfig().get("Launch.layout", "horizontal")
        if layout_mode == "vertical":
            self.group_layout = QVBoxLayout(self.group_frame)
        else:
            self.group_layout = QHBoxLayout(self.group_frame)
        
        self.group_layout.setContentsMargins(0, 0, 0, 0)
        self.group_layout.setSpacing(0)
    
    def refreshGroup(self):
        """刷新分组按钮"""
        while self.group_layout.count():
            item = self.group_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        groups = _get_groups(self._tools)
        
        hover_enabled = getConfig().get("Launch.hover_switch", False)
        
        for group in groups:
            btn = QPushButton(group)
            btn.setObjectName("group_btn")
            btn.setAttribute(Qt.WidgetAttribute.WA_Hover)
            btn.setCheckable(True)
            btn.setChecked(group == self._current_group)
            btn.clicked.connect(lambda checked, g=group: self.switchGroup(g))
            config = getConfig()
            btn.setFixedSize(config.get("Launch.g_w", 80), config.get("Launch.g_h", 30))
            if hover_enabled:
                btn.installEventFilter(self)
            self.group_layout.addWidget(btn)
        
        if not groups:
            h = getConfig().get("Launch.g_h", 30)
            self.group_frame.setFixedHeight(h)
        self.group_layout.addStretch()
    
    def eventFilter(self, obj, event):
        """事件过滤器 - 处理分组按钮悬停"""
        if isinstance(obj, QPushButton):
            if event.type() == QEvent.Type.HoverEnter:
                group = obj.text()
                if group != self._current_group:
                    self._hover_timer.stop()
                    self._hover_target = group
                    self._hover_timer.start(500)
            elif event.type() == QEvent.Type.HoverLeave:
                self._hover_timer.stop()
        return super().eventFilter(obj, event)

    def _on_hover_timeout(self):
        """悬停定时器触发时切换分组"""
        if self._hover_target:
            self.switchGroup(self._hover_target)
    
    def _show_group_frame_menu(self, pos):
        child = self.group_frame.childAt(pos)
        group = child.text() if isinstance(child, QPushButton) else None
        menu = QMenu(self)
        menu.addAction("新建", self._add_group)
        if group:
            menu.addSeparator()
            rename_action = QAction("重命名", self)
            rename_action.triggered.connect(lambda checked, g=group: self._rename_group(g))
            menu.addAction(rename_action)
            delete_action = QAction("删除", self)
            delete_action.triggered.connect(lambda checked, g=group: self._delete_group(g))
            menu.addAction(delete_action)
            menu.addSeparator()
            menu.addAction("上移", lambda g=group: self._move_group(g, -1))
            menu.addAction("下移", lambda g=group: self._move_group(g, 1))
        menu.exec(self.group_frame.mapToGlobal(pos))
    
    def _add_group(self):
        """添加分组"""
        name = inputDialog(self, "新建", "分组名称")
        if name:
            groups = _get_groups(self._tools)
            if name.strip() in groups:
                messageBox(self, "警告", "分组名称已存在", 1)
                return
            self._tools[name.strip()] = []
            self._save_tools()
            self.refreshGroup()
    
    def _rename_group(self, group: str):
        """重命名分组"""
        name = inputDialog(self, "重命名分组", "新名称", default=group)
        if name and name != group:
            groups = _get_groups(self._tools)
            if name.strip() in groups:
                messageBox(self, "警告", "分组名称已存在", 1)
                return
            
            self._tools[name.strip()] = self._tools.pop(group)
            
            if self._current_group == group:
                self._current_group = name.strip()
                getConfig().set("Launch.active_group", name.strip())
            
            self._save_tools()
            self.refreshGroup()
            self.refreshTool()
    
    def _delete_group(self, group: str):
        """删除分组"""
        if messageBox(self, "确认删除", f"确定要删除分组 \"{group}\" 吗？\n该分组下的启动项将被删除。"):
            del self._tools[group]
            
            if self._current_group == group:
                groups = _get_groups(self._tools)
                self._current_group = groups[0] if groups else ""
                getConfig().set("Launch.active_group", self._current_group)
            
            self._save_tools()
            self.refreshGroup()
            self.refreshTool()
    
    def _move_group(self, group: str, direction: int):
        """移动分组"""
        groups = _get_groups(self._tools)
        idx = groups.index(group)
        new_idx = idx + direction
        if 0 <= new_idx < len(groups):
            groups[idx], groups[new_idx] = groups[new_idx], groups[idx]
            self._tools = {g: self._tools.get(g, []) for g in groups}
            self._sync_tools()
            self.refreshGroup()
    
    def switchGroup(self, group: str):
        """切换分组"""
        self._current_group = group
        getConfig().set("Launch.active_group", group)
        self.refreshGroup()
        self.refreshTool()
    
    def _create_tools_area(self, parent_layout: QVBoxLayout):
        """创建启动项区域"""
        scroll_area = QScrollArea()
        scroll_area.setObjectName("tools_scroll")
        scroll_area.setWidgetResizable(True)
        scroll_area.setAcceptDrops(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.dragEnterEvent = lambda e: self.dragEnterEvent(e)
        scroll_area.dropEvent = lambda e: self.dropEvent(e)
        
        self.tools_widget = QWidget()
        self.tools_widget.setAcceptDrops(True)
        self.tools_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tools_widget.customContextMenuRequested.connect(self._show_empty_area_menu)
        padding = getConfig().get("Launch.padding", 8)
        self.tools_layout = QGridLayout(self.tools_widget)
        self.tools_layout.setContentsMargins(padding, padding, padding, padding)
        self.tools_layout.setSpacing(padding)
        self.tools_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        
        # 启用工具排序拖放
        self.tools_widget.dragEnterEvent = self._tool_drag_enter
        self.tools_widget.dragMoveEvent = self._tool_drag_move
        self.tools_widget.dropEvent = self._tool_drop
        
        scroll_area.setWidget(self.tools_widget)
        parent_layout.addWidget(scroll_area, 1)
    
    def _tool_drag_enter(self, event):
        """工具排序拖拽进入"""
        if event.mimeData().hasFormat("application/x-tool-index"):
            event.acceptProposedAction()
        else:
            event.ignore()
    
    def _tool_drag_move(self, event):
        """工具排序拖拽移动"""
        if event.mimeData().hasFormat("application/x-tool-index"):
            event.acceptProposedAction()
        else:
            event.ignore()
    
    def _tool_drop(self, event):
        """工具排序拖拽放下"""
        if not event.mimeData().hasFormat("application/x-tool-index"):
            event.ignore()
            return
        
        source_index = int(bytes(event.mimeData().data("application/x-tool-index")).decode())
        drop_pos = event.position().toPoint()
        
        # 计算目标位置
        target_index = -1
        for i in range(self.tools_layout.count()):
            item = self.tools_layout.itemAt(i)
            if item and item.widget():
                if item.widget().geometry().contains(drop_pos):
                    target_index = i
                    # 落在右半侧 → 插入到该控件之后
                    if drop_pos.x() > item.widget().geometry().center().x():
                        target_index = i + 1
                    break
        
        if target_index == -1 and self.tools_layout.count() > 0:
            # 拖到空白区域，追加到最后
            tools_widget_rect = self.tools_widget.rect()
            if tools_widget_rect.contains(drop_pos):
                target_index = self.tools_layout.count()
        
        if target_index >= 0 and target_index != source_index:
            tools = _get_group_tools(self._tools, self._current_group)
            if 0 <= source_index < len(tools) and 0 <= target_index <= len(tools):
                item = tools.pop(source_index)
                if source_index < target_index:
                    target_index -= 1
                tools.insert(target_index, item)
                self._tools[self._current_group] = tools
                self._save_tools()
                self.refreshTool()
        
        event.acceptProposedAction()
    
    def refreshTool(self):
        """刷新启动项"""
        while self.tools_layout.count():
            item = self.tools_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        tools = _get_group_tools(self._tools, self._current_group) or []
        if not tools:
            return

        config = getConfig()
        padding = config.get("Launch.padding", 8)
        item_width = config.get("Launch.i_w", 100)
        item_height = config.get("Launch.i_h", 75)
        available = self._tools_widget.width() - 2 * padding
        if available <= 0:
            available = self.width() - 2 * padding

        cols = max(1, available // (item_width + padding))
        cols = min(cols, len(tools))

        if len(tools) <= cols:
            self.tools_layout.setSpacing(padding)
        else:
            cols = max(1, min(available // item_width, len(tools)))
            spacing = (available - cols * item_width) // (cols - 1) if cols > 1 else 0
            self.tools_layout.setSpacing(spacing)

        for i, tool in enumerate(tools):
            btn = self._create_tool_button(tool, i)
            btn.setFixedSize(item_width, item_height)
            self.tools_layout.addWidget(btn, i // cols, i % cols)

    def _create_tool_button(self, tool: dict, index: int) -> DragToolButton:
        """创建工具按钮"""
        name = tool.get("name", "未命名")
        icon_path = tool.get("icon", "")
        path = tool.get("path", "") or tool.get("url", "")
        note = tool.get("note", "")
        
        btn = DragToolButton()
        btn.setObjectName("tool_btn")
        font = btn.font()
        font.setPointSizeF(max(1.0, font.pointSizeF() - 0.5))
        btn.setFont(font)
        
        # 设置提示
        tip_parts = [name]
        if path:
            tip_parts.append(path)
        if note:
            tip_parts.append(note)
        btn.setToolTip("\n".join(tip_parts))
        
        # 获取绝对路径用于图标
        path_mode = self._path_mode
        path = os.path.expandvars(path)
        if path_mode == "relative":
            path = convertPath(path, "absolute")

        # 设置图标
        icon = QIcon()
        icon_size = getConfig().get("Launch.icon", 32)
        if icon_path:
            icon_check_path = convertPath(icon_path, "absolute") if path_mode == "relative" else icon_path
            if os.path.isfile(icon_check_path):
                if Path(icon_check_path).suffix.lower() == ".exe":
                    provider = QFileIconProvider()
                    icon = provider.icon(QFileInfo(icon_check_path))
                else:
                    icon = QIcon(icon_check_path)
        elif path:
            try:
                icon = getFileIcon(path, icon_size)
            except Exception:
                logger.exception("获取图标失败")
        btn.setIcon(icon)
        btn.setIconSize(QSize(icon_size, icon_size))

        # 根据按钮宽度，在空格或英文与其他字符交界处自动换行
        item_width = getConfig().get("Launch.i_w", 100)
        padding = getConfig().get("Launch.padding", 8)
        fm = btn.fontMetrics()
        text_max_width = item_width - padding
        if fm.horizontalAdvance(name) > text_max_width * 1.1:
            mid = len(name) // 2
            best = -1
            for i in range(len(name) - 1):
                a_eng = 'a' <= name[i].lower() <= 'z'
                b_eng = 'a' <= name[i + 1].lower() <= 'z'
                if a_eng != b_eng and (best == -1 or abs(i - mid) < abs(best - mid)):
                    best = i
            if best > 0:
                name = name[:best + (name[best] != ' ')] + '\n' + name[best + 1:]

        # 设置文字在图标下方
        btn.setText(name)
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        
        btn.clicked.connect(lambda checked=False, t=tool: self.runItem(t))
        btn.drag_started.connect(self._on_drag_started)
        
        # 右键菜单
        btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        btn.customContextMenuRequested.connect(lambda pos, t=tool, i=index, b=btn: self._show_tool_menu(pos, t, i, b))
        
        return btn
    
    def _on_drag_started(self, source_btn, pos):
        """处理拖拽开始"""
        drag = QDrag(source_btn)
        mime = QMimeData()
        mime.setData("application/x-tool-index", str(self.tools_layout.indexOf(source_btn)).encode())
        drag.setMimeData(mime)
        
        # 创建拖拽时的预览图
        pixmap = source_btn.grab()
        drag.setPixmap(pixmap)
        drag.setHotSpot(pos)
        
        # 执行拖拽
        result = drag.exec(Qt.DropAction.MoveAction)
        
        # 拖拽完成后重置样式
        source_btn.setProperty("dragging", False)
        source_btn.style().unpolish(source_btn)
        source_btn.style().polish(source_btn)
        
    def _show_tool_menu(self, pos, tool: dict, index: int, btn: QToolButton):
        """显示工具右键菜单"""
        menu = QMenu(self)

        open_location_action = QAction(tr("打开文件位置"), self)
        open_location_action.triggered.connect(lambda: self._open_tool_location(tool))
        menu.addAction(open_location_action)

        open_editor_action = QAction("编辑器打开", self)
        open_editor_action.triggered.connect(lambda _, t=tool: self._openInEditor(t))
        menu.addAction(open_editor_action)

        menu.addSeparator()
        
        edit_action = QAction(tr("编辑"), self)
        edit_action.triggered.connect(lambda: self._edit_tool(index))
        menu.addAction(edit_action)
        
        copy_action = QAction("复制", self)
        copy_action.triggered.connect(lambda: self.copyTool(tool))
        menu.addAction(copy_action)

        move_menu = menu.addMenu("移动到")
        groups = _get_groups(self._tools)
        for g in groups:
            if g == self._current_group:
                continue
            action = QAction(g, self)
            action.triggered.connect(lambda checked, target=g, t=tool, i=index:
                self._move_tool_to_group(t, i, target))
            move_menu.addAction(action)
        
        menu.addSeparator()
        
        delete_action = QAction("删除", self)
        delete_action.triggered.connect(lambda: self._delete_tool(index))
        menu.addAction(delete_action)
        
        menu.exec(btn.mapToGlobal(pos))
    
    def _show_empty_area_menu(self, pos):
        """在空白区域显示右键菜单"""
        menu = QMenu(self)
        
        add_action = QAction("添加启动项", self)
        add_action.triggered.connect(self._add_tool)
        menu.addAction(add_action)
        
        add_preset_action = QAction("添加预设项", self)
        menu.addAction(add_preset_action)
        
        global_pos = self.tools_widget.mapToGlobal(pos)
        action = menu.exec(global_pos)
        
        if action == add_preset_action:
            self._show_preset_items_menu(global_pos)
    
    def _show_preset_items_menu(self, global_pos):
        """显示预设项菜单"""
        menu = QMenu(self)
        
        # 编辑器子菜单
        in_menu = menu.addMenu("功能")
        for name, path, note in [
            ("编辑器", "editor", "打开编辑器窗口"),
            ("插件管理", "plugin_manager", "管理插件启用/禁用"),
            ("重载插件", "reload_plugins", "重新加载插件"),
            ("打开日志", "openLog", "打开日志文件"),
            ("打开配置", "openConfig", "打开配置文件"),
            ("重启程序", "restart_app", "重启本程序"),
            ("退出", "quit_app", "退出本程序"),
        ]:
            action = QAction(name, self)
            action.triggered.connect(lambda checked, n=name, p=path, nt=note: self._add_preset_item(n, p, nt))
            in_menu.addAction(action)

        # 系统子菜单
        sys_menu = menu.addMenu("系统")

        for name, path in SYSTEM_ACT.items():
            action = QAction(name, self)
            action.triggered.connect(lambda checked, n=name, p=path: self._add_preset_item(n, p, ""))
            sys_menu.addAction(action)

        # 获取插件列表
        plugin_items = self._get_plugin_items()
        if plugin_items:
            menu.addSeparator()
            for name, path, note in plugin_items:
                if path.startswith("plugin_menu:"):
                    sub_menu = self._build_plugin_submenu(name)
                    if sub_menu:
                        menu.addMenu(sub_menu)
                        continue
                action = QAction(name, self)
                action.triggered.connect(lambda checked, n=name, p=path, nt=note: self._add_preset_item(n, p, nt))
                menu.addAction(action)
        
        menu.exec(global_pos)
    
    def _get_plugin_items(self):
        """获取可用的插件项"""
        items = []
        try:
            pm = getPluginManager(self)
            for description, menu_item, plugin in pluginActionMenu(pm, self):
                if isinstance(menu_item, QMenu):
                    items.append((description, f"plugin_menu:{description}", description))
                elif isinstance(menu_item, QAction) and menu_item.text():
                    items.append((description, f"plugin_action:{description}", description))
        except Exception:
            logger.exception("获取插件列表失败")

        return items

    def _get_plugin_instance(self, plugin_name: str):
        """按显示名称获取插件实例"""
        try:
            pm = getPluginManager(self)
            pm.setMainWindow(self)
            for _, _, plugin in pluginActionMenu(pm, self):
                if plugin.description == plugin_name or getattr(plugin, 'name', '') == plugin_name:
                    return plugin
        except Exception:
            logger.exception("获取插件实例失败")
        return None

    def _build_plugin_submenu(self, plugin_name: str, source_menu: QMenu = None) -> QMenu:
        """构建插件的子菜单"""
        if source_menu is None:
            inst = self._get_plugin_instance(plugin_name)
            if not inst:
                return None
            try:
                source_menu = inst.getAction()
            except Exception:
                return None
            if not isinstance(source_menu, QMenu):
                return None

        sub_menu = QMenu(plugin_name, self)
        for action in source_menu.actions():
            action_text = action.text()
            if not action_text:
                continue
            sub_action = QAction(action_text, self)
            sub_action.triggered.connect(
                lambda checked, at=action_text, pn=plugin_name:
                self._add_preset_item(at, f"plugin_action:{pn}:{at}", pn)
            )
            sub_menu.addAction(sub_action)
        return sub_menu

    def _add_preset_item(self, name: str, path: str, note: str):
        """添加预设项"""
        tool_data = {
            "name": name,
            "type": "预设",
            "path": path,
            "cwd": "",
            "args": "",
            "note": note,
            "hotkey": "",
            "icon": ""
        }
        tool_data = {k: v for k, v in tool_data.items() if v}
        self._tools.setdefault(self._current_group, []).append(tool_data)
        self._save_tools()
        self.refreshTool()
    
    def _add_tool(self):
        """添加启动项"""
        dialog = EditTool(parent=self)
        dialog.accepted.connect(lambda: self._edit_accepted(dialog))
        dialog.show()
    
    def _edit_tool(self, index: int):
        """编辑启动项"""
        tools = _get_group_tools(self._tools, self._current_group)
        if 0 <= index < len(tools):
            tool = tools[index]
            dialog = EditTool(tool, parent=self)
            dialog.accepted.connect(lambda d=dialog, i=index: self._on_edit_tool_updated(d, i))
            dialog.show()
    
    def _on_edit_tool_updated(self, dialog, index):
        tool_data = dialog.get_data()
        self._tools[self._current_group][index] = tool_data
        self._save_tools()
        self.refreshTool()
        self.registerHotkeys()
        dialog.close()
    
    def copyTool(self, tool: dict):
        """复制启动项"""
        new_tool = copy.deepcopy(tool)
        new_tool["name"] = f"{new_tool['name']}_副本"
        new_tool = {k: v for k, v in new_tool.items() if v}
        self._tools.setdefault(self._current_group, []).append(new_tool)
        self._save_tools()
        self.refreshTool()

    def _move_tool_to_group(self, tool: dict, index: int, target_group: str):
        """将工具移动到目标分组"""
        self._tools[self._current_group].pop(index)
        self._tools.setdefault(target_group, []).append(tool)
        self._save_tools()
        self.refreshTool()
        self.refreshGroup()
    
    def _open_tool_location(self, tool: dict):
        """打开工具文件位置"""
        tool_type = tool.get("type", "文件")
        if tool_type == "网址":
            webbrowser.open(tool.get("url", ""))
            return
        tool_path = tool.get("path", "")
        if not tool_path:
            return
        if self._path_mode == "relative":
            tool_path = convertPath(tool_path, "absolute")
        showFile(tool_path)

    def _openInEditor(self, tool: dict):
        """在编辑器中打开工具文件"""
        tool_path = tool.get("path", "")
        if not tool_path:
            return
        path_mode = self._path_mode
        if path_mode == "relative":
            tool_path = convertPath(tool_path, "absolute")
        self._open_editor(tool_path)
    
    def _delete_tool(self, index: int):
        """删除启动项"""
        tools = _get_group_tools(self._tools, self._current_group)
        if 0 <= index < len(tools):
            tool = tools[index]
            if messageBox(self, "确认删除", f"确定要删除 \"{tool.get('name', '')}\" 吗？"):
                self._tools[self._current_group].pop(index)
                self._save_tools()
                self.refreshTool()
                self.registerHotkeys()
    
    def runItem(self, tool: dict):
        """启动工具"""
        args_raw = tool.get("args", "")

        if "{Select}" in args_raw:
            self.hide()
            QTimer.singleShot(500, lambda t=tool: self._run_with_selection(t))
            return

        tool_type = tool.get("type", "文件")
        path = tool.get("path") or tool.get("url")
        cwd = tool.get("cwd", "")
        args = argsPlaceholder(tool.get("args", ""))

        if not path and tool_type != "预设":
            messageBox(self, "警告", "路径为空", 1)
            return

        path_mode = self._path_mode
        if path_mode == "relative":
            path = convertPath(path, "absolute")
            cwd = convertPath(cwd, "absolute")

        try:
            # 为避免信号阻塞，先隐藏再运行
            if getConfig().get("Launch.run_hide", False):
                self.hide()
            operation = "runas" if tool.get("run_as_admin") and tool_type == "文件" else "open"
            if tool_type == "预设":
                self.runPreset(tool)
            elif tool_type == "文件" and path and Path(path).suffix.lower() == '.exe':
                service_str = tool.get("service", "").strip()
                process_str = tool.get("process", "").strip()
                if service_str or process_str:
                    self._run_with_services(tool, path, cwd, args, service_str, process_str)
                else:
                    action = TYPES.get(tool_type)
                    if action:
                        action(path, cwd, args, operation)

            else:
                action = TYPES.get(tool_type)
                if action:
                    action(path, cwd, args, operation)
            logger.info(f"启动工具: {tool.get('name', '')} ({tool_type})")
            
        except Exception as e:
            messageBox(self, "错误", f"启动失败: {str(e)}", 1)
            logger.error(f"启动工具失败: {e}")

    def runPreset(self, tool: dict):
        """运行预设工具"""
        path = tool.get("path") or ""
        
        if path == "Terminal":
            cwd = tool.get("cwd", "")
            openTerminal(cwd if cwd else os.path.expanduser("~"))
            return
        
        func_act = {
            "editor": lambda: self._open_editor(),
            "restart_app": lambda: restartApplication(self),
            "quit_app": QApplication.quit,
            "plugin_manager": lambda: show_plugin_dialog(self),
            "reload_plugins": lambda: getPluginManager().initConfig(getConfig()),
            "openLog": lambda: self._open_editor(str(log_file)),
            "openConfig": lambda: self._open_editor(str(config_file)),
        }
        action = func_act.get(path)
        if action:
            action()
            return
        
        if path in SYSTEM_ACT.values():
            openFile(path)
            return
        
        if path.startswith("plugin_action:"):
            # 解析插件：plugin_action:插件名 或 plugin_action:插件名:动作文本
            try:
                parts = path.split(":", 2)
                plugin_name = parts[1]
                action_text = parts[2] if len(parts) >= 3 else None
                
                plugin_instance = self._get_plugin_instance(plugin_name)
                if not plugin_instance:
                    messageBox(self, "警告", f"未找到插件: {plugin_name}", 1)
                    return
                
                menu_or_action = plugin_instance.getAction()
                if not menu_or_action:
                    messageBox(self, "警告", f"插件 {plugin_name} 没有菜单或动作", 1)
                    return
                
                # 有指定动作文本，在 QMenu 中查找对应 QAction
                if action_text and isinstance(menu_or_action, QMenu):
                    for action in menu_or_action.actions():
                        if action.text() == action_text:
                            action.trigger()
                            return
                    messageBox(self, "警告", f"未找到插件动作: {action_text}", 1)
                    return
                
                # QAction 直接触发
                if hasattr(menu_or_action, 'trigger'):
                    menu_or_action.trigger()
                    return
                
            except Exception as e:
                messageBox(self, "错误", f"执行插件动作失败: {str(e)}", 1)
                logger.error(f"执行插件动作失败: {e}")
            return
        
        messageBox(self, "警告", f"未知的预设功能: {path}", 1)

    def _run_with_services(self, tool: dict, path: str, cwd: str, args: str, service_str: str, process_str: str):
        """启动带有附属服务/进程管理的 exe 程序"""
        services = [s.strip().strip('"') for s in service_str.split("|") if s.strip()]
        process_names = [p.strip().strip('"') for p in process_str.split("|") if p.strip()]

        services = filterList(services, SERVICE_LIST, "服务")
        process_names = filterList(process_names, PROCESS_LIST, "进程")

        if not isAdmin():
            if messageBox(self, "需要管理员权限", "附属服务/进程管理需要管理员权限，是否重启程序？"):
                runAdmin()
            else:
                openFile(path, cwd, args)
            return

        if services:
            service(services, "disable", 3)
            service(services, "start", 10)

        basename = Path(path).name.lower()
        before_pids = set()
        for p in process_iter(['pid', 'name']):
            try:
                if p.info['name'] and p.info['name'].lower() == basename:
                    before_pids.add(p.info['pid'])
            except (NoSuchProcess, AccessDenied):
                pass

        openFile(path, cwd, args)
        logger.info(f"主程序已启动: {tool.get('name', '')} ({path})")

        QTimer.singleShot(3000, lambda: self._startMonitor(path, before_pids, services, process_names))
        logger.info(f"启动服务管理模式: {tool.get('name', '')}, 服务={services}, 进程={process_names}")

    def _startMonitor(self, path: str, before_pids: set, services: list, process_names: list):
        """3秒后检测新进程并开始监控"""
        basename = Path(path).name.lower()
        after_pids = set()
        for p in process_iter(['pid', 'name']):
            try:
                if p.info['name'] and p.info['name'].lower() == basename:
                    after_pids.add(p.info['pid'])
            except (NoSuchProcess, AccessDenied):
                pass

        new_pids = after_pids - before_pids
        if not new_pids:
            logger.warning(f"未检测到新启动的进程: {basename}")
            return

        logger.info(f"检测到 {len(new_pids)} 个新进程: {new_pids}")
        mgr = ServiceProcess(process_names, services)
        mgr.startMonitor(new_pids)
        self._service_processes.append(mgr)
    
    def _configDialog(self):
        """显示设置对话框"""
        app_config = getConfig()
        dialog = SettingsDialog(app_config, self)
        dialog.settings_changed.connect(self._configAccept)
        dialog.restart_required.connect(lambda: restartApplication(self))
        dialog.exec()
    
    def _configAccept(self):
        env.loadConfig(getConfig())
        env.inject()
        listener = GlobalHotkeyListener()
        self._path_mode = getConfig().get("Launch.path_mode", "absolute")
        self._on_top = getConfig().get("Launch.on_top", False)
        self._tools = getConfig().get("Launch.tools", {})
        hotkey_str = getConfig().get("Launch.hotkey", "Ctrl+L")
        mouse_side_enabled = getConfig().get("Launch.mouse_side", False)
        double_ctrl_enabled = getConfig().get("Launch.double_ctrl", False)
        listener.restart(self, hotkey_str, mouse_side_enabled, double_ctrl_enabled)
        self._register_editor_hotkeys()
        self.system_tray.update_tray()
        
        # 更新窗口置顶状态
        self._update_on_top()
        
        # 始终重建UI以确保事件过滤器正确安装/卸载
        old_widget = self.centralWidget()
        if old_widget:
            old_widget.deleteLater()
        self._setup_ui()
        QTimer.singleShot(0, self.refreshTool)
    
    def _update_on_top(self):
        """更新窗口置顶状态"""
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window
        if self._on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        
        self.setWindowFlags(flags)
        
        # 恢复窗口位置和可见状态
        self.setGeometry(self.geometry())
        if self.isVisible():
            self.show()
    
    def _load_geometry(self):
        """加载窗口位置和大小"""
        width = getConfig().get("Launch.width", 600)
        height = getConfig().get("Launch.height", 400)
        x = getConfig().get("Launch.x")
        y = getConfig().get("Launch.y")
        
        if x and y:
            self.setGeometry(x, y, width, height)
        else:
            self.resize(width, height)
    
    def _save_geometry(self):
        """保存窗口位置和大小"""
        geometry = self.geometry()
        getConfig().set("Launch.width", geometry.width())
        getConfig().set("Launch.height", geometry.height())
        getConfig().set("Launch.x", geometry.x())
        getConfig().set("Launch.y", geometry.y())
    
    def dragEnterEvent(self, event: QDragEnterEvent):
        """拖拽进入事件"""
        try:
            if event.mimeData().hasUrls():
                event.acceptProposedAction()
                return
        except Exception:
            logger.exception("拖入事件处理失败")
        event.ignore()
    
    def dropEvent(self, event: QDropEvent):
        """拖拽放下事件"""
        try:
            if not event.mimeData().hasUrls():
                event.ignore()
                return
            
            paths = []
            for u in event.mimeData().urls():
                try:
                    p = u.toLocalFile()
                except Exception:
                    p = ""
                if p:
                    paths.append(os.path.normpath(p))
            
            if not paths:
                event.ignore()
                return
            
            self.addFromDrop(paths[0])
            event.acceptProposedAction()
        except Exception as e:
            messageBox(self, "拖拽添加", str(e), 1)
            event.ignore()
    
    def addFromDrop(self, path: str):
        """从拖拽路径添加工具"""
        
        path = os.path.normpath(path)
        ext = os.path.splitext(path)[1].lower()
        
        base = os.path.basename(path.rstrip("\\/"))
        name = os.path.splitext(base)[0] if os.path.isfile(path) else base
        
        type_map = {
            ".py": "Python",
            ".jar": "Java",
        }
        tool_type = type_map.get(ext, "文件")
        
        tool_data = {
            "type": tool_type,
            "name": name,
            "path": path,
            "cwd": "",
            "args": "",
            "note": "",
            "hotkey": "",
            "icon": ""
        }
        
        dialog = EditTool(tool_data, self)
        dialog.accepted.connect(lambda: self._edit_accepted(dialog))
        dialog.show()
    
    def _edit_accepted(self, dialog):
        final_data = dialog.get_data()
        self._tools.setdefault(self._current_group, []).append(final_data)
        self._save_tools()
        self.refreshTool()
        self.registerHotkeys()
        dialog.close()
    
    def closeEvent(self, event):
        """关闭事件"""
        self._save_geometry()
        if getConfig().get("tray", False) and self.system_tray.tray_icon:
            event.ignore()
            self.hide()
        else:
            getConfig().save()
            event.accept()
            if self.app:
                self.app.quit()
    
    def resizeEvent(self, event):
        """窗口大小改变事件"""
        super().resizeEvent(event)
        self._resize_timer.stop()
        self._resize_timer.start(200)
    
    @Slot()
    def _toggle_launcher(self):
        """接收全局快捷键（双击 Ctrl、Ctrl+L），显示/隐藏启动器"""
        if self.windowState() & Qt.WindowState.WindowMinimized:
            self.setWindowState(Qt.WindowState.WindowActive)
            self.raise_()
            self.activateWindow()
        elif self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()
            self.activateWindow()
            if sys.platform == "win32":
                windll.user32.SetForegroundWindow(int(self.winId()))

    def keyPressEvent(self, event):
        """键盘事件"""
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(event)
    
    def hideEvent(self, event):
        """窗口隐藏事件（托盘隐藏时暂停资源占用监视）"""
        super().hideEvent(event)
        if hasattr(self, '_usage_monitor'):
            self._usage_timer_was_active = self._usage_monitor.pause()

    def showEvent(self, event):
        """窗口显示事件"""
        super().showEvent(event)
        if hasattr(self, '_usage_monitor'):
            self._usage_monitor.resume(getattr(self, '_usage_timer_was_active', False))
        if not self._tools_initialized:
            self.refreshTool()
            self._tools_initialized = True
    
    def _run_with_selection(self, tool):
        """隐藏后捕获选中文本替换 {Select} 再执行工具"""
        clipboard = QApplication.clipboard()
        grabbed = False

        def grab():
            nonlocal grabbed
            if grabbed:
                return
            grabbed = True
            try:
                clipboard.dataChanged.disconnect(grab)
            except (TypeError, RuntimeError):
                pass
            text = clipboard.text() or ""
            GlobalHotkeyListener._placeholders["Select"] = text
            t = dict(tool)
            t["args"] = tool.get("args", "").replace("{Select}", text)
            self.runItem(t)

        clipboard.dataChanged.connect(grab)
        copy_selection()
        QTimer.singleShot(500, grab)
    
    def registerHotkeys(self):
        """注册所有工具快捷键"""
        listener = GlobalHotkeyListener()
        listener.clear_tool_hotkeys()
        
        all_tools = [t for g in self._tools.values() for t in g]
        for tool in all_tools:
            hotkey = tool.get("hotkey", "")
            if hotkey:
                listener.registerHotkey(hotkey, tool)
