import os
import json
import uuid
from datetime import datetime

import markdown

from PySide6.QtWidgets import QDockWidget, QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QTextBrowser, QPushButton, QTextEdit, QFrame, QComboBox, QLabel, QApplication, QFileDialog, QToolTip, QLineEdit, QDialog, QListWidget, QListWidgetItem
from PySide6.QtGui import QAction, QDragEnterEvent, QDropEvent, QPixmap, QCursor, QTextCursor
from PySide6.QtCore import Qt, Signal, QThread, QTimer

from src.plugin import PluginBase
from src.util import data_dir, logger, getFilePath, messageBox, inputDialog
from src.core.AI import AI_ADAPTER, getAIClient, resolve_image_urls, get_adapter_endpoint

history_file = data_dir / "ai.json"

class AutoHeightTextBrowser(QTextBrowser):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.document().contentsChanged.connect(self._adjust_height)

    def _adjust_height(self):
        w = self.viewport().width()
        if w <= 0:
            return
        self.document().setTextWidth(w)
        h = int(self.document().size().height()) + 5
        if self.height() != h:
            self.setFixedHeight(h)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._adjust_height()

    def wheelEvent(self, event):
        event.ignore()


class AINonStreamThread(QThread):
    """AI非流式响应线程"""
    finished = Signal(str)
    error = Signal(str)

    def __init__(self, messages, client):
        super().__init__()
        self.messages = messages
        self.client = client

    def run(self):
        try:
            text, _, _ = self.client.chat(messages=self.messages)
            self.finished.emit(text)
        except Exception as e:
            self.error.emit(str(e))

class AIStreamThread(QThread):
    """AI流式响应线程"""
    chunk_received = Signal(str)
    finished = Signal(str)
    error = Signal(str)

    def __init__(self, messages, client):
        super().__init__()
        self.messages = messages
        self.client = client
        self._is_running = True

    def run(self):
        try:
            full_response = []

            def on_chunk(chunk):
                if self._is_running:
                    full_response.append(chunk)
                    self.chunk_received.emit(chunk)

            self.client.stream_chat(self.messages, callback=on_chunk)
            self.finished.emit(''.join(full_response))
        except Exception as e:
            self.error.emit(str(e))

    def stop(self):
        self._is_running = False


class AIViewPlugin(PluginBase):

    version = "1.0.0"
    description = "AI 聊天面板"
    file = [history_file]

    def __init__(self, main_window):
        super().__init__(main_window)
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

    def initialize(self):
        self._load_history()
        self._create_ui()
        self.dock.setStyleSheet(self.main_window.styleSheet())

    def getAction(self):
        if self._toggle_action is None:
            self._toggle_action = QAction(self.description, self.main_window)
            self._toggle_action.triggered.connect(self._toggle_panel)
        return self._toggle_action

    def deactivate(self):
        self._destroy_dock()

    def _create_ui(self):
        if self.dock is not None:
            return

        self.dock = QDockWidget("AI助手", self.main_window)
        self.dock.setObjectName("AIViewDock")
        self.dock.setMaximumWidth(600)
        self.dock.setFeatures(
            QDockWidget.DockWidgetMovable |
            QDockWidget.DockWidgetFloatable |
            QDockWidget.DockWidgetClosable
        )
        self.dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)

        self._ensure_content()
        self.dock.setWidget(self._panel)
        self.dock.visibilityChanged.connect(self._on_visibility_changed)

        self.main_window.addDockWidget(Qt.LeftDockWidgetArea, self.dock)
        self._reload_conversation_list()
        self.dock.hide()

    def _ensure_content(self):
        if self._panel is not None:
            return
        panel = QWidget()
        self._panel = panel
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self._build_top_bar(layout)
        self._build_prompt_bar(layout)
        self._build_message_list(layout)
        self._build_input_area(layout)

    def _build_top_bar(self, layout):
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
        profiles = self._get_available_profiles()
        active = self._get_profile_name()
        for p in profiles:
            self._profile_combo.addItem(p)
        idx = self._profile_combo.findText(active)
        if idx >= 0:
            self._profile_combo.setCurrentIndex(idx)
        self._profile_combo.currentIndexChanged.connect(self._on_profile_changed)

        self._lb_label = QLabel("负载均衡")
        self._lb_label.setStyleSheet("color: #008000; font-size: 14px; padding: 0 4px;")
        row1.addWidget(self._lb_label)

        row1.addWidget(QLabel("配置"))
        row1.addWidget(self._profile_combo, 1)
        self._refresh_lb_ui()

        new_conv_btn = QPushButton("新对话")
        new_conv_btn.setFixedSize(70, 30)
        new_conv_btn.clicked.connect(self._new_conversation)
        row1.addWidget(new_conv_btn)

        search_btn = QPushButton("搜索")
        search_btn.setFixedSize(60, 30)
        search_btn.clicked.connect(self._search_conversations)
        row1.addWidget(search_btn)

        export_btn = QPushButton("导出")
        export_btn.setFixedSize(60, 30)
        export_btn.clicked.connect(self._export_conversation)
        row1.addWidget(export_btn)

        top_layout.addLayout(row1)

        # 第二行：模型 + 刷新
        row2 = QHBoxLayout()
        row2.setSpacing(4)

        row2.addWidget(QLabel("模型"))

        self._model_combo = QComboBox()
        self._model_combo.setMinimumWidth(100)
        self._model_combo.setEditable(True)
        self._update_model_combo()
        self._model_combo.currentTextChanged.connect(self._on_model_changed)
        row2.addWidget(self._model_combo, 1)

        refresh_btn = QPushButton("刷新")
        refresh_btn.setFixedSize(60, 30)
        refresh_btn.clicked.connect(self._refresh_models)
        row2.addWidget(refresh_btn)

        top_layout.addLayout(row2)

        # 第三行：对话 + 重命名 + 删除
        row3 = QHBoxLayout()
        row3.setSpacing(4)

        self._conv_combo = QComboBox()
        self._conv_combo.setMinimumWidth(100)
        self._conv_combo.currentIndexChanged.connect(self._on_conversation_changed)
        row3.addWidget(QLabel("对话"))
        row3.addWidget(self._conv_combo, 1)

        rename_btn = QPushButton("重命名")
        rename_btn.setFixedSize(70, 30)
        rename_btn.clicked.connect(self._rename_conversation)
        row3.addWidget(rename_btn)

        delete_btn = QPushButton("删除")
        delete_btn.setFixedSize(60, 30)
        delete_btn.clicked.connect(self._delete_conversation)
        row3.addWidget(delete_btn)

        top_layout.addLayout(row3)

        layout.addWidget(top_frame)

    def _build_prompt_bar(self, layout):
        config = self.main_window.config if self.main_window else None
        prompts = config.get("AI.prompts", {}) if config else {}
        builtin_names = ("系统提示词", "自动补全")
        visible_prompts = [n for n in prompts if n not in builtin_names]

        if visible_prompts:
            prompt_frame = QFrame()
            prompt_frame.setObjectName("ai_prompt_bar")
            prompt_layout = QHBoxLayout(prompt_frame)
            prompt_layout.setContentsMargins(5, 5, 5, 5)

            for name in visible_prompts:
                btn = QPushButton(name)
                btn.setFixedWidth(75)
                btn.clicked.connect(lambda checked, n=name: self._on_prompt_clicked(n))
                prompt_layout.addWidget(btn)

            prompt_layout.addStretch()
            layout.addWidget(prompt_frame)

    def _build_message_list(self, layout):
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
        self._load_messages_to_ui()

        layout.addWidget(self._message_scroll, 1)

    def _build_input_area(self, layout):
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
        self.input_edit.dragEnterEvent = self._input_drag_enter
        self.input_edit.dropEvent = self._input_drop
        input_layout.addWidget(self.input_edit)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(4)

        clear_btn = QPushButton("清空")
        clear_btn.setFixedSize(60, 30)
        clear_btn.clicked.connect(self._clear_history)
        btn_layout.addWidget(clear_btn)

        upload_btn = QPushButton("上传文件")
        upload_btn.setFixedSize(80, 30)
        upload_btn.clicked.connect(self._on_upload_file_clicked)
        btn_layout.addWidget(upload_btn)

        btn_layout.addStretch()

        self.send_btn = QPushButton("发送")
        self.send_btn.setFixedSize(60, 30)
        self.send_btn.clicked.connect(self._send_message)
        self.send_btn.setDefault(True)
        btn_layout.addWidget(self.send_btn)

        input_layout.addLayout(btn_layout)
        layout.addWidget(input_frame)

        self.input_edit.keyPressEvent = self._on_input_keyPress

    # ── 对话 / 配置 切换 ──────────────────────────────

    def _get_available_profiles(self):
        config = self.main_window.config if self.main_window else None
        if not config:
            return ["默认配置"]
        profiles = config.get("AI.profiles", {})
        return list(profiles.keys()) if profiles else ["默认配置"]

    def _on_profile_changed(self, index):
        name = self._profile_combo.currentText()
        if not name:
            return
        config = self.main_window.config
        if config:
            config.set("AI.active", name)
            config.save()
        self._update_model_combo()
        self._reload_conversation_list()

    def _on_conversation_changed(self, index):
        conv_id = self._conv_combo.currentData()
        if conv_id:
            self._current_conv_id = conv_id
            self._load_messages_to_ui()

    def _new_conversation(self):
        conv_id = str(uuid.uuid4())[:8]
        profile = self._get_profile_name()
        if profile not in self._history:
            self._history[profile] = {}
        count = len(self._history[profile]) + 1
        self._history[profile][conv_id] = {
            "title": f"对话 {count}",
            "messages": []
        }
        self._current_conv_id = conv_id
        self._save_history()
        self._reload_conversation_list(select_id=conv_id)

    def _rename_conversation(self):
        conv_id = self._conv_combo.currentData()
        if not conv_id:
            return
        profile = self._get_profile_name()
        conv = self._history.get(profile, {}).get(conv_id)
        if not conv:
            return
        new_title = inputDialog(self.dock, "重命名对话", "新名称:", default=conv.get("title", ""))
        if new_title:
            conv["title"] = new_title
            self._save_history()
            self._reload_conversation_list(select_id=conv_id)

    def _delete_conversation(self):
        conv_id = self._conv_combo.currentData()
        if not conv_id:
            return
        if not messageBox(self.dock, "删除对话", "确定要删除当前对话吗？", 2):
            return
        profile = self._get_profile_name()
        if profile in self._history and conv_id in self._history[profile]:
            del self._history[profile][conv_id]
            self._save_history()
        remaining = list(self._history.get(profile, {}).keys())
        if remaining:
            self._current_conv_id = remaining[0]
        else:
            self._current_conv_id = None
            self._new_conversation()
        self._reload_conversation_list()

    def _export_conversation(self):
        conv = self._get_current_conv()
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
            content = self._format_export_markdown(title, messages)
        else:
            content = self._format_export_text(title, messages)
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
        except Exception as e:
            messageBox(self.dock, "导出失败", f"写入文件失败: {e}", 1)

    @staticmethod
    def _format_export_markdown(title: str, messages: list) -> str:
        lines = [f"# {title}\n"]
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            ts = msg.get("timestamp", "")
            if isinstance(content, list):
                parts = []
                for p in content:
                    if isinstance(p, dict) and p.get("type") == "text":
                        parts.append(p.get("text", ""))
                    elif isinstance(p, dict) and p.get("type") == "image_url":
                        parts.append("[图片]")
                content = "\n".join(parts)
            if ts:
                lines.append(f"\n## {ts[:19]}\n")
            label = "你" if role == "user" else "AI"
            lines.append(f"\n**{label}：**\n{content}\n")
        return "".join(lines)

    @staticmethod
    def _format_export_text(title: str, messages: list) -> str:
        lines = [f"{title}", "=" * len(title), ""]
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            ts = msg.get("timestamp", "")
            if isinstance(content, list):
                parts = []
                for p in content:
                    if isinstance(p, dict) and p.get("type") == "text":
                        parts.append(p.get("text", ""))
                    elif isinstance(p, dict) and p.get("type") == "image_url":
                        parts.append("[图片]")
                content = "\n".join(parts)
            label = "你" if role == "user" else "AI"
            if ts:
                lines.append(f"[{ts[:19]}]")
            lines.append(f"{label}: {content}\n")
        return "\n".join(lines)

    def _search_conversations(self):
        profile = self._get_profile_name()
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

        def do_search():
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
                        parts = []
                        for p in content:
                            if isinstance(p, dict) and p.get("type") == "text":
                                parts.append(p.get("text", ""))
                        content = "\n".join(parts)
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

        search_input.textChanged.connect(do_search)

        def on_result_clicked(item):
            conv_id = item.data(Qt.ItemDataRole.UserRole)
            if conv_id:
                self._current_conv_id = conv_id
                self._reload_conversation_list(select_id=conv_id)
                self._load_messages_to_ui()
                dlg.accept()

        result_list.itemClicked.connect(on_result_clicked)
        dlg.exec()

    def _on_content_click(self, event, text):
        if event.button() == Qt.MouseButton.LeftButton:
            clipboard = QApplication.clipboard()
            clipboard.setText(str(text))
            QToolTip.showText(QCursor.pos(), "已复制", self._message_scroll)

    def _update_model_combo(self):
        self._model_combo_updating = True
        config = self.main_window.config if self.main_window else None
        if config:
            profiles = config.config.get("AI", {}).get("profiles", {})
            name = self._get_profile_name()
            profile = profiles.get(name, {})
            model = profile.get("model", "") or ""
            if model:
                self._model_combo.setCurrentText(model)
            else:
                self._model_combo.setCurrentText("")
                if self._model_combo.lineEdit():
                    self._model_combo.lineEdit().setPlaceholderText("输入模型名...")
        self._model_combo_updating = False

    def _on_model_changed(self, text):
        if self._model_combo_updating:
            return
        config = self.main_window.config if self.main_window else None
        if not config:
            return
        profiles = config.config.get("AI", {}).get("profiles", {})
        name = self._get_profile_name()
        if name in profiles:
            profiles[name]["model"] = text
            config.save()

    def _refresh_models(self):
        config = self.main_window.config if self.main_window else None
        if not config:
            return
        try:
            profiles = config.config.get("AI", {}).get("profiles", {})
            name = self._get_profile_name()
            profile = profiles.get(name, {})
            if not profile.get("api_key") or not profile.get("api_url"):
                return
            for ep_name, cls, url in AI_ADAPTER:
                if url == profile["api_url"]:
                    endpoint_name = ep_name
                    break
            else:
                endpoint_name = "自定义"
            adapter = get_adapter_endpoint(endpoint_name, config,
                                           api_key=profile.get("api_key", ""),
                                           api_url=profile.get("api_url", ""))
            models = adapter.get_models()
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

    def _reload_conversation_list(self, select_id=None):
        self._conv_combo.blockSignals(True)
        self._conv_combo.clear()
        profile = self._get_profile_name()
        convs = self._history.get(profile, {})
        if not convs:
            self._new_conversation()
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
        self._load_messages_to_ui()

    def _on_visibility_changed(self, visible):
        if visible:
            self._refresh_lb_ui()

    def _refresh_lb_ui(self):
        lb = self.main_window.config.get("AI.load_balance", {}).get("enabled", False) if self.main_window else False
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

    def _load_history(self):
        self._history = {}
        if history_file.exists():
            try:
                with open(history_file, 'r', encoding='utf-8') as f:
                    all_data = json.load(f)
                self._history = {k: v for k, v in all_data.items() if not k.startswith("_")}
            except Exception:
                logger.exception("加载 AI 历史失败")
                self._history = {}
        self._ensure_default_conversation()

    def _ensure_default_conversation(self):
        profile = self._get_profile_name()
        if profile not in self._history:
            self._history[profile] = {}
        convs = self._history[profile]
        if not convs:
            conv_id = str(uuid.uuid4())[:8]
            convs[conv_id] = {"title": "对话 1", "messages": []}
            self._current_conv_id = conv_id
        elif self._current_conv_id is None or self._current_conv_id not in convs:
            self._current_conv_id = list(convs.keys())[0]

    def _save_history(self):
        try:
            history_file.parent.mkdir(parents=True, exist_ok=True)
            all_data = {}
            if history_file.exists():
                with open(history_file, 'r', encoding='utf-8') as f:
                    existing = json.load(f)
                for k, v in existing.items():
                    if k.startswith("_"):
                        all_data[k] = v
            all_data.update(self._history)
            with open(history_file, 'w', encoding='utf-8') as f:
                json.dump(all_data, f, ensure_ascii=False, indent=2)
        except Exception:
            logger.exception("保存 AI 历史失败")

    def _get_profile_name(self):
        config = self.main_window.config if self.main_window else None
        active = config.get("AI.active", "默认配置") if config else "默认配置"
        return active or "默认配置"

    def _get_current_conv(self):
        profile = self._get_profile_name()
        if profile not in self._history:
            self._history[profile] = {}
        convs = self._history[profile]
        if not convs:
            self._ensure_default_conversation()
        if self._current_conv_id not in convs:
            self._current_conv_id = list(convs.keys())[0]
        return convs.get(self._current_conv_id, {"title": "对话", "messages": []})

    def _get_messages(self):
        conv = self._get_current_conv()
        return conv.get("messages", [])

    def _add_message(self, role, content):
        profile = self._get_profile_name()
        if profile not in self._history:
            self._history[profile] = {}
        convs = self._history[profile]
        if not convs or self._current_conv_id not in convs:
            self._new_conversation()
            return self._add_message(role, content)
        conv = convs[self._current_conv_id]
        conv["messages"].append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat()
        })
        max_messages = 100
        if len(conv["messages"]) > max_messages:
            conv["messages"] = conv["messages"][-max_messages:]
        self._save_history()

    def _clear_history(self):
        profile = self._get_profile_name()
        if profile in self._history and self._current_conv_id:
            conv = self._history[profile].get(self._current_conv_id)
            if conv:
                conv["messages"] = []
                self._save_history()
        self._load_messages_to_ui()

    # ── UI 交互 ───────────────────────────────────────

    def _toggle_panel(self):
        main_visible = self.main_window.isVisible() and not (self.main_window.windowState() & Qt.WindowState.WindowMinimized)
        if not main_visible:
            self._toggle_standalone()
            return

        if self._standalone_window:
            self._move_panel_to_dock()
        if self.dock:
            if self.dock.widget() is not self._panel:
                self.dock.setWidget(self._panel)
            self.dock.setVisible(not self.dock.isVisible())

    def _toggle_standalone(self):
        if self._standalone_window and self._standalone_window.isVisible():
            self._move_panel_to_dock()
            return

        if self._standalone_window:
            if self._panel and self._panel.parent() is self._standalone_window:
                self._panel.setParent(None)
            self._standalone_window.deleteLater()
            self._standalone_window = None

        self._ensure_content()
        if self.dock:
            self.dock.setWidget(QWidget())

        self._standalone_window = QFrame(None, Qt.Window | Qt.WindowStaysOnTopHint)
        self._standalone_window.setStyleSheet(self.main_window.styleSheet())
        self._standalone_window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
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

    def _move_panel_to_dock(self):
        if self._standalone_window:
            self._panel.setParent(None)
            self._standalone_window.close()
            self._standalone_window.deleteLater()
            self._standalone_window = None
        if self.dock:
            self.dock.setWidget(self._panel)
            self._panel.show()

    def _on_input_keyPress(self, event):
        if event.key() == Qt.Key.Key_Return and event.modifiers() == Qt.KeyboardModifier.NoModifier:
            self._send_message()
        else:
            QTextEdit.keyPressEvent(self.input_edit, event)

    def _input_drag_enter(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def _input_drop(self, event: QDropEvent):
        urls = event.mimeData().urls()
        if not urls:
            return
        client = getAIClient(self.main_window.config if self.main_window else None)
        for url in urls:
            path = url.toLocalFile()
            if not path:
                continue
            if os.path.isdir(path):
                messages = client.build_folder_message(path)
                if messages:
                    self._send_messages(messages)
                continue
            ext = os.path.splitext(path)[1].lower()
            if ext in ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp'):
                if path not in self._pending_images:
                    self._pending_images.append(path)
            else:
                if path not in self._pending_files:
                    self._pending_files.append(path)
        self._refresh_preview_bar()

    def _refresh_preview_bar(self):
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
            tag = self._build_preview_tag(path, is_image=True)
            layout.addWidget(tag)
        for path in self._pending_files:
            tag = self._build_preview_tag(path, is_image=False)
            layout.addWidget(tag)
        layout.addStretch()
        self._preview_bar.setVisible(True)

    def _build_preview_tag(self, path: str, is_image: bool) -> QFrame:
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
        remove_btn.clicked.connect(lambda checked, p=path: self._remove_pending_item(p))
        row.addWidget(remove_btn)

        return frame

    def _remove_pending_item(self, path: str):
        if path in self._pending_images:
            self._pending_images.remove(path)
        if path in self._pending_files:
            self._pending_files.remove(path)
        self._refresh_preview_bar()

    def _load_messages_to_ui(self):
        if not self._message_layout:
            return
        self._clear_message_widgets()
        self._last_ai_browser = None
        for msg in self._get_messages():
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role and content:
                self._add_message_item(role, content)

    def _clear_message_widgets(self):
        layout = self._message_layout
        if not layout:
            return
        while layout.count() > 0:
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        layout.addStretch()

    def _content_to_text(self, content):
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

    def _make_message_clickable(self, widget, text):
        widget.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        widget._long_pressed = False

        orig_press = widget.mousePressEvent
        def on_press(e):
            widget._long_pressed = False
            widget._lp_timer = QTimer()
            widget._lp_timer.setSingleShot(True)
            widget._lp_timer.setInterval(500)
            widget._lp_timer.timeout.connect(lambda: _on_long_press())
            widget._lp_timer.start()
            if orig_press:
                orig_press(e)

        orig_release = widget.mouseReleaseEvent
        def on_release(e):
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

        widget.mousePressEvent = on_press
        widget.mouseReleaseEvent = on_release

    def _add_message_item(self, role, content):
        text = self._content_to_text(content)
        is_ai = role == "assistant"

        frame = QFrame()
        frame.setObjectName("msg_frame")
        frame.setStyleSheet("QFrame#msg_frame { background: transparent; border-bottom: 1px solid #eee; }")
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(10, 6, 10, 6)
        fl.setSpacing(3)

        header = QLabel("AI:" if is_ai else "你:")
        header.setStyleSheet("font-weight: bold; color: #666; border: none; background: transparent; font-size: 12px;")
        self._make_message_clickable(header, text)
        fl.addWidget(header)

        if is_ai:
            browser = AutoHeightTextBrowser()
            browser.setOpenExternalLinks(False)
            browser.setStyleSheet("border: none; background: transparent;")
            browser.setHtml(self._render_markdown(text))
            self._make_message_clickable(browser, text)
            fl.addWidget(browser)
            self._last_ai_browser = browser
        else:
            label = QLabel(text)
            label.setWordWrap(True)
            label.setStyleSheet("border: none; background: transparent;")
            self._make_message_clickable(label, text)
            fl.addWidget(label)

        layout = self._message_layout
        layout.insertWidget(layout.count() - 1, frame)
        self._scroll_to_bottom()

    def _render_markdown(self, text: str) -> str:
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

    def _scroll_to_bottom(self):
        if self._message_scroll:
            QTimer.singleShot(0, lambda: self._message_scroll.verticalScrollBar().setValue(
                self._message_scroll.verticalScrollBar().maximum()))

    def _on_prompt_clicked(self, prompt_name):
        config = self.main_window.config if self.main_window else None
        if not config:
            return
        prompts = config.get("AI.prompts", {})
        if isinstance(prompts, dict):
            value = prompts.get(prompt_name, "")
            if "{request}" in value:
                value = value.replace("{request}", "")
            self.input_edit.setPlainText(value)

    def _on_upload_file_clicked(self):
        file_path = getFilePath(self.main_window, "选择文件")
        if not file_path:
            return
        client = getAIClient(self.main_window.config if self.main_window else None)
        messages = client.build_file_message(file_path)
        if messages:
            self._send_messages(messages)

    # ── 发送消息 / 流式响应 ────────────────────────────

    def _send_message(self):
        text = self.input_edit.toPlainText().strip()
        content_parts = []
        if text:
            content_parts.append({"type": "text", "text": text})
        for path in self._pending_images:
            content_parts.append({"type": "image_url", "url": f"file://{path}"})

        messages = []
        if content_parts:
            messages.append({"role": "user", "content": content_parts})

        client = getAIClient(self.main_window.config if self.main_window else None)
        for path in self._pending_files:
            file_msgs = client.build_file_message(path)
            if file_msgs:
                messages.extend(file_msgs)

        if not messages:
            return

        self._pending_images.clear()
        self._pending_files.clear()
        self._refresh_preview_bar()
        self._send_messages(messages)

    def _send_messages(self, messages):
        self.input_edit.clear()

        for msg in messages:
            content = msg.get("content", "")
            self._add_message("user", content)
            self._add_message_item("user", content)

        self.send_btn.setEnabled(False)
        self.send_btn.setText("...")

        self.current_response = ""
        self._last_ai_browser = None

        full_messages = self._get_messages()
        resolve_image_urls(full_messages)
        client = getAIClient(self.main_window.config if self.main_window else None)

        use_stream = self.main_window.config.get("AI.stream", True) if self.main_window else True
        if use_stream:
            self.stream_thread = AIStreamThread(full_messages, client)
            self.stream_thread.chunk_received.connect(self._on_chunk_received)
        else:
            self.stream_thread = AINonStreamThread(full_messages, client)
        self.stream_thread.finished.connect(self._on_stream_finished)
        self.stream_thread.error.connect(self._on_stream_error)
        self.stream_thread.start()

    def _on_chunk_received(self, chunk):
        self.current_response += chunk

        if self._last_ai_browser:
            cursor = self._last_ai_browser.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.insertText(chunk)
        else:
            self._add_message_item("assistant", chunk)
        self._scroll_to_bottom()

    def _on_stream_finished(self, full_response):
        self.send_btn.setEnabled(True)
        self.send_btn.setText("发送")

        response = self.current_response or full_response
        if response:
            self._add_message("assistant", response)
            if self._last_ai_browser:
                self._last_ai_browser.setHtml(self._render_markdown(response))

    def _on_stream_error(self, error):
        self.send_btn.setEnabled(True)
        self.send_btn.setText("发送")
        if self.dock:
            messageBox(self.dock, "AI错误", f"请求失败: {error}", 1)

    # 销毁
    def _destroy_dock(self):
        if self._standalone_window:
            if self._panel:
                self._panel.setParent(None)
            self._standalone_window.close()
            self._standalone_window.deleteLater()
            self._standalone_window = None
        if self.dock:
            if self.stream_thread and self.stream_thread.isRunning():
                self.stream_thread.stop()
                self.stream_thread.wait(2000)
            self.main_window.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None
            self._panel = None
