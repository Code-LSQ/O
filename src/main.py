import os
import sys
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

from src.util import logger, theme_dir, icon_dir, logo_ico, logo_png, logo_icn, isAdmin, runAdmin, scale_value, openTerminal, convertPath, getFilePath, Translator, tr, APP_NAME, monitor, restartApplication, showFile, dialogBox, messageBox, service, inputDialog
from src.config import SettingsDialog, getConfig
from src.gui.mouse import WindowMouse
from src.gui.control import WindowControl, PluginControl
from src.core.input import GlobalHotkeyListener, KeyCaptureFilter, copy_selection
from src.core.timer import TimerManager
from src.plugin import getPluginManager, pluginActionMenu
from src.edit import EditorWindow

# 全局快捷键是 hotkey，编辑器快捷键是 shortcut。在程序中只提供一种全局快捷键，即通过启动器的快捷键间接调用，减少复杂性。提供的快捷键页面后续也分成两种。


# 附属服务/进程管理，预期行为
# 针对文件类型的 .exe 程序，若设置附属服务或附属进程，在启动时，先检查附属服务状态，如果不是手动启用，设置其为手动启用，然后开启附属服务，然后设置一个定时器，十秒检查一次主进程状态，在用户结束主进程后，结束其附属进程和关闭附属服务。适用于 百度网盘、WPS的流氓进程，还有 VMware 这种关闭后有好几个服务在运行的。
# 用 | 分隔，有空格要用 "" 包裹，有白名单，免得误操作给系统搞崩溃了。需要管理员权限 
# 服务: VMAuthdService | VMnetDHCP | VMUSBArbService | "VMware NAT Service"
# 进程: YunDetectService.exe

def setApp(app: QApplication):
    config = getConfig()
    app.setFont(QFont(config.get("font_family"), config.get("font_size")))
    if sys.platform == "win32":
        app.setWindowIcon(QIcon(str(logo_ico)))
    elif sys.platform == "linux":
        app.setWindowIcon(QIcon(str(logo_png)))
    elif sys.platform == "darwin":
        app.setWindowIcon(QIcon(str(logo_icn)))

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
        
        self.tray_menu = QMenu()
        self.tray_menu.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.tray_menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.tray_menu.setStyleSheet("""
            QMenu {
                background: #ffffff;
                border: 1px solid #c0c0c0;
            }
            QMenu::item {
                padding: 4px 16px;
                background: transparent;
            }
            QMenu::item:selected {
                background: #e0e0e0;
            }
        """)
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

    def __init__(self, process_names: list, service_names: list):
        self.process_names = process_names
        self.service_names = service_names
        self.monitored_pids: set = set()
        self._timer = None

    def startMonitor(self, pids: set):
        """开始监控指定 PID"""
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

    def _cleanup(self):
        """清理附属进程和服务"""
        if self._timer:
            self._timer.stop()
            self._timer.deleteLater()
            self._timer = None

        safe_names = filterList(self.process_names, PROCESS_LIST, "进程")
        for name in safe_names:
            for p in process_iter(['pid', 'name']):
                try:
                    if p.info['name'] and p.info['name'].lower() == name.lower():
                        Process(p.info['pid']).terminate()
                        logger.info(f"已终止进程: {name} (PID {p.info['pid']})")
                except (NoSuchProcess, AccessDenied):
                    pass

        safe_services = filterList(self.service_names, SERVICE_LIST, "服务")
        service(safe_services, "stop", 10)
        logger.info("附属清理完成")

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

# 打开回收站
def openRecycle():
    if sys.platform == "win32":
        os.startfile("::{645FF040-5081-101B-9F08-00AA002F954E}")
    elif sys.platform == "linux":
        try:
            subprocess.run(["gio", "open", "trash://"], check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            try:
                subprocess.run(["xdg-open", "trash://"], check=True)
            except (subprocess.CalledProcessError, FileNotFoundError):
                raise RuntimeError("无法打开回收站，请确保已安装 gio 或 xdg-utils")
    elif sys.platform == "darwin":
        subprocess.run(["open", "-a", "Finder", "~/.Trash"])

# 打开文件或文件夹，不用检查 if os.path.exists(path): ，runItem 统一检查并抛异常
def openFile(path: str, cwd=None, args=None, operation="open"):
    if sys.platform == "win32":
        # os.startfile(path) 不支持参数，所以使用 windll
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
        openTerminal(cwd, config=getConfig())
    else:
        openTerminal(Path(path).resolve().parent, config=getConfig())

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

def runScript(path: str, cwd, args, operation="open"):
    ext = Path(path).suffix.lower()
    if sys.platform == "win32":
        if ext in ('.bat', '.cmd', '.vbs'):
            windll.shell32.ShellExecuteW(None, operation, path, args or None, cwd or None, 1)
        elif ext == '.ps1':
            cmd = ['powershell.exe', '-ExecutionPolicy', 'Bypass', '-File', path]
            if args:
                cmd.append(args)
            subprocess.Popen(cmd, shell=True, cwd=cwd or None)
        else:
            raise TypeError(f"不支持的脚本类型: {ext}")
    else:
        if ext == '.sh':
            cmd = ['sh', path]
            if args:
                cmd.append(args)
            subprocess.Popen(cmd, cwd=cwd or None)
        else:
            raise TypeError(f"不支持的脚本类型: {ext}")

def open_url(url, *args, **kwargs):
    webbrowser.open(url)

# 类型映射，对于 .py，.jar，.ps1，设置 shell=True 是为了方便在关闭 cmd 窗口时结束脚本或程序，否则不好结束
TYPES = {
    "文件": openFile,
    "命令行": cmdApp,
    "Python": runPython,
    "Java": runJava,
    "脚本": runScript,
    "网址": open_url,
    "预设": None  # 预设类型由 runPreset 方法处理
}


class DraggableToolButton(QToolButton):
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
        self.setMinimumWidth(scale_value(450))
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
        path_layout = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("选择文件或输入路径")
        path_layout.addWidget(self.path_edit)
        
        self.browse_btn = QPushButton("浏览")
        self.browse_btn.clicked.connect(self._browse_path)
        self.browse_btn.setFixedWidth(80)
        path_layout.addWidget(self.browse_btn)
        
        self.path_label = QLabel("路径")
        form_layout.addRow(self.path_label, path_layout)
        
        # 工作目录
        cwd_layout = QHBoxLayout()
        self.cwd_edit = QLineEdit()
        cwd_layout.addWidget(self.cwd_edit)
        
        self.cwd_browse_btn = QPushButton("浏览")
        self.cwd_browse_btn.clicked.connect(lambda: getFilePath(self, self.cwd_edit, "", "", mode="dir"))
        self.cwd_browse_btn.setFixedWidth(80)
        cwd_layout.addWidget(self.cwd_browse_btn)
        form_layout.addRow("工作目录", cwd_layout)
        
        # 参数
        self.args_edit = QLineEdit()
        form_layout.addRow("启动参数", self.args_edit)
        
        # 附属服务（仅文件类型显示）
        self.service_edit = QLineEdit()
        self.service_edit.setPlaceholderText('VMAuthdService | VMnetDHCP')
        self._service_label = QLabel("服务")
        form_layout.addRow(self._service_label, self.service_edit)
        
        # 附属进程（仅文件类型显示）
        self.process_edit = QLineEdit()
        self.process_edit.setPlaceholderText('YunDetectService.exe')
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
        icon_layout = QHBoxLayout()
        self.icon_edit = QLineEdit()
        icon_layout.addWidget(self.icon_edit)
        
        self.icon_browse_btn = QPushButton("浏览")
        self.icon_browse_btn.clicked.connect(lambda: getFilePath(self, self.icon_edit, "选择图标", "图片文件 (*.png *.jpg *.jpeg *.bmp *.ico *.gif);;所有文件 (*)"))
        self.icon_browse_btn.setFixedWidth(80)
        icon_layout.addWidget(self.icon_browse_btn)
        form_layout.addRow("图标", icon_layout)
        
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
            self.path_label.setText("URL")
            self.path_edit.setPlaceholderText("输入网址")
            self.browse_btn.setVisible(False)
            self.cwd_edit.setEnabled(False)
            self.cwd_browse_btn.setEnabled(False)
        elif tool_type == "预设":
            self.path_label.setText("路径")
            self.browse_btn.setVisible(False)
            self.cwd_edit.setEnabled(False)
            self.cwd_browse_btn.setEnabled(False)
        else:
            self.path_label.setText("路径")
            self.browse_btn.setVisible(True)
            self.cwd_edit.setEnabled(True)
            self.cwd_browse_btn.setEnabled(True)
    
    def _browse_path(self):
        tool_type = self.type_combo.currentText()
        if tool_type == "预设":
            return

        choices = {
            "文件": ("选择文件", ""),
            "命令行": ("选择可执行文件", "可执行文件 (*.exe);;所有文件 (*)"),
            "Python": ("Python 脚本", "Python 文件 (*.py);;所有文件 (*)"),
            "Java": ("JAR 文件", "JAR 文件 (*.jar);;所有文件 (*)"),
            "脚本": ("选择脚本文件", "脚本文件 (*.bat *.cmd *.vbs *.ps1);;所有文件 (*)"),
        }
        title, filter = choices.get(tool_type, ("选择文件", ""))
        path = getFilePath(self, self.path_edit, title, filter)
        if path and not self.name_edit.text():
            self.name_edit.setText(Path(path).stem)
    
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

def _get_groups(config) -> list:
    tools = config.get("Launch.tools", {})
    return list(tools.keys()) if tools else ["默认"]


def _get_group_tools(config, group: str) -> list:
    return config.get(f"Launch.tools.{group}", [])

class MainWindow(WindowMouse, QMainWindow):
    """启动器主窗口"""
    
    def __init__(self, app: QApplication=None, file_path=None):
        super().__init__()

        self.setStyleSheet("""
            QToolTip {
                background-color: #f0f0f0;
                color: #333333;
                border: none;
                padding: 2px 4px;
                font-size: 14px;
            }
            QMenu {
                font-size: 16px;
                padding: 2px;
            }
            QMenu::separator {
                height: 1px;
                background: #cccccc;
                margin: 2px 4px;
            }
        """)

        self.app = app
        self._editor_window = None
        self.config = getConfig()
        self.system_tray = SystemTray(self, self.app)
        Translator().setLanguage(self.config.get("language", "简体中文"))
        self._current_group = getConfig().get("Launch.active_group", "默认")
        self._fallback_size = (600, 400)
        self._first_show = True
        self._ai_capturing = False
        self.window_control = WindowControl(self)
        self._plugin_shortcuts = []
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

        QTimer.singleShot(0, self._lazy_init)

    def _open_editor(self, file_path=None) -> EditorWindow:
        """打开/激活编辑器窗口"""
        if self._editor_window is None:
            self._editor_window = EditorWindow(self.app, file_path, main_window=self)
            self.applyTheme(self._editor_window)
            self._editor_window._init_plugins()
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
            if editor and hasattr(editor, '_plugin_controller'):
                plugin = editor._plugin_controller.plugin_manager.plugins.get(name)
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
        if getConfig().get("Launch.on_top", False):
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
        self.cpu_label.setVisible(self.config.get("usage", True))
        title_layout.addWidget(self.cpu_label)

        self.settings_btn = QPushButton("设置")
        self.settings_btn.setObjectName("settings_btn")
        self.settings_btn.setFixedSize(70, 32)
        self.settings_btn.clicked.connect(self._show_settings_dialog)
        title_layout.addWidget(self.settings_btn)
        
        self.window_control.createWindowButton(title_layout)
        
        parent_layout.addWidget(title_bar)

        self.separate = QFrame()
        self.separate.setObjectName("sep_h")
        self.separate.setFrameShape(QFrame.Shape.HLine)
        parent_layout.addWidget(self.separate)
        
        # 标题栏双击最大化
        title_bar.mouseDoubleClickEvent = self._title_double_click

        self._update_timer = QTimer(title_bar)
        self._update_timer.timeout.connect(self._update_usage)
        self._update_timer.start(2000)
        self._update_usage()

    def _update_usage(self):
        """更新CPU和内存显示"""
        usage = monitor()
        if usage:
            self.cpu_label.setText(usage)

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
        
        groups = _get_groups(getConfig())
        
        self._hover_timers = {}
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
        
        self.group_layout.addStretch()
    
    def eventFilter(self, obj, event):
        """事件过滤器 - 处理分组按钮悬停"""
        if isinstance(obj, QPushButton):
            if event.type() == QEvent.Type.HoverEnter:
                group = obj.text()
                if group != self._current_group:
                    timer = QTimer(self)
                    timer.setSingleShot(True)
                    timer.timeout.connect(lambda g=group: self.switchGroup(g))
                    timer.start(500)
                    self._hover_timers[obj] = timer
            elif event.type() == QEvent.Type.HoverLeave:
                if obj in self._hover_timers:
                    self._hover_timers[obj].stop()
                    self._hover_timers[obj].deleteLater()
                    del self._hover_timers[obj]
        return super().eventFilter(obj, event)
    
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
            groups = _get_groups(getConfig())
            if name.strip() in groups:
                messageBox(self, "警告", "分组名称已存在", 1)
                return
            tools = getConfig().get("Launch.tools", {})
            if not tools:
                tools["默认"] = []
            tools[name.strip()] = []
            getConfig().set("Launch.tools", tools)
            getConfig().save()
            self.refreshGroup()
    
    def _rename_group(self, group: str):
        """重命名分组"""
        name = inputDialog(self, "重命名分组", "新名称", default=group)
        if name and name != group:
            groups = _get_groups(getConfig())
            if name.strip() in groups:
                messageBox(self, "警告", "分组名称已存在", 1)
                return
            
            tools = getConfig().get("Launch.tools", {})
            tools[name.strip()] = tools.pop(group)
            getConfig().set("Launch.tools", tools)
            
            if self._current_group == group:
                self._current_group = name.strip()
                getConfig().set("Launch.active_group", name.strip())
            
            getConfig().save()
            self.refreshGroup()
            self.refreshTool()
    
    def _delete_group(self, group: str):
        """删除分组"""
        groups = _get_groups(getConfig())
        if len(groups) <= 1:
            messageBox(self, "警告", "至少保留一个分组", 1)
            return
        
        if messageBox(self, "确认删除", f"确定要删除分组 \"{group}\" 吗？\n该分组下的启动项将被删除。"):
            tools = getConfig().get("Launch.tools", {})
            del tools[group]
            getConfig().set("Launch.tools", tools)
            
            if self._current_group == group:
                groups = _get_groups(getConfig())
                self._current_group = groups[0] if groups else "默认"
                getConfig().set("Launch.active_group", self._current_group)
            
            getConfig().save()
            self.refreshGroup()
            self.refreshTool()
    
    def _move_group(self, group: str, direction: int):
        """移动分组"""
        groups = _get_groups(getConfig())
        idx = groups.index(group)
        new_idx = idx + direction
        if 0 <= new_idx < len(groups):
            groups[idx], groups[new_idx] = groups[new_idx], groups[idx]
            getConfig().set("Launch.tools", {g: getConfig().get("Launch.tools", {}).get(g, []) for g in groups})
            getConfig().save()
            self.refreshGroup()
    
    def switchGroup(self, group: str):
        """切换分组"""
        self._current_group = group
        getConfig().set("Launch.active_group", group)
        getConfig().save()
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
                    break
        
        if target_index >= 0 and target_index != source_index:
            # 交换工具位置
            tools = _get_group_tools(getConfig(), self._current_group)
            if 0 <= source_index < len(tools) and 0 <= target_index < len(tools):
                tools[source_index], tools[target_index] = tools[target_index], tools[source_index]
                tools_dict = getConfig().get("Launch.tools", {})
                tools_dict[self._current_group] = tools
                getConfig().set("Launch.tools", tools_dict)
                getConfig().save()
                self.refreshTool()
        
        event.acceptProposedAction()
    
    def refreshTool(self):
        """刷新启动项"""
        while self.tools_layout.count():
            item = self.tools_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        tools = _get_group_tools(getConfig(), self._current_group) or []
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

    def _create_tool_button(self, tool: dict, index: int) -> DraggableToolButton:
        """创建工具按钮"""
        name = tool.get("name", "未命名")
        tool_type = tool.get("type", "文件")
        icon_path = tool.get("icon", "")
        path = tool.get("path", "") or tool.get("url", "")
        note = tool.get("note", "")
        
        btn = DraggableToolButton()
        btn.setObjectName("tool_btn")
        
        # 设置提示
        tip_parts = [name]
        if path:
            tip_parts.append(path)
        if note:
            tip_parts.append(note)
        btn.setToolTip("\n".join(tip_parts))
        
        # 获取绝对路径用于图标
        display_path = path
        path_mode = getConfig().get("Launch.path_mode", "absolute")
        if path_mode == "relative":
            display_path = convertPath(path, "absolute")

        # 设置图标
        if icon_path:
            icon_check_path = convertPath(icon_path, "absolute") if path_mode == "relative" else icon_path
            if os.path.isfile(icon_check_path):
                if Path(icon_check_path).suffix.lower() == ".exe":
                    provider = QFileIconProvider()
                    icon = provider.icon(QFileInfo(icon_check_path))
                else:
                    icon = QIcon(icon_check_path)
            else:
                icon = QIcon()
        elif tool_type == "预设":
            if name == "回收站":
                local_icon_path = icon_dir / "Recycle.png"
                if local_icon_path.exists():
                    icon = QIcon(str(local_icon_path))
                else:
                    provider = QFileIconProvider()
                    icon = provider.icon(QFileIconProvider.IconType.Trashcan)
            elif name == "命令提示符":
                if sys.platform == "win32":
                    exe_path = "C:\\Windows\\System32\\cmd.exe"
                elif sys.platform == "linux":
                    exe_path = "/usr/bin/gnome-terminal"
                elif sys.platform == "darwin":
                    exe_path = "/System/Applications/Utilities/Terminal.app"
                provider = QFileIconProvider()
                icon = provider.icon(QFileInfo(exe_path))
            else:
                icon = QIcon()
        elif display_path and os.path.exists(display_path):
            abs_path = os.path.normpath(os.path.abspath(display_path))
            provider = QFileIconProvider()
            icon = provider.icon(QFileInfo(abs_path))
        else:
            icon = QIcon()
        btn.setIcon(icon)
        icon_size = getConfig().get("Launch.icon", 32)
        btn.setIconSize(QSize(icon_size, icon_size))
        
        # 设置文字在图标下方
        btn.setText(name)
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        
        # 点击启动
        btn.clicked.connect(lambda checked=False, t=tool: self.runItem(t))
        
        # 拖拽排序
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

        launch_action = QAction("启动", self)
        launch_action.triggered.connect(lambda: self.runItem(tool))
        menu.addAction(launch_action)
        
        open_location_action = QAction(tr("打开文件位置"), self)
        open_location_action.triggered.connect(lambda: self._open_tool_location(tool))
        menu.addAction(open_location_action)
        
        menu.addSeparator()
        
        edit_action = QAction(tr("编辑"), self)
        edit_action.triggered.connect(lambda: self._edit_tool(index))
        menu.addAction(edit_action)
        
        copy_action = QAction("复制", self)
        copy_action.triggered.connect(lambda: self.copyTool(tool))
        menu.addAction(copy_action)
        
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
        in_menu = menu.addMenu("内部")
        for name, path, note in [
            ("编辑器", "editor", "打开编辑器窗口"),
            ("插件管理", "plugin_manager", "管理插件启用/禁用"),
            ("重启", "restart_app", "重启本程序"),
            ("退出", "quit_app", "退出本程序"),
        ]:
            action = QAction(name, self)
            action.triggered.connect(lambda checked, n=name, p=path, nt=note: self._add_preset_item(n, p, nt))
            in_menu.addAction(action)

        preset_items = [
            ("回收站", "Recycle", "打开系统回收站"),
            ("命令提示符", "Terminal", ""),
        ]
        
        for name, path, note in preset_items:
            action = QAction(name, self)
            action.triggered.connect(lambda checked, n=name, p=path, nt=note: self._add_preset_item(n, p, nt))
            menu.addAction(action)

        # AI 提示词预设
        prompts = getConfig().get("AI.prompts", {})
        ai_names = [n for n in prompts if n not in ("系统提示词", "自动补全")]
        if ai_names:
            menu.addSeparator()
            ai_menu = QMenu("AI", self)
            for n in ai_names:
                action = QAction(n, self)
                action.triggered.connect(lambda checked, n=n: self._add_ai_preset_item(n))
                ai_menu.addAction(action)
            menu.addMenu(ai_menu)
        
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

    def _show_plugin_manager(self):
        """显示插件管理对话框"""
        pc = PluginControl(self)
        pc.init_plugins()
        pc.show_plugin_manager()

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

    def _build_plugin_submenu(self, plugin_name: str, source_menu: QMenu = None) -> QMenu | None:
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
        tools = getConfig().get("Launch.tools", {})
        tools.setdefault(self._current_group, []).append(tool_data)
        getConfig().set("Launch.tools", tools)
        getConfig().save()
        self.refreshTool()
    
    def _add_ai_preset_item(self, name: str):
        tool_data = {
            "name": name, "type": "AI", "path": name,
            "cwd": "", "args": "", "note": "",
            "hotkey": "", "icon": ""
        }
        tools = getConfig().get("Launch.tools", {})
        tools.setdefault(self._current_group, []).append(tool_data)
        getConfig().set("Launch.tools", tools)
        getConfig().save()
        self.refreshTool()
    
    def _add_tool(self):
        """添加启动项"""
        dialog = EditTool(parent=self)
        dialog.accepted.connect(lambda: self._edit_accepted(dialog))
        dialog.show()
    
    def _edit_tool(self, index: int):
        """编辑启动项"""
        tools = _get_group_tools(getConfig(), self._current_group)
        if 0 <= index < len(tools):
            tool = tools[index]
            dialog = EditTool(tool, parent=self)
            dialog.accepted.connect(lambda d=dialog, i=index: self._on_edit_tool_updated(d, i))
            dialog.show()
    
    def _on_edit_tool_updated(self, dialog, index):
        tool_data = dialog.get_data()
        tools = getConfig().get("Launch.tools", {})
        tools[self._current_group][index] = tool_data
        getConfig().set("Launch.tools", tools)
        getConfig().save()
        self.refreshTool()
        self.registerHotkeys()
        dialog.close()
    
    def copyTool(self, tool: dict):
        """复制启动项"""
        new_tool = copy.deepcopy(tool)
        new_tool["name"] = f"{new_tool['name']}_副本"
        new_tool = {k: v for k, v in new_tool.items() if v}
        tools = getConfig().get("Launch.tools", {})
        tools.setdefault(self._current_group, []).append(new_tool)
        getConfig().set("Launch.tools", tools)
        getConfig().save()
        self.refreshTool()
    
    def _open_tool_location(self, tool: dict):
        """打开工具文件位置"""
        tool_type = tool.get("type", "文件")
        if tool_type == "网址":
            webbrowser.open(tool.get("url", ""))
            return
        tool_path = tool.get("path", "")
        if not tool_path:
            return
        if getConfig().get("Launch.path_mode", "absolute") == "relative":
            tool_path = convertPath(tool_path, "absolute")
        showFile(tool_path)

    def _delete_tool(self, index: int):
        """删除启动项"""
        tools = _get_group_tools(getConfig(), self._current_group)
        if 0 <= index < len(tools):
            tool = tools[index]
            if messageBox(self, "确认删除", f"确定要删除 \"{tool.get('name', '')}\" 吗？"):
                tools = getConfig().get("Launch.tools", {})
                tools[self._current_group].pop(index)
                getConfig().set("Launch.tools", tools)
                getConfig().save()
                self.refreshTool()
                self.registerHotkeys()
    
    def runItem(self, tool: dict):
        """启动工具"""
        tool_type = tool.get("type", "文件")
        path = tool.get("path") or tool.get("url")
        cwd = tool.get("cwd", "")
        args = argsPlaceholder(tool.get("args", ""))

        if not path and tool_type != "预设":
            messageBox(self, "警告", "路径为空", 1)
            return

        path_mode = getConfig().get("Launch.path_mode", "absolute")
        if path_mode == "relative":
            path = convertPath(path, "absolute")
            cwd = convertPath(cwd, "absolute")

        if tool_type == "AI":
            self._run_ai_prompt(tool.get("name", ""))
            return

        try:
            # 为避免信号阻塞，先隐藏再运行
            if getConfig().get("Launch.run_hide", False):
                self.hide()
            operation = "runas" if tool.get("run_as_admin") and tool_type in ("文件", "脚本") else "open"
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

    def _run_ai_prompt(self, name: str):
        """运行 AI 提示词"""
        if not name or getattr(self, '_ai_capturing', False):
            return
        self._ai_capturing = True
        GlobalHotkeyListener()._is_pasting = True
        self._pending_ai_prompt = name
        QTimer.singleShot(300, self._do_ai_capture)

    def _do_ai_capture(self):
        copy_selection()
        QTimer.singleShot(100, self._finish_ai_capture)

    def _finish_ai_capture(self):
        self._ai_capturing = False
        GlobalHotkeyListener()._is_pasting = False
        name = getattr(self, '_pending_ai_prompt', None)

        mime = QApplication.clipboard().mimeData()
        if mime.hasUrls():
            for url in mime.urls():
                path = url.toLocalFile()
                if not path:
                    continue
                if os.path.isfile(path):
                    from src.core.AI import getAIClient
                    messages = getAIClient().build_file_message(path)
                    if messages:
                        self._open_ai_dialog(messages, name)
                    return
                if os.path.isdir(path):
                    from src.core.AI import getAIClient
                    messages = getAIClient().build_folder_message(path)
                    if messages:
                        self._open_ai_dialog(messages, name)
                    return

        text = QApplication.clipboard().text().strip()
        if not text:
            return
        self._open_ai_dialog([{"role": "user", "content": text}], name)

    def _open_ai_dialog(self, messages, prompt_name):
        from src.gui.widget import AIDialog
        dialog = AIDialog(messages, prompt_name, main_window=self)
        dialog.setStyleSheet(self.styleSheet())
        dialog.show()

    def runPreset(self, tool: dict):
        """运行预设工具"""
        path = tool.get("path") or ""
        
        if path == "Recycle":
            openRecycle()
            return
        
        if path == "Terminal":
            cwd = tool.get("cwd", "")
            openTerminal(cwd if cwd else os.path.expanduser("~"), config=getConfig())
            return
        
        if path == "editor":
            self._open_editor()
            return

        if path == "restart_app":
            restartApplication(self)
            return

        if path == "quit_app":
            QApplication.quit()
            return
        
        if path == "plugin_manager":
            self._show_plugin_manager()
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

        mgr = ServiceProcess(process_names, services)
        mgr.startMonitor(new_pids)
    
    def _show_settings_dialog(self):
        """显示设置对话框"""
        app_config = getConfig()
        dialog = SettingsDialog(app_config, self)
        dialog.settings_changed.connect(self._on_settings_dialog_accepted)
        dialog.restart_required.connect(lambda: restartApplication(self))
        dialog.exec()
    
    def _on_settings_dialog_accepted(self):
        listener = GlobalHotkeyListener()
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
        if getConfig().get("Launch.on_top", False):
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
        getConfig().save()
    
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
            ".bat": "脚本",
            ".cmd": "脚本",
            ".vbs": "脚本",
            ".ps1": "脚本",
            ".sh": "脚本",
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
        tools = getConfig().get("Launch.tools", {})
        tools.setdefault(self._current_group, []).append(final_data)
        getConfig().set("Launch.tools", tools)
        getConfig().save()
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
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()
            self.activateWindow()

    def keyPressEvent(self, event):
        """键盘事件"""
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(event)
    
    def showEvent(self, event):
        """窗口显示事件"""
        super().showEvent(event)
        self.refreshTool()
    
    def show(self):
        if self._first_show:
            self._first_show = False
        else:
            if getConfig().get("Launch.capture", True):
                self._capture_placeholders()
        super().show()
    
    def _capture_placeholders(self):
        """捕获当前剪贴板和选中文本到 GlobalHotkeyListener._placeholders"""
        clip = QApplication.clipboard()
        GlobalHotkeyListener._placeholders["Clipboard"] = clip.text() or ""
        self._pending_clipboard = GlobalHotkeyListener._placeholders["Clipboard"]
        copy_selection()
        QTimer.singleShot(50, self._finish_capture_placeholders)
    
    def _finish_capture_placeholders(self):
        clip = QApplication.clipboard()
        current = clip.text() or ""
        old = self._pending_clipboard
        GlobalHotkeyListener._placeholders["Select"] = current
        if old:
            clip.setText(old)
        if hasattr(self, '_delayed_tool') and self._delayed_tool:
            self.runItem(self._delayed_tool)
            self._delayed_tool = None
    
    def registerHotkeys(self):
        """注册所有工具快捷键"""
        listener = GlobalHotkeyListener()
        listener.clear_tool_hotkeys()
        
        all_tools = [t for g in getConfig().get("Launch.tools", {}).values() for t in g]
        for tool in all_tools:
            hotkey = tool.get("hotkey", "")
            if hotkey:
                listener.registerHotkey(hotkey, tool)
