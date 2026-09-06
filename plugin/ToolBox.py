import os
import re
import json
import sys
import threading
from pathlib import Path
from typing import Dict, List, Optional

if sys.platform == "win32":
    import winreg

from pynput import keyboard, mouse
from PySide6.QtWidgets import QApplication, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QComboBox, QCheckBox, QWidget, QStackedWidget, QScrollArea, QSpinBox, QListWidget, QListWidgetItem, QTreeWidget, QTreeWidgetItem, QMenu, QFormLayout, QFrame, QStyle, QAbstractSpinBox, QStyledItemDelegate
from PySide6.QtCore import Qt, QSize, QTimer, QThread, Signal, QEvent
from PySide6.QtGui import QAction, QTextCursor, QColor

from src.plugin import PluginBase
from src.main import getIcon
from src.file import FileSelect, collectFiles, EXCLUDE_PATTERNS
from src.config import getConfig
from src.api import logger, root, data_dir, tr, messageBox, getFilePath, FileDrop, fileHash, showFile, ClipboardMonitor, formatFileSize, activateWidget, searchFiles, filePathWidget
from src.system import activateWindow
from src.core.timer import TimerManager


cache_file = data_dir / "MD5.json"
copy_file = data_dir / "copy.txt"

# URL 协议方案名正则，仅字母开头，可含字母数字 + - .
URL_SCHEME_RE = r"^[a-z][a-z0-9+.\-]*$"
# 常用协议，覆盖会劫持浏览器等系统默认处理程序，注册前需警告
URL_RESERVED = {"http", "https", "ftp", "ftps", "file", "data", "about", "res",
                "mailto", "tel", "sms", "javascript", "ws", "wss", "chrome", "edge"}


class _UrlProtocol:
    """URL 协议注册表管理，仅在 Windows 下生效，其余平台函数直接返回"""

    _BASE_KEY = r"Software\Classes"

    def __init__(self):
        self._win32 = sys.platform == "win32"

    def register(self, scheme: str, path: str, args: str = "") -> bool:
        """注册 URL 协议到当前用户，命令形如 "{path}" {args} "%1" """
        if not self._win32 or not scheme or not path:
            return False
        scheme = scheme.lower()
        try:
            root = winreg.CreateKeyEx(
                winreg.HKEY_CURRENT_USER, rf"{self._BASE_KEY}\{scheme}", 0,
                winreg.KEY_SET_VALUE
            )
            try:
                winreg.SetValueEx(root, "", 0, winreg.REG_SZ, f"{scheme} URL")
                winreg.SetValueEx(root, "URL Protocol", 0, winreg.REG_SZ, "")
            finally:
                winreg.CloseKey(root)

            icon_key = winreg.CreateKeyEx(
                winreg.HKEY_CURRENT_USER, rf"{self._BASE_KEY}\{scheme}\DefaultIcon", 0,
                winreg.KEY_SET_VALUE
            )
            try:
                winreg.SetValueEx(icon_key, "", 0, winreg.REG_SZ, f'"{path}",0')
            finally:
                winreg.CloseKey(icon_key)

            cmd = f'"{path}"'
            if args:
                cmd += f" {args}"
            cmd += ' "%1"'
            cmd_key = winreg.CreateKeyEx(
                winreg.HKEY_CURRENT_USER,
                rf"{self._BASE_KEY}\{scheme}\shell\open\command", 0,
                winreg.KEY_SET_VALUE
            )
            try:
                winreg.SetValueEx(cmd_key, "", 0, winreg.REG_SZ, cmd)
            finally:
                winreg.CloseKey(cmd_key)
            logger.info(f"URL 协议已注册: {scheme}:// -> {path}")
            return True
        except Exception:
            logger.exception(f"注册 URL 协议失败: {scheme}")
            return False

    def unregister(self, scheme: str) -> bool:
        """取消注册 URL 协议，键不存在时视为成功"""
        if not self._win32 or not scheme:
            return False
        scheme = scheme.lower()
        try:
            self._deleteKey(winreg.HKEY_CURRENT_USER, rf"{self._BASE_KEY}\{scheme}")
            logger.info(f"URL 协议已取消注册: {scheme}://")
            return True
        except Exception:
            logger.exception(f"取消注册 URL 协议失败: {scheme}")
            return False

    def isRegistered(self, scheme: str) -> bool:
        """检查 URL 协议是否已注册"""
        if not self._win32 or not scheme:
            return False
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, rf"{self._BASE_KEY}\{scheme}", 0,
                winreg.KEY_READ
            )
            winreg.CloseKey(key)
            return True
        except FileNotFoundError:
            return False
        except Exception:
            logger.exception(f"检查 URL 协议注册状态失败: {scheme}")
            return False

    def _deleteKey(self, root, key_path: str):
        """递归删除注册表键及其子键，键不存在时静默返回"""
        try:
            sub = winreg.OpenKey(root, key_path, 0, winreg.KEY_ALL_ACCESS)
        except FileNotFoundError:
            return
        try:
            while True:
                try:
                    child = winreg.EnumKey(sub, 0)
                    self._deleteKey(sub, child)
                except OSError:
                    break
        finally:
            winreg.CloseKey(sub)
        try:
            winreg.DeleteKey(root, key_path)
        except OSError:
            logger.exception(f"删除注册表键失败: {key_path}")


_url_protocol = _UrlProtocol()


class ToolBox(PluginBase):

    version = "1.0.0"
    description = "工具箱"
    file = [cache_file, copy_file]

    def __init__(self, main=None):
        super().__init__(main=main)
        self.settings = {
            "copy_path": "data/copy.txt",
            "search": {"paths": [], "case_sensitive": False, "regex": False, "close_delay": 3},
            "quick_text": [],
            "duplicate_paths": [],
            "duplicate_exclude": list(EXCLUDE_PATTERNS),
            "click": 3,
            "scroll": 50,
            "paste_regex": r"^[ \t]*\n",
            "url_protocol": []
        }
        self._scroll_timer = None
        self._copy_mgr = None
        self._search_mgr = None
        self._duplicate_mgr = None
        self._click_mgr = None
        self._tb_widget = None

    def initialize(self):
        if not super().initialize():
            return
        self._scroll_timer = _AutoScrollTimer(self.main)
        self._copy_mgr = _AutoCopyManager()
        self._copy_mgr.initMonitor(self.main)
        self._search_mgr = _AutoSearchManager(self.main)
        self._search_mgr.initMonitor(self.main)
        self._click_mgr = _AutoClickManager(self.main)

    def cleanup(self):
        if self._scroll_timer:
            self._scroll_timer.stop()
        if self._copy_mgr:
            self._copy_mgr.setEnabled(False)
        if self._search_mgr:
            self._search_mgr.setEnabled(False)
        if self._click_mgr:
            self._click_mgr.setEnabled(False)

    def getAction(self):
        menu = QMenu(self.description, self.main)

        menu.addAction(tr("搜索"), self._openSearch)
        menu.addAction("快速文本", self._quickText)
        menu.addAction("批量重命名", self._batchRename)
        menu.addAction("查找重复文件", self._findDuplicates)
        menu.addAction("快速粘贴", lambda: self.getSelect(self._quickPaste))

        menu.addAction("自动滑动", self._toggleScroll)
        menu.addAction("自动复制", self._toggleCopy)
        menu.addAction("自动搜索", self._toggleSearch)
        menu.addAction("自动点击", self._toggleClick)

        return menu

    def _toggleScroll(self):
        self.initialize()
        if self._scroll_timer.enabled:
            self._scroll_timer.stop()
            logger.info("自动滑动已停止")
        else:
            speed = self.settings.get("scroll", 50)
            self._scroll_timer.start(speed)
            logger.info(f"自动滑动已启动 (速度: {speed})")

    def _toggleCopy(self):
        self.initialize()
        if self._copy_mgr.enabled:
            self._copy_mgr.setEnabled(False)
            logger.info("自动复制已停止")
        else:
            self._copy_mgr.target_file = self.settings.get("copy_path", "data/copy.txt")
            self._copy_mgr.setEnabled(True)
            logger.info("自动复制已启动")

    def _toggleSearch(self):
        self.initialize()
        if self._search_mgr.enabled:
            self._search_mgr.setEnabled(False)
            logger.info("自动搜索已停止")
        else:
            search = self.settings.get("search", {})
            self._search_mgr.search_paths = search.get("paths", [])
            self._search_mgr.case_sensitive = search.get("case_sensitive", False)
            self._search_mgr.regex = search.get("regex", False)
            self._search_mgr.close_delay = search.get("close_delay", 3)
            self._search_mgr.setEnabled(True)
            if not self._search_mgr.search_paths:
                logger.info("自动搜索已启动（未设置搜索路径）")
            else:
                logger.info("自动搜索已启动")

    def _toggleClick(self):
        self.initialize()
        if self._click_mgr.enabled:
            self._click_mgr.setEnabled(False)
            logger.info("自动点击已停止")
        else:
            self._click_mgr.interval = self.settings.get("click", 3)
            self._click_mgr.setEnabled(True)
            logger.info(f"自动点击已启动（间隔: {self.settings.get('click', 3)}秒）")

    def _batchRename(self):
        dialog = BatchRenameDialog(self.main)
        dialog.exec()

    def _findDuplicates(self):
        editor = self._ensureEditor()
        if not editor:
            return

        default_paths = self.settings.get("duplicate_paths", [])
        default_exclude = self.settings.get("duplicate_exclude", list(EXCLUDE_PATTERNS))

        def scan(paths, rules, on_progress, on_done, on_error):
            finder = DuplicateFinder(folder_path=paths[0] if paths else None, paths=paths, rules=rules)
            finder.progress.connect(on_progress)
            finder.finished.connect(on_done)
            finder.error.connect(on_error)
            return finder

        result = FileSelect.select(editor, default_paths, default_exclude, scanner=scan)
        if not result:
            return
        duplicates, paths, rules = result
        self.settings["duplicate_paths"] = paths
        self.settings["duplicate_exclude"] = rules
        self.saveConfig()
        self._showDupResults(editor, duplicates)

    def _showDupResults(self, editor, duplicates: dict):
        logger.info("扫描完成")
        if not duplicates:
            messageBox(editor, "查找结果", "未找到重复文件", 1)
            return
        folder_mgr = getattr(editor, '_folder_panel_manager', None)
        mgr = DuplicatePanelManager(editor, editor.splitter, None, editor, folder_mgr)
        mgr.showDuplicates(duplicates)
        fw = folder_mgr._folder_panel_width if folder_mgr and folder_mgr.isVisible() else 0
        mgr.setSizes(editor.width(), fw)
        self._duplicate_mgr = mgr

    def _quickPaste(self, text):
        if not text:
            return
        text = re.sub(r'[\u2028\u2029\u0085\f\v]', '\n', text)
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        # 统一换行符为 \n ，否则 ^\n 类正则无法处理 \r\n 或 U+2029 等变体
        regex_text = self.settings.get("paste_regex", "")
        if regex_text:
            try:
                text = re.sub(regex_text, '', text, flags=re.MULTILINE)
            except re.error:
                logger.warning(f"快速粘贴正则无效: {regex_text}")
        QApplication.clipboard().setText(text)

        editor = self._ensureEditor()
        if not editor:
            logger.error("快速粘贴: 无法获取编辑器窗口")
            return

        editor.activateWindow()
        editor.raise_()
        editor_widget = editor.getEditor()
        if not editor_widget:
            editor_widget = editor.addTab()
        editor_widget.text_edit.setFocus()
        editor_widget.text_edit.paste()

    def _quickText(self):
        widget = activateWidget(QuickTextDialog)
        if widget:
            widget._focusSearch()
            return
        self.initialize()
        items = self.settings.get("quick_text", [])
        dialog = QuickTextDialog(items, self.main)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            text = dialog.selected_text
            if text:
                QApplication.clipboard().setText(text)
                QTimer.singleShot(50, self._globalPaste)

    def _globalPaste(self):
        try:
            kb = keyboard.Controller()
            kb.press(keyboard.Key.ctrl)
            kb.press("v")
            kb.release("v")
            kb.release(keyboard.Key.ctrl)
        except Exception:
            logger.exception("全局粘贴失败")

    def _openSearch(self):
        widget = activateWidget(SearchDialog)
        if widget:
            widget._focusSearch()
            return
        config = getConfig()
        dialog = SearchDialog(config.get("Launch.tools", {}), self.main)
        dialog.exec()

    def configWidget(self, parent=None):
        self._tb_widget = ToolBoxSettings(self.settings, parent)
        self._tb_widget.destroyed.connect(lambda: setattr(self, '_tb_widget', None))
        return self._tb_widget

    def saveConfig(self, save=True):
        if self._tb_widget is not None:
            old_speed = self.settings.get("scroll", 50)
            self.settings.update(self._tb_widget.getSetting())
            new_speed = self.settings.get("scroll", 50)
            if self._scroll_timer and self._scroll_timer.enabled and old_speed != new_speed:
                self._scroll_timer.stop()
                self._scroll_timer.start(new_speed)
        super().saveConfig(save=save)

    def _ensureEditor(self):
        if not self.main:
            return None
        if self.main.editor:
            return self.main.editor
        return self.main._openEditor()

class SearchDialog(QDialog):
    def __init__(self, tools, parent=None):
        super().__init__(parent)
        self._tools = tools
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFixedSize(500, 400)
        self._setupUI()
        self._populate()
        self.search_edit.setFocus()
        QTimer.singleShot(0, self._doCenter)

    def _doCenter(self):
        parent = self.parent()
        if parent:
            center = parent.geometry().center()
            self.move(center.x() - self.width() // 2, center.y() - self.height() // 2)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._focusSearch)

    def _focusSearch(self):
        self.raise_()
        self.activateWindow()
        activateWindow(self.winId())
        self.search_edit.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def eventFilter(self, obj, event):
        if obj is self.search_edit and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Up:
                self._moveSelection(-1)
                return True
            elif event.key() == Qt.Key.Key_Down:
                self._moveSelection(1)
                return True
        return super().eventFilter(obj, event)

    def _moveSelection(self, direction):
        current = self.list_widget.currentRow()
        count = self.list_widget.count()
        if count == 0:
            return
        next_row = (current + direction) % count
        self.list_widget.setCurrentRow(next_row)

    def _setupUI(self):
        self.setObjectName("search_dialog")
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.search_edit = QLineEdit()
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setFixedHeight(38)
        self.search_edit.setStyleSheet("border-radius: 0;")
        self.search_edit.textChanged.connect(self._filter)
        self.search_edit.returnPressed.connect(self._acceptCurrent)
        main_layout.addWidget(self.search_edit)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        main_layout.addWidget(sep)

        self.list_widget = QListWidget()
        self.list_widget.setFrameShape(QFrame.Shape.NoFrame)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.itemClicked.connect(self._onItemClicked)
        self.list_widget.setIconSize(QSize(24, 24))
        main_layout.addWidget(self.list_widget, 1)

        self.search_edit.installEventFilter(self)

    def _allTools(self):
        result = []
        for group_tools in self._tools.values():
            result.extend(group_tools)
        return result

    def _populate(self, tools=None):
        self.list_widget.clear()
        source = self._allTools() if tools is None else tools
        for tool in source:
            item = QListWidgetItem(getIcon(tool, 24), tool.get("name", ""))
            item.setSizeHint(QSize(0, 36))
            self.list_widget.addItem(item)
            item.setData(Qt.ItemDataRole.UserRole, tool)

    def _filter(self, text):
        text = text.strip().lower()
        if not text:
            self._populate()
            return
        matched = []
        for tool in self._allTools():
            if (text in tool.get("name", "").lower()
                    or text in (tool.get("path", "") or tool.get("url", "")).lower()
                    or text in tool.get("note", "").lower()):
                matched.append(tool)
        self._populate(matched)

    def _acceptCurrent(self):
        current = self.list_widget.currentItem()
        if current:
            self._onItemClicked(current)

    def _onItemClicked(self, item):
        tool = item.data(Qt.ItemDataRole.UserRole)
        if tool:
            self.accept()
            main_window = self.parent()
            if main_window and hasattr(main_window, "runItem"):
                main_window.runItem(tool)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self.focusWidget() is not self.search_edit:
                self._acceptCurrent()
        else:
            super().keyPressEvent(event)


class SearchWorkerThread(QThread):
    finished = Signal(list)
    error = Signal(str)

    def __init__(self, search_text: str, search_paths: List[str],
                 case_sensitive: bool, regex: bool):
        super().__init__()
        self.search_text = search_text
        self.search_paths = search_paths
        self.case_sensitive = case_sensitive
        self.regex = regex

    def run(self):
        try:
            results = searchFiles(
                self.search_text, self.search_paths,
                case_sensitive=self.case_sensitive, regex=self.regex,
                abort_check=self.isInterruptionRequested
            )
            if not self.isInterruptionRequested():
                self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))


class RenameItem:
    def __init__(self, original_path: str):
        self.original_path = original_path
        self.original_name = os.path.basename(original_path)
        self.new_name = self.original_name
        self.is_directory = os.path.isdir(original_path)
        self._extension = os.path.splitext(self.original_name)[1] if not self.is_directory else ""
        self._name_without_ext = os.path.splitext(self.original_name)[0] if not self.is_directory else self.original_name

    def applyFindReplace(self, find_text: str, replace_text: str, case_sensitive: bool = True):
        if not find_text:
            return
        name = self.original_name if self.is_directory else self._name_without_ext
        if case_sensitive:
            self.new_name = name.replace(find_text, replace_text)
        else:
            self.new_name = re.sub(re.escape(find_text), replace_text, name, flags=re.IGNORECASE)
        if not self.is_directory:
            self.new_name += self._extension

    def applyPrefix(self, prefix: str):
        if self.is_directory:
            self.new_name = prefix + self.original_name
        else:
            self.new_name = prefix + self._name_without_ext + self._extension

    def applySuffix(self, suffix: str):
        if self.is_directory:
            self.new_name = self.original_name + suffix
        else:
            self.new_name = self._name_without_ext + suffix + self._extension

    def applyNumbering(self, start: int = 1, step: int = 1, position: str = "prefix", padding: int = 3):
        num_str = str(start).zfill(padding)
        if self.is_directory:
            if position == "prefix":
                self.new_name = f"{num_str}_{self.original_name}"
            elif position == "suffix":
                self.new_name = f"{self.original_name}_{num_str}"
        else:
            if position == "prefix":
                self.new_name = f"{num_str}_{self._name_without_ext}{self._extension}"
            elif position == "suffix":
                self.new_name = f"{self._name_without_ext}_{num_str}{self._extension}"
            elif position == "replace":
                self.new_name = f"{num_str}{self._extension}"
        return step

    def getNewPath(self) -> str:
        return os.path.join(os.path.dirname(self.original_path), self.new_name)


class BatchRenameDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Window)
        self.folder_path = ""
        self.rename_items: list[RenameItem] = []
        self._preview_timer = QTimer()
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._doPreview)
        self.initUI()
        self.setMinimumSize(700, 500)

    def initUI(self):
        self.setWindowTitle("批量重命名")
        layout = QVBoxLayout(self)

        folder_layout = QHBoxLayout()

        self.folder_label = FileDrop()
        self.folder_label.folderDropped.connect(self.onFolderDropped)
        self.folder_label.fileDropped.connect(self.onFileDropped)
        self.folder_label.filesDropped.connect(self.onFilesDropped)
        self.folder_label.resetStyle()
        folder_layout.addWidget(self.folder_label, 1)
        self.file_count_label = QLabel("文件数: 0   ")
        layout.addLayout(folder_layout)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setMinimumHeight(200)
        self.items_container = QWidget()
        self.items_layout = QVBoxLayout(self.items_container)
        self.items_layout.setSpacing(0)
        self.items_layout.addStretch()
        self.scroll_area.setWidget(self.items_container)
        layout.addWidget(self.scroll_area, 1)

        mode_layout = QHBoxLayout()
        mode_layout.addWidget(self.file_count_label)
        mode_layout.addWidget(QLabel("模式"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["查找替换", "数字排序", "固定前缀", "固定后缀"])
        self.mode_combo.currentTextChanged.connect(self.onModeChanged)
        mode_layout.addWidget(self.mode_combo)

        self.mode_options_stack = QStackedWidget()

        fr_widget = QWidget()
        fr_layout = QHBoxLayout(fr_widget)
        fr_layout.setContentsMargins(0, 0, 0, 0)
        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText("查找")
        self.replace_edit = QLineEdit()
        self.replace_edit.setPlaceholderText("替换为")
        self.case_sensitive_check = QCheckBox("区分大小写")
        self.case_sensitive_check.setChecked(True)
        fr_layout.addWidget(self.find_edit)
        fr_layout.addWidget(QLabel("->"))
        fr_layout.addWidget(self.replace_edit)
        fr_layout.addWidget(self.case_sensitive_check)
        fr_layout.addStretch()
        self.mode_options_stack.addWidget(fr_widget)

        num_widget = QWidget()
        num_layout = QHBoxLayout(num_widget)
        num_layout.setContentsMargins(0, 0, 0, 0)
        num_layout.addWidget(QLabel("起始"))
        self.start_spin = QSpinBox()
        self.start_spin.setRange(1, 9999)
        self.start_spin.setValue(1)
        self.start_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        num_layout.addWidget(self.start_spin)
        num_layout.addWidget(QLabel("步长"))
        self.step_spin = QSpinBox()
        self.step_spin.setRange(1, 9999)
        self.step_spin.setValue(1)
        self.step_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        num_layout.addWidget(self.step_spin)
        num_layout.addWidget(QLabel("位置"))
        self.position_combo = QComboBox()
        self.position_combo.addItems(["前缀", "后缀", "替换名字"])
        num_layout.addWidget(self.position_combo)
        num_layout.addWidget(QLabel("填充"))
        self.padding_spin = QSpinBox()
        self.padding_spin.setRange(1, 10)
        self.padding_spin.setValue(3)
        self.padding_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        num_layout.addWidget(self.padding_spin)
        num_layout.addStretch()
        self.mode_options_stack.addWidget(num_widget)

        pref_widget = QWidget()
        pref_layout = QHBoxLayout(pref_widget)
        pref_layout.setContentsMargins(0, 0, 0, 0)
        self.prefix_edit = QLineEdit()
        self.prefix_edit.setPlaceholderText("输入前缀")
        pref_layout.addWidget(self.prefix_edit)
        pref_layout.addStretch()
        self.mode_options_stack.addWidget(pref_widget)

        suff_widget = QWidget()
        suff_layout = QHBoxLayout(suff_widget)
        suff_layout.setContentsMargins(0, 0, 0, 0)
        self.suffix_edit = QLineEdit()
        self.suffix_edit.setPlaceholderText("输入后缀")
        suff_layout.addWidget(self.suffix_edit)
        suff_layout.addStretch()
        self.mode_options_stack.addWidget(suff_widget)

        mode_layout.addWidget(self.mode_options_stack, 1)
        layout.addLayout(mode_layout)

        btn_layout = QHBoxLayout()
        self.selectFolder_btn = QPushButton("选择文件夹")
        self.selectFolder_btn.clicked.connect(self.selectFolder)
        btn_layout.addWidget(self.selectFolder_btn)
        self.execute_btn = QPushButton("执行重命名")
        self.execute_btn.clicked.connect(self.executeRename)
        self.execute_btn.setEnabled(False)
        btn_layout.addWidget(self.execute_btn)
        self.clear_btn = QPushButton("清空")
        self.clear_btn.clicked.connect(self.clearItems)
        btn_layout.addWidget(self.clear_btn)
        self.close_btn = QPushButton("关闭")
        self.close_btn.clicked.connect(self.close)
        btn_layout.addWidget(self.close_btn)
        layout.addLayout(btn_layout)

        self.onModeChanged("查找替换")
        self.find_edit.textChanged.connect(self.applyPreview)
        self.replace_edit.textChanged.connect(self.applyPreview)
        self.case_sensitive_check.stateChanged.connect(self.applyPreview)
        self.start_spin.valueChanged.connect(self.applyPreview)
        self.step_spin.valueChanged.connect(self.applyPreview)
        self.position_combo.currentTextChanged.connect(self.applyPreview)
        self.padding_spin.valueChanged.connect(self.applyPreview)
        self.prefix_edit.textChanged.connect(self.applyPreview)
        self.suffix_edit.textChanged.connect(self.applyPreview)

    def onFolderDropped(self, folder_path: str):
        self.folder_path = folder_path
        self.folder_label.setFolderPath(folder_path)
        self.loadFolder(folder_path)

    def onFileDropped(self, file_path: str):
        self.folder_path = os.path.dirname(file_path)
        self.folder_label.setFolderPath(self.folder_path)
        self.rename_items = [RenameItem(file_path)]
        self.file_count_label.setText("文件数: 1")
        self.applyPreview()
        self.execute_btn.setEnabled(True)

    def onFilesDropped(self, files: list):
        self.folder_path = os.path.dirname(files[0])
        self.folder_label.setFolderPath(self.folder_path)
        self.rename_items = [RenameItem(f) for f in files]
        self.file_count_label.setText(f"文件数: {len(files)}")
        self.applyPreview()
        self.execute_btn.setEnabled(len(self.rename_items) > 0)

    def selectFolder(self):
        folder = getFilePath(self, "选择文件夹", mode="dir")
        if folder:
            self.folder_path = folder
            self.folder_label.setFolderPath(folder)
            self.loadFolder(folder)

    def loadFolder(self, folder_path: str):
        self.rename_items = []
        for root_dir, dirs, files in os.walk(folder_path):
            for f in files:
                self.rename_items.append(RenameItem(os.path.join(root_dir, f)))
        self.file_count_label.setText(f"文件数: {len(self.rename_items)}")
        self.applyPreview()
        self.execute_btn.setEnabled(len(self.rename_items) > 0)

    def onModeChanged(self, mode: str):
        idx = {"查找替换": 0, "数字排序": 1, "固定前缀": 2, "固定后缀": 3}.get(mode, 0)
        self.mode_options_stack.setCurrentIndex(idx)
        if self.rename_items:
            self.applyPreview()

    def applyPreview(self):
        self._preview_timer.start(1000)

    def _doPreview(self):
        if not self.rename_items:
            self.updateList()
            return
        mode = self.mode_combo.currentText()
        for item in self.rename_items:
            item.new_name = item.original_name
        if mode == "查找替换":
            ft = self.find_edit.text()
            rt = self.replace_edit.text()
            cs = self.case_sensitive_check.isChecked()
            for item in self.rename_items:
                item.applyFindReplace(ft, rt, cs)
        elif mode == "数字排序":
            start = self.start_spin.value()
            step = self.step_spin.value()
            pos = {"前缀": "prefix", "后缀": "suffix", "替换名字": "replace"}.get(
                self.position_combo.currentText(), "prefix")
            padding = self.padding_spin.value()
            num = start
            for item in self.rename_items:
                item.applyNumbering(num, step, pos, padding)
                num += step
        elif mode == "固定前缀":
            pf = self.prefix_edit.text()
            for item in self.rename_items:
                item.applyPrefix(pf)
        elif mode == "固定后缀":
            sf = self.suffix_edit.text()
            for item in self.rename_items:
                item.applySuffix(sf)
        self.updateList()

    def updateList(self):
        while self.items_layout.count() > 1:
            w = self.items_layout.takeAt(0).widget()
            if w:
                w.deleteLater()
        for item in self.rename_items:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(5, 2, 5, 2)
            orig = QLabel(item.original_name)
            orig.setMinimumWidth(200)
            orig.setStyleSheet("color: gray;")
            arrow = QLabel(" -> ")
            edit = QLineEdit(item.new_name)
            edit.setStyleSheet("background: white;")
            edit.textChanged.connect(lambda text, i=item: setattr(i, 'new_name', text))
            row_layout.addWidget(orig)
            row_layout.addWidget(arrow)
            row_layout.addWidget(edit, 1)
            self.items_layout.insertWidget(self.items_layout.count() - 1, row)

    def executeRename(self):
        if not self.rename_items:
            return
        success = 0
        errors = 0
        error_files = []
        for item in self.rename_items:
            if item.original_name == item.new_name:
                continue
            try:
                os.rename(item.original_path, item.getNewPath())
                success += 1
            except Exception as e:
                errors += 1
                error_files.append(f"{item.original_name}: {e}")
        if errors:
            messageBox(self, "重命名完成",
                       f"成功: {success} 个\n失败: {errors} 个\n\n失败详情:\n" + "\n".join(error_files[:10]), 1)
        else:
            messageBox(self, "重命名完成", f"成功重命名: {success} 个文件", 1)
        if self.folder_path:
            self.loadFolder(self.folder_path)

    def clearItems(self):
        self.rename_items = []
        self.folder_path = ""
        self.folder_label.setFolderPath("拖拽文件夹到此处")
        self.folder_label.resetStyle()
        self.file_count_label.setText("文件数: 0")
        self.updateList()
        self.execute_btn.setEnabled(False)


class DuplicateFinder(QThread):
    progress = Signal(int, int)
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, files: List[str] = None, parent=None, folder_path: str = None, paths: List[str] = None, rules: List[str] = None):
        super().__init__(parent)
        self.files = files or []
        self.folder_path = folder_path
        self.paths = paths or []
        self.rules = rules or []
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        try:
            if self.paths:
                self.progress.emit(0, 0)
                self.files = collectFiles(self.paths, self.rules, abort_check=lambda: self._is_cancelled)
                if self._is_cancelled:
                    self.finished.emit({})
                    return
            result = self.findDuplicates(self.files, self.folder_path)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))

    def findDuplicates(self, files: List[str], folder_path: str = None) -> Dict[str, List[dict]]:
        cached_files = self._loadCache()
        new_or_modified = []
        current_files = {}

        if folder_path:
            current_files = self._buildFileTree(folder_path)
            new_or_modified = self._scanChanges(current_files, cached_files)
            self._removeDeleted(current_files, cached_files)

        md5_dict: Dict[str, List[dict]] = {}
        file_hash_map = {}

        if cached_files and folder_path:
            for fp, info in cached_files.items():
                if fp not in new_or_modified and os.path.exists(fp):
                    file_hash_map[fp] = {"path": fp, "size": info.get("size", 0), "md5": info.get("md5", "")}

        total = len(files)
        for i, file_path in enumerate(files):
            if self._is_cancelled:
                break
            try:
                size = os.path.getsize(file_path)
                if size == 0:
                    continue
                if file_path in file_hash_map and file_hash_map[file_path].get("md5"):
                    md5 = file_hash_map[file_path]["md5"]
                else:
                    md5 = fileHash(file_path)
                if md5:
                    info = {"path": file_path, "size": size, "md5": md5}
                    if folder_path and file_path in current_files:
                        current_files[file_path]["md5"] = md5
                    md5_dict.setdefault(md5, []).append(info)
            except (PermissionError, OSError):
                continue
            self.progress.emit(i + 1, total)

        duplicates = {md5: fl for md5, fl in md5_dict.items() if len(fl) > 1}
        if folder_path and current_files:
            self._saveCache(current_files)
        return duplicates

    def _loadCache(self) -> Dict:
        if not cache_file.exists():
            return {}
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _saveCache(self, files_dict: Dict):
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(files_dict, ensure_ascii=False, indent=2), encoding="utf-8")

    def _buildFileTree(self, folder_path: str) -> Dict:
        files = {}
        for root_dir, dirs, filenames in os.walk(folder_path):
            if self._is_cancelled:
                break
            for fn in filenames:
                fp = os.path.join(root_dir, fn)
                try:
                    st = os.stat(fp)
                    files[fp] = {"size": st.st_size, "mtime": st.st_mtime, "md5": ""}
                except Exception:
                    continue
        return files

    def _scanChanges(self, current: Dict, cached: Dict) -> List[str]:
        result = []
        for fp, info in current.items():
            if fp not in cached:
                result.append(fp)
            elif info.get("size") != cached[fp].get("size") or \
                 abs(info.get("mtime", 0) - cached[fp].get("mtime", 0)) > 1:
                result.append(fp)
        return result

    def _removeDeleted(self, current: Dict, cached: Dict):
        deleted = set(cached.keys()) - set(current.keys())
        for p in deleted:
            del cached[p]


class DuplicatePanelManager:
    def __init__(self, parent, splitter, placeholder, editor, folder_panel_manager=None):
        self.parent = parent
        self.splitter = splitter
        self.editor = editor
        self.folder_panel_manager = folder_panel_manager
        self.panel = None
        self.tree = None
        self.placeholder = placeholder
        self._panel_width = 300

    def create(self) -> QWidget:
        self.panel = QWidget()
        layout = QVBoxLayout(self.panel)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QLabel("重复文件")
        header.setObjectName("duplicate_header")
        header.setCursor(Qt.CursorShape.PointingHandCursor)
        header.mousePressEvent = lambda e: self.close()
        layout.addWidget(header)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["文件路径", "大小", "MD5", "操作"])
        self.tree.setColumnWidth(0, 300)
        self.tree.setColumnWidth(1, 80)
        self.tree.setColumnWidth(3, 60)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._treeContextMenu)
        self.tree.itemDoubleClicked.connect(self._onItemDoubleClicked)
        layout.addWidget(self.tree)
        return self.panel

    def _treeContextMenu(self, pos):
        menu = QMenu(self.parent)
        close_action = QAction("关闭重复文件视图", self.parent)
        close_action.triggered.connect(self.close)
        menu.addAction(close_action)
        index = self.tree.indexAt(pos)
        if index.isValid():
            item = self.tree.itemFromIndex(index)
            if item:
                data = item.data(0, Qt.ItemDataRole.UserRole)
                if data and data.get("type") == "file":
                    fp = data.get("path")
                    if fp:
                        show_in = QAction("在文件资源管理器中显示", self.parent)
                        show_in.triggered.connect(lambda checked=False, p=fp: showFile(p, self.parent))
                        menu.addAction(show_in)
                        to_trash = QAction("移动到回收站", self.parent)
                        to_trash.triggered.connect(lambda checked=False, p=fp: self._moveToTrash(p))
                        menu.addAction(to_trash)
        menu.exec_(self.tree.mapToGlobal(pos))

    def _moveToTrash(self, file_path: str):
        from src.system import moveTrash
        if not moveTrash(file_path):
            messageBox(self.parent, tr("错误"), tr("移动到回收站失败"), 1)
        for md5, files in list(self._current_duplicates.items()):
            self._current_duplicates[md5] = [f for f in files if f["path"] != file_path]
            if not self._current_duplicates[md5]:
                del self._current_duplicates[md5]
        self.showDuplicates(self._current_duplicates)

    def _onItemDoubleClicked(self, item, column):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data and data.get("type") == "file":
            fp = data.get("path")
            if fp and hasattr(self.parent, 'openFilePath'):
                self.parent.openFilePath(fp)

    def ensureCreated(self):
        if self.panel is not None:
            return
        self.create()
        if self.placeholder is not None:
            idx = self.splitter.indexOf(self.placeholder)
            if idx >= 0:
                self.splitter.replaceWidget(idx, self.panel)
            self.placeholder.deleteLater()
            self.placeholder = None
        else:
            idx = self.splitter.count() - 1
            if idx >= 0:
                self.splitter.insertWidget(idx, self.panel)
        self.panel.setMinimumWidth(200)
        self.panel.show()
        w = self.parent.width()
        if w > 0:
            fw = (self.folder_panel_manager._folder_panel_width
                  if self.folder_panel_manager and self.folder_panel_manager.isVisible()
                  else 0)
            self.setSizes(w, fw)

    def showDuplicates(self, duplicates: dict):
        self._current_duplicates = {md5: [dict(f) for f in files] for md5, files in duplicates.items()}
        self.ensureCreated()
        self.tree.clear()
        style = self.parent.style()
        folder_icon = style.standardIcon(QStyle.SP_DirIcon)
        file_icon = style.standardIcon(QStyle.SP_FileIcon)
        for md5, files in duplicates.items():
            group_item = QTreeWidgetItem(self.tree, [
                f"重复文件组 ({len(files)} 个)",
                formatFileSize(files[0]["size"]),
                md5[:16] + "...", ""
            ])
            group_item.setIcon(0, folder_icon)
            group_item.setData(0, Qt.ItemDataRole.UserRole, {"type": "group", "md5": md5})
            for fi in files:
                file_item = QTreeWidgetItem(group_item, [
                    fi["path"], formatFileSize(fi["size"]), fi["md5"], ""
                ])
                file_item.setIcon(0, file_icon)
                file_item.setData(0, Qt.ItemDataRole.UserRole, {
                    "type": "file", "path": fi["path"], "size": fi["size"], "md5": fi["md5"]
                })
                del_btn = QPushButton("删除")
                del_btn.setFixedSize(50, 22)
                del_btn.clicked.connect(lambda checked=False, p=fi["path"]: self._moveToTrash(p))
                self.tree.setItemWidget(file_item, 3, del_btn)
            group_item.setExpanded(True)
        self.showPanelView()

    def showPanelView(self):
        if self.panel is None:
            return
        self.panel.setMinimumWidth(200)
        self.panel.show()

    def setSizes(self, available_width: int, folder_panel_width: int = 0):
        if not self.panel or available_width <= 0:
            return
        dup_w = self._panel_width
        ed_w = available_width - dup_w - folder_panel_width
        if ed_w < 400:
            ed_w = 400
            dup_w = available_width - folder_panel_width - ed_w
            if dup_w < 200:
                dup_w = 200
        if folder_panel_width > 0:
            self.splitter.setSizes([dup_w, folder_panel_width, ed_w])
        else:
            self.splitter.setSizes([dup_w, ed_w])

    def close(self):
        if self.panel is not None:
            self.panel.deleteLater()
            self.panel = None
            self.tree = None
            if self.placeholder is None:
                ph = QWidget()
                self.splitter.insertWidget(0, ph)
                self.placeholder = ph
            self.placeholder.setFixedWidth(0)
            self.placeholder.hide()

    def isVisible(self) -> bool:
        return self.panel is not None

class ToolBoxSettings(QWidget):
    def __init__(self, settings: dict, parent=None):
        super().__init__(parent)
        self.settings = dict(settings)
        self.initUI()

    def initUI(self):
        layout = QFormLayout(self)

        scroll_speed = self.settings.get("scroll", 50)
        self.scroll_speed = QSpinBox()
        self.scroll_speed.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.scroll_speed.setRange(1, 100)
        self.scroll_speed.setValue(scroll_speed)
        layout.addRow("滑动速度", self.scroll_speed)

        copy_path = self.settings.get("copy_path", "data/copy.txt")
        self.copy_path_edit = QLineEdit(copy_path)
        layout.addRow("自动复制目标", self.copy_path_edit)

        search_paths = self.settings.get("search", {}).get("paths", [])
        self.search_paths_list = QListWidget()
        self.search_paths_list.setMaximumHeight(80)
        for p in search_paths:
            self.search_paths_list.addItem(os.path.normpath(p))
        layout.addRow("自动搜索路径", self.search_paths_list)

        btn_h = QHBoxLayout()
        add_btn = QPushButton("添加路径")
        add_btn.clicked.connect(self._addSearchPath)
        rm_btn = QPushButton("移除选中")
        rm_btn.clicked.connect(self._removeSearchPath)
        btn_h.addWidget(add_btn)
        btn_h.addWidget(rm_btn)
        btn_h.addStretch()
        layout.addRow("", btn_h)

        cs = self.settings.get("search", {}).get("case_sensitive", False)
        self.case_check = QCheckBox("区分大小写")
        self.case_check.setChecked(cs)
        rx = self.settings.get("search", {}).get("regex", False)
        self.regex_check = QCheckBox("正则表达式")
        self.regex_check.setChecked(rx)
        opt_row = QHBoxLayout()
        opt_row.addWidget(self.case_check)
        opt_row.addWidget(self.regex_check)
        opt_row.addStretch()
        layout.addRow("搜索选项", opt_row)

        delay = self.settings.get("search", {}).get("close_delay", 3)
        self.close_delay = QSpinBox()
        self.close_delay.setRange(1, 60)
        self.close_delay.setSuffix(" 秒")
        self.close_delay.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.close_delay.setValue(delay)
        layout.addRow("弹窗显示时间", self.close_delay)

        click_interval = self.settings.get("click", 3)
        self.click_interval = QSpinBox()
        self.click_interval.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.click_interval.setRange(1, 9)
        self.click_interval.setSuffix(" 秒")
        self.click_interval.setValue(click_interval)
        layout.addRow("自动点击间隔", self.click_interval)

        paste_regex = self.settings.get("paste_regex", "")
        display_text = (paste_regex
            .replace('\n', '\\n')
            .replace('\r', '\\r')
            .replace('\t', '\\t'))
        self.paste_regex_edit = QLineEdit(display_text)
        layout.addRow("快速粘贴正则", self.paste_regex_edit)

        self._qt_list = QListWidget()
        self._qt_list.setMaximumHeight(120)
        self._qt_list.itemDoubleClicked.connect(self._qtEdit)
        layout.addRow("快速文本", self._qt_list)

        qt_btn_h = QHBoxLayout()
        qt_add = QPushButton("新建")
        qt_add.clicked.connect(self._qtAdd)
        qt_edit = QPushButton("编辑")
        qt_edit.clicked.connect(self._qtEdit)
        qt_del = QPushButton("删除")
        qt_del.clicked.connect(self._qtDel)
        qt_up = QPushButton("上移")
        qt_up.clicked.connect(self._qtUp)
        qt_down = QPushButton("下移")
        qt_down.clicked.connect(self._qtDown)
        qt_btn_h.addWidget(qt_add)
        qt_btn_h.addWidget(qt_edit)
        qt_btn_h.addWidget(qt_del)
        qt_btn_h.addWidget(qt_up)
        qt_btn_h.addWidget(qt_down)
        qt_btn_h.addStretch()
        layout.addRow("", qt_btn_h)

        for entry in self.settings.get("quick_text", []):
            item = QListWidgetItem(entry.get("note", "") or entry.get("text", ""))
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self._qt_list.addItem(item)

        self._url_list = QListWidget()
        self._url_list.setMaximumHeight(100)
        self._url_list.itemDoubleClicked.connect(self._urlEdit)
        layout.addRow("URL 协议", self._url_list)

        url_btn_h = QHBoxLayout()
        url_add = QPushButton("新建")
        url_add.clicked.connect(self._urlAdd)
        url_edit = QPushButton("编辑")
        url_edit.clicked.connect(self._urlEdit)
        url_del = QPushButton("删除")
        url_del.clicked.connect(self._urlDel)
        url_reg = QPushButton("注册")
        url_reg.clicked.connect(self._urlRegister)
        url_unreg = QPushButton("取消注册")
        url_unreg.clicked.connect(self._urlUnregister)
        url_btn_h.addWidget(url_add)
        url_btn_h.addWidget(url_edit)
        url_btn_h.addWidget(url_del)
        url_btn_h.addWidget(url_reg)
        url_btn_h.addWidget(url_unreg)
        url_btn_h.addStretch()
        layout.addRow("", url_btn_h)

        for entry in self.settings.get("url_protocol", []):
            item = QListWidgetItem(f"{entry[0]} → {entry[1]}")
            item.setData(Qt.ItemDataRole.UserRole, list(entry))
            self._url_list.addItem(item)

    def _addSearchPath(self):
        path = getFilePath(self, "选择搜索路径", mode="dir")
        if path:
            self.search_paths_list.addItem(path)

    def _removeSearchPath(self):
        row = self.search_paths_list.currentRow()
        if row >= 0:
            self.search_paths_list.takeItem(row)

    def _qtAdd(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("添加快速文本")
        dialog.setMinimumWidth(400)
        layout = QVBoxLayout(dialog)
        text_edit = QLineEdit()
        text_edit.setPlaceholderText("文本内容")
        note_edit = QLineEdit()
        note_edit.setPlaceholderText("备注")
        layout.addWidget(QLabel("文本"))
        layout.addWidget(text_edit)
        layout.addWidget(QLabel("备注"))
        layout.addWidget(note_edit)
        btn_h = QHBoxLayout()
        ok = QPushButton("确定")
        ok.clicked.connect(dialog.accept)
        cancel = QPushButton("取消")
        cancel.clicked.connect(dialog.reject)
        btn_h.addStretch()
        btn_h.addWidget(ok)
        btn_h.addWidget(cancel)
        layout.addLayout(btn_h)
        if dialog.exec() == QDialog.DialogCode.Accepted and text_edit.text().strip():
            entry = {"text": text_edit.text().strip(), "note": note_edit.text().strip()}
            item = QListWidgetItem(entry["note"] or entry["text"])
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self._qt_list.addItem(item)

    def _qtEdit(self):
        current = self._qt_list.currentItem()
        if not current:
            return
        entry = dict(current.data(Qt.ItemDataRole.UserRole) or {"text": "", "note": ""})
        dialog = QDialog(self)
        dialog.setWindowTitle("编辑快速文本")
        dialog.setMinimumWidth(400)
        layout = QVBoxLayout(dialog)
        text_edit = QLineEdit(entry.get("text", ""))
        note_edit = QLineEdit(entry.get("note", ""))
        layout.addWidget(QLabel("文本"))
        layout.addWidget(text_edit)
        layout.addWidget(QLabel("备注"))
        layout.addWidget(note_edit)
        btn_h = QHBoxLayout()
        ok = QPushButton("确定")
        ok.clicked.connect(dialog.accept)
        cancel = QPushButton("取消")
        cancel.clicked.connect(dialog.reject)
        btn_h.addStretch()
        btn_h.addWidget(ok)
        btn_h.addWidget(cancel)
        layout.addLayout(btn_h)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            entry["text"] = text_edit.text().strip()
            entry["note"] = note_edit.text().strip()
            current.setText(entry["note"] or entry["text"])
            current.setData(Qt.ItemDataRole.UserRole, entry)

    def _qtDel(self):
        current = self._qt_list.currentItem()
        if current:
            row = self._qt_list.row(current)
            self._qt_list.takeItem(row)

    def _qtUp(self):
        current = self._qt_list.currentItem()
        if not current:
            return
        row = self._qt_list.row(current)
        if row <= 0:
            return
        self._qtSwapItems(row, row - 1)
        self._qt_list.setCurrentRow(row - 1)

    def _qtDown(self):
        current = self._qt_list.currentItem()
        if not current:
            return
        row = self._qt_list.row(current)
        if row < 0 or row >= self._qt_list.count() - 1:
            return
        self._qtSwapItems(row, row + 1)
        self._qt_list.setCurrentRow(row + 1)

    def _qtSwapItems(self, a: int, b: int):
        if a > b:
            a, b = b, a
        item_a = self._qt_list.takeItem(a)
        item_b = self._qt_list.takeItem(b - 1)
        self._qt_list.insertItem(a, item_b)
        self._qt_list.insertItem(b, item_a)

    def _urlDialog(self, entry: list = None) -> list:
        """URL 协议编辑对话框，确定返回 [scheme, path, args]，否则返回 None"""
        scheme = entry[0] if entry else ""
        path = entry[1] if entry else ""
        args = entry[2] if entry and len(entry) > 2 else ""
        dialog = QDialog(self)
        dialog.setWindowTitle("编辑URL协议" if entry else "添加URL协议")
        dialog.setMinimumWidth(420)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        scheme_edit = QLineEdit(scheme)
        form.addRow("协议名", scheme_edit)
        path_edit, _ = filePathWidget(dialog, form, "程序路径", "选择",
                                      "可执行文件 (*.exe);;所有文件 (*)")
        path_edit.setText(path)
        args_edit = QLineEdit(args)
        args_edit.setToolTip('注册命令为 "{path}" {args} "%1"，参数可留空')
        form.addRow("参数", args_edit)
        layout.addLayout(form)
        btn_h = QHBoxLayout()
        ok = QPushButton("确定")
        ok.clicked.connect(dialog.accept)
        cancel = QPushButton("取消")
        cancel.clicked.connect(dialog.reject)
        btn_h.addStretch()
        btn_h.addWidget(ok)
        btn_h.addWidget(cancel)
        layout.addLayout(btn_h)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        scheme_text = scheme_edit.text().strip().lower()
        if not re.match(URL_SCHEME_RE, scheme_text):
            messageBox(dialog, "警告",
                       "协议名不合法（字母开头，仅含字母数字 + - .）", 1)
            return None
        path_text = path_edit.text().strip().strip('"')
        if not path_text:
            messageBox(dialog, "警告", "程序路径不能为空", 1)
            return None
        if not os.path.isfile(path_text):
            if not messageBox(dialog, "提示",
                              f"程序路径不存在: {path_text}\n是否仍要保存?"):
                return None
        return [scheme_text, path_text, args_edit.text().strip()]

    def _urlFindIndex(self, scheme: str) -> int:
        """按协议名查找列表索引，大小写不敏感，不存在返回 -1"""
        for i in range(self._url_list.count()):
            entry = self._url_list.item(i).data(Qt.ItemDataRole.UserRole)
            if entry and entry[0].lower() == scheme.lower():
                return i
        return -1

    def _urlAddItem(self, entry: list, row: int = None):
        """构造 URL 协议列表项，row 为空则追加到末尾，否则插入指定位置"""
        item = QListWidgetItem(f"{entry[0]} → {entry[1]}")
        item.setData(Qt.ItemDataRole.UserRole, entry)
        if row is None:
            self._url_list.addItem(item)
        else:
            self._url_list.insertItem(row, item)
        return item

    def _urlAdd(self):
        entry = self._urlDialog()
        if entry is None:
            return
        idx = self._urlFindIndex(entry[0])
        if idx >= 0:
            if not messageBox(self, "警告", f"协议 {entry[0]}:// 已存在，是否覆盖?"):
                return
            self._url_list.takeItem(idx)
            _url_protocol.unregister(entry[0])
        self._urlAddItem(entry)

    def _urlEdit(self):
        current = self._url_list.currentItem()
        if not current:
            return
        entry = current.data(Qt.ItemDataRole.UserRole)
        new_entry = self._urlDialog(entry)
        if new_entry is None:
            return
        old_scheme = entry[0]
        new_scheme = new_entry[0]
        was_registered = _url_protocol.isRegistered(old_scheme)
        if new_scheme != old_scheme:
            idx = self._urlFindIndex(new_scheme)
            if idx >= 0:
                if not messageBox(self, "警告", f"协议 {new_scheme}:// 已存在，是否覆盖?"):
                    return
                self._url_list.takeItem(idx)
                _url_protocol.unregister(new_scheme)
            if was_registered:
                _url_protocol.unregister(old_scheme)
        row = self._url_list.row(current)
        self._url_list.takeItem(row)
        self._urlAddItem(new_entry, row)
        self._url_list.setCurrentRow(row)
        if was_registered:
            if old_scheme != new_scheme:
                messageBox(self, "提示",
                           f"已取消注册 {old_scheme}://，如需新协议请点击\"注册\"", 1)
            elif new_entry[1] != entry[1] or new_entry[2] != entry[2]:
                messageBox(self, "提示",
                           "该协议已注册，路径或参数已更改，请点击\"注册\"应用新配置", 1)

    def _urlDel(self):
        current = self._url_list.currentItem()
        if not current:
            return
        scheme = current.data(Qt.ItemDataRole.UserRole)[0]
        if not messageBox(self, "确认删除", f"是否删除 {scheme}:// 并取消注册?"):
            return
        _url_protocol.unregister(scheme)
        self._url_list.takeItem(self._url_list.row(current))

    def _urlRegister(self):
        current = self._url_list.currentItem()
        if not current:
            return
        scheme, path, args = current.data(Qt.ItemDataRole.UserRole)
        if scheme in URL_RESERVED:
            if not messageBox(self, "警告",
                              f"{scheme}:// 是常用协议，注册会覆盖系统默认处理程序，是否继续?"):
                return
        if not os.path.isfile(path):
            if not messageBox(self, "提示",
                              f"程序路径不存在: {path}\n是否仍要注册?"):
                return
        if _url_protocol.register(scheme, path, args):
            messageBox(self, "提示", f"已注册 {scheme}://", 1)
        else:
            messageBox(self, "错误", f"注册 {scheme}:// 失败", 1)

    def _urlUnregister(self):
        current = self._url_list.currentItem()
        if not current:
            return
        scheme = current.data(Qt.ItemDataRole.UserRole)[0]
        if _url_protocol.unregister(scheme):
            messageBox(self, "提示", f"已取消注册 {scheme}://", 1)
        else:
            messageBox(self, "错误", f"取消注册 {scheme}:// 失败", 1)

    def getSetting(self) -> dict:
        self.settings["scroll"] = self.scroll_speed.value()
        self.settings["copy_path"] = os.path.normpath(self.copy_path_edit.text()) if self.copy_path_edit.text() else "data/copy.txt"
        self.settings["search"] = {
            "paths": [
                os.path.normpath(self.search_paths_list.item(i).text())
                for i in range(self.search_paths_list.count())
            ],
            "case_sensitive": self.case_check.isChecked(),
            "regex": self.regex_check.isChecked(),
            "close_delay": self.close_delay.value(),
        }
        self.settings["quick_text"] = [
            self._qt_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self._qt_list.count())
        ]
        self.settings["click"] = self.click_interval.value()
        raw = self.paste_regex_edit.text().strip()
        self.settings["paste_regex"] = (raw
            .replace('\\n', '\n')
            .replace('\\r', '\r')
            .replace('\\t', '\t'))
        self.settings["url_protocol"] = [
            self._url_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self._url_list.count())
        ]
        return self.settings


class _AutoCopyManager:
    def __init__(self, parent=None):
        self.enabled = False
        self.target_file = "data/copy.txt"
        self._monitor = None

    def initMonitor(self, parent):
        self._monitor = ClipboardMonitor()
        self._monitor._callbacks.add(self._onClipboardChange)
    
    def setEnabled(self, enabled: bool):
        self.enabled = enabled
        if enabled:
            self._monitor.start()
            logger.info("自动复制已启动")
        else:
            self._monitor.stop()
            logger.info("自动复制已停止")

    def _onClipboardChange(self, text: str):
        if self.enabled and text:
            self.copyToFile(text)

    def copyToFile(self, text: str) -> bool:
        if not text:
            return False
        try:
            target = Path(self.target_file)
            if not target.is_absolute():
                target = root / self.target_file
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, 'a', encoding="utf-8") as f:
                if target.exists() and target.stat().st_size > 0:
                    f.write("\n\n\n")
                f.write(text)
            return True
        except Exception:
            logger.exception("自动复制失败")
            return False


class _AutoSearchManager:
    def __init__(self, main=None):
        self.main = main
        self.enabled = False
        self.search_paths: List[str] = []
        self.case_sensitive = False
        self.regex = False
        self._monitor = None
        self._popup = None
        self._popup_timer = None
        self._search_thread: Optional[SearchWorkerThread] = None
        self._current_search_text = ""
        self.close_delay = 3

    def initMonitor(self, parent):
        self._monitor = ClipboardMonitor()
        self._monitor._callbacks.add(self._onClipboardChange)
    
    def setEnabled(self, enabled: bool):
        self.enabled = enabled
        if enabled and self.search_paths:
            self._monitor.start()
        else:
            self._monitor.stop()

    def _onClipboardChange(self, text: str):
        if self.enabled and text and self.search_paths:
            self._current_search_text = text
            if self._search_thread and self._search_thread.isRunning():
                self._search_thread.requestInterruption()
                self._search_thread.wait(3000)
            self._search_thread = SearchWorkerThread(
                text, self.search_paths, self.case_sensitive, self.regex
            )
            self._search_thread.finished.connect(
                lambda results, st=text: self._onSearchFinished(results, st)
            )
            self._search_thread.error.connect(
                lambda err: logger.error(f"搜索失败: {err}")
            )
            self._search_thread.start()

    def _onSearchFinished(self, results: List[dict], search_text: str):
        if results and search_text == self._current_search_text:
            self._showPopup(results, search_text)

    def _showPopup(self, results: List[dict], search_text: str):
        if self._popup:
            self._popup.close()
            self._popup = None
        self._popup = QDialog(self.main)
        self._popup.setWindowTitle(tr("自动搜索结果"))
        self._popup.setFixedSize(500, 300)
        layout = QVBoxLayout()
        info = QLabel(f"在 {len(results)} 个位置找到: \"{search_text}\"")
        info.setStyleSheet("font-weight: bold;")
        layout.addWidget(info)
        hint = QLabel(tr("双击打开文件"))
        hint.setStyleSheet("color: #666; font-size: 9pt;")
        layout.addWidget(hint)
        result_list = QListWidget()
        for r in results[:20]:
            fp = os.path.abspath(r['file'])
            ln = r['line']
            ct = r['content'][:60]
            item = QListWidgetItem(f"{fp}:{ln}\n{ct}...")
            item.setData(Qt.UserRole, r)
            result_list.addItem(item)
        if len(results) > 20:
            more = QLabel(str(len(results) - 20) + " " + tr("个搜索结果"))
            layout.addWidget(more)
        layout.addWidget(result_list)
        self._popup.setLayout(layout)

        def onDoubleClick(item):
            r = item.data(Qt.UserRole)
            if r:
                self._openFile(r['file'], r['line'])
                self._popup.close()

        result_list.itemDoubleClicked.connect(onDoubleClick)
        self._popup.show()
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            self._popup.move(geo.right() - self._popup.width() - 20,
                             geo.bottom() - self._popup.height() - 20)
        if self._popup_timer:
            self._popup_timer.stop()
            self._popup_timer.deleteLater()
        self._popup_timer = QTimer()
        self._popup_timer.timeout.connect(self._popup.close)
        self._popup_timer.setSingleShot(True)
        self._popup_timer.start(self.close_delay * 1000)

    def _openFile(self, file_path: str, line: int):
        if not Path(file_path).exists():
            logger.info(f"自动搜索打开文件失败: 文件不存在 {file_path}")
            messageBox(self._popup, tr("错误"), tr("文件不存在") + " " + file_path, 1)
            return
        editor = self.main.editor if self.main.editor else self.main._openEditor()
        open_method = getattr(editor, 'openFilePath', None)
        if open_method:
            open_method(file_path)
            editor.activateWindow()
            editor.raise_()
            QTimer.singleShot(100, lambda: self._gotoLine(editor, line))
        else:
            logger.info(f"自动搜索打开文件失败: 编辑器无 openFilePath, editor={editor}")

    def _gotoLine(self, mw, line: int):
        get_ed = getattr(mw, 'getEditor', None)
        if not get_ed:
            return
        editor = get_ed()
        if editor and line >= 1:
            block = editor.text_edit.document().findBlockByNumber(line - 1)
            if block.isValid():
                editor.text_edit.setTextCursor(
                    QTextCursor(block)
                )
                editor.text_edit.ensureCursorVisible()

class _AutoClickManager:
    _TICK_MS = 100

    def __init__(self, parent=None):
        self._tm = TimerManager()
        self.parent = parent
        self._timer = self._tm.createTimer(parent)
        self._timer.timeout.connect(self._doClick)
        self.enabled = False
        self.interval = 3
        self._elapsed = 0
        self._mouse_controller = mouse.Controller()
        self._keyboard_listener = None

    def setEnabled(self, enabled: bool):
        self.enabled = enabled
        if enabled:
            self._elapsed = 0
            self._startListener()
            self._timer.start(self._TICK_MS)
        else:
            self._stopListener()
            self._timer.stop()

    def _doClick(self):
        if not self.enabled:
            self._stopListener()
            self._timer.stop()
            return
        self._elapsed += self._TICK_MS
        if self._elapsed >= self.interval * 1000:
            self._elapsed = 0
            try:
                self._mouse_controller.click(mouse.Button.left)
            except Exception:
                logger.exception("模拟鼠标点击失败")

    def _onKeyPress(self, key):
        try:
            if key == keyboard.Key.esc:
                self.enabled = False
            elif hasattr(key, 'char') and key.char and key.char.isdigit():
                d = int(key.char)
                if 1 <= d <= 9:
                    self.interval = d
        except Exception:
            logger.exception("按键监听回调失败")

    def _startListener(self):
        self._stopListener()
        self._keyboard_listener = keyboard.Listener(on_press=self._onKeyPress)
        self._keyboard_listener.daemon = True
        self._keyboard_listener.start()

    def _stopListener(self):
        if self._keyboard_listener:
            self._keyboard_listener.stop()
            self._keyboard_listener.join()
            self._keyboard_listener = None


class QuickTextDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        painter.save()
        entry = index.data(Qt.ItemDataRole.UserRole)
        if isinstance(entry, dict):
            text = entry.get("text", "")
            note = entry.get("note", "")
        else:
            text = str(entry) if entry else ""
            note = ""

        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, QColor("#dddddd"))

        text_rect = option.rect.adjusted(8, 4, -8, -option.rect.height() // 2 + 2)
        painter.setPen(option.palette.text().color())
        f = painter.font()
        f.setBold(True)
        painter.setFont(f)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)

        note_rect = option.rect.adjusted(8, option.rect.height() // 2, -8, -4)
        painter.setPen(QColor("gray"))
        f.setBold(False)
        f.setPointSize(max(f.pointSize() - 2, 8))
        painter.setFont(f)
        painter.drawText(note_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, note)
        painter.restore()

    def sizeHint(self, option, index):
        return QSize(0, 50)


class QuickTextDialog(QDialog):
    def __init__(self, items: list, parent=None):
        super().__init__(parent)
        self._items = items
        self.selected_text = ""
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFixedSize(500, 490)
        self._setupUI()
        self._populate()
        self.search_edit.setFocus()
        QTimer.singleShot(0, self._doCenter)

    def _doCenter(self):
        parent = self.parent()
        if parent:
            center = parent.geometry().center()
            self.move(center.x() - self.width() // 2, center.y() - self.height() // 2)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._focusSearch)

    def _focusSearch(self):
        self.raise_()
        self.activateWindow()
        activateWindow(self.winId())
        self.search_edit.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def eventFilter(self, obj, event):
        if obj is self.search_edit and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Up:
                self._moveSelection(-1)
                return True
            elif key == Qt.Key.Key_Down:
                self._moveSelection(1)
                return True
            elif key == Qt.Key.Key_Escape:
                self.reject()
                return True
        return super().eventFilter(obj, event)

    def _moveSelection(self, direction):
        current = self.list_widget.currentRow()
        count = self.list_widget.count()
        if count == 0:
            return
        next_row = (current + direction) % count
        self.list_widget.setCurrentRow(next_row)

    def _setupUI(self):
        self.setObjectName("quick_text_dialog")
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.search_edit = QLineEdit()
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setFixedHeight(38)
        self.search_edit.setStyleSheet("border-radius: 0;")
        self.search_edit.textChanged.connect(self._filter)
        self.search_edit.returnPressed.connect(self._acceptCurrent)
        main_layout.addWidget(self.search_edit)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        main_layout.addWidget(sep)

        self.list_widget = QListWidget()
        self.list_widget.setFrameShape(QFrame.Shape.NoFrame)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.setItemDelegate(QuickTextDelegate(self.list_widget))
        self.list_widget.itemClicked.connect(self._onItemClicked)
        self.list_widget.itemDoubleClicked.connect(self._onItemClicked)
        main_layout.addWidget(self.list_widget, 1)

        self.search_edit.installEventFilter(self)

    def _populate(self, items=None):
        self.list_widget.clear()
        source = self._items if items is None else items
        for entry in source:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self.list_widget.addItem(item)
        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)

    def _filter(self, text):
        text = text.strip().lower()
        if not text:
            self._populate()
            return
        matched = []
        for entry in self._items:
            if (text in entry.get("text", "").lower()
                    or text in entry.get("note", "").lower()):
                matched.append(entry)
        self._populate(matched)

    def _acceptCurrent(self):
        current = self.list_widget.currentItem()
        if current:
            self._onItemClicked(current)

    def _onItemClicked(self, item):
        entry = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(entry, dict):
            text = entry.get("text", "")
        else:
            text = str(entry) if entry else ""
        if text:
            self.selected_text = text
            self.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self.focusWidget() is not self.search_edit:
                self._acceptCurrent()
        else:
            super().keyPressEvent(event)


class _AutoScrollTimer:
    def __init__(self, parent=None):
        self.parent = parent
        self._mouse_controller = mouse.Controller()
        self._thread = None
        self._stop_event = threading.Event()
        self.enabled = False
        self.speed = 0
        self._gen = 0

    def start(self, speed: int):
        self.stop()
        self.speed = speed
        self._gen += 1
        gen = self._gen
        self.enabled = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, args=(gen,), daemon=True)
        self._thread.start()

    def stop(self):
        self.enabled = False
        self._stop_event.set()
        self._thread = None

    _FRAME_RATE = 120

    def _run(self, gen):
        scroll_per_frame = self.speed / self._FRAME_RATE
        frame_interval = 1.0 / self._FRAME_RATE
        while self.enabled and not self._stop_event.is_set() and gen == self._gen:
            self._mouse_controller.scroll(0, -scroll_per_frame)
            self._stop_event.wait(frame_interval)
