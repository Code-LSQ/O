import os
import json

from PySide6.QtWidgets import QDockWidget, QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QTextBrowser, QPushButton, QTextEdit, QFrame, QComboBox, QLabel, QApplication, QFileDialog, QToolTip, QLineEdit, QDialog, QListWidget, QListWidgetItem, QFormLayout, QCheckBox, QSpinBox, QDoubleSpinBox, QAbstractSpinBox, QMenu, QProgressBar
from PySide6.QtGui import QAction, QDragEnterEvent, QDropEvent, QPixmap, QCursor, QTextCursor
from PySide6.QtCore import Qt, Signal, QThread, QTimer, QByteArray

from src.plugin import PluginBase
from src.util import data_dir, logger, getFilePath, messageBox, inputDialog, tr, dialogBox, getTimestamp, FileDrop, imageBase64, EXTENSION, urlToPath

from src.core.AI import AI_ADAPTER, getAIClient, resolveImageUrls, getAdapterEndpoint, getAdapterUrl, OllamaAdapter

AI_dir = data_dir / "AI"
AI_dir.mkdir(parents=True, exist_ok=True)
history_file = AI_dir / "ai.json"


def _contentToText(content):
    """将 AI 消息内容列表转换为纯文本"""
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, dict) and p.get("type") == "text":
                parts.append(p.get("text", ""))
            elif isinstance(p, dict) and p.get("type") == "image_url":
                url = p.get("url", "") or p.get("image_url", {}).get("url", "")
                if url.startswith("file://"):
                    parts.append(f"[图片: {os.path.basename(url[7:])}]")
                else:
                    parts.append("[图片]")
        return "\n".join(parts) if parts else "[图片]"
    return str(content)


class AIExtendPlugin(PluginBase):
    """AI Extension"""

    version = "1.0.0"
    description = "AI 扩展"
    file = [AI_dir]

    def __init__(self, main_window):
        super().__init__(main_window)
        self._launcher = main_window
        self.dock = None
        self._toggle_action = None
        self._panel = None
        self._message_scroll = None
        self._message_content = None
        self._message_layout = None
        self._last_ai_browser = None
        self.input_edit = None
        self.send_btn = None
        self.stream_thread = None
        self.current_response = ""
        self._history = {}
        self._current_conv_id = None
        self._profile_combo = None
        self._conv_combo = None
        self._pending_images: list[str] = []
        self._pending_files: list[str] = []
        self._preview_bar = None
        self._model_combo_updating = False
        self._standalone_window = None
        self._ai_capturing = False
        self._pending_ai_prompt = None
        self._current_ai_dialog = None


    def loadConfig(self):
        super().loadConfig()
        self.settings.setdefault("active", "默认配置")
        self.settings.setdefault("stream", False)
        self.settings.setdefault("profiles", {"默认配置": {
            "api_key": "", "model": "", "api_url": "https://api.deepseek.com",
            "custom_url": "https://api.deepseek.com", "temperature": 0.7, "max_tokens": 2000, "endpoint": "DeepSeek"
        }})
        self.settings.setdefault("dialog", "")
        self.settings.setdefault("load_balance", {"enabled": False, "profiles": {}})
        self.settings.setdefault("prompt", "请识别图片中的所有文字内容，直接输出识别到的文字，不需要额外说明。如果图片中没有文字，请回复'未识别到文字'。")
        self.settings.setdefault("prompts", {
            "系统提示词": "",
            "提取内容": "请提取以下内容中的关键信息，按条理清晰的结构输出，不需要额外解释。",
            "代码": "你是一位经验丰富的软件工程师，在多种编程语言、框架、设计模式和最佳实践方面拥有广泛的知识。请帮助我编写和优化以下代码。\n\n{request}",
            "翻译": "你是一名翻译，请将以下文本 {request} 翻译成中文，你只需要返回翻译结果，无需额外解释。",
            "写作": "你是一名作家，请帮助我改进以下文本 {request} 的流畅性和表达，不需要过多的修饰和形容词。"
        })

    def initialize(self):
        if not super().initialize():
            return
        self._loadHistory()
        self._ensureContent()

    def _get_editor_window(self):
        if hasattr(self._launcher, '_editor_window') and self._launcher._editor_window:
            return self._launcher._editor_window
        return None

    def getAction(self):
        menu = QMenu(self.description, self.main_window)

        menu.addAction("AI 设置", self._showSettingsDialog)
        menu.addAction("OCR", self.showOcrDialog)
        menu.addAction("面板", self._togglePanel)

        self._buildPromptsMenu(menu)

        return menu

    def cleanup(self):
        if not self._initialized:
            return
        super().cleanup()
        self._destroyDock()

    def runAiPrompt(self, name: str):
        """运行 AI 提示词"""
        if not name or self._ai_capturing:
            return
        self.initialize()
        self._ai_capturing = True
        self._pending_ai_prompt = name
        self.getSelect(self.selectionCapture)

    def selectionCapture(self, text):
        self._ai_capturing = False
        name = getattr(self, '_pending_ai_prompt', None)
        text = text.strip()
        if not text:
            messageBox(self._launcher, tr("提示"), tr("请先选中文本后再执行此操作"), 1)
            return

        mime = QApplication.clipboard().mimeData()
        if mime.hasUrls():
            for url in mime.urls():
                path = urlToPath(url)
                if not path:
                    continue
                if os.path.isfile(path):
                    messages = getAIClient(self.settings).buildFileMessage(path)
                    if messages:
                        self._openAiDialog(messages, name)
                    return
                if os.path.isdir(path):
                    messages = getAIClient(self.settings).buildFolderMessage(path)
                    if messages:
                        self._openAiDialog(messages, name)
                    return

        self._openAiDialog([{"role": "user", "content": text}], name)

    def _openAiDialog(self, messages, prompt_name):
        stream = self.settings.get("stream", False)
        geometry = self.settings.get("dialog", "")
        def onGeometrySave(geo):
            self.settings["dialog"] = geo
            self.saveConfig()
        dialog = AIDialog(messages, prompt_name, stream=stream,
                          dialog=geometry, main_window=self._launcher,
                          onGeometrySave=onGeometrySave,
                          config=self.settings)
        if self._launcher:
            dialog.setStyleSheet(self._launcher.styleSheet())
        if self._current_ai_dialog is not None:
            self._current_ai_dialog.close()
        self._current_ai_dialog = dialog
        dialog.finished.connect(lambda _: setattr(self, '_current_ai_dialog', None))
        dialog.show()

    def showOcrDialog(self):
        self.initialize()
        dlg = OCRDialog(self._launcher, self)
        dlg.show()

    def _buildAiClient(self, profile_name=None):
        name = profile_name or self.settings.get("active", "默认配置")
        return getAIClient(self.settings, profile_name=name)

    def _buildPromptsMenu(self, menu):
        prompts = self.settings.get("prompts", {})
        names = [n for n in prompts if n != "系统提示词"]

        if names:
            for name in names:
                act = QAction(name, self.main_window)
                act.triggered.connect(lambda checked, n=name: self.runAiPrompt(n))
                menu.addAction(act)

    # ── AI 设置 ──

    def _showSettingsDialog(self):
        """打开 AI 设置对话框"""
        dlg = QDialog(self._launcher)
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        dlg.setWindowTitle("AI " + tr("设置"))
        dlg.setMinimumSize(500, 500)
        layout = QVBoxLayout(dlg)

        tab = self._buildSettingsTab(dlg)
        layout.addWidget(tab, 1)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        ok_btn = QPushButton(tr("确定"))
        ok_btn.clicked.connect(lambda: self._saveSettings(dlg))
        btn_layout.addWidget(ok_btn)
        cancel_btn = QPushButton(tr("取消"))
        cancel_btn.clicked.connect(dlg.reject)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

        dlg.exec()

    def _buildSettingsTab(self, parent):
        """构建 AI 设置界面"""
        tab = QWidget(parent)
        layout = QVBoxLayout(tab)

        # 暂存当前配置用于编辑
        self._edit_profiles = {k: dict(v) for k, v in self.settings.get("profiles", {"默认配置": {}}).items()}
        self._edit_load_balance = {k: v for k, v in self.settings.get("load_balance", {"enabled": False, "profiles": {}}).items()}
        self._edit_prompts = dict(self.settings.get("prompts", {}))
        active = self.settings.get("active", "")
        if not active and self._edit_profiles:
            active = list(self._edit_profiles.keys())[0]
        self._edit_active = active

        # 配置选择区
        profile_row = QHBoxLayout()
        profile_row.addWidget(QLabel(tr("当前配置")))
        self._settings_profile_combo = QComboBox()
        self._settings_profile_combo.setFixedWidth(160)
        for name in self._edit_profiles:
            self._settings_profile_combo.addItem(name, name)
        idx = self._settings_profile_combo.findText(self._edit_active)
        if idx >= 0:
            self._settings_profile_combo.setCurrentIndex(idx)
        self._settings_profile_combo.currentIndexChanged.connect(self._onSettingsProfileChanged)
        profile_row.addWidget(self._settings_profile_combo)

        rename_btn = QPushButton(tr("重命名"))
        rename_btn.setFixedWidth(70)
        rename_btn.clicked.connect(lambda: self._settingsRenameProfile(parent))
        profile_row.addWidget(rename_btn)

        new_btn = QPushButton(tr("新建"))
        new_btn.setFixedWidth(60)
        new_btn.clicked.connect(lambda: self._settingsNewProfile(parent))
        profile_row.addWidget(new_btn)

        copy_btn = QPushButton(tr("复制"))
        copy_btn.setFixedWidth(60)
        copy_btn.clicked.connect(lambda: self._settingsCopyProfile(parent))
        profile_row.addWidget(copy_btn)

        delete_btn = QPushButton(tr("删除"))
        delete_btn.setFixedWidth(60)
        delete_btn.clicked.connect(lambda: self._settingsDeleteProfile(parent))
        profile_row.addWidget(delete_btn)

        profile_row.addStretch()
        layout.addLayout(profile_row)

        # API 端点
        endpoint_layout = QFormLayout()
        self._settings_endpoint_combo = QComboBox()
        for name, cls, url in AI_ADAPTER:
            self._settings_endpoint_combo.addItem(name, url)
        endpoint_layout.addRow("API " + tr("接口"), self._settings_endpoint_combo)

        self._settings_endpoint_combo.currentTextChanged.connect(self._onSettingsEndpointChanged)
        self._settings_custom_url_edit = QLineEdit()
        self._settings_custom_url_edit.setPlaceholderText(tr("输入API地址"))
        endpoint_layout.addRow(tr("接口地址"), self._settings_custom_url_edit)
        layout.addLayout(endpoint_layout)

        # API Key
        key_layout = QHBoxLayout()
        key_layout.addWidget(QLabel("API Key"))
        self._settings_api_key_edit = QLineEdit()
        self._settings_api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        key_layout.addWidget(self._settings_api_key_edit)
        layout.addLayout(key_layout)

        # 模型
        model_layout = QHBoxLayout()
        model_layout.addWidget(QLabel(tr("模型")))
        self._settings_model_combo = QComboBox()
        self._settings_model_combo.setEditable(True)
        self._settings_model_combo.setMinimumWidth(300)
        model_layout.addWidget(self._settings_model_combo)
        self._settings_refresh_btn = QPushButton(tr("刷新"))
        self._settings_refresh_btn.setFixedWidth(70)
        self._settings_refresh_btn.clicked.connect(lambda: self._settingsRefreshModels(parent))
        model_layout.addWidget(self._settings_refresh_btn)
        model_layout.addStretch()
        layout.addLayout(model_layout)

        # 参数
        param_row = QHBoxLayout()
        left = QHBoxLayout()
        left.addWidget(QLabel(tr("温度")))
        self._settings_temp_spin = QDoubleSpinBox()
        self._settings_temp_spin.setRange(0.0, 1.0)
        self._settings_temp_spin.setSingleStep(0.1)
        self._settings_temp_spin.setFixedWidth(120)
        self._settings_temp_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        left.addWidget(self._settings_temp_spin)
        left.addStretch()
        param_row.addLayout(left)
        right = QHBoxLayout()
        right.addStretch()
        right.addWidget(QLabel(tr("最大Token")))
        self._settings_max_tokens_spin = QSpinBox()
        self._settings_max_tokens_spin.setRange(100, 10000)
        self._settings_max_tokens_spin.setFixedWidth(120)
        self._settings_max_tokens_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        right.addWidget(self._settings_max_tokens_spin)
        param_row.addLayout(right)
        layout.addLayout(param_row)

        # 流式输出 + 按钮行
        action_row = QHBoxLayout()
        self._settings_stream_cb = QCheckBox(tr("流式输出"))
        self._settings_stream_cb.setChecked(self.settings.get("stream", False))
        action_row.addWidget(self._settings_stream_cb)
        action_row.addStretch()
        balance_btn = QPushButton(tr("负载均衡"))
        balance_btn.clicked.connect(lambda: self._settingsShowBalance(parent))
        action_row.addWidget(balance_btn)
        test_btn = QPushButton(tr("测试"))
        test_btn.clicked.connect(lambda: self._settingsTest(parent))
        action_row.addWidget(test_btn)
        layout.addLayout(action_row)

        self._settings_prompt_list = QListWidget()
        self._settings_prompt_list.setMaximumHeight(120)
        layout.addWidget(self._settings_prompt_list)

        prompt_btn_row = QHBoxLayout()
        add_prompt_btn = QPushButton(tr("添加"))
        add_prompt_btn.clicked.connect(self._settingsPromptAdd)
        prompt_btn_row.addWidget(add_prompt_btn)
        edit_prompt_btn = QPushButton(tr("编辑"))
        edit_prompt_btn.clicked.connect(self._settingsPromptEdit)
        prompt_btn_row.addWidget(edit_prompt_btn)
        delete_prompt_btn = QPushButton(tr("删除"))
        delete_prompt_btn.clicked.connect(self._settingsPromptDelete)
        prompt_btn_row.addWidget(delete_prompt_btn)
        prompt_btn_row.addStretch()
        layout.addLayout(prompt_btn_row)

        layout.addStretch()

        # 加载当前配置
        self._settingsLoadProfile()
        self._settingsReloadPromptList()

        return tab

    def _onSettingsProfileChanged(self, index):
        profile_name = self._settings_profile_combo.currentText()
        if not profile_name:
            return
        self._edit_active = profile_name
        self._settingsLoadProfile()

    def _onSettingsEndpointChanged(self, text):
        if text == "自定义":
            self._settings_custom_url_edit.clear()
            self._settings_custom_url_edit.setPlaceholderText(tr("输入自定义API地址"))
        else:
            for name, cls, url in AI_ADAPTER:
                if name == text:
                    self._settings_custom_url_edit.setText(url)
                    break

    def _settingsLoadProfile(self):
        profile = self._edit_profiles.get(self._edit_active, {})
        self._settings_api_key_edit.setText(profile.get("api_key", ""))

        endpoint_name = profile.get("endpoint", "")
        idx = self._settings_endpoint_combo.findText(endpoint_name)
        if idx >= 0:
            self._settings_endpoint_combo.setCurrentIndex(idx)
        else:
            self._settings_endpoint_combo.setCurrentText("自定义")

        if self._settings_endpoint_combo.currentText() == "自定义":
            self._settings_custom_url_edit.setText(profile.get("custom_url", ""))
        else:
            for name, cls, url in AI_ADAPTER:
                if name == self._settings_endpoint_combo.currentText():
                    self._settings_custom_url_edit.setText(url)
                    break

        self._settings_model_combo.clear()
        model = profile.get("model", "")
        if model:
            self._settings_model_combo.addItem(model)
        self._settings_temp_spin.setValue(profile.get("temperature", 0.7))
        self._settings_max_tokens_spin.setValue(profile.get("max_tokens", 2000))

    def _settingsSaveProfile(self):
        endpoint_name = self._settings_endpoint_combo.currentText()
        api_url = self._settings_custom_url_edit.text().strip()
        self._edit_profiles[self._edit_active] = {
            "api_key": self._settings_api_key_edit.text(),
            "api_url": api_url,
            "custom_url": api_url,
            "model": self._settings_model_combo.currentText(),
            "temperature": self._settings_temp_spin.value(),
            "max_tokens": self._settings_max_tokens_spin.value(),
            "endpoint": endpoint_name,
        }

    def _settingsRenameProfile(self, parent):
        old_name = self._settings_profile_combo.currentText()
        if not old_name:
            return
        new_name = inputDialog(parent, tr("重命名配置"), tr("新名称"), default=old_name)
        if new_name and new_name != old_name:
            self._edit_profiles[new_name] = self._edit_profiles.pop(old_name)
            self._edit_active = new_name
            idx = self._settings_profile_combo.findText(old_name)
            if idx >= 0:
                self._settings_profile_combo.setItemText(idx, new_name)
                self._settings_profile_combo.setCurrentIndex(idx)

    def _settingsNewProfile(self, parent):
        name = inputDialog(parent, tr("新建配置"), tr("配置名称"), default="新配置")
        if name:
            self._edit_profiles[name] = {
                "api_key": "", "model": "", "api_url": "https://api.deepseek.com",
                "custom_url": "https://api.deepseek.com", "temperature": 0.7, "max_tokens": 2000, "endpoint": "DeepSeek"
            }
            self._edit_active = name
            self._settings_profile_combo.addItem(name, name)
            self._settings_profile_combo.setCurrentIndex(self._settings_profile_combo.count() - 1)

    def _settingsCopyProfile(self, parent):
        current = self._settings_profile_combo.currentText()
        if not current or current not in self._edit_profiles:
            return
        name = inputDialog(parent, tr("复制配置"), tr("新配置名称"), default=current + " - 副本")
        if name:
            self._edit_profiles[name] = dict(self._edit_profiles[current])
            self._edit_active = name
            self._settings_profile_combo.addItem(name, name)
            self._settings_profile_combo.setCurrentIndex(self._settings_profile_combo.count() - 1)

    def _settingsDeleteProfile(self, parent):
        current = self._settings_profile_combo.currentText()
        if not current:
            return
        if len(self._edit_profiles) <= 1:
            messageBox(parent, tr("警告"), tr("至少保留一个配置，不能删除"), 1)
            return
        if messageBox(parent, tr("确认删除"), f"{tr('确定要删除配置')} \"{current}\" {tr('吗')}？"):
            self._edit_profiles.pop(current, None)
            self._settings_profile_combo.removeItem(self._settings_profile_combo.currentIndex())
            if self._edit_profiles:
                self._edit_active = list(self._edit_profiles.keys())[0]
                idx = self._settings_profile_combo.findText(self._edit_active)
                if idx >= 0:
                    self._settings_profile_combo.setCurrentIndex(idx)

    def _settingsRefreshModels(self, parent):
        self._settingsSaveProfile()
        profile = self._edit_profiles[self._edit_active]
        api_key = profile.get("api_key", "")
        api_url = profile.get("api_url", "")
        endpoint_name = profile.get("endpoint", "")
        if not api_url:
            messageBox(parent, tr("警告"), tr("请先设置 API 地址"), 1)
            return
        is_ollama = isinstance(getAdapterUrl(api_url, self.main_window.config, api_key=api_key), OllamaAdapter)
        if not is_ollama and not api_key:
            messageBox(parent, tr("警告"), tr("请先设置 API Key"), 1)
            return
        original_text = self._settings_refresh_btn.text()
        original_enabled = self._settings_refresh_btn.isEnabled()
        self._settings_refresh_btn.setText(tr("刷新中..."))
        self._settings_refresh_btn.setEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            adapter = getAdapterEndpoint(endpoint_name, self.main_window.config,
                                           api_key=api_key, api_url=api_url)
            models = adapter.getModels()
            current_model = self._settings_model_combo.currentText()
            self._settings_model_combo.clear()
            self._settings_model_combo.addItems(models)
            if models:
                if current_model in models:
                    idx = self._settings_model_combo.findText(current_model)
                    if idx >= 0:
                        self._settings_model_combo.setCurrentIndex(idx)
                else:
                    self._settings_model_combo.setCurrentIndex(0)
            messageBox(parent, tr("刷新成功"), f"{tr('已获取')} {len(models)} {tr('个模型')}", 1)
        except Exception as e:
            messageBox(parent, tr("刷新失败"), f"{tr('获取模型列表失败')}: {str(e)}", 1)
        finally:
            self._settings_refresh_btn.setText(original_text)
            self._settings_refresh_btn.setEnabled(original_enabled)
            QApplication.restoreOverrideCursor()

    def _settingsShowBalance(self, parent):
        dlg2 = QDialog(parent)
        dlg2.setWindowTitle(tr("负载均衡配置"))
        dlg2.setMinimumSize(300, 300)
        layout2 = QVBoxLayout(dlg2)

        lb_enable_cb = QCheckBox(tr("启用负载均衡"))
        lb_enable_cb.setChecked(self._edit_load_balance.get("enabled", False))
        layout2.addWidget(lb_enable_cb)

        layout2.addWidget(QLabel(tr("优先级（0 禁用，1-10 值越小越优先）；权重：同优先级内按比例分配")))
        existing = self._edit_load_balance.get("profiles", {})
        rows = []
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_widget = QWidget()
        form = QFormLayout(scroll_widget)
        for name in self._edit_profiles:
            cfg = existing.get(name, {"priority": 1, "weight": 1})
            priority_sb = QSpinBox()
            priority_sb.setRange(0, 10)
            priority_sb.setSpecialValueText(tr("禁用"))
            priority_sb.setValue(cfg.get("priority", 1))
            priority_sb.setMinimumWidth(60)
            priority_sb.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            weight_sb = QSpinBox()
            weight_sb.setRange(1, 100)
            weight_sb.setValue(cfg.get("weight", 1))
            weight_sb.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            h = QHBoxLayout()
            h.addWidget(QLabel(tr("优先级")))
            h.addWidget(priority_sb)
            h.addSpacing(10)
            h.addWidget(QLabel(tr("权重")))
            h.addWidget(weight_sb)
            h.addStretch()
            form.addRow(name, h)
            rows.append((name, priority_sb, weight_sb))
        scroll.setWidget(scroll_widget)
        layout2.addWidget(scroll)

        if dialogBox(layout2, dlg2):
            profiles_dict = {}
            for name, p_sb, w_sb in rows:
                profiles_dict[name] = {"priority": p_sb.value(), "weight": w_sb.value()}
            self._edit_load_balance = {
                "enabled": lb_enable_cb.isChecked(),
                "profiles": profiles_dict
            }

    def _settingsTest(self, parent):
        dlg2 = QDialog(parent)
        dlg2.setWindowTitle(tr("测试配置连通性"))
        dlg2.setMinimumWidth(450)
        layout2 = QVBoxLayout(dlg2)

        items = []
        for name in self._edit_profiles:
            cb = QCheckBox(name)
            cb.setChecked(False)
            status = QLabel(tr("等待测试"))
            status.setFixedWidth(200)
            row = QHBoxLayout()
            row.addWidget(cb)
            row.addWidget(status)
            row.addStretch()
            layout2.addLayout(row)
            items.append((name, cb, status))

        def _run_test(selected_only):
            for pname, cb, status in items:
                if selected_only and not cb.isChecked():
                    continue
                status.setText(tr("测试中..."))
                status.setStyleSheet("color: orange")
                QApplication.processEvents()
                try:
                    profile = self._edit_profiles.get(pname, {})
                    api_url = profile.get("api_url", "")
                    if not api_url:
                        status.setText("API URL " + tr("未设置"))
                        status.setStyleSheet("color: red")
                        continue
                    is_ollama = isinstance(getAdapterUrl(api_url, self.main_window.config, api_key=profile.get("api_key", "")), OllamaAdapter)
                    if not is_ollama and not profile.get("api_key"):
                        status.setText("API Key " + tr("未设置"))
                        status.setStyleSheet("color: red")
                        continue
                    endpoint_name = profile.get("endpoint", "")
                    adapter = getAdapterEndpoint(endpoint_name, self.main_window.config,
                                                   api_key=profile.get("api_key", ""),
                                                   api_url=api_url)
                    test_model = profile.get("model", "")
                    if not test_model:
                        status.setText(tr("模型未设置"))
                        status.setStyleSheet("color: red")
                        continue
                    adapter.chat(messages=[{"role": "user", "content": "Hello"}],
                                 model=test_model, temperature=0.7, max_tokens=10)
                    status.setText(tr("响应正常"))
                    status.setStyleSheet("color: green")
                except Exception as e:
                    status.setText(f"× {str(e)[:40]}")
                    status.setStyleSheet("color: red")
                QApplication.processEvents()

        btn_layout = QHBoxLayout()
        test_sel_btn = QPushButton(tr("测试已选"))
        test_sel_btn.clicked.connect(lambda: _run_test(True))
        btn_layout.addWidget(test_sel_btn)
        test_all_btn = QPushButton(tr("测试全部"))
        test_all_btn.clicked.connect(lambda: _run_test(False))
        btn_layout.addWidget(test_all_btn)
        btn_layout.addStretch()
        close_btn = QPushButton(tr("关闭"))
        close_btn.clicked.connect(dlg2.close)
        btn_layout.addWidget(close_btn)
        layout2.addLayout(btn_layout)
        dlg2.exec()

    def _saveSettings(self, dlg):
        self._settingsSaveProfile()
        self.settings["active"] = self._edit_active
        self.settings["profiles"] = self._edit_profiles
        self.settings["stream"] = self._settings_stream_cb.isChecked()
        self.settings["load_balance"] = self._edit_load_balance
        self.settings["prompts"] = self._edit_prompts
        self.saveConfig()
        dlg.accept()

    def _settingsReloadPromptList(self):
        """重新加载提示词列表"""
        self._settings_prompt_list.clear()
        for name, value in self._edit_prompts.items():
            builtin = name in ("系统提示词",)
            display = name + "  (内置)" if builtin else name
            item = QListWidgetItem(display)
            item.setData(Qt.ItemDataRole.UserRole, {"name": name, "value": value})
            self._settings_prompt_list.addItem(item)

    def _settingsPromptAdd(self):
        dlg = QDialog(self._settings_prompt_list)
        dlg.setWindowTitle(tr("添加提示词"))
        dlg.setMinimumSize(500, 400)
        d_layout = QVBoxLayout(dlg)
        form_layout = QFormLayout()
        form_layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        name_edit = QLineEdit()
        form_layout.addRow(tr("提示词名称"), name_edit)
        value_edit = QTextEdit()
        form_layout.addRow(tr("提示词内容"), value_edit)
        d_layout.addLayout(form_layout)
        if dialogBox(d_layout, dlg):
            name = name_edit.text().strip()
            value = value_edit.toPlainText().strip()
            if not name:
                messageBox(self._settings_prompt_list, tr("警告"), tr("提示词名称不能为空"), 1)
                return
            self._edit_prompts[name] = value
            self._settingsReloadPromptList()

    def _settingsPromptEdit(self):
        current_item = self._settings_prompt_list.currentItem()
        if not current_item:
            messageBox(self._settings_prompt_list, tr("警告"), tr("请先选择一个要编辑的项"), 1)
            return
        data = current_item.data(Qt.ItemDataRole.UserRole)
        old_name = data.get("name", "")
        old_value = data.get("value", "")
        builtin = old_name in ("系统提示词",)

        dlg = QDialog(self._settings_prompt_list)
        dlg.setWindowTitle(tr("编辑提示词"))
        dlg.setMinimumSize(500, 400)
        d_layout = QVBoxLayout(dlg)
        form_layout = QFormLayout()
        form_layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)

        name_edit = QLineEdit()
        name_edit.setText(old_name)
        if builtin:
            name_edit.setReadOnly(True)
            name_edit.setStyleSheet("background: #e0e0e0;")
        form_layout.addRow(tr("提示词名称"), name_edit)

        value_edit = QTextEdit()
        value_edit.setPlainText(old_value)
        form_layout.addRow(tr("提示词内容"), value_edit)

        d_layout.addLayout(form_layout)
        if dialogBox(d_layout, dlg):
            name = name_edit.text().strip()
            value = value_edit.toPlainText().strip()
            if not name:
                messageBox(self._settings_prompt_list, tr("警告"), tr("提示词名称不能为空"), 1)
                return
            if name != old_name and name in self._edit_prompts:
                messageBox(self._settings_prompt_list, tr("警告"), tr("提示词名称已存在"), 1)
                return
            if name != old_name:
                del self._edit_prompts[old_name]
            self._edit_prompts[name] = value
            self._settingsReloadPromptList()

    def _settingsPromptDelete(self):
        current_item = self._settings_prompt_list.currentItem()
        if not current_item:
            messageBox(self._settings_prompt_list, tr("警告"), tr("请先选择一个要删除的项"), 1)
            return
        data = current_item.data(Qt.ItemDataRole.UserRole)
        name = data.get("name", "") if isinstance(data, dict) else current_item.text()
        if name in ("系统提示词",):
            messageBox(self._settings_prompt_list, tr("禁止删除"), tr("内置提示词不可删除"), 1)
            return
        if messageBox(self._settings_prompt_list, tr("确认删除"), f"{tr('确定要删除')} '{current_item.text()}' {tr('吗')}？"):
            self._edit_prompts.pop(name, None)
            self._settingsReloadPromptList()

    # ── 提示词管理 ──

    def _createUi(self, editor):
        if self.dock is not None:
            return

        self.dock = QDockWidget("AI助手", editor)
        self.dock.setObjectName("AIViewDock")
        self.dock.setMaximumWidth(600)
        self.dock.setFeatures(
            QDockWidget.DockWidgetMovable |
            QDockWidget.DockWidgetFloatable |
            QDockWidget.DockWidgetClosable
        )
        self.dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)

        self._ensureContent()
        self.dock.setWidget(self._panel)
        self.dock.visibilityChanged.connect(self._onVisibilityChanged)

        editor.addDockWidget(Qt.RightDockWidgetArea, self.dock)
        self.dock.setStyleSheet(editor.styleSheet())
        self._reloadConversationList()
        self.dock.hide()

    def _ensureDockInEditor(self):
        editor = self._get_editor_window()
        if not editor:
            return
        if self.dock is None:
            self._createUi(editor)
        elif self.dock.parent() is not editor:
            editor.addDockWidget(Qt.RightDockWidgetArea, self.dock)
            self.dock.setStyleSheet(editor.styleSheet())

    def _ensureContent(self):
        if self._panel is not None:
            return
        panel = QWidget()
        self._panel = panel
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self._buildTopBar(layout)
        self._buildPromptBar(layout)
        self._buildMessageList(layout)
        self._buildInputArea(layout)

    def _buildTopBar(self, layout):
        top_frame = QFrame()
        top_frame.setObjectName("ai_top_bar")
        top_layout = QVBoxLayout(top_frame)
        top_layout.setContentsMargins(5, 5, 5, 5)
        top_layout.setSpacing(4)

        # 第一行：配置 + 新建对话 + 导出
        row1 = QHBoxLayout()
        row1.setSpacing(4)

        self._profile_combo = QComboBox()
        self._profile_combo.setMinimumWidth(80)
        profile_dict = self.settings.get("profiles", {})
        profiles = list(profile_dict.keys()) if profile_dict else ["默认配置"]
        active = self._getProfileName()
        for p in profiles:
            self._profile_combo.addItem(p)
        idx = self._profile_combo.findText(active)
        if idx >= 0:
            self._profile_combo.setCurrentIndex(idx)
        self._profile_combo.currentIndexChanged.connect(self._onProfileChanged)

        self._lb_label = QLabel("负载均衡")
        self._lb_label.setStyleSheet("color: #008000; font-size: 14px; padding: 0 4px;")
        row1.addWidget(self._lb_label)

        row1.addWidget(QLabel("配置"))
        row1.addWidget(self._profile_combo, 1)
        self._refreshLbUi()

        new_conv_btn = QPushButton("新对话")
        new_conv_btn.setFixedSize(70, 30)
        new_conv_btn.clicked.connect(self._newConversation)
        row1.addWidget(new_conv_btn)

        search_btn = QPushButton("搜索")
        search_btn.setFixedSize(60, 30)
        search_btn.clicked.connect(self._searchConversations)
        row1.addWidget(search_btn)

        export_btn = QPushButton("导出")
        export_btn.setFixedSize(60, 30)
        export_btn.clicked.connect(self._exportConversation)
        row1.addWidget(export_btn)

        top_layout.addLayout(row1)

        # 第二行：模型 + 刷新
        row2 = QHBoxLayout()
        row2.setSpacing(4)

        row2.addWidget(QLabel("模型"))

        self._model_combo = QComboBox()
        self._model_combo.setMinimumWidth(100)
        self._model_combo.setEditable(True)
        self._updateModelCombo()
        self._model_combo.currentTextChanged.connect(self._onModelChanged)
        row2.addWidget(self._model_combo, 1)

        refresh_btn = QPushButton("刷新")
        refresh_btn.setFixedSize(60, 30)
        refresh_btn.clicked.connect(self._refreshModels)
        row2.addWidget(refresh_btn)

        top_layout.addLayout(row2)

        # 第三行：对话 + 重命名 + 删除
        row3 = QHBoxLayout()
        row3.setSpacing(4)

        self._conv_combo = QComboBox()
        self._conv_combo.setMinimumWidth(100)
        self._conv_combo.currentIndexChanged.connect(self._onConversationChanged)
        row3.addWidget(QLabel("对话"))
        row3.addWidget(self._conv_combo, 1)

        rename_btn = QPushButton("重命名")
        rename_btn.setFixedSize(70, 30)
        rename_btn.clicked.connect(self._renameConversation)
        row3.addWidget(rename_btn)

        delete_btn = QPushButton("删除")
        delete_btn.setFixedSize(60, 30)
        delete_btn.clicked.connect(self._deleteConversation)
        row3.addWidget(delete_btn)

        top_layout.addLayout(row3)

        layout.addWidget(top_frame)

    def _buildPromptBar(self, layout):
        prompts = self.settings.get("prompts", {})
        visible_prompts = [n for n in prompts if n != "系统提示词"]

        if visible_prompts:
            prompt_frame = QFrame()
            prompt_frame.setObjectName("ai_prompt_bar")
            prompt_layout = QHBoxLayout(prompt_frame)
            prompt_layout.setContentsMargins(5, 5, 5, 5)

            for name in visible_prompts:
                btn = QPushButton(name)
                btn.setFixedWidth(75)
                btn.clicked.connect(lambda checked, n=name: self._onPromptClicked(n))
                prompt_layout.addWidget(btn)

            prompt_layout.addStretch()
            layout.addWidget(prompt_frame)

    def _buildMessageList(self, layout):
        self._message_scroll = QScrollArea()
        self._message_scroll.setWidgetResizable(True)
        self._message_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._message_scroll.setStyleSheet("QScrollArea { border: none; background: white; }")

        self._message_content = QWidget()
        self._message_layout = QVBoxLayout(self._message_content)
        self._message_layout.setContentsMargins(0, 0, 0, 0)
        self._message_layout.setSpacing(0)
        self._message_layout.addStretch()

        self._message_scroll.setWidget(self._message_content)
        self._loadMessagesToUi()

        layout.addWidget(self._message_scroll, 1)

    def _buildInputArea(self, layout):
        self._preview_bar = QFrame()
        self._preview_bar.setVisible(False)
        self._preview_bar.setFixedHeight(48)
        self._preview_bar.setStyleSheet("border-top: 1px solid #ddd; background: #f5f5f5;")
        preview_layout = QHBoxLayout(self._preview_bar)
        preview_layout.setContentsMargins(6, 6, 6, 6)
        preview_layout.setSpacing(6)
        layout.addWidget(self._preview_bar)

        input_frame = QFrame()
        input_frame.setStyleSheet("border-top: 1px solid #ddd;")
        input_frame.setMaximumHeight(140)
        input_layout = QVBoxLayout(input_frame)
        input_layout.setContentsMargins(10, 10, 10, 10)

        self.input_edit = QTextEdit()
        self.input_edit.setPlaceholderText("输入消息...（支持拖拽文件/图片）")
        self.input_edit.setMaximumHeight(80)
        self.input_edit.setAcceptDrops(True)
        self.input_edit.dragEnterEvent = self._inputDragEnter
        self.input_edit.dropEvent = self._inputDrop
        input_layout.addWidget(self.input_edit)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(4)

        clear_btn = QPushButton("清空")
        clear_btn.setFixedSize(60, 30)
        clear_btn.clicked.connect(self._clearHistory)
        btn_layout.addWidget(clear_btn)

        upload_btn = QPushButton("上传文件")
        upload_btn.setFixedSize(80, 30)
        upload_btn.clicked.connect(self._onUploadFileClicked)
        btn_layout.addWidget(upload_btn)

        btn_layout.addStretch()

        self.send_btn = QPushButton("发送")
        self.send_btn.setFixedSize(60, 30)
        self.send_btn.clicked.connect(self._sendMessage)
        self.send_btn.setDefault(True)
        btn_layout.addWidget(self.send_btn)

        input_layout.addLayout(btn_layout)
        layout.addWidget(input_frame)

        self.input_edit.keyPressEvent = self._onInputKeyPress

    # ── 对话 / 配置 切换 ──────────────────────────────

    def _onProfileChanged(self, index):
        name = self._profile_combo.currentText()
        if not name:
            return
        self.settings["active"] = name
        self.saveConfig()
        self._updateModelCombo()
        self._reloadConversationList()

    def _onConversationChanged(self, index):
        conv_id = self._conv_combo.currentData()
        if conv_id:
            self._current_conv_id = conv_id
            self._loadMessagesToUi()

    def _newConversation(self):
        conv_id = getTimestamp()
        profile = self._getProfileName()
        if profile not in self._history:
            self._history[profile] = {}
        count = len(self._history[profile]) + 1
        self._history[profile][conv_id] = {
            "title": f"对话 {count}",
            "messages": []
        }
        self._current_conv_id = conv_id
        self._saveHistory()
        self._reloadConversationList(select_id=conv_id)

    def _renameConversation(self):
        conv_id = self._conv_combo.currentData()
        if not conv_id:
            return
        profile = self._getProfileName()
        conv = self._history.get(profile, {}).get(conv_id)
        if not conv:
            return
        new_title = inputDialog(self.dock, "重命名对话", "新名称:", default=conv.get("title", ""))
        if new_title:
            conv["title"] = new_title
            self._saveHistory()
            self._reloadConversationList(select_id=conv_id)

    def _deleteConversation(self):
        conv_id = self._conv_combo.currentData()
        if not conv_id:
            return
        if not messageBox(self.dock, "删除对话", "确定要删除当前对话吗？", 2):
            return
        profile = self._getProfileName()
        if profile in self._history and conv_id in self._history[profile]:
            del self._history[profile][conv_id]
            self._saveHistory()
        remaining = list(self._history.get(profile, {}).keys())
        if remaining:
            self._current_conv_id = remaining[0]
        else:
            self._current_conv_id = None
            self._newConversation()
        self._reloadConversationList()

    def _exportConversation(self):
        conv = self._getCurrentConv()
        if not conv or not conv.get("messages"):
            messageBox(self.dock, "导出", "当前对话为空，无法导出", 1)
            return
        title = conv.get("title", "对话")
        messages = conv.get("messages", [])
        default_name = f"{title}.md"
        file_path, selected_filter = QFileDialog.getSaveFileName(
            self.dock, "导出对话",
            str(data_dir / default_name),
            "Markdown (*.md);;纯文本 (*.txt)"
        )
        if not file_path:
            return
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".md":
            content = self._formatExportMarkdown(title, messages)
        else:
            content = self._formatExportText(title, messages)
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            messageBox(self.dock, "导出失败", f"写入文件失败: {e}", 1)

    @staticmethod
    def _formatExportMarkdown(title: str, messages: list) -> str:
        lines = [f"# {title}\n"]
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            ts = msg.get("timestamp", "")
            if isinstance(content, list):
                content = _contentToText(content)
            if ts:
                lines.append(f"\n## {ts[:19]}\n")
            label = "你" if role == "user" else "AI"
            lines.append(f"\n**{label}：**\n{content}\n")
        return "".join(lines)

    @staticmethod
    def _formatExportText(title: str, messages: list) -> str:
        lines = [f"{title}", "=" * len(title), ""]
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            ts = msg.get("timestamp", "")
            if isinstance(content, list):
                content = _contentToText(content)
            label = "你" if role == "user" else "AI"
            if ts:
                lines.append(f"[{ts[:19]}]")
            lines.append(f"{label}: {content}\n")
        return "\n".join(lines)

    def _searchConversations(self):
        profile = self._getProfileName()
        convs = self._history.get(profile, {})
        if not convs:
            messageBox(self.dock, "搜索", "暂无对话", 1)
            return

        dlg = QDialog(self.dock)
        dlg.setWindowTitle("搜索对话")
        dlg.resize(400, 500)
        layout = QVBoxLayout(dlg)

        search_input = QLineEdit()
        search_input.setPlaceholderText("输入关键词搜索所有对话...")
        layout.addWidget(search_input)

        result_list = QListWidget()
        result_list.setWordWrap(True)
        layout.addWidget(result_list, 1)

        status_label = QLabel()
        layout.addWidget(status_label)

        def doSearch():
            keyword = search_input.text().strip().lower()
            result_list.clear()
            if not keyword:
                status_label.setText("")
                return
            hits = []
            for conv_id, conv_data in convs.items():
                title = conv_data.get("title", "对话")
                messages = conv_data.get("messages", [])
                for msg in messages:
                    content = msg.get("content", "")
                    if isinstance(content, list):
                        content = _contentToText(content)
                    if keyword in str(content).lower():
                        role = "你" if msg.get("role") == "user" else "AI"
                        preview = str(content)[:120].replace("\n", " ")
                        hits.append((conv_id, title, role, preview))
                        break
            status_label.setText(f"找到 {len(hits)} 条匹配")
            for conv_id, title, role, preview in hits:
                display = f"[{title}] {role}: {preview}"
                item = QListWidgetItem(display)
                item.setData(Qt.ItemDataRole.UserRole, conv_id)
                result_list.addItem(item)

        search_input.textChanged.connect(doSearch)

        def onResultClicked(item):
            conv_id = item.data(Qt.ItemDataRole.UserRole)
            if conv_id:
                self._current_conv_id = conv_id
                self._reloadConversationList(select_id=conv_id)
                self._loadMessagesToUi()
                dlg.accept()

        result_list.itemClicked.connect(onResultClicked)
        dlg.exec()

    def _updateModelCombo(self):
        self._model_combo_updating = True
        profiles = self.settings.get("profiles", {})
        name = self._getProfileName()
        profile = profiles.get(name, {})
        model = profile.get("model", "") or ""
        if model:
            self._model_combo.setCurrentText(model)
        else:
            self._model_combo.setCurrentText("")
            if self._model_combo.lineEdit():
                self._model_combo.lineEdit().setPlaceholderText("输入模型名...")
        self._model_combo_updating = False

    def _onModelChanged(self, text):
        if self._model_combo_updating:
            return
        profiles = self.settings.get("profiles", {})
        name = self._getProfileName()
        if name in profiles:
            profiles[name]["model"] = text
            self.saveConfig()

    def _refreshModels(self):
        try:
            profiles = self.settings.get("profiles", {})
            name = self._getProfileName()
            profile = profiles.get(name, {})
            if not profile.get("api_key") or not profile.get("api_url"):
                return
            for ep_name, cls, url in AI_ADAPTER:
                if url == profile["api_url"]:
                    endpoint_name = ep_name
                    break
            else:
                endpoint_name = "自定义"
            adapter = getAdapterEndpoint(endpoint_name, self.main_window.config,
                                           api_key=profile.get("api_key", ""),
                                           api_url=profile.get("api_url", ""))
            models = adapter.getModels()
            if models:
                current = self._model_combo.currentText()
                self._model_combo_updating = True
                self._model_combo.clear()
                self._model_combo.addItems(models)
                idx = self._model_combo.findText(current)
                self._model_combo.setCurrentIndex(max(idx, 0))
                self._model_combo_updating = False
        except Exception:
            logger.exception("刷新模型失败")

    def _reloadConversationList(self, select_id=None):
        self._conv_combo.blockSignals(True)
        self._conv_combo.clear()
        profile = self._getProfileName()
        convs = self._history.get(profile, {})
        if not convs:
            self._newConversation()
            self._conv_combo.blockSignals(False)
            return
        for conv_id, conv_data in convs.items():
            self._conv_combo.addItem(conv_data.get("title", "对话"), conv_id)
        target = select_id or self._current_conv_id
        if target:
            idx = self._conv_combo.findData(target)
            if idx >= 0:
                self._conv_combo.setCurrentIndex(idx)
                self._current_conv_id = target
        self._conv_combo.blockSignals(False)
        self._loadMessagesToUi()

    def _onVisibilityChanged(self, visible):
        if visible:
            self._refreshLbUi()
        else:
            self._destroyDock()

    def _onStandaloneDestroyed(self):
        self._standalone_window = None
        self._panel = None

    def _refreshLbUi(self):
        lb = self.settings.get("load_balance", {}).get("enabled", False)
        self._profile_combo.setEnabled(not lb)
        if lb:
            if not hasattr(self, '_lb_label') or not self._lb_label:
                self._lb_label = QLabel("负载均衡")
                self._lb_label.setStyleSheet("color: #008000; font-size: 14px; padding: 0 4px;")
            self._lb_label.setVisible(True)
        else:
            if hasattr(self, '_lb_label') and self._lb_label:
                self._lb_label.setVisible(False)

    # ── 历史消息 ──────────────────────────────────────

    def _loadHistory(self):
        self._history = {}
        if history_file.exists():
            try:
                with open(history_file, "r", encoding="utf-8") as f:
                    all_data = json.load(f)
                self._history = {k: v for k, v in all_data.items() if not k.startswith("_")}
            except Exception:
                logger.exception("加载 AI 历史失败")
                self._history = {}
        self._ensureDefaultConversation()

    def _ensureDefaultConversation(self):
        profile = self._getProfileName()
        if profile not in self._history:
            self._history[profile] = {}
        convs = self._history[profile]
        if not convs:
            conv_id = getTimestamp()
            convs[conv_id] = {"title": "对话 1", "messages": []}
            self._current_conv_id = conv_id
        elif self._current_conv_id is None or self._current_conv_id not in convs:
            self._current_conv_id = list(convs.keys())[0]

    def _saveHistory(self):
        try:
            history_file.parent.mkdir(parents=True, exist_ok=True)
            all_data = {}
            if history_file.exists():
                with open(history_file, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                for k, v in existing.items():
                    if k.startswith("_"):
                        all_data[k] = v
            all_data.update(self._history)
            with open(history_file, "w", encoding="utf-8") as f:
                json.dump(all_data, f, ensure_ascii=False, indent=2)
        except Exception:
            logger.exception("保存 AI 历史失败")

    def _getProfileName(self):
        active = self.settings.get("active", "默认配置")
        return active or "默认配置"

    def _getCurrentConv(self):
        profile = self._getProfileName()
        if profile not in self._history:
            self._history[profile] = {}
        convs = self._history[profile]
        if not convs:
            self._ensureDefaultConversation()
        if self._current_conv_id not in convs:
            self._current_conv_id = list(convs.keys())[0]
        return convs.get(self._current_conv_id, {"title": "对话", "messages": []})

    def _getMessages(self):
        conv = self._getCurrentConv()
        return conv.get("messages", [])

    def _addMessage(self, role, content):
        profile = self._getProfileName()
        if profile not in self._history:
            self._history[profile] = {}
        convs = self._history[profile]
        if not convs or self._current_conv_id not in convs:
            self._newConversation()
            return self._addMessage(role, content)
        conv = convs[self._current_conv_id]
        conv["messages"].append({
            "role": role,
            "content": content,
            "timestamp": getTimestamp()
        })
        max_messages = 100
        if len(conv["messages"]) > max_messages:
            conv["messages"] = conv["messages"][-max_messages:]
        self._saveHistory()

    def _clearHistory(self):
        profile = self._getProfileName()
        if profile in self._history and self._current_conv_id:
            conv = self._history[profile].get(self._current_conv_id)
            if conv:
                conv["messages"] = []
                self._saveHistory()
        self._loadMessagesToUi()

    # ── UI 交互 ───────────────────────────────────────

    def _togglePanel(self):
        self.initialize()
        editor = self._get_editor_window()
        if editor:
            if self.dock and self.dock.isVisible():
                self._destroyDock()
                return
            self._ensureDockInEditor()
            if self._standalone_window and self._standalone_window.isVisible():
                self._movePanelToDock()
            if self.dock:
                self.dock.setWidget(self._panel)
                self.dock.show()
        else:
            if self._standalone_window and self._standalone_window.isVisible():
                self._standalone_window.close()
                return
            self._toggleStandalone()

    def _toggleStandalone(self):
        self.initialize()
        if self._standalone_window and self._standalone_window.isVisible():
            self._standalone_window.close()
            return

        if self._standalone_window:
            if self._panel and self._panel.parent() is self._standalone_window:
                self._panel.setParent(None)
            self._standalone_window.deleteLater()
            self._standalone_window = None

        self._ensureContent()
        if self.dock:
            self.dock.setWidget(QWidget())

        self._standalone_window = QFrame(None, Qt.Window | Qt.WindowStaysOnTopHint)
        self._standalone_window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._standalone_window.destroyed.connect(self._onStandaloneDestroyed)
        self._standalone_window.setStyleSheet(self.main_window.styleSheet())
        layout = QVBoxLayout(self._standalone_window)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._panel)

        screen = QApplication.primaryScreen().availableGeometry()
        w = min(420, screen.width() // 3)
        self._standalone_window.resize(w, screen.height() - 50)
        self._standalone_window.move(screen.x(), screen.y() + 50)
        self._standalone_window.show()
        self._standalone_window.raise_()
        self._standalone_window.layout().activate()
        self._panel.show()
        QTimer.singleShot(0, self._standalone_window.adjustSize)

    def _movePanelToDock(self):
        if self._standalone_window:
            self._panel.setParent(None)
            self._standalone_window.close()
            self._standalone_window.deleteLater()
            self._standalone_window = None
        if self.dock:
            self.dock.setWidget(self._panel)
            self._panel.show()

    def _onInputKeyPress(self, event):
        if event.key() == Qt.Key.Key_Return and event.modifiers() == Qt.KeyboardModifier.NoModifier:
            self._sendMessage()
        else:
            QTextEdit.keyPressEvent(self.input_edit, event)

    def _inputDragEnter(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def _inputDrop(self, event: QDropEvent):
        urls = event.mimeData().urls()
        if not urls:
            return
        client = getAIClient(config=self.settings)
        for url in urls:
            path = url.toLocalFile()
            if not path:
                continue
            if os.path.isdir(path):
                messages = client.buildFolderMessage(path)
                if messages:
                    self._sendMessages(messages)
                continue
            ext = os.path.splitext(path)[1].lower()
            if ext in EXTENSION["IMAGE"]:
                if path not in self._pending_images:
                    self._pending_images.append(path)
            else:
                if path not in self._pending_files:
                    self._pending_files.append(path)
        self._refreshPreviewBar()

    def _refreshPreviewBar(self):
        layout = self._preview_bar.layout()
        while layout.count():
            w = layout.takeAt(0).widget()
            if w:
                w.deleteLater()
        has_items = bool(self._pending_images or self._pending_files)
        if not has_items:
            self._preview_bar.setVisible(False)
            return

        for path in self._pending_images:
            tag = self._buildPreviewTag(path, is_image=True)
            layout.addWidget(tag)
        for path in self._pending_files:
            tag = self._buildPreviewTag(path, is_image=False)
            layout.addWidget(tag)
        layout.addStretch()
        self._preview_bar.setVisible(True)

    def _buildPreviewTag(self, path: str, is_image: bool) -> QFrame:
        frame = QFrame()
        frame.setFixedHeight(36)
        frame.setStyleSheet("QFrame { background: #e8e8e8; border: 1px solid #ccc; border-radius: 4px; }")
        row = QHBoxLayout(frame)
        row.setContentsMargins(4, 0, 4, 0)
        row.setSpacing(4)

        if is_image:
            pixmap = QPixmap(path)
            thumb = QLabel()
            thumb.setPixmap(pixmap.scaled(28, 28, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            thumb.setFixedSize(28, 28)
            thumb.setStyleSheet("border: 1px solid #aaa; border-radius: 2px; background: #fff;")
            row.addWidget(thumb)
        else:
            icon = QLabel("📄")
            icon.setFixedSize(20, 28)
            icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
            row.addWidget(icon)

        name = os.path.basename(path)
        label = QLabel(name)
        label.setMaximumWidth(150)
        label.setStyleSheet("color: #333; font-size: 12px; background: transparent; border: none;")
        row.addWidget(label)

        remove_btn = QPushButton("×")
        remove_btn.setFixedSize(20, 20)
        remove_btn.setStyleSheet("""
            QPushButton {
                border: none; color: #999; font-size: 14px; font-weight: bold;
                background: transparent; padding: 0;
            }
            QPushButton:hover { color: #e55; }
        """)
        remove_btn.clicked.connect(lambda checked, p=path: self._removePendingItem(p))
        row.addWidget(remove_btn)

        return frame

    def _removePendingItem(self, path: str):
        if path in self._pending_images:
            self._pending_images.remove(path)
        if path in self._pending_files:
            self._pending_files.remove(path)
        self._refreshPreviewBar()

    def _loadMessagesToUi(self):
        if not self._message_layout:
            return
        self._clearMessageWidgets()
        self._last_ai_browser = None
        for msg in self._getMessages():
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role and content:
                self._addMessageItem(role, content)

    def _clearMessageWidgets(self):
        layout = self._message_layout
        if not layout:
            return
        while layout.count() > 0:
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        layout.addStretch()

    def _makeMessageClickable(self, widget, text):
        widget.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        widget._long_pressed = False

        orig_press = widget.mousePressEvent
        def onPress(e):
            widget._long_pressed = False
            widget._lp_timer = QTimer()
            widget._lp_timer.setSingleShot(True)
            widget._lp_timer.setInterval(500)
            widget._lp_timer.timeout.connect(lambda: _on_long_press())
            widget._lp_timer.start()
            if orig_press:
                orig_press(e)

        orig_release = widget.mouseReleaseEvent
        def onRelease(e):
            if hasattr(widget, '_lp_timer') and widget._lp_timer:
                widget._lp_timer.stop()
            if not widget._long_pressed:
                clipboard = QApplication.clipboard()
                clipboard.setText(str(text))
                QTimer.singleShot(0, lambda: QToolTip.showText(QCursor.pos(), "已复制"))
            if orig_release:
                orig_release(e)

        def _on_long_press():
            widget._long_pressed = True
            if isinstance(widget, QLabel):
                widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        widget.mousePressEvent = onPress
        widget.mouseReleaseEvent = onRelease

    def _addMessageItem(self, role, content):
        text = _contentToText(content)
        is_ai = role == "assistant"

        frame = QFrame()
        frame.setObjectName("msg_frame")
        frame.setStyleSheet("QFrame#msg_frame { background: transparent; border-bottom: 1px solid #eee; }")
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(10, 6, 10, 6)
        fl.setSpacing(3)

        header = QLabel("AI:" if is_ai else "你:")
        header.setStyleSheet("font-weight: bold; color: #666; border: none; background: transparent; font-size: 12px;")
        self._makeMessageClickable(header, text)
        fl.addWidget(header)

        if is_ai:
            browser = AutoHeightTextBrowser()
            browser.setOpenExternalLinks(False)
            browser.setStyleSheet("border: none; background: transparent;")
            browser.setHtml(self._renderMarkdown(text))
            self._makeMessageClickable(browser, text)
            fl.addWidget(browser)
            self._last_ai_browser = browser
        else:
            label = QLabel(text)
            label.setWordWrap(True)
            label.setStyleSheet("border: none; background: transparent;")
            self._makeMessageClickable(label, text)
            fl.addWidget(label)

        layout = self._message_layout
        layout.insertWidget(layout.count() - 1, frame)
        self._scrollToBottom()

    def _renderMarkdown(self, text: str) -> str:
        import markdown
        html = markdown.markdown(text, extensions=['fenced_code', 'tables', 'nl2br'])
        css = """
            <style>
                body { font-size: 13px; color: #333; line-height: 1.6; margin: 0; padding: 0; }
                pre { background: #f5f5f5; padding: 8px 12px; border-radius: 4px; white-space: pre-wrap; word-break: break-all; font-size: 13px; }
                code { background: #f0f0f0; padding: 1px 4px; border-radius: 2px; font-size: 13px; }
                pre code { background: transparent; padding: 0; }
                table { border-collapse: collapse; margin: 8px 0; width: 100%; }
                th, td { border: 1px solid #ddd; padding: 4px 8px; text-align: left; }
                th { background: #f5f5f5; }
                blockquote { border-left: 3px solid #ddd; margin: 4px 0; padding: 2px 12px; color: #666; }
                p { margin: 4px 0; }
                ul, ol { margin: 4px 0; padding-left: 20px; }
                h1, h2, h3, h4 { margin: 8px 0 4px; }
                a { color: #1a73e8; }
            </style>
        """
        return css + html

    def _scrollToBottom(self):
        if self._message_scroll:
            QTimer.singleShot(0, lambda: self._message_scroll.verticalScrollBar().setValue(
                self._message_scroll.verticalScrollBar().maximum()))

    def _onPromptClicked(self, prompt_name):
        prompts = self.settings.get("prompts", {})
        if isinstance(prompts, dict):
            value = prompts.get(prompt_name, "")
            if "{request}" in value:
                value = value.replace("{request}", "")
            self.input_edit.setPlainText(value)

    def _onUploadFileClicked(self):
        file_path = getFilePath(self.main_window, "选择文件")
        if not file_path:
            return
        client = getAIClient(config=self.settings)
        messages = client.buildFileMessage(file_path)
        if messages:
            self._sendMessages(messages)

    # ── 发送消息 / 流式响应 ────────────────────────────

    def _sendMessage(self):
        text = self.input_edit.toPlainText().strip()
        content_parts = []
        if text:
            content_parts.append({"type": "text", "text": text})
        for path in self._pending_images:
            content_parts.append({"type": "image_url", "url": f"file://{path}"})

        messages = []
        if content_parts:
            messages.append({"role": "user", "content": content_parts})

        client = getAIClient(config=self.settings)
        for path in self._pending_files:
            file_msgs = client.buildFileMessage(path)
            if file_msgs:
                messages.extend(file_msgs)

        if not messages:
            return

        self._pending_images.clear()
        self._pending_files.clear()
        self._refreshPreviewBar()
        self._sendMessages(messages)

    def _sendMessages(self, messages):
        self.input_edit.clear()

        for msg in messages:
            content = msg.get("content", "")
            self._addMessage("user", content)
            self._addMessageItem("user", content)

        self.send_btn.setEnabled(False)
        self.send_btn.setText("...")

        self.current_response = ""
        self._last_ai_browser = None

        full_messages = self._getMessages()
        resolveImageUrls(full_messages)

        use_stream = self.settings.get("stream", False)
        self.stream_thread = AIThread(full_messages, stream=use_stream, config=self.settings)
        if use_stream:
            self.stream_thread.chunk_received.connect(self._onChunkReceived)
        self.stream_thread.finished.connect(self._onStreamFinished)
        self.stream_thread.error.connect(self._onStreamError)
        self.stream_thread.start()

    def _onChunkReceived(self, chunk):
        self.current_response += chunk

        if self._last_ai_browser:
            cursor = self._last_ai_browser.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.insertText(chunk)
        else:
            self._addMessageItem("assistant", chunk)
        self._scrollToBottom()

    def _onStreamFinished(self, full_response):
        self.send_btn.setEnabled(True)
        self.send_btn.setText("发送")

        response = self.current_response or full_response
        if response:
            self._addMessage("assistant", response)
            if self._last_ai_browser:
                self._last_ai_browser.setHtml(self._renderMarkdown(response))

    def _onStreamError(self, error):
        self.send_btn.setEnabled(True)
        self.send_btn.setText("发送")
        if self.dock:
            messageBox(self.dock, "AI错误", f"请求失败: {error}", 1)

    # 销毁
    def _destroyDock(self):
        if self._standalone_window:
            if self._panel:
                self._panel.setParent(None)
            self._standalone_window.close()
            self._standalone_window.deleteLater()
            self._standalone_window = None
        if self.dock:
            if self.stream_thread and self.stream_thread.isRunning():
                self.stream_thread.requestInterruption()
                self.stream_thread.wait(2000)
            self.main_window.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None
            self._panel = None


class AIThread(QThread):
    """AI工作线程（支持流式和非流式）"""
    chunk_received = Signal(str)
    finished = Signal(str)
    error = Signal(str)

    _alive: set = set()

    def __init__(self, messages, prompt_name=None, stream=True, config=None):
        super().__init__()
        self.messages = messages
        self.prompt_name = prompt_name
        self.stream = stream
        self._config = config
        AIThread._alive.add(self)
        self.finished.connect(self._cleanup)
        self.error.connect(self._cleanup)

    def _cleanup(self):
        AIThread._alive.discard(self)

    def run(self):
        try:
            client = getAIClient(config=self._config)
            if self.stream:
                full_response = []
                def onChunk(chunk):
                    if self.isInterruptionRequested():
                        return
                    full_response.append(chunk)
                    self.chunk_received.emit(chunk)
                client.streamChat(
                    messages=self.messages,
                    callback=onChunk,
                    prompt_name=self.prompt_name
                )
                if not self.isInterruptionRequested():
                    self.finished.emit("".join(full_response))
            else:
                text, _, _ = client.chat(messages=self.messages, prompt_name=self.prompt_name)
                if not self.isInterruptionRequested():
                    self.finished.emit(text)
        except Exception as e:
            if not self.isInterruptionRequested():
                self.error.emit(str(e))


class AIDialog(QDialog):
    """AI回复对话框（支持流式和非流式，可编辑后粘贴）"""

    def __init__(self, messages, prompt_name, stream=True, dialog="", main_window=None, onGeometrySave=None, config=None):
        super().__init__()
        self._main_window = main_window
        self._onGeometrySave = onGeometrySave
        self.setWindowTitle("AI " + tr("回复"))
        self.setMinimumSize(300, 200)
        self.resize(420, 280)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)

        if dialog:
            self.restoreGeometry(QByteArray.fromBase64(dialog.encode()))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(False)
        self.text_edit.setPlaceholderText(tr("连接中..."))
        layout.addWidget(self.text_edit)

        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 0, 0, 0)

        copy_btn = QPushButton(tr("复制"))
        copy_btn.clicked.connect(self._copy)
        btn_layout.addWidget(copy_btn)

        self.paste_btn = QPushButton(tr("粘贴"))
        self.paste_btn.setEnabled(False)
        self.paste_btn.clicked.connect(self._paste)
        btn_layout.addWidget(self.paste_btn)

        self.apply_btn = QPushButton(tr("编辑器"))
        self.apply_btn.setEnabled(False)
        self.apply_btn.clicked.connect(self._apply)
        btn_layout.addWidget(self.apply_btn)

        btn_layout.addStretch()

        close_btn = QPushButton(tr("关闭"))
        close_btn.clicked.connect(self.close)
        btn_layout.addWidget(close_btn)

        layout.addLayout(btn_layout)

        self.worker = AIThread(messages, prompt_name, stream=stream, config=config)
        if stream:
            self.worker.chunk_received.connect(self._onChunk)
        self.worker.finished.connect(self._onFinished)
        self.worker.error.connect(self._onError)
        self.worker.start()

    def closeEvent(self, event):
        if self.worker.isRunning():
            self.worker.requestInterruption()
            self.worker.wait(3000)
        if self._onGeometrySave:
            self._onGeometrySave(self.saveGeometry().toBase64().data().decode())
        super().closeEvent(event)

    def _onChunk(self, chunk):
        self.text_edit.setPlaceholderText("")
        cursor = self.text_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(chunk)
        self.text_edit.setTextCursor(cursor)

    def _onFinished(self, response):
        self.text_edit.setPlaceholderText("")
        self.text_edit.setPlainText(response)
        self.apply_btn.setEnabled(True)
        self.paste_btn.setEnabled(True)

    def _onError(self, error):
        self.text_edit.setPlaceholderText("")
        self.text_edit.setPlainText(tr("请求失败") + f": {error}")
        self.apply_btn.setEnabled(False)
        self.paste_btn.setEnabled(False)

    def _copy(self):
        text = self.text_edit.toPlainText()
        if text:
            QApplication.clipboard().setText(text)
        self.close()

    def _paste(self):
        text = self.text_edit.toPlainText()
        if not text:
            return
        if self._main_window:
            editor = self._main_window.getCurrentEditor()
            if editor:
                editor.text_edit.textCursor().insertText(text)
        self.close()

    def _apply(self):
        text = self.text_edit.toPlainText()
        if not text:
            return
        if self._main_window:
            self._main_window.activateWindow()
            self._main_window.raise_()
            editor = self._main_window.getCurrentEditor()
            if editor:
                editor.text_edit.setFocus()
                editor.text_edit.textCursor().insertText(text)
        self.close()

class AutoHeightTextBrowser(QTextBrowser):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.document().contentsChanged.connect(self._adjustHeight)

    def _adjustHeight(self):
        w = self.viewport().width()
        if w <= 0:
            return
        self.document().setTextWidth(w)
        h = int(self.document().size().height()) + 5
        if self.height() != h:
            self.setFixedHeight(h)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._adjustHeight()

    def wheelEvent(self, event):
        event.ignore()

class OCRThread(QThread):
    """OCR处理线程"""
    finished = Signal(str)
    progress = Signal(int, str)

    def __init__(self, file_paths, ai_client, prompt="", parent=None):
        super().__init__(parent)
        self.file_paths = file_paths[:]
        self.ai_client = ai_client
        self.prompt = prompt or "请识别图片中的所有文字内容，直接输出识别到的文字，不需要额外说明。如果图片中没有文字，请回复'未识别到文字'。"

    def run(self):
        results = []
        total = len(self.file_paths)

        for i, file_path in enumerate(self.file_paths):
            try:
                self.progress.emit(int((i / total) * 100), os.path.basename(file_path))

                img_data, mime = imageBase64(file_path)

                messages = [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": self.prompt},
                            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{img_data}"}}
                        ]
                    }
                ]

                response, _, _ = self.ai_client.chat(messages)
                results.append(f"=== {os.path.basename(file_path)} ===\n{response}\n")

            except Exception as e:
                error_msg = str(e)
                if "image_url" in error_msg.lower() or "multimodal" in error_msg.lower():
                    error_msg += "\n\n提示：当前AI模型不支持多模态识别。\n请切换到支持多模态的模型后再试。"
                results.append(f"=== {os.path.basename(file_path)} ===\n错误: {error_msg}\n")

        self.progress.emit(100, "完成")
        self.finished.emit("\n\n".join(results))


class OCRDialog(QDialog):
    """OCR对话框"""

    def __init__(self, parent, plugin: AIExtendPlugin):
        super().__init__(parent)
        self.plugin = plugin
        self.setWindowTitle("AI OCR 识别")
        self.setMinimumSize(600, 450)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.Dialog)
        self._initUi()
        self.ocr_thread = None

    def _initUi(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(10, 10, 10, 10)

        prompt_label = QLabel("OCR提示词（注意-多模态大模型能进行OCR，纯文本大模型不能）:")
        content_layout.addWidget(prompt_label)

        self.prompt_edit = QTextEdit()
        self.prompt_edit.setMaximumHeight(60)
        self.prompt_edit.setText(self.plugin.settings.get("prompt", "请识别图片中的所有文字内容，直接输出识别到的文字，不需要额外说明。如果图片中没有文字，请回复'未识别到文字'。"))
        content_layout.addWidget(self.prompt_edit)

        self.drop_widget = FileDrop(list(EXTENSION["IMAGE"]))
        self.drop_widget.filesDropped.connect(self._onFilesDropped)
        self.drop_widget.folderDropped.connect(self._onFolderDropped)
        content_layout.addWidget(self.drop_widget)

        self.file_list = QListWidget()
        self.file_list.setAlternatingRowColors(True)
        content_layout.addWidget(self.file_list)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        content_layout.addWidget(self.progress_bar)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #666;")
        content_layout.addWidget(self.status_label)

        bottom_bar = QFrame()
        bottom_bar.setObjectName("bottom_bar")
        bottom_bar.setStyleSheet("background: #f0f0f0;")
        bottom_layout = QHBoxLayout(bottom_bar)

        bottom_layout.addWidget(QLabel("AI配置:"))

        self.ai_profile_combo = QComboBox()
        self._refreshProfile()
        bottom_layout.addWidget(self.ai_profile_combo)

        bottom_layout.addSpacing(10)

        add_files_btn = QPushButton(tr("添加文件"))
        add_files_btn.clicked.connect(self._addFiles)
        bottom_layout.addWidget(add_files_btn)

        add_folder_btn = QPushButton(tr("选择文件夹"))
        add_folder_btn.clicked.connect(self._addFolder)
        bottom_layout.addWidget(add_folder_btn)

        bottom_layout.addStretch()

        self.start_btn = QPushButton("开始识别")
        self.start_btn.clicked.connect(self._startOcr)
        self.start_btn.setEnabled(False)
        bottom_layout.addWidget(self.start_btn)

        save_btn = QPushButton("保存设置")
        save_btn.clicked.connect(self._saveSettings)
        bottom_layout.addWidget(save_btn)

        clear_btn = QPushButton("清空")
        clear_btn.clicked.connect(self._clearFiles)
        bottom_layout.addWidget(clear_btn)

        content_layout.addWidget(bottom_bar)

        layout.addWidget(content)

        self.file_paths = []
        self.ocr_result = ""

    def _refreshProfile(self):
        """刷新AI配置列表"""
        profiles = self.plugin.settings.get("profiles", {})
        profile_names = list(profiles.keys()) if profiles else ["默认配置"]

        self.ai_profile_combo.clear()
        self.ai_profile_combo.addItems(profile_names)

        current_profile = self.plugin.settings.get("active", "默认配置")
        self.ai_profile_combo.setCurrentText(current_profile)

    def _onFilesDropped(self, files: list):
        for path in files:
            self._addFile(path)

    def _onFolderDropped(self, folder: str):
        self._addFolderFiles(folder)

    def _addFiles(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择图片", "",
            "图片文件 (*.png *.jpg *.jpeg *.gif *.bmp *.webp);;所有文件 (*.*)"
        )
        for f in files:
            self._addFile(f)

    def _addFolder(self):
        folder = getFilePath(self, "选择文件夹", mode="dir")
        if folder:
            self._addFolderFiles(folder)

    def _addFolderFiles(self, folder: str):
        image_exts = EXTENSION["IMAGE"]
        for root, _, files in os.walk(folder):
            for f in files:
                if os.path.splitext(f)[1].lower() in image_exts:
                    self._addFile(os.path.join(root, f))

    def _addFile(self, path: str):
        path = os.path.normpath(path)
        if path not in self.file_paths:
            self.file_paths.append(path)
            self.file_list.addItem(path)
            self.start_btn.setEnabled(len(self.file_paths) > 0)

    def _clearFiles(self):
        self.file_paths.clear()
        self.file_list.clear()
        self.start_btn.setEnabled(False)

    def _startOcr(self):
        if not self.file_paths:
            return

        self.start_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.status_label.setText("正在识别...")

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)

        try:
            self._saveSettings(show_message=False)
            ai_client = self.plugin._buildAiClient(
                profile_name=self.ai_profile_combo.currentText().strip()
            )
            prompt = self.prompt_edit.toPlainText().strip() or self.plugin.settings.get("prompt", "")

            self.ocr_thread = OCRThread(self.file_paths, ai_client, prompt)
            self.ocr_thread.progress.connect(self._onProgress)
            self.ocr_thread.finished.connect(self._onFinished)
            self.ocr_thread.start()

        except Exception as e:
            QApplication.restoreOverrideCursor()
            messageBox(self, "错误", f"初始化AI失败: {str(e)}", 1)
            self.start_btn.setEnabled(True)
            self.progress_bar.setVisible(False)

    def _saveSettings(self, show_message=True):
        try:
            self.plugin.settings["prompt"] = self.prompt_edit.toPlainText().strip()
            self.plugin.settings["active"] = self.ai_profile_combo.currentText().strip()
            self.plugin.saveConfig()
            if show_message:
                messageBox(self, "保存成功", "OCR设置已保存", 1)
        except Exception as e:
            if show_message:
                messageBox(self, "保存失败", f"保存设置时出错: {str(e)}", 1)

    def _onProgress(self, value: int, filename: str):
        self.progress_bar.setValue(value)
        self.status_label.setText(f"正在识别: {filename}")

    def _onFinished(self, result: str):
        QApplication.restoreOverrideCursor()
        self.ocr_result = result
        self.status_label.setText("识别完成")
        self.progress_bar.setVisible(False)
        self.start_btn.setEnabled(True)

        self._saveToDefault()
        messageBox(self, "完成", "OCR识别完成，结果已自动保存", 1)

    def _saveToDefault(self):
        if not self.ocr_result:
            return

        save_path = AI_dir / f"{getTimestamp()}.txt"

        try:
            with open(save_path, "w", encoding="utf-8") as f:
                f.write(self.ocr_result)

            self._openFileWithApp(str(save_path))

        except Exception as e:
            messageBox(self, "保存失败", str(e), 1)

    def _openFileWithApp(self, file_path: str):
        try:
            main_window = self.parent()
            if main_window and hasattr(main_window, 'openFilePath'):
                main_window.openFilePath(file_path)
            elif main_window and hasattr(main_window, 'open_file'):
                main_window.open_file()
            else:
                os.startfile(file_path)
        except Exception:
            logger.exception("打开文件失败")
