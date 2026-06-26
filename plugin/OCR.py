import os

from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QListWidget, QFileDialog, QWidget, QFrame, QApplication, QProgressBar, QComboBox, QTextEdit
from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtGui import QAction

from src.plugin import PluginBase
from src.util import FileDrop, data_dir, logger, getTimestamp, getFilePath, messageBox
from src.config import getConfig
from src.core.AI import _read_image_base64, getAIClient

save_dir = data_dir / "OCR"

class OCRPlugin(PluginBase):
    """OCR插件"""
    
    version = "1.0.0"
    description = "AI 文本识别"
    file = [data_dir / "OCR"]
    
    def __init__(self, main_window):
        super().__init__(main_window)
        self.settings = {
            "ai_profile": "默认配置",
            "prompt": "请识别图片中的所有文字内容，直接输出识别到的文字，不需要额外说明。如果图片中没有文字，请回复'未识别到文字'。"
        }

    def getAction(self):
        action = QAction(self.description, self.main_window)
        action.triggered.connect(self.show_ocr_dialog)
        return action
    
    def _build_ai_client(self, profile_name=None):
        selected = profile_name or self.settings.get("ai_profile", "默认配置")
        return getAIClient(profile_name=selected)

    def show_ocr_dialog(self):
        """显示OCR对话框"""
        dialog = OCRDialog(self.main_window, self)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.show()

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
                
                img_data, mime = _read_image_base64(file_path)

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
    
    def __init__(self, parent, plugin: OCRPlugin):
        super().__init__(parent)
        self.plugin = plugin
        self.setWindowTitle("AI OCR 识别")
        self.setMinimumSize(600, 450)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.Dialog)
        self._init_ui()
        self.ocr_thread = None
    
    def _init_ui(self):
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
        
        self.drop_widget = FileDrop(['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp'])
        self.drop_widget.filesDropped.connect(self._on_files_dropped)
        self.drop_widget.folderDropped.connect(self._on_folder_dropped)
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
        self._refresh_ai_profiles()
        bottom_layout.addWidget(self.ai_profile_combo)
        
        bottom_layout.addSpacing(10)
        
        add_files_btn = QPushButton("添加文件")
        add_files_btn.clicked.connect(self._add_files)
        bottom_layout.addWidget(add_files_btn)
        
        add_folder_btn = QPushButton("添加文件夹")
        add_folder_btn.clicked.connect(self._add_folder)
        bottom_layout.addWidget(add_folder_btn)
        
        bottom_layout.addStretch()
        
        self.start_btn = QPushButton("开始识别")
        self.start_btn.clicked.connect(self._start_ocr)
        self.start_btn.setEnabled(False)
        bottom_layout.addWidget(self.start_btn)
        
        save_btn = QPushButton("保存设置")
        save_btn.clicked.connect(self._save_settings)
        bottom_layout.addWidget(save_btn)
        
        clear_btn = QPushButton("清空")
        clear_btn.clicked.connect(self._clear_files)
        bottom_layout.addWidget(clear_btn)
        
        content_layout.addWidget(bottom_bar)
        
        layout.addWidget(content)
        
        self.file_paths = []
        self.ocr_result = ""
    
    def _refresh_ai_profiles(self):
        """刷新AI配置列表"""
        config = getConfig()
        ai_config = config.get("AI", {})
        profiles = ai_config.get("profiles", {}) if isinstance(ai_config, dict) else {}
        profile_names = list(profiles.keys()) if profiles else ["默认配置"]
        
        self.ai_profile_combo.clear()
        self.ai_profile_combo.addItems(profile_names)
        
        current_profile = self.plugin.settings.get("ai_profile", "默认配置")
        self.ai_profile_combo.setCurrentText(current_profile)
    
    def _on_files_dropped(self, files: list):
        """处理拖拽的文件"""
        for path in files:
            self._add_file(path)
    
    def _on_folder_dropped(self, folder: str):
        """处理拖拽的文件夹"""
        self._add_folder_files(folder)
    
    def _add_files(self):
        """添加文件"""
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择图片", "", 
            "图片文件 (*.png *.jpg *.jpeg *.gif *.bmp *.webp);;所有文件 (*.*)"
        )
        for f in files:
            self._add_file(f)
    
    def _add_folder(self):
        """添加文件夹"""
        folder = getFilePath(self, "选择文件夹", mode="dir")
        if folder:
            self._add_folder_files(folder)
    
    def _add_folder_files(self, folder: str):
        """添加文件夹中的图片"""
        image_exts = {'.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp'}
        for root, _, files in os.walk(folder):
            for f in files:
                if os.path.splitext(f)[1].lower() in image_exts:
                    self._add_file(os.path.join(root, f))
    
    def _add_file(self, path: str):
        """添加单个文件"""
        path = os.path.normpath(path)
        if path not in self.file_paths:
            self.file_paths.append(path)
            self.file_list.addItem(path)
            self.start_btn.setEnabled(len(self.file_paths) > 0)
    
    def _clear_files(self):
        """清空文件列表"""
        self.file_paths.clear()
        self.file_list.clear()
        self.start_btn.setEnabled(False)
    
    def _start_ocr(self):
        """开始OCR识别"""
        if not self.file_paths:
            return
        
        self.start_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.status_label.setText("正在识别...")
        
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        
        try:
            self._save_settings(show_message=False)
            ai_client = self.plugin._build_ai_client(
                profile_name=self.ai_profile_combo.currentText().strip()
            )
            prompt = self.prompt_edit.toPlainText().strip() or self.plugin.settings.get("prompt", "")
            
            self.ocr_thread = OCRThread(self.file_paths, ai_client, prompt)
            self.ocr_thread.progress.connect(self._on_progress)
            self.ocr_thread.finished.connect(self._on_finished)
            self.ocr_thread.start()
            
        except Exception as e:
            QApplication.restoreOverrideCursor()
            messageBox(self, "错误", f"初始化AI失败: {str(e)}", 1)
            self.start_btn.setEnabled(True)
            self.progress_bar.setVisible(False)
    
    def _save_settings(self, show_message=True):
        """保存当前设置到插件配置"""
        try:
            self.plugin.settings["prompt"] = self.prompt_edit.toPlainText().strip()
            self.plugin.settings["ai_profile"] = self.ai_profile_combo.currentText().strip()
            self.plugin.saveConfig()
            if show_message:
                messageBox(self, "保存成功", "OCR设置已保存", 1)
        except Exception as e:
            if show_message:
                messageBox(self, "保存失败", f"保存设置时出错: {str(e)}", 1)
    
    def _on_progress(self, value: int, filename: str):
        """进度更新"""
        self.progress_bar.setValue(value)
        self.status_label.setText(f"正在识别: {filename}")
    
    def _on_finished(self, result: str):
        """OCR完成"""
        QApplication.restoreOverrideCursor()
        self.ocr_result = result
        self.status_label.setText("识别完成")
        self.progress_bar.setVisible(False)
        self.start_btn.setEnabled(True)
        
        self._save_to_default()
        messageBox(self, "完成", "OCR识别完成，结果已自动保存", 1)
    
    def _save_to_default(self):
        """保存到默认位置"""
        if not self.ocr_result:
            return

        save_dir.mkdir(parents=True, exist_ok=True)
        save_path = save_dir / f"{getTimestamp()}.txt"
        
        try:
            with open(save_path, 'w', encoding='utf-8') as f:
                f.write(self.ocr_result)
            
            # 尝试用本程序打开文件
            self._open_file_with_app(str(save_path))
            
        except Exception as e:
            messageBox(self, "保存失败", str(e), 1)
    
    def _open_file_with_app(self, file_path: str):
        """用本程序打开文件"""
        try:
            main_window = self.parent()
            if main_window and hasattr(main_window, 'open_file_path'):
                main_window.open_file_path(file_path)
            elif main_window and hasattr(main_window, 'open_file'):
                main_window.open_file()
            else:
                os.startfile(file_path)
        except Exception:
            logger.exception("打开文件失败")
