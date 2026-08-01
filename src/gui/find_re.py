import json

from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit, QCheckBox, QPushButton, QLabel, QComboBox, QListWidget, QListWidgetItem
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence

from src.config import getConfig
from src.util import logger, dialogBox, messageBox, dictDialog, tr

class FindReplaceDialog(QDialog):
    """查找替换对话框"""

    find_requested = Signal(str, bool, bool, bool)
    replace_requested = Signal(str, str, bool, bool)
    replace_all_requested = Signal(str, str, bool, bool)

    def __init__(self, parent=None, config=None):
        super().__init__(parent)
        self.setWindowTitle(tr("查找与替换"))
        self.setMinimumWidth(450)
        self.config = config if config is not None else getConfig()
        if self.config:
            presets = self.config.get("Edit.find_presets", [])
            if isinstance(presets, list):
                self.presets = presets
            else:
                self.presets = []
        else:
            self.presets = []
        self.current_preset_rules = []
        self.initUI()

    def initUI(self):
        layout = QVBoxLayout(self)

        # 预设规则
        preset_layout = QHBoxLayout()
        preset_layout.addWidget(QLabel(tr("预设")))

        self.preset_combo = QComboBox()
        self.preset_combo.addItem(tr("不使用"), "")
        self._updatePresetCombo()
        self.preset_combo.currentIndexChanged.connect(self._onPresetChanged)
        preset_layout.addWidget(self.preset_combo)

        self.manage_btn = QPushButton(tr("管理"))
        self.manage_btn.setFixedWidth(70)
        self.manage_btn.clicked.connect(self._managePresets)
        preset_layout.addWidget(self.manage_btn)

        layout.addLayout(preset_layout)

        # 查找
        find_layout = QFormLayout()
        self.find_edit = QLineEdit()
        find_layout.addRow(tr("查找"), self.find_edit)

        self.case_check = QCheckBox(tr("区分大小写"))
        self.regex_check = QCheckBox(tr("正则表达式"))
        option_layout = QHBoxLayout()
        option_layout.addWidget(self.case_check, 1)
        option_layout.addWidget(self.regex_check, 1)
        find_layout.addRow("", option_layout)

        layout.addLayout(find_layout)

        # 替换
        replace_layout = QFormLayout()
        self.replace_edit = QLineEdit()
        self.replace_edit.setToolTip(tr("留空表示删除"))
        replace_layout.addRow(tr("替换"), self.replace_edit)
        layout.addLayout(replace_layout)

        # 按钮
        button_layout = QHBoxLayout()

        self.btn_prev = QPushButton(tr("上一个"))
        self.btn_prev.clicked.connect(self.onFindPrev)
        button_layout.addWidget(self.btn_prev)

        self.btn_next = QPushButton(tr("下一个"))
        self.btn_next.clicked.connect(self.onFindNext)
        button_layout.addWidget(self.btn_next)

        self.btn_replace = QPushButton(tr("替换"))
        self.btn_replace.clicked.connect(self.onReplace)
        button_layout.addWidget(self.btn_replace)

        self.btn_replace_all = QPushButton(tr("全部替换"))
        self.btn_replace_all.clicked.connect(self.onReplaceAll)
        button_layout.addWidget(self.btn_replace_all)

        layout.addLayout(button_layout)

        # 快捷键
        self.btn_next.setShortcut(QKeySequence("F3"))
        self.btn_prev.setShortcut(QKeySequence("Shift+F3"))
        self.find_edit.returnPressed.connect(self.onFindNext)
        self.replace_edit.returnPressed.connect(self.onReplaceAll)

    def _updatePresetCombo(self):
        while self.preset_combo.count() > 1:
            self.preset_combo.removeItem(1)
        for preset in self.presets:
            self.preset_combo.addItem(preset["name"], preset["value"])

    def _onPresetChanged(self, index):
        if index <= 0:
            self.current_preset_rules = []
            return
        value = self.preset_combo.currentData()
        try:
            rules = json.loads(value)
            if not isinstance(rules, list):
                raise ValueError("规则必须是列表")

            validated_rules = []
            for rule in rules:
                if isinstance(rule, list) and len(rule) == 2:
                    find_text = str(rule[0])
                    replace_text = str(rule[1])
                    if find_text:
                        validated_rules.append([find_text, replace_text])
                else:
                    raise ValueError("规则是 [查找, 替换] 格式")

            self.current_preset_rules = validated_rules
            if validated_rules:
                self.find_edit.setText(validated_rules[0][0])
                self.replace_edit.setText(validated_rules[0][1])
            else:
                self.find_edit.clear()
                self.replace_edit.clear()

        except (json.JSONDecodeError, ValueError, TypeError) as e:
            self.current_preset_rules = []
            self.find_edit.clear()
            self.replace_edit.clear()
            messageBox(self, tr("格式错误"), tr("预设规则格式不正确") + ":\n" + str(e) + "\n\n" + tr("正确格式示例") + ":\n" + '["find1","replace1"],["find2","replace2"]', 1)

    def _managePresets(self):
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("管理预设规则"))
        dialog.setMinimumSize(500, 350)

        layout = QVBoxLayout(dialog)

        pair_list = QListWidget()
        layout.addWidget(pair_list)

        for preset in self.presets:
            item = QListWidgetItem(preset["name"])
            item.setData(Qt.ItemDataRole.UserRole, preset["value"])
            pair_list.addItem(item)

        btn_layout = QHBoxLayout()
        add_btn = QPushButton(tr("新建"))
        edit_btn = QPushButton(tr("编辑"))
        delete_btn = QPushButton(tr("删除"))
        btn_layout.addWidget(add_btn)
        btn_layout.addWidget(edit_btn)
        btn_layout.addWidget(delete_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        def updateButton():
            has_selection = pair_list.currentItem() is not None
            edit_btn.setEnabled(has_selection)
            delete_btn.setEnabled(has_selection)

        pair_list.itemSelectionChanged.connect(updateButton)
        updateButton()

        def checkValue(value):
            try:
                rules = json.loads(value)
                if not isinstance(rules, list):
                    return False, tr("规则必须是列表")
                for rule in rules:
                    if not isinstance(rule, list) or len(rule) != 2:
                        return False, tr("规则是 [查找, 替换] 格式") + '["a","b"]'
                return True, tr("格式正确")
            except json.JSONDecodeError:
                return False, "JSON" + tr("格式错误")
            except Exception as e:
                return False, tr("验证错误") + ": " + str(e)

        def addItem():
            result = dictDialog(dialog, tr("新建"), name=tr("名称"), value=tr("规则"),
                                name_text="",
                                value_text='["find1","replace1"],["find2","replace2"]',
                                textedit=True)
            if result[0]:
                saved_value = f"[{result[1]}]"
                is_valid, message = checkValue(saved_value)
                if not is_valid:
                    messageBox(dialog, tr("格式错误"), tr("预设规则格式不正确") + f":\n{message}", 1)
                    return
                item = QListWidgetItem(result[0])
                item.setData(Qt.ItemDataRole.UserRole, saved_value)
                pair_list.addItem(item)

        def editItem():
            current = pair_list.currentItem()
            if not current:
                return
            raw = current.data(Qt.ItemDataRole.UserRole)
            display = raw[1:-1] if raw.startswith("[") else raw
            result = dictDialog(dialog, tr("编辑"), name=tr("名称"), value=tr("规则"),
                                name_text=current.text(),
                                value_text=display,
                                textedit=True)
            if result[0]:
                saved_value = f"[{result[1]}]"
                is_valid, message = checkValue(saved_value)
                if not is_valid:
                    messageBox(dialog, tr("格式错误"), tr("预设规则格式不正确") + f":\n{message}", 1)
                    return
                current.setText(result[0])
                current.setData(Qt.ItemDataRole.UserRole, saved_value)

        def deleteItem():
            current = pair_list.currentItem()
            if current:
                row = pair_list.row(current)
                pair_list.takeItem(row)

        add_btn.clicked.connect(addItem)
        edit_btn.clicked.connect(editItem)
        delete_btn.clicked.connect(deleteItem)

        if dialogBox(layout, dialog):
            self.presets = []
            for i in range(pair_list.count()):
                item = pair_list.item(i)
                self.presets.append({"name": item.text(), "value": item.data(Qt.ItemDataRole.UserRole)})
            if self.config:
                self.config.set("Edit.find_presets", self.presets)
                self.config.save()
            self._updatePresetCombo()

    def onFindNext(self):
        text = self.find_edit.text()
        if not text:
            return
        self.find_requested.emit(text, self.case_check.isChecked(),
            self.regex_check.isChecked(), True)

    def onFindPrev(self):
        text = self.find_edit.text()
        if not text:
            return
        self.find_requested.emit(text, self.case_check.isChecked(),
            self.regex_check.isChecked(), False)

    def onReplace(self):
        find_text = self.find_edit.text()
        if not find_text:
            return
        self.replace_requested.emit(find_text, self.replace_edit.text(),
            self.case_check.isChecked(),
            self.regex_check.isChecked())

    def onReplaceAll(self):
        if self.current_preset_rules and self.preset_combo.currentIndex() > 0:
            # 应用预设规则中的所有替换规则
            for rule in self.current_preset_rules:
                try:
                    if isinstance(rule, list) and len(rule) == 2:
                        find_text = str(rule[0])
                        replace_text = str(rule[1])
                    else:
                        continue

                    if find_text:  # 确保查找文本不为空
                        self.replace_all_requested.emit(
                            str(find_text), 
                            str(replace_text) if replace_text is not None else "",
                            self.case_check.isChecked(),
                            self.regex_check.isChecked()
                        )
                except Exception:
                    logger.exception("应用预设规则时出错")
        else:
            # 使用当前输入的单个规则
            find_text = self.find_edit.text()
            if not find_text:
                return
            self.replace_all_requested.emit(find_text, self.replace_edit.text(),
                self.case_check.isChecked(),
                self.regex_check.isChecked())

    def setFindText(self, text: str):
        self.find_edit.setText(text)
