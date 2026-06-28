import os
import re
import json
import threading
from pathlib import Path
from typing import Dict, List, Optional

from pynput import keyboard, mouse
from PySide6.QtCore import Qt, QTimer, QThread, Signal
from PySide6.QtGui import QAction, QTextCursor
from PySide6.QtWidgets import QApplication, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QComboBox, QCheckBox, QWidget, QStackedWidget, QScrollArea, QSpinBox, QListWidget, QListWidgetItem, QTreeWidget, QTreeWidgetItem, QMenu, QFormLayout, QStyle, QAbstractSpinBox

from src.plugin import PluginBase
from src.util import logger, root, data_dir, tr, BINARY_EXTENSIONS, messageBox, getFilePath, FileDrop, fileHash, showFile, ClipboardMonitor, formatFileSize

from src.core.timer import TimerManager
from src.core.input import GlobalHotkeyListener
from src.file import FileSelect

_MAX_SEARCH_FILE_SIZE = 10 * 1024 * 1024

cache_file = data_dir / "MD5.json"
copy_file = data_dir / "copy.txt"

class ToolBox(PluginBase):

    version = "1.0.0"
    description = "工具箱"
    file = [cache_file, copy_file]

    def __init__(self, main_window):
        super().__init__(main_window)
        self.settings = {
            "copy.target_file": "data/copy.txt",
            "search.paths": [],
            "search.case_sensitive": False,
            "search.regex": False,
            "search.close_delay": 3,
            "duplicate.paths": [],
            "duplicate.exclude": ["*.pyc", "*/__pycache__/", "*/.git/"],
            "enter.interval": 3,
            "enter.digit_control": True,
            "click.interval": 3,
            "scroll.speed": 50
        }
        self._scroll_timer = None
        self._enter_mgr = None
        self._copy_mgr = None
        self._search_mgr = None
        self._duplicate_mgr = None
        self._click_mgr = None
        self._dup_finder = None
        self._dup_finder_gen = 0

    def initialize(self):
        if not super().initialize():
            return
        self._scroll_timer = _AutoScrollTimer(self.main_window)
        self._copy_mgr = _AutoCopyManager()
        self._copy_mgr.init_monitor(self.main_window)
        self._search_mgr = _AutoSearchManager(self.main_window)
        self._search_mgr.init_monitor(self.main_window)
        self._enter_mgr = _AutoEnterManager(self.main_window)
        self._click_mgr = _AutoClickManager(self.main_window)

    def cleanup(self):
        if self._scroll_timer:
            self._scroll_timer.stop()
        if self._copy_mgr:
            self._copy_mgr.set_enabled(False)
        if self._search_mgr:
            self._search_mgr.set_enabled(False)
        if self._enter_mgr:
            self._enter_mgr.set_enabled(False)
        if self._click_mgr:
            self._click_mgr.set_enabled(False)

    def getAction(self):
        menu = QMenu(self.description, self.main_window)

        menu.addAction("批量重命名", self._batch_rename)
        menu.addAction("查找重复文件", self._find_duplicates)
        menu.addAction("快速粘贴", self._quick_paste)

        self._scroll_action = menu.addAction("自动滑动")
        self._scroll_action.triggered.connect(self._toggle_scroll)

        self._copy_action = menu.addAction("自动复制")
        self._copy_action.triggered.connect(self._toggle_copy)

        self._search_action = menu.addAction("自动搜索")
        self._search_action.triggered.connect(self._toggle_search)

        self._enter_action = menu.addAction("自动回车")
        self._enter_action.triggered.connect(self._toggle_enter)

        self._click_action = menu.addAction("自动点击")
        self._click_action.triggered.connect(self._toggle_click)

        menu.addSeparator()

        menu.addAction("工具箱设置", self._show_settings)

        return menu

    def _toggle_scroll(self):
        self.initialize()
        if self._scroll_timer.enabled:
            self._scroll_timer.stop()
            logger.info("自动滑动已停止")
        else:
            speed = self.settings.get("scroll.speed", 50)
            self._scroll_timer.start(speed)
            logger.info(f"自动滑动已启动 (速度: {speed})")

    def _toggle_copy(self):
        self.initialize()
        if self._copy_mgr.enabled:
            self._copy_mgr.set_enabled(False)
            logger.info("自动复制已停止")
        else:
            self._copy_mgr.target_file = self.settings.get("copy.target_file", "data/copy.txt")
            self._copy_mgr.set_enabled(True)
            logger.info("自动复制已启动")

    def _toggle_search(self):
        self.initialize()
        if self._search_mgr.enabled:
            self._search_mgr.set_enabled(False)
            logger.info("自动搜索已停止")
        else:
            self._search_mgr.search_paths = self.settings.get("search.paths", [])
            self._search_mgr.case_sensitive = self.settings.get("search.case_sensitive", False)
            self._search_mgr.regex = self.settings.get("search.regex", False)
            self._search_mgr.close_delay = self.settings.get("search.close_delay", 3)
            self._search_mgr.set_enabled(True)
            if not self._search_mgr.search_paths:
                logger.info("自动搜索已启动（未设置搜索路径）")
            else:
                logger.info("自动搜索已启动")

    def _toggle_enter(self):
        self.initialize()
        if self._enter_mgr.enabled:
            self._enter_mgr.set_enabled(False)
            logger.info("自动回车已停止")
        else:
            self._enter_mgr.interval = self.settings.get("enter.interval", 3)
            self._enter_mgr._digit_control = self.settings.get("enter.digit_control", True)
            self._enter_mgr.set_enabled(True)
            logger.info(f"自动回车已启动（间隔: {self.settings.get('enter.interval', 3)}秒）")

    def _toggle_click(self):
        self.initialize()
        if self._click_mgr.enabled:
            self._click_mgr.set_enabled(False)
            logger.info("自动点击已停止")
        else:
            self._click_mgr.interval = self.settings.get("click.interval", 3)
            self._click_mgr._digit_control = True
            self._click_mgr.set_enabled(True)
            logger.info(f"自动点击已启动（间隔: {self.settings.get('click.interval', 3)}秒）")

    def _batch_rename(self):
        dialog = BatchRenameDialog(self.main_window)
        dialog.exec()

    def _find_duplicates(self):
        editor = self._ensure_editor()
        if not editor:
            return

        if self._dup_finder:
            self._dup_finder.cancel()
            if self._dup_finder.isRunning():
                self._dup_finder.wait()

        self._dup_finder_gen += 1
        gen = self._dup_finder_gen

        default_paths = self.settings.get("duplicate.paths", [])
        default_exclude = self.settings.get("duplicate.exclude", ["*.pyc", "*/__pycache__/", "*/.git/"])
        result = FileSelect.select(editor, default_paths, default_exclude)
        if not result:
            return
        files, paths, rules = result
        self.settings["duplicate.paths"] = paths
        self.settings["duplicate.exclude"] = rules
        self.saveConfig()
        logger.info("正在扫描重复文件...")
        finder = DuplicateFinder(files, folder_path=paths[0] if paths else None)
        finder.progress.connect(lambda c, t: logger.info(f"正在扫描: {c}/{t}"))
        finder.finished.connect(lambda dups, g=gen: self._on_dup_finished(editor, dups, g))
        finder.error.connect(lambda err: messageBox(editor, "错误", f"扫描失败: {err}", 1))
        finder.start()
        self._dup_finder = finder

    def _on_dup_finished(self, editor, duplicates: dict, gen: int = 0):
        if gen != self._dup_finder_gen:
            return
        logger.info("扫描完成")
        if not duplicates:
            messageBox(editor, "查找结果", "未找到重复文件", 1)
            return
        folder_mgr = getattr(editor, '_folder_panel_manager', None)
        mgr = DuplicatePanelManager(editor, editor.splitter, None, editor, folder_mgr)
        mgr.show_duplicates(duplicates)
        fw = folder_mgr._folder_panel_width if folder_mgr and folder_mgr.is_visible() else 0
        mgr.set_sizes(editor.width(), fw)
        self._duplicate_mgr = mgr

    def _quick_paste(self):
        text = GlobalHotkeyListener._placeholders.get("Select", "")
        if not text:
            return
        mw = self.main_window
        activate = getattr(mw, 'activateWindow', None)
        raise_fn = getattr(mw, 'raise_', None)
        get_ed = getattr(mw, 'get_current_editor', None)
        if not get_ed:
            return
        if activate:
            activate()
        if raise_fn:
            raise_fn()
        editor = get_ed()
        if not editor:
            return
        editor.text_edit.setFocus()
        cursor = editor.text_edit.textCursor()
        cursor.insertText(text)

    def _show_settings(self):
        self.initialize()
        dialog = ToolBoxSettings(self.settings, self.main_window)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            old_speed = self.settings.get("scroll.speed", 50)
            self.settings.update(dialog.getSetting())
            new_speed = self.settings.get("scroll.speed", 50)
            if self._scroll_timer.enabled and old_speed != new_speed:
                self._scroll_timer.stop()
                self._scroll_timer.start(new_speed)
            self.saveConfig()

    def _ensure_editor(self):
        mw = self.main_window
        if hasattr(mw, 'get_current_editor'):
            return mw
        if hasattr(mw, '_editor_window') and mw._editor_window:
            return mw._editor_window
        if hasattr(mw, '_open_editor'):
            return mw._open_editor()
        return None

class FileSearcher:
    def __init__(self, search_text: str, case_sensitive: bool = False, regex: bool = False):
        self.search_text = search_text
        self.case_sensitive = case_sensitive
        self.regex = regex

    def search_files(self, paths: List[str], abort_check=None) -> List[dict]:
        results = []
        for path in paths:
            if abort_check and abort_check():
                break
            if not os.path.exists(path):
                continue
            if os.path.isfile(path):
                results.extend(self._search_file(path))
            elif os.path.isdir(path):
                results.extend(self._search_directory(path, abort_check))
        return results

    def _search_file(self, file_path: str) -> List[dict]:
        results = []
        abs_path = os.path.abspath(file_path)
        try:
            if os.path.getsize(file_path) > _MAX_SEARCH_FILE_SIZE:
                return []
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                for line_num, line in enumerate(f, 1):
                    matches = self._find_matches(line)
                    if matches:
                        results.append({
                            "file": abs_path,
                            "line": line_num,
                            "content": line.strip(),
                            "matches": matches
                        })
        except Exception:
            logger.exception(f"搜索文件失败 {file_path}")
        return results

    def _search_directory(self, dir_path: str, abort_check=None) -> List[dict]:
        results = []
        for root_dir, dirs, files in os.walk(dir_path):
            if abort_check and abort_check():
                break
            for file in files:
                if self._is_text_file(file):
                    file_path = os.path.join(root_dir, file)
                    results.extend(self._search_file(file_path))
        return results

    @staticmethod
    def _is_text_file(filename: str) -> bool:
        ext = os.path.splitext(filename)[1].lower()
        return ext not in BINARY_EXTENSIONS or filename in {'Makefile', 'Dockerfile', 'Vagrantfile'}

    def _find_matches(self, line: str) -> List[str]:
        matches = []
        search_text = self.search_text
        if self.regex:
            try:
                flags = 0 if self.case_sensitive else re.IGNORECASE
                for match in re.finditer(search_text, line, flags):
                    matches.append(match.group())
            except re.error:
                pass
        else:
            if self.case_sensitive:
                if search_text in line:
                    matches.append(search_text)
            else:
                if search_text.lower() in line.lower():
                    matches.append(search_text)
        return matches


class SearchWorkerThread(QThread):
    finished = Signal(list)
    error = Signal(str)

    def __init__(self, search_text: str, search_paths: List[str],
                 case_sensitive: bool, regex: bool):
        super().__init__()
        self._searcher = FileSearcher(search_text, case_sensitive, regex)
        self.search_paths = search_paths

    def run(self):
        try:
            results = self._searcher.search_files(
                self.search_paths, abort_check=self.isInterruptionRequested
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

    def apply_find_replace(self, find_text: str, replace_text: str, case_sensitive: bool = True):
        if not find_text:
            return
        name = self.original_name if self.is_directory else self._name_without_ext
        if case_sensitive:
            self.new_name = name.replace(find_text, replace_text)
        else:
            self.new_name = re.sub(re.escape(find_text), replace_text, name, flags=re.IGNORECASE)
        if not self.is_directory:
            self.new_name += self._extension

    def apply_prefix(self, prefix: str):
        if self.is_directory:
            self.new_name = prefix + self.original_name
        else:
            self.new_name = prefix + self._name_without_ext + self._extension

    def apply_suffix(self, suffix: str):
        if self.is_directory:
            self.new_name = self.original_name + suffix
        else:
            self.new_name = self._name_without_ext + suffix + self._extension

    def apply_numbering(self, start: int = 1, step: int = 1, position: str = "prefix", padding: int = 3):
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

    def get_new_path(self) -> str:
        return os.path.join(os.path.dirname(self.original_path), self.new_name)


class BatchRenameDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.folder_path = ""
        self.rename_items: list[RenameItem] = []
        self._preview_timer = QTimer()
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._do_preview)
        self.init_ui()
        self.setMinimumSize(700, 500)

    def init_ui(self):
        self.setWindowTitle("批量重命名")
        layout = QVBoxLayout(self)

        folder_layout = QHBoxLayout()

        self.folder_label = FileDrop()
        self.folder_label.folderDropped.connect(self.on_folder_dropped)
        self.folder_label.fileDropped.connect(self.on_file_dropped)
        self.folder_label.filesDropped.connect(self.on_files_dropped)
        self.folder_label.reset_style()
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
        self.mode_combo.currentTextChanged.connect(self.on_mode_changed)
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
        self.select_folder_btn = QPushButton("选择文件夹")
        self.select_folder_btn.clicked.connect(self.select_folder)
        btn_layout.addWidget(self.select_folder_btn)
        self.execute_btn = QPushButton("执行重命名")
        self.execute_btn.clicked.connect(self.execute_rename)
        self.execute_btn.setEnabled(False)
        btn_layout.addWidget(self.execute_btn)
        self.clear_btn = QPushButton("清空")
        self.clear_btn.clicked.connect(self.clear_items)
        btn_layout.addWidget(self.clear_btn)
        self.close_btn = QPushButton("关闭")
        self.close_btn.clicked.connect(self.close)
        btn_layout.addWidget(self.close_btn)
        layout.addLayout(btn_layout)

        self.on_mode_changed("查找替换")
        self.find_edit.textChanged.connect(self.apply_preview)
        self.replace_edit.textChanged.connect(self.apply_preview)
        self.case_sensitive_check.stateChanged.connect(self.apply_preview)
        self.start_spin.valueChanged.connect(self.apply_preview)
        self.step_spin.valueChanged.connect(self.apply_preview)
        self.position_combo.currentTextChanged.connect(self.apply_preview)
        self.padding_spin.valueChanged.connect(self.apply_preview)
        self.prefix_edit.textChanged.connect(self.apply_preview)
        self.suffix_edit.textChanged.connect(self.apply_preview)

    def on_folder_dropped(self, folder_path: str):
        self.folder_path = folder_path
        self.folder_label.set_folder_path(folder_path)
        self.load_folder(folder_path)

    def on_file_dropped(self, file_path: str):
        self.folder_path = os.path.dirname(file_path)
        self.folder_label.set_folder_path(self.folder_path)
        self.rename_items = [RenameItem(file_path)]
        self.file_count_label.setText("文件数: 1")
        self.apply_preview()
        self.execute_btn.setEnabled(True)

    def on_files_dropped(self, files: list):
        self.folder_path = os.path.dirname(files[0])
        self.folder_label.set_folder_path(self.folder_path)
        self.rename_items = [RenameItem(f) for f in files]
        self.file_count_label.setText(f"文件数: {len(files)}")
        self.apply_preview()
        self.execute_btn.setEnabled(len(self.rename_items) > 0)

    def select_folder(self):
        folder = getFilePath(self, "选择文件夹", mode="dir")
        if folder:
            self.folder_path = folder
            self.folder_label.set_folder_path(folder)
            self.load_folder(folder)

    def load_folder(self, folder_path: str):
        self.rename_items = []
        for root_dir, dirs, files in os.walk(folder_path):
            for f in files:
                self.rename_items.append(RenameItem(os.path.join(root_dir, f)))
        self.file_count_label.setText(f"文件数: {len(self.rename_items)}")
        self.apply_preview()
        self.execute_btn.setEnabled(len(self.rename_items) > 0)

    def on_mode_changed(self, mode: str):
        idx = {"查找替换": 0, "数字排序": 1, "固定前缀": 2, "固定后缀": 3}.get(mode, 0)
        self.mode_options_stack.setCurrentIndex(idx)
        if self.rename_items:
            self.apply_preview()

    def apply_preview(self):
        self._preview_timer.start(150)

    def _do_preview(self):
        if not self.rename_items:
            self.update_list()
            return
        mode = self.mode_combo.currentText()
        for item in self.rename_items:
            item.new_name = item.original_name
        if mode == "查找替换":
            ft = self.find_edit.text()
            rt = self.replace_edit.text()
            cs = self.case_sensitive_check.isChecked()
            for item in self.rename_items:
                item.apply_find_replace(ft, rt, cs)
        elif mode == "数字排序":
            start = self.start_spin.value()
            step = self.step_spin.value()
            pos = {"前缀": "prefix", "后缀": "suffix", "替换名字": "replace"}.get(
                self.position_combo.currentText(), "prefix")
            padding = self.padding_spin.value()
            num = start
            for item in self.rename_items:
                item.apply_numbering(num, step, pos, padding)
                num += step
        elif mode == "固定前缀":
            pf = self.prefix_edit.text()
            for item in self.rename_items:
                item.apply_prefix(pf)
        elif mode == "固定后缀":
            sf = self.suffix_edit.text()
            for item in self.rename_items:
                item.apply_suffix(sf)
        self.update_list()

    def update_list(self):
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

    def execute_rename(self):
        if not self.rename_items:
            return
        success = 0
        errors = 0
        error_files = []
        for item in self.rename_items:
            if item.original_name == item.new_name:
                continue
            try:
                os.rename(item.original_path, item.get_new_path())
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
            self.load_folder(self.folder_path)

    def clear_items(self):
        self.rename_items = []
        self.folder_path = ""
        self.folder_label.set_folder_path("拖拽文件夹到此处")
        self.folder_label.reset_style()
        self.file_count_label.setText("文件数: 0")
        self.update_list()
        self.execute_btn.setEnabled(False)


class DuplicateFinder(QThread):
    progress = Signal(int, int)
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, files: List[str] = None, parent=None, folder_path: str = None):
        super().__init__(parent)
        self.files = files or []
        self.folder_path = folder_path
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        try:
            result = self.find_duplicates(self.files, self.folder_path)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))

    def find_duplicates(self, files: List[str], folder_path: str = None) -> Dict[str, List[dict]]:
        cached_files = self._load_cache()
        new_or_modified = []
        current_files = {}

        if folder_path:
            current_files = self._build_file_tree(folder_path)
            new_or_modified = self._scan_new_or_modified(current_files, cached_files)
            self._remove_deleted(current_files, cached_files)

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
            self._save_cache(current_files)
        return duplicates

    def _load_cache(self) -> Dict:
        if not cache_file.exists():
            return {}
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_cache(self, files_dict: Dict):
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(files_dict, ensure_ascii=False, indent=2), encoding="utf-8")

    def _build_file_tree(self, folder_path: str) -> Dict:
        files = {}
        for root_dir, dirs, filenames in os.walk(folder_path):
            for fn in filenames:
                fp = os.path.join(root_dir, fn)
                try:
                    st = os.stat(fp)
                    files[fp] = {"size": st.st_size, "mtime": st.st_mtime, "md5": ""}
                except Exception:
                    continue
        return files

    def _scan_new_or_modified(self, current: Dict, cached: Dict) -> List[str]:
        result = []
        for fp, info in current.items():
            if fp not in cached:
                result.append(fp)
            elif info.get("size") != cached[fp].get("size") or \
                 abs(info.get("mtime", 0) - cached[fp].get("mtime", 0)) > 1:
                result.append(fp)
        return result

    def _remove_deleted(self, current: Dict, cached: Dict):
        deleted = set(cached.keys()) - set(current.keys())
        for p in deleted:
            del cached[p]


class DuplicatePanelManager:
    def __init__(self, parent, splitter, placeholder, main_window, folder_panel_manager=None):
        self.parent = parent
        self.splitter = splitter
        self.main_window = main_window
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
        self.tree.customContextMenuRequested.connect(self._show_tree_context_menu)
        self.tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self.tree)
        return self.panel

    def _show_tree_context_menu(self, pos):
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
                        to_trash.triggered.connect(lambda checked=False, p=fp: self._move_to_trash(p))
                        menu.addAction(to_trash)
        menu.exec_(self.tree.mapToGlobal(pos))

    def _move_to_trash(self, file_path: str):
        if self.folder_panel_manager:
            self.folder_panel_manager.move_to_trash(file_path)
        for md5, files in list(self._current_duplicates.items()):
            self._current_duplicates[md5] = [f for f in files if f["path"] != file_path]
            if not self._current_duplicates[md5]:
                del self._current_duplicates[md5]
        self.show_duplicates(self._current_duplicates)

    def _on_item_double_clicked(self, item, column):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data and data.get("type") == "file":
            fp = data.get("path")
            if fp and hasattr(self.parent, 'open_file_path'):
                self.parent.open_file_path(fp)

    def ensure_created(self):
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
                  if self.folder_panel_manager and self.folder_panel_manager.is_visible()
                  else 0)
            self.set_sizes(w, fw)

    def show_duplicates(self, duplicates: dict):
        self._current_duplicates = {md5: [dict(f) for f in files] for md5, files in duplicates.items()}
        self.ensure_created()
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
                del_btn.clicked.connect(lambda checked=False, p=fi["path"]: self._move_to_trash(p))
                self.tree.setItemWidget(file_item, 3, del_btn)
            group_item.setExpanded(True)
        self.show_panel_view()

    def show_panel_view(self):
        if self.panel is None:
            return
        self.panel.setMinimumWidth(200)
        self.panel.show()

    def set_sizes(self, available_width: int, folder_panel_width: int = 0):
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

    def is_visible(self) -> bool:
        return self.panel is not None

class ToolBoxSettings(QDialog):
    def __init__(self, settings: dict, parent=None):
        super().__init__(parent)
        self.settings = dict(settings)
        self.setWindowTitle("工具箱设置")
        self.setMinimumWidth(450)
        self.init_ui()

    def init_ui(self):
        layout = QFormLayout(self)

        scroll_speed = self.settings.get("scroll.speed", 50)
        self.scroll_speed = QSpinBox()
        self.scroll_speed.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.scroll_speed.setRange(1, 100)
        self.scroll_speed.setValue(scroll_speed)
        layout.addRow("滑动速度", self.scroll_speed)

        copy_path = self.settings.get("copy.target_file", "data/copy.txt")
        self.copy_path_edit = QLineEdit(copy_path)
        layout.addRow("自动复制目标", self.copy_path_edit)

        search_paths = self.settings.get("search.paths", [])
        self.search_paths_list = QListWidget()
        self.search_paths_list.setMaximumHeight(80)
        for p in search_paths:
            self.search_paths_list.addItem(os.path.normpath(p))
        layout.addRow("自动搜索路径", self.search_paths_list)

        btn_h = QHBoxLayout()
        add_btn = QPushButton("添加路径")
        add_btn.clicked.connect(self._add_search_path)
        rm_btn = QPushButton("移除选中")
        rm_btn.clicked.connect(self._remove_search_path)
        btn_h.addWidget(add_btn)
        btn_h.addWidget(rm_btn)
        btn_h.addStretch()
        layout.addRow("", btn_h)

        cs = self.settings.get("search.case_sensitive", False)
        self.case_check = QCheckBox("区分大小写")
        self.case_check.setChecked(cs)
        rx = self.settings.get("search.regex", False)
        self.regex_check = QCheckBox("正则表达式")
        self.regex_check.setChecked(rx)
        opt_row = QHBoxLayout()
        opt_row.addWidget(self.case_check)
        opt_row.addWidget(self.regex_check)
        opt_row.addStretch()
        layout.addRow("搜索选项", opt_row)

        delay = self.settings.get("search.close_delay", 3)
        self.close_delay = QSpinBox()
        self.close_delay.setRange(1, 60)
        self.close_delay.setSuffix(" 秒")
        self.close_delay.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.close_delay.setValue(delay)
        layout.addRow("弹窗显示时间", self.close_delay)

        enter_interval = self.settings.get("enter.interval", 3)
        self.enter_interval = QSpinBox()
        self.enter_interval.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.enter_interval.setRange(1, 9)
        self.enter_interval.setSuffix(" 秒")
        self.enter_interval.setValue(enter_interval)
        layout.addRow("自动回车间隔", self.enter_interval)

        digit_control = self.settings.get("enter.digit_control", True)
        self.enter_digit_control = QCheckBox("数字键控制间隔")
        self.enter_digit_control.setChecked(digit_control)
        layout.addRow("", self.enter_digit_control)

        click_interval = self.settings.get("click.interval", 3)
        self.click_interval = QSpinBox()
        self.click_interval.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.click_interval.setRange(1, 9)
        self.click_interval.setSuffix(" 秒")
        self.click_interval.setValue(click_interval)
        layout.addRow("自动点击间隔", self.click_interval)

        btn_row = QHBoxLayout()
        ok_btn = QPushButton("确定")
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addRow(btn_row)

    def _add_search_path(self):
        path = getFilePath(self, "选择搜索路径", mode="dir")
        if path:
            self.search_paths_list.addItem(path)

    def _remove_search_path(self):
        row = self.search_paths_list.currentRow()
        if row >= 0:
            self.search_paths_list.takeItem(row)

    def getSetting(self) -> dict:
        self.settings["scroll.speed"] = self.scroll_speed.value()
        self.settings["copy.target_file"] = os.path.normpath(self.copy_path_edit.text()) if self.copy_path_edit.text() else "data/copy.txt"
        self.settings["search.paths"] = [
            os.path.normpath(self.search_paths_list.item(i).text())
            for i in range(self.search_paths_list.count())
        ]
        self.settings["search.case_sensitive"] = self.case_check.isChecked()
        self.settings["search.regex"] = self.regex_check.isChecked()
        self.settings["search.close_delay"] = self.close_delay.value()
        self.settings["enter.interval"] = self.enter_interval.value()
        self.settings["enter.digit_control"] = self.enter_digit_control.isChecked()
        self.settings["click.interval"] = self.click_interval.value()
        return self.settings


class _AutoCopyManager:
    def __init__(self, parent=None):
        self.enabled = False
        self.target_file = "data/copy.txt"
        self._monitor = None

    def init_monitor(self, parent):
        self._monitor = ClipboardMonitor()
        self._monitor._callbacks.add(self._on_clipboard_change)
    
    def set_enabled(self, enabled: bool):
        self.enabled = enabled
        if enabled:
            self._monitor.start()
            logger.info("自动复制已启动")
        else:
            self._monitor.stop()
            logger.info("自动复制已停止")

    def _on_clipboard_change(self, text: str):
        if self.enabled and text:
            self.copy_to_file(text)

    def copy_to_file(self, text: str) -> bool:
        if not text:
            return False
        try:
            target = Path(self.target_file)
            if not target.is_absolute():
                target = root / self.target_file
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, 'a', encoding='utf-8') as f:
                if target.exists() and target.stat().st_size > 0:
                    f.write("\n\n\n")
                f.write(text)
            return True
        except Exception:
            logger.exception("自动复制失败")
            return False


class _AutoSearchManager:
    def __init__(self, parent=None):
        self.parent = parent
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

    def init_monitor(self, parent):
        self._monitor = ClipboardMonitor()
        self._monitor._callbacks.add(self._on_clipboard_change)
    
    def set_enabled(self, enabled: bool):
        self.enabled = enabled
        if enabled and self.search_paths:
            self._monitor.start()
            logger.info("自动搜索已启动")
        else:
            self._monitor.stop()
            logger.info("自动搜索已停止")

    def _on_clipboard_change(self, text: str):
        if self.enabled and text and self.search_paths:
            self._current_search_text = text
            if self._search_thread and self._search_thread.isRunning():
                self._search_thread.requestInterruption()
                self._search_thread.wait(3000)
            self._search_thread = SearchWorkerThread(
                text, self.search_paths, self.case_sensitive, self.regex
            )
            self._search_thread.finished.connect(
                lambda results, st=text: self._on_search_finished(results, st)
            )
            self._search_thread.error.connect(
                lambda err: logger.error(f"搜索失败: {err}")
            )
            self._search_thread.start()

    def _on_search_finished(self, results: List[dict], search_text: str):
        if results and search_text == self._current_search_text:
            self._show_popup(results, search_text)

    def _show_popup(self, results: List[dict], search_text: str):
        if self._popup:
            self._popup.close()
            self._popup = None
        mw = self.parent
        self._popup = QDialog(mw)
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

        def on_double_click(item):
            r = item.data(Qt.UserRole)
            if r:
                self._open_file(r['file'], r['line'])
                self._popup.close()

        result_list.itemDoubleClicked.connect(on_double_click)
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

    def _open_file(self, file_path: str, line: int):
        if not Path(file_path).exists():
            messageBox(self._popup, tr("错误"), tr("文件不存在") + file_path, 1)
            return
        mw = self.parent
        open_method = getattr(mw, 'open_file_path', None)
        if open_method:
            open_method(file_path)
            QTimer.singleShot(100, lambda: self._goto_line(mw, line))

    def _goto_line(self, mw, line: int):
        get_ed = getattr(mw, 'get_current_editor', None)
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

class _AutoEnterManager:
    def __init__(self, parent=None):
        self._tm = TimerManager()
        self.parent = parent
        self._timer = self._tm.create_timer(parent)
        self._timer.timeout.connect(self._press_enter)
        self.enabled = False
        self.interval = 3
        self._paused = False
        self._digit_control = True
        self._active_interval = 3
        self._keyboard_controller = keyboard.Controller()
        self._keyboard_listener = None

    def set_enabled(self, enabled: bool):
        self.enabled = enabled
        if enabled:
            self._paused = False
            self._start_listener()
            self._active_interval = self.interval
            self._timer.start(int(self.interval * 1000))
        else:
            self._paused = False
            self._stop_listener()
            self._timer.stop()

    def _press_enter(self):
        if self.interval != self._active_interval:
            self._active_interval = self.interval
            self._timer.stop()
            self._timer.start(int(self.interval * 1000))
        if not self._paused:
            try:
                self._keyboard_controller.press(keyboard.Key.enter)
                self._keyboard_controller.release(keyboard.Key.enter)
            except Exception:
                logger.exception("模拟回车键失败")

    def _start_listener(self):
        self._stop_listener()

        def on_press(key):
            try:
                if key == keyboard.Key.esc:
                    self._paused = True
                elif hasattr(key, 'char') and key.char and key.char.isdigit():
                    if self._digit_control:
                        d = int(key.char)
                        if 1 <= d <= 9:
                            self.interval = d
            except Exception:
                logger.exception("按键监听回调失败")

        self._keyboard_listener = keyboard.Listener(on_press=on_press)
        self._keyboard_listener.daemon = True
        self._keyboard_listener.start()

    def _stop_listener(self):
        if self._keyboard_listener:
            self._keyboard_listener.stop()
            self._keyboard_listener = None

class _AutoClickManager:
    def __init__(self, parent=None):
        self._tm = TimerManager()
        self.parent = parent
        self._timer = self._tm.create_timer(parent)
        self._timer.timeout.connect(self._do_click)
        self.enabled = False
        self.interval = 3
        self._paused = False
        self._digit_control = True
        self._active_interval = 3
        self._mouse_controller = mouse.Controller()
        self._keyboard_listener = None

    def set_enabled(self, enabled: bool):
        self.enabled = enabled
        if enabled:
            self._paused = False
            self._start_listener()
            self._active_interval = self.interval
            self._timer.start(int(self.interval * 1000))
        else:
            self._paused = False
            self._stop_listener()
            self._timer.stop()

    def _do_click(self):
        if self.interval != self._active_interval:
            self._active_interval = self.interval
            self._timer.stop()
            self._timer.start(int(self.interval * 1000))
        if not self._paused:
            try:
                self._mouse_controller.click(mouse.Button.left)
            except Exception:
                logger.exception("模拟鼠标点击失败")

    def _start_listener(self):
        self._stop_listener()

        def on_press(key):
            try:
                if key == keyboard.Key.esc:
                    self._paused = True
                elif hasattr(key, 'char') and key.char and key.char.isdigit():
                    if self._digit_control:
                        d = int(key.char)
                        if 1 <= d <= 9:
                            self.interval = d
            except Exception:
                logger.exception("按键监听回调失败")

        self._keyboard_listener = keyboard.Listener(on_press=on_press)
        self._keyboard_listener.daemon = True
        self._keyboard_listener.start()

    def _stop_listener(self):
        if self._keyboard_listener:
            self._keyboard_listener.stop()
            self._keyboard_listener = None


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
