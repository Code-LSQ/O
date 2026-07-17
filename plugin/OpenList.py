import os
import time
import json
import shutil
import queue
import tarfile
import threading
import subprocess
import webbrowser
from pathlib import Path
from urllib.parse import quote
from datetime import datetime
from typing import Optional, List

import psutil
import requests
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit, QPushButton, QComboBox, QDialog, QTreeWidget, QTreeWidgetItem, QTextEdit, QProgressBar, QFormLayout, QCheckBox, QHeaderView
from PySide6.QtGui import QAction, QColor
from PySide6.QtCore import QTimer, Qt, Signal, QThread

from src.plugin import PluginBase
from src.file import filterFiles
from src.util import logger, formatFileSize, data_dir, folderLastModified, parseMtime, getFilePath, messageBox, dialogBox, tr, fileTree

PluginLib = ["queue"]

"""
有时候迅雷或什么存储失效会导致空指针然后程序会直接启动失败，需要在 cmd 中手动列出存储和禁用

OpenList API
OpenList 文档里有些 API 没写啊，暂时够用但有些功能不好做，与 Alist 的 API 部分兼容。去反馈文档不全和没有中文吧。
任务管理 {base}/api/task/
也可以通过 WebDAV 调用

请求头中，'Authorization' 均为 token
响应体中，"code": 200 表示成功，"code": 400 表示失败。标准响应如下
{
    "code": 200,
    "message": "success",
    "data": null
}


POST /api/auth/login  登录
请求
{
    "username": "admin",
    "password": "my password",
    "otp_code": "123456"
}
headers = {
    'Content-Type': 'application/json'
}

响应
{
    "code": 200,
    "message": "success",
    "data": {
        "token": "abc..."
    }
}


POST /api/fs/list  列出文件目录
请求（都是可选项，但路径大部分时候要填）
{
    "path": "/",
    "password": "",
    "refresh": False,
    "page": 1,
    "per_page": 30
}
headers = {
    'Authorization': ' <token>',
    'Content-Type': 'application/json'
}

响应
{
    "code": 200,
    "message": "success",
    "data": {
        "content": [
            {
                "id": "",
                "path": "D:\\files\\document.pdf",
                "name": "document.pdf",
                "size": 1024000,
                "is_dir": false,
                "modified": "2025-10-20T15:30:00+08:00",
                "created": "2025-10-20T10:00:00+08:00",
                "sign": "YBgnmykwCXUstXvNGtECaz_12gseXSL03cpqh5rTcGA=:0",
                "thumb": "",
                "type": 4,
                "hashinfo": "null",
                "hash_info": null,
                "mount_details": {
                    "driver_name": "Local",
                    "total_space": 1000000000000,
                    "free_space": 500000000000
                }
            }
        ],
        "total": 14,
        "readme": "",
        "header": "",
        "write": true,
        "provider": "Local"
    }
}


POST /api/fs/mkdir  在远程（云存储）创建目录
请求   标准响应
{
    "path": "/newfolder"
}
headers = {
    'Authorization': ' <token>',
    'Content-Type': 'application/json'
}


PUT /api/fs/put  上传文件 - 二进制流 (stream)
请求（路径必需，其余可选。As-Task，是否添加为任务。Last-Modified，最后修改时间，Unix 时间戳，单位为毫秒，由于大部分云盘强制使用服务器时间，因此大概率无效）   标准响应
payload = "<file contents>"
headers = {
    'File-Path': '',
    'As-Task': '',
    'Overwrite': '',
    'Last-Modified': '',
    'Authorization': ' <token>',
    'Content-Type': 'application/octet-stream'
}


POST /api/fs/remove  删除远程文件或目录
请求   标准响应
{
    "dir": "/folder",
    "names": [
        "file1.txt",
        "file2.pdf"
    ]
}
headers = {
    'Authorization': ' <token>',
    'Content-Type': 'application/json'
}


POST /api/fs/copy  复制文件或目录，暂时没有使用，但后续会使用，增加把文件从一个云盘复制到另一个云盘的功能。
请求   标准响应
{
    "src_dir": "/source",
    "dst_dir": "/destination",
    "names": [
        "file1.txt",
        "file2.pdf"
   ]
}
headers = {
    'Authorization': ' <token>',
    'Content-Type': 'application/json'
}


POST /api/fs/rename  重命名文件或目录，备份模式下使用
请求   标准响应
{
    "path": "/oldname.txt",
    "name": "newname.txt"
}
headers = {
    'Authorization': ' <token>',
    'Content-Type': 'application/json'
}


"""

# 上传逻辑，对于普通文件，大小有改变、最后修改时间新于云端文件的最后修改时间，就要重新上传。对于要打包成 tar 的文件夹，如果源文件夹中最新的最后修改时间新于云端文件的最后修改时间，就要重新上传，不判断大小。

# 同步模式
MODE_SYNC = "sync"      # 同步：删除多余文件，会覆盖
MODE_BACKUP = "backup"  # 备份：只上传不删除，会保留历史文件

# 任务状态
TASK_STATUS_RUNNING = "running"
TASK_STATUS_SUCCESS = "success"
TASK_STATUS_FAILED = "failed"
TASK_STATUS_ABORTED = "aborted"

temp_dir = data_dir / "OpenList"
temp_dir.mkdir(parents=True, exist_ok=True)
cache_file = temp_dir / "cache.json"

class OpenListPlugin(PluginBase):

    version = "1.0.0"
    description = "文件同步"
    file = [temp_dir]

    def __init__(self, main=None):
        super().__init__(main=main)
        self.api_url = ""
        self.token = ""
        self.client = None
        self.tasks: List[TaskConfig] = []
        self.selected_task_name = ""

    def loadConfig(self):
        super().loadConfig()
        self.openlist_path = self.settings.get("path", "")
        self.port = self.settings.get("port", "127.0.0.1:5244")
        self.username = self.settings.get("username", "")
        self.password = self.settings.get("password", "")
        self.selected_task_name = self.settings.get("selected_task", "")
        self.tasks = [TaskConfig.fromDict(t) for t in self.settings.get("tasks", [])]

    def saveConfig(self) -> dict:
        self.settings.update({
            "path": self.openlist_path,
            "port": self.port,
            "username": self.username,
            "password": self.password,
            "selected_task": self.selected_task_name,
            "tasks": [t.toDict() for t in self.tasks]
        })
        return super().saveConfig()

    def initialize(self):
        if not super().initialize():
            return

    def getAction(self):
        action = QAction(self.description, self.main)
        action.triggered.connect(self.showSettings)
        return action

    def showSettings(self):
        self.initialize()
        dialog = QDialog(self.main)
        dialog.setWindowTitle("OpenList")
        dialog.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.Dialog)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        layout = QVBoxLayout(dialog)
        widget = OpenListWidget(self.main, self)
        layout.addWidget(widget)
        dialog.setMinimumSize(600, 500)
        dialog.finished.connect(lambda: (widget._saveSettings(), widget._cleanupTimer()))
        dialog.show()

    def getClient(self):
        """获取 API 客户端"""
        if self.client is None:
            self.client = OpenListClient(f"http://{self.port}")
            self.client.login(self.username, self.password)
        return self.client

    def login(self) -> bool:
        """登录 OpenList"""
        client = self.getClient()
        if client:
            return client.login(self.username, self.password)
        return False

    def cleanup(self):
        """插件停用时保存配置"""
        if not self._initialized:
            return
        if self.client:
            self.client.close()
        self.saveConfig()


class TaskConfig:
    """任务配置"""

    def __init__(self, name="", src_path="", dst_path="", exclude_rules="",
                 mode=MODE_BACKUP, confirm_before_sync=False, use_cloud_cache=False,
                 tar_folders="", tree_folders=""):
        self.name = name
        self.src_path = src_path          # 源目录（本地）
        self.dst_path = dst_path          # 目标目录（OpenList）
        self.exclude_rules = exclude_rules # 排除规则
        self.mode = mode                  # 同步模式
        self.confirm_before_sync = confirm_before_sync
        self.use_cloud_cache = use_cloud_cache
        self.tar_folders = tar_folders    # 打包成 tar 的文件夹
        self.tree_folders = tree_folders  # 上传文件树的文件夹

    def toDict(self) -> dict:
        return {
            "name": self.name,
            "src_path": self.src_path,
            "dst_path": self.dst_path,
            "exclude_rules": self.exclude_rules,
            "mode": self.mode,
            "confirm_before_sync": self.confirm_before_sync,
            "use_cloud_cache": self.use_cloud_cache,
            "tar_folders": self.tar_folders,
            "tree_folders": self.tree_folders
        }

    @classmethod
    def fromDict(cls, data: dict) -> 'TaskConfig':
        return cls(
            name=data.get("name", ""),
            src_path=data.get("src_path", ""),
            dst_path=data.get("dst_path", ""),
            exclude_rules=data.get("exclude_rules", ""),
            mode=data.get("mode", MODE_BACKUP),
            confirm_before_sync=data.get("confirm_before_sync", False),
            use_cloud_cache=data.get("use_cloud_cache", False),
            tar_folders=data.get("tar_folders", ""),
            tree_folders=data.get("tree_folders", "")
        )


class TaskResult:
    """任务执行结果"""

    def __init__(self, **kwargs):
        self.total_files = 0
        self.upload_success = 0
        self.upload_failed = 0
        self.delete_success = 0
        self.delete_failed = 0
        self.rename_success = 0
        self.rename_failed = 0
        self.total_size = 0
        self.uploaded_size = 0
        self.duration = 0.0
        self.status = ""
        self.error_msg = ""
        self.upload_success_files = []
        self.upload_failed_files = []
        self.delete_success_files = []
        self.delete_failed_files = []
        self.__dict__.update(kwargs)

    def toDict(self) -> dict:
        return {
            "total_files": self.total_files,
            "upload_success": self.upload_success,
            "upload_failed": self.upload_failed,
            "delete_success": self.delete_success,
            "delete_failed": self.delete_failed,
            "rename_success": self.rename_success,
            "rename_failed": self.rename_failed,
            "total_size": self.total_size,
            "uploaded_size": self.uploaded_size,
            "duration": self.duration,
            "status": self.status,
            "error_msg": self.error_msg,
            "upload_success_files": self.upload_success_files,
            "upload_failed_files": self.upload_failed_files,
            "delete_success_files": self.delete_success_files,
            "delete_failed_files": self.delete_failed_files
        }


class OpenListClient:
    """OpenList API 客户端"""

    def __init__(self, base_url: str, token: str = ""):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.session = requests.Session()
        self.session.headers["Authorization"] = token

    def close(self):
        self.session.close()

    def login(self, username: str, password: str) -> bool:
        """登录获取 Token"""
        try:
            resp = self.session.post(
                f"{self.base_url}/api/auth/login",
                json={"username": username, "password": password},
                timeout=10
            )
            data = resp.json()
            if data.get("code") == 200:
                self.token = data["data"].get("token", "")
                self.session.headers["Authorization"] = self.token
                return True
            return False
        except Exception:
            logger.exception("登录失败")
            return False

    def listDir(self, path: str) -> List[dict]:
        """获取目录列表"""
        try:
            resp = self.session.post(
                f"{self.base_url}/api/fs/list",
                json={"path": path, "password": ""},
                timeout=30
            )
            data = resp.json()
            if data.get("code") == 200:
                content = data["data"].get("content", [])
                return content if content else []
            return []
        except Exception:
            logger.exception("获取目录列表失败")
            return []

    def mkdir(self, path: str) -> bool:
        """创建目录"""
        try:
            resp = self.session.post(
                f"{self.base_url}/api/fs/mkdir",
                json={"path": path},
                timeout=30
            )
            data = resp.json()
            return data.get("code") == 200
        except Exception:
            logger.exception("创建目录失败")
            return False

    def uploadFile(self, local_path: str, remote_path: str, overwrite: bool = True, mtime: int = 0) -> bool:
        """上传文件"""
        try:
            with open(local_path, "rb") as f:
                file_data = f.read()

            # 对路径进行 URL 编码以支持中文
            encoded_path = quote(remote_path, safe='/')

            # 设置 headers
            headers = {
                "File-Path": encoded_path,
                "As-Task": "true",
                "Overwrite": "true" if overwrite else "false",
                "Last-Modified": str(mtime),
                "Content-Type": "application/octet-stream"
            }

            resp = self.session.put(
                f"{self.base_url}/api/fs/put",
                data=file_data,
                headers=headers,
                timeout=300
            )
            data = resp.json()
            return data.get("code") == 200
        except Exception:
            logger.exception(f"上传文件失败 {local_path}")
            return False

    def remove(self, dir_path: str, names: List[str]) -> bool:
        """删除文件或目录"""
        try:
            resp = self.session.post(
                f"{self.base_url}/api/fs/remove",
                json={"dir": dir_path, "names": names},
                timeout=30
            )
            data = resp.json()
            return data.get("code") == 200
        except Exception:
            logger.exception("删除失败")
            return False

    def rename(self, path: str, new_name: str) -> bool:
        """重命名文件或目录"""
        try:
            resp = self.session.post(
                f"{self.base_url}/api/fs/rename",
                json={"path": path, "name": new_name},
                timeout=30
            )
            data = resp.json()
            return data.get("code") == 200
        except Exception:
            logger.exception("重命名失败")
            return False


class FileConfirmDialog(QDialog):
    """文件操作确认对话框"""

    def __init__(self, parent, to_upload: dict, to_delete: dict, mode: str):
        super().__init__(parent)
        self.to_upload = to_upload
        self.to_delete = to_delete
        self.mode = mode
        self.setWindowTitle("确认同步操作")
        self.setMinimumSize(600, 500)
        self._initUI()

    def _initUI(self):
        layout = QVBoxLayout(self)

        # 统计信息
        upload_count = len(self.to_upload)
        delete_count = len(self.to_delete)
        total_size = sum(info.get("size", 0) for info in self.to_upload.values())

        info_text = f"需要上传: {upload_count} 个文件 (共 {formatFileSize(total_size)})"
        if self.mode == MODE_SYNC and self.to_delete:
            info_text += f"\n需要删除: {delete_count} 个文件"
        info_label = QLabel(info_text)
        layout.addWidget(info_label)

        # 文件树
        layout.addWidget(QLabel("待上传文件"))
        self.upload_tree = QTreeWidget()
        self.upload_tree.setHeaderLabels(["文件路径", "大小"])
        self.upload_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._buildTree(self.upload_tree, self.to_upload)
        layout.addWidget(self.upload_tree)

        if self.mode == MODE_SYNC and self.to_delete:
            layout.addWidget(QLabel("待删除文件"))
            self.delete_tree = QTreeWidget()
            self.delete_tree.setHeaderLabels(["文件路径", "大小"])
            self.delete_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            self._buildTree(self.delete_tree, self.to_delete)
            layout.addWidget(self.delete_tree)

        # 按钮
        dialogBox(layout, self, show=False)

    def _buildTree(self, tree: QTreeWidget, files: dict):
        """构建树状文件结构"""
        # 构建目录节点缓存
        dir_items = {}

        for path, info in sorted(files.items()):
            parts = path.replace("\\", "/").split("/")
            current_item = None
            current_path = ""

            # 逐级创建目录节点
            for i, part in enumerate(parts[:-1]):
                current_path = f"{current_path}/{part}" if current_path else part
                if current_path not in dir_items:
                    dir_item = QTreeWidgetItem([part, ""])
                    if current_item:
                        current_item.addChild(dir_item)
                    else:
                        tree.addTopLevelItem(dir_item)
                    dir_items[current_path] = dir_item
                    current_item = dir_item
                else:
                    current_item = dir_items[current_path]

            # 添加文件节点
            file_name = parts[-1]
            file_size = info.get("size", 0)
            file_item = QTreeWidgetItem([file_name, formatFileSize(file_size)])
            if current_item:
                current_item.addChild(file_item)
            else:
                tree.addTopLevelItem(file_item)

        # 默认展开所有节点
        tree.expandAll()


class SyncResultDialog(QDialog):
    """同步结果对话框"""

    def __init__(self, parent, result: dict):
        super().__init__(parent)
        self.result = result
        self.setWindowTitle("同步结果")
        self.setMinimumSize(600, 500)
        self._initUI()

    def _initUI(self):
        layout = QVBoxLayout(self)

        status = self.result.get("status", "unknown")
        duration = self.result.get("duration", 0)
        upload_success = self.result.get("upload_success", 0)
        upload_failed = self.result.get("upload_failed", 0)
        delete_success = self.result.get("delete_success", 0)
        delete_failed = self.result.get("delete_failed", 0)

        # 状态标题
        status_label = QLabel()
        if status == TASK_STATUS_SUCCESS:
            if upload_failed > 0 or delete_failed > 0:
                status_label.setText("任务完成（部分失败）")
                status_label.setStyleSheet("font-weight: bold; color: #FF9800; font-size: 16px;")
            else:
                status_label.setText("任务完成")
                status_label.setStyleSheet("font-weight: bold; color: #4CAF50; font-size: 16px;")
        elif status == TASK_STATUS_ABORTED:
            status_label.setText("任务已中止")
            status_label.setStyleSheet("font-weight: bold; color: #F44336; font-size: 16px;")
        else:
            status_label.setText("任务失败")
            status_label.setStyleSheet("font-weight: bold; color: #F44336; font-size: 16px;")
        layout.addWidget(status_label)

        # 统计信息
        info_text = f"上传: 成功 {upload_success}，失败 {upload_failed}\n"
        info_text += f"删除: 成功 {delete_success}，失败 {delete_failed}\n"
        info_text += f"耗时: {duration:.1f} 秒"
        info_label = QLabel(info_text)
        layout.addWidget(info_label)

        # 上传成功文件
        upload_success_files = self.result.get("upload_success_files", [])
        if upload_success_files:
            layout.addWidget(QLabel(f"上传成功 ({len(upload_success_files)}):"))
            tree = QTreeWidget()
            tree.setHeaderLabels(["文件路径", "状态"])
            tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            self._buildTree(tree, upload_success_files, "成功")
            tree.setMaximumHeight(150)
            layout.addWidget(tree)

        # 上传失败文件
        upload_failed_files = self.result.get("upload_failed_files", [])
        if upload_failed_files:
            layout.addWidget(QLabel(f"上传失败 ({len(upload_failed_files)}):"))
            tree = QTreeWidget()
            tree.setHeaderLabels(["文件路径", "状态"])
            tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            self._buildTree(tree, upload_failed_files, "失败")
            tree.setMaximumHeight(150)
            layout.addWidget(tree)

        # 删除成功文件
        delete_success_files = self.result.get("delete_success_files", [])
        if delete_success_files:
            layout.addWidget(QLabel(f"删除成功 ({len(delete_success_files)}):"))
            tree = QTreeWidget()
            tree.setHeaderLabels(["文件路径", "状态"])
            tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            self._buildTree(tree, delete_success_files, "成功")
            tree.setMaximumHeight(150)
            layout.addWidget(tree)

        # 删除失败文件
        delete_failed_files = self.result.get("delete_failed_files", [])
        if delete_failed_files:
            layout.addWidget(QLabel(f"删除失败 ({len(delete_failed_files)}):"))
            tree = QTreeWidget()
            tree.setHeaderLabels(["文件路径", "状态"])
            tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            self._buildTree(tree, delete_failed_files, "失败")
            tree.setMaximumHeight(150)
            layout.addWidget(tree)

        # 关闭按钮
        dialogBox(layout, self, num=1, show=False)

    def _buildTree(self, tree: QTreeWidget, files: list, status: str):
        """构建树状文件结构"""
        dir_items = {}

        for path in sorted(files):
            parts = path.replace("\\", "/").split("/")
            current_item = None
            current_path = ""

            for i, part in enumerate(parts[:-1]):
                current_path = f"{current_path}/{part}" if current_path else part
                if current_path not in dir_items:
                    dir_item = QTreeWidgetItem([part, ""])
                    if current_item:
                        current_item.addChild(dir_item)
                    else:
                        tree.addTopLevelItem(dir_item)
                    dir_items[current_path] = dir_item
                    current_item = dir_item
                else:
                    current_item = dir_items[current_path]

            file_name = parts[-1]
            file_item = QTreeWidgetItem([file_name, status])
            if status == "失败":
                file_item.setForeground(1, QColor("#F44336"))
            else:
                file_item.setForeground(1, QColor("#4CAF50"))
            if current_item:
                current_item.addChild(file_item)
            else:
                tree.addTopLevelItem(file_item)

        tree.expandAll()


class SyncWorker(QThread):
    """同步工作线程"""
    progress = Signal(str, int, int)  # message, current, total
    file_progress = Signal(str, int)  # file_name, percent
    finished = Signal(dict)  # result dict
    need_confirm = Signal(dict, dict)  # to_upload, to_delete
    confirm_result = Signal(bool)  # 用户确认结果

    def __init__(self, client: OpenListClient, task_config: TaskConfig):
        super().__init__()
        self.client = client
        self.task = task_config
        self._abort = False
        self.result = TaskResult()
        self._confirmed = False
        self._confirm_event = threading.Event()
        self.log_queue = queue.Queue()
        self.progress_queue = queue.Queue(maxsize=1)
        self._created_dirs = set()

    def log(self, message: str, level="info"):
        self.log_queue.put(message)
        if level == "info":
            logger.info(message)
        else:
            logger.exception(message)

    def abort(self):
        self._abort = True
        self._confirm_event.set()  # 唤醒等待的线程

    def confirm(self, accepted: bool):
        """用户确认回调"""
        self._confirmed = accepted
        self._confirm_event.set()

    def run(self):
        self.result.status = TASK_STATUS_RUNNING
        self._upload_start = 0

        try:
            self._doSync()
        except Exception as e:
            self.result.status = TASK_STATUS_FAILED
            self.result.error_msg = str(e)
            self.log(f"任务执行失败: {e}", "error")
        finally:
            self.cleanTar()

        self.result.duration = (time.time() - self._upload_start) if self._upload_start > 0 else 0
        if self.result.status == TASK_STATUS_RUNNING:
            if self._abort:
                self.result.status = TASK_STATUS_ABORTED
            elif self.result.upload_failed > 0 or self.result.delete_failed > 0:
                self.result.status = TASK_STATUS_SUCCESS
            else:
                self.result.status = TASK_STATUS_SUCCESS

        self.log(
            f"任务完成: 上传成功 {self.result.upload_success}，上传失败 {self.result.upload_failed}，"
            f"删除成功 {self.result.delete_success}，删除失败 {self.result.delete_failed}"
        )
        if self.task.mode == MODE_BACKUP:
            self.log(f"备份重命名: 成功 {self.result.rename_success}，失败 {self.result.rename_failed}")

        self.finished.emit(self.result.__dict__)

    def _doSync(self):
        self.log(f"开始执行 OpenList 任务: {self.task.name}\n源目录: {self.task.src_path}，目标目录: {self.task.dst_path}，模式: {'同步' if self.task.mode == MODE_SYNC else '备份'}")

        # 扫描本地目录
        scan_start = time.time()
        self.log_queue.put("正在扫描本地目录...")
        local_files = self._scanLocal()
        self.result.total_files = len(local_files)
        self.log(f"本地文件数: {len(local_files)}")

        if self._abort:
            return

        # 获取云目录
        self.log_queue.put("正在获取远程目录...")
        remote_files = self._getRemoteFiles()
        self.log(f"远程文件数: {len(remote_files)}")
        self.log(f"扫描完成，耗时: {time.time() - scan_start:.2f}s，"
                 f"本地 {len(local_files)} 个文件，远程 {len(remote_files)} 个文件")

        if self._abort:
            return

        # 处理 tar 和 tree 文件夹（需要远程文件信息判断是否需要重建）
        self._processTarFolders(local_files, remote_files)
        self._processTreeFolders(local_files, remote_files)

        # 对比差异
        self.log_queue.put("正在对比文件差异...")
        to_upload, to_delete = self._compareFiles(local_files, remote_files)
        self.log(f"需要上传: {len(to_upload)} 个文件")
        if self.task.mode == MODE_SYNC:
            self.log(f"需要删除: {len(to_delete)} 个文件")
        self.log(f"对比完成，待上传: {len(to_upload)}，待删除: {len(to_delete)}")

        if self._abort:
            return

        # 如果需要确认，等待用户确认
        if self.task.confirm_before_sync and (to_upload or to_delete):
            self.log_queue.put("等待用户确认...")
            self.need_confirm.emit(to_upload, to_delete)
            self._confirm_event.wait()

            if self._abort or not self._confirmed:
                self.log("用户取消了同步操作")
                self._abort = True
                return

            self.log_queue.put("用户确认，开始执行同步...")

        # 上传文件
        self._upload_start = time.time()
        total = len(to_upload)
        for i, (rel_path, file_info) in enumerate(to_upload.items()):
            if self._abort:
                break

            local_file = file_info["local_path"]
            remote_file = f"{self.task.dst_path}/{rel_path}".replace("//", "/")

            # 备份模式：云端已存在 → 先 rename 旧文件为备份名，再以原名上传新版本
            if self.task.mode == MODE_BACKUP and rel_path in remote_files:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                base_name = os.path.basename(rel_path)
                name_only, ext = os.path.splitext(base_name)
                backup_name = f"{name_only}-O-Backup-{timestamp}{ext}"
                if self.client.rename(remote_file, backup_name):
                    self.result.rename_success += 1
                    self.log(f"  旧文件已备份为: {backup_name}")
                else:
                    self.result.rename_failed += 1
                    self.log("  旧文件备份失败（可能已被删除），继续上传")

            self._emitProgress(f"上传: {rel_path}", i + 1, total)
            self.log_queue.put(f"[{i+1}/{total}] 上传: {rel_path}")

            # 确保目标目录存在
            remote_dir = os.path.dirname(remote_file)
            self._ensureRemoteDir(remote_dir)

            # 上传文件（自动重试3次）
            mtime = file_info.get("mtime", 0) * 1000
            success = self._uploadWithRetry(local_file, remote_file, rel_path, mtime=mtime)
            if success:
                self.result.upload_success += 1
                self.result.uploaded_size += file_info["size"]
                self.result.upload_success_files.append(rel_path)
                logger.info(f"OpenList 上传成功: {rel_path}")
            else:
                self.result.upload_failed += 1
                self.result.upload_failed_files.append(rel_path)
                self.log_queue.put(f"  [{i+1}/{total}] 失败: {rel_path}")
                logger.error(f"OpenList 上传失败，已重试3次: {rel_path}")

        self.log(f"上传完成: 成功 {self.result.upload_success}，失败 {self.result.upload_failed}")
        if self.task.mode == MODE_BACKUP:
            self.log(f"备份重命名: 成功 {self.result.rename_success}，失败 {self.result.rename_failed}")

        # 同步模式删除多余文件
        if self.task.mode == MODE_SYNC and not self._abort:
            self.log_queue.put("开始删除多余文件...")
            self._deleteRemoteFiles(to_delete)
            self.log(f"删除完成: 成功 {self.result.delete_success}，失败 {self.result.delete_failed}")

    def _uploadWithRetry(self, local_file: str, remote_file: str,
                                rel_path: str = "", max_retries: int = 3, mtime: int = 0) -> bool:
        """上传单个文件，失败自动重试（指数退避）"""
        for attempt in range(1, max_retries + 1):
            if self._abort:
                return False
            success = self.client.uploadFile(local_file, remote_file, mtime=mtime)
            if success:
                return True
            if attempt < max_retries:
                wait = 1.0 * (2 ** (attempt - 1))
                self.log_queue.put(f"  上传失败，{wait:.1f}s后第{attempt+1}次重试")
                logger.warning(f"OpenList 上传重试 {attempt}/{max_retries}: {rel_path}")
                time.sleep(wait)
        logger.error(f"OpenList 上传失败，已重试{max_retries}次: {rel_path}")
        return False

    def _deleteWithRetry(self, remote_dir: str, file_name: str,
                                file_path: str = "", max_retries: int = 3) -> bool:
        """删除远程文件，失败自动重试（指数退避）"""
        for attempt in range(1, max_retries + 1):
            if self._abort:
                return False
            success = self.client.remove(remote_dir, [file_name])
            if success:
                return True
            if attempt < max_retries:
                wait = 1.0 * (2 ** (attempt - 1))
                self.log_queue.put(f"  删除失败，{wait:.1f}s后第{attempt+1}次重试")
                logger.warning(f"OpenList 删除重试 {attempt}/{max_retries}: {file_path}")
                time.sleep(wait)
        logger.error(f"OpenList 删除失败，已重试{max_retries}次: {file_path}")
        return False

    def _scanLocal(self) -> dict:
        """扫描本地目录，返回 {相对路径: {size, mtime}}"""
        files = {}
        src_path = self.task.src_path

        # 处理排除规则
        exclude_rules_raw = []
        if self.task.exclude_rules:
            exclude_rules_raw = [r.strip() for r in self.task.exclude_rules.splitlines() if r.strip()]

        # 获取需要打包成 tar 的文件夹列表（排除这些文件夹的内容）
        tar_folders_list = []
        if self.task.tar_folders:
            tar_folders_list = [os.path.normpath(f.strip()) for f in self.task.tar_folders.splitlines() if f.strip()]

        filtered_files = filterFiles(src_path, exclude_rules_raw)

        for full_path in filtered_files:
            if self._abort:
                break

            # 排除 tar_folders 中的文件（不作为普通文件上传，只打包成 tar）
            full_path_norm = os.path.normpath(full_path)
            is_in_tar_folder = False
            for tar_folder in tar_folders_list:
                if full_path_norm.startswith(tar_folder + os.sep) or full_path_norm == tar_folder:
                    is_in_tar_folder = True
                    break
            if is_in_tar_folder:
                continue

            rel_path = os.path.relpath(full_path, src_path).replace("\\", "/")

            try:
                stat = os.stat(full_path)
                files[rel_path] = {
                    "local_path": full_path,
                    "size": stat.st_size,
                    "mtime": int(stat.st_mtime)
                }
                self.result.total_size += stat.st_size
            except OSError:
                continue

        return files

    def _createTar(self, src_folder: str, tar_path: Path) -> bool:
        """创建 tar 打包文件（应用排除规则）"""
        try:
            folder_name = os.path.basename(src_folder)
            self.log_queue.put(f"正在打包: {folder_name}")

            exclude_rules_raw = []
            if self.task.exclude_rules:
                exclude_rules_raw = [r.strip() for r in self.task.exclude_rules.splitlines() if r.strip()]

            with tarfile.open(tar_path, "w") as tar:
                parent_dir = os.path.dirname(src_folder)
                original_cwd = os.getcwd()
                try:
                    os.chdir(parent_dir)
                    if exclude_rules_raw:
                        filtered = filterFiles(src_folder, exclude_rules_raw)
                        for full_path in filtered:
                            rel = os.path.relpath(full_path, parent_dir).replace("\\", "/")
                            tar.add(rel)
                    else:
                        tar.add(folder_name)
                finally:
                    os.chdir(original_cwd)
            return True
        except Exception as e:
            self.log(f"打包失败: {e}", "error")
            return False

    def _fileTreeFile(self, folder_path: str) -> Path:
        """生成树形文本文件"""
        folder_name = os.path.basename(folder_path)
        tree_filename = f"{folder_name}_tree.txt"
        tree_path = temp_dir / tree_filename

        try:
            folder = Path(folder_path)
            tree_lines = fileTree(folder)
            with open(tree_path, "w", encoding="utf-8") as f:
                f.write(f"{folder_path}\n")
                for line in tree_lines:
                    f.write(line + "\n")
            return tree_path
        except Exception as e:
            self.log(f"生成树形文件失败: {e}", "error")
            return None

    def cleanTar(self):
        """清理临时目录"""
        try:
            tar_dir = temp_dir / "tar"
            if tar_dir.exists():
                shutil.rmtree(tar_dir)
                self.log_queue.put("临时文件已清理")
        except Exception as e:
            self.log_queue.put(f"清理临时文件失败: {e}")

    def _processTarFolders(self, files: dict, remote_files: dict):
        """处理需要打包成 tar 的文件夹"""
        if not self.task.tar_folders:
            return

        tar_folders = [f.strip() for f in self.task.tar_folders.splitlines() if f.strip()]

        for folder_path in tar_folders:
            if self._abort:
                break

            if not os.path.isdir(folder_path):
                self.log_queue.put(f"跳过不存在的目录: {folder_path}")
                continue

            folder_name = os.path.basename(folder_path)
            tar_filename = f"{folder_name}.tar"
            tar_dir = temp_dir / "tar"
            tar_dir.mkdir(parents=True, exist_ok=True)
            tar_path = tar_dir / tar_filename

            # 获取源文件夹最新 mtime
            folder_mtime = folderLastModified(folder_path)

            # 检查远程 tar 的 mtime
            remote_tar = remote_files.get(tar_filename)
            if remote_tar:
                remote_mtime = parseMtime(remote_tar.get("mtime", ""))
                if remote_mtime and folder_mtime <= remote_mtime:
                    self.log_queue.put(f"跳过未变化的文件夹: {folder_name}")
                    continue

            self.log_queue.put(f"需要重新打包: {folder_name}")
            if not self._createTar(folder_path, tar_path):
                continue

            # 添加到上传列表
            if tar_path.exists():
                try:
                    stat = tar_path.stat()
                    tar_mtime = int(stat.st_mtime)
                    files[tar_filename] = {
                        "local_path": str(tar_path),
                        "size": stat.st_size,
                        "mtime": tar_mtime
                    }
                    self.result.total_size += stat.st_size
                    self.log_queue.put(f"添加 tar 文件: {tar_filename}")
                except Exception as e:
                    self.log_queue.put(f"添加 tar 文件失败: {e}")

    def _processTreeFolders(self, files: dict, remote_files: dict):
        """处理需要生成文件树的文件夹"""
        if not self.task.tree_folders:
            return

        tree_folders = [f.strip() for f in self.task.tree_folders.splitlines() if f.strip()]

        for folder_path in tree_folders:
            if self._abort:
                break

            if not os.path.isdir(folder_path):
                self.log_queue.put(f"跳过不存在的目录: {folder_path}")
                continue

            folder_name = os.path.basename(folder_path)
            tree_filename = f"{folder_name}_tree.txt"

            # 检查远程 mtime，未变化则跳过
            remote_tree = remote_files.get(tree_filename)
            if remote_tree:
                folder_mtime = folderLastModified(folder_path)
                remote_mtime = parseMtime(remote_tree.get("mtime", 0))
                if remote_mtime and folder_mtime <= remote_mtime:
                    self.log_queue.put(f"跳过未变化的树形文件: {tree_filename}")
                    continue

            tree_path = self._fileTreeFile(folder_path)
            if tree_path and tree_path.exists():
                try:
                    stat = tree_path.stat()
                    # 文件名使用相对路径（目标目录根目录）
                    tree_filename = tree_path.name
                    files[tree_filename] = {
                        "local_path": str(tree_path),
                        "size": stat.st_size,
                        "mtime": int(stat.st_mtime)
                    }
                    self.result.total_size += stat.st_size
                    self.log_queue.put(f"添加树形文件: {tree_filename}")
                except Exception as e:
                    self.log_queue.put(f"添加树形文件失败: {e}")

    def _getRemoteFiles(self) -> dict:
        """获取远程文件列表"""
        task_name = self.task.name

        # 启用云缓存且缓存存在 → 按任务名查找
        if self.task.use_cloud_cache and cache_file.exists():
            self.log_queue.put("使用缓存中的远程文件列表...")
            try:
                all_caches = json.loads(cache_file.read_text(encoding="utf-8"))
                cached = all_caches.get(task_name)
                if cached is not None:
                    return cached
            except Exception:
                self.log_queue.put("缓存读取失败，重新扫描远程目录")

        self.log_queue.put("正在扫描远程目录...")
        remote_files = {}
        self._cache_flush_count = 0
        self._scanRemoteDir(self.task.dst_path, "", remote_files)

        # 启用云缓存 → 最终写入缓存
        if self.task.use_cloud_cache:
            self._saveCacheProgress(task_name, remote_files)
            self.log_queue.put("远程文件列表已缓存")

        return remote_files

    def _saveCacheProgress(self, task_name: str, remote_files: dict):
        """写入云缓存到文件"""
        try:
            all_caches = {}
            if cache_file.exists():
                all_caches = json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            all_caches = {}
        all_caches[task_name] = remote_files
        cache_file.write_text(json.dumps(all_caches, ensure_ascii=False), encoding="utf-8")

    def _scanRemoteDir(self, full_path: str, rel_path: str, result: dict):
        """递归扫描远程目录"""
        if self._abort:
            return

        items = self.client.listDir(full_path)
        for item in items:
            if self._abort:
                break

            name = item.get("name", "")
            is_dir = item.get("is_dir", False)
            item_rel = f"{rel_path}/{name}" if rel_path else name

            if is_dir:
                self._scanRemoteDir(f"{full_path}/{name}", item_rel, result)
            else:
                result[item_rel] = {
                    "size": item.get("size", 0),
                    "mtime": item.get("modified", 0)
                }
                if self.task.use_cloud_cache:
                    self._cache_flush_count += 1
                    if self._cache_flush_count % 1000 == 0:
                        self._saveCacheProgress(self.task.name, result)
                        self.log_queue.put(f"缓存已写入 ({self._cache_flush_count} 个文件)")

    def _compareFiles(self, local: dict, remote: dict) -> tuple:
        """对比文件差异，返回待上传和待删除的文件"""
        to_upload = {}
        to_delete = {}

        # 找出需要上传的文件
        for rel_path, local_info in local.items():
            if self._abort:
                break

            remote_info = remote.get(rel_path)
            if remote_info is None:
                # 远程不存在，需要上传
                to_upload[rel_path] = local_info
            else:
                # 大小不同或本地 mtime 新于远程 mtime，需要上传
                local_mtime = local_info["mtime"]
                remote_mtime = parseMtime(remote_info.get("mtime", 0))
                if remote_info["size"] != local_info["size"] or local_mtime > remote_mtime:
                    to_upload[rel_path] = local_info

        # 找出需要删除的文件（仅同步模式）
        if self.task.mode == MODE_SYNC:
            for rel_path in remote:
                if rel_path not in local:
                    to_delete[rel_path] = remote[rel_path]

        return to_upload, to_delete

    def _emitProgress(self, message: str, current: int, total: int):
        try:
            self.progress_queue.get_nowait()
        except queue.Empty:
            pass
        self.progress_queue.put_nowait((message, current, total))

    def _ensureRemoteDir(self, remote_dir: str):
        """确保远程目录存在"""
        if not remote_dir or remote_dir in self._created_dirs:
            return

        suffix = remote_dir
        if remote_dir.startswith(self.task.dst_path):
            suffix = remote_dir[len(self.task.dst_path):]
        parts = suffix.strip("/").split("/")
        current = self.task.dst_path
        for part in parts:
            if part:
                current = f"{current}/{part}".replace("//", "/")
                if current not in self._created_dirs:
                    self.client.mkdir(current)
                    self._created_dirs.add(current)
        self._created_dirs.add(remote_dir)

    def _deleteRemoteFiles(self, to_delete: dict):
        """删除远程多余文件"""
        # 按目录分组
        dir_files = {}
        for rel_path in to_delete:
            dir_path = os.path.dirname(rel_path)
            file_name = os.path.basename(rel_path)
            if dir_path not in dir_files:
                dir_files[dir_path] = []
            dir_files[dir_path].append(file_name)

        total = len(to_delete)
        count = 0
        for dir_path, files in dir_files.items():
            if self._abort:
                break

            remote_dir = f"{self.task.dst_path}/{dir_path}".replace("//", "/")
            for file_name in files:
                if self._abort:
                    break

                count += 1
                file_path = f"{dir_path}/{file_name}" if dir_path else file_name
                self._emitProgress(f"删除: {file_path}", count, total)
                self.log_queue.put(f"[{count}/{total}] 删除: {file_path}")

                success = self._deleteWithRetry(remote_dir, file_name, file_path)
                if success:
                    self.result.delete_success += 1
                    self.result.delete_success_files.append(file_path)
                    logger.info(f"OpenList 删除成功: {file_path}")
                else:
                    self.result.delete_failed += 1
                    self.result.delete_failed_files.append(file_path)
                    self.log_queue.put(f"  [{count}/{total}] 删除失败: {file_path}")
                    logger.error(f"OpenList 删除失败，已重试3次: {file_path}")


class TaskEditDialog(QDialog):
    """任务编辑对话框"""

    def __init__(self, parent, client: OpenListClient, task: TaskConfig = None):
        super().__init__(parent)
        self.client = client
        self.task = task or TaskConfig()
        self.setWindowTitle("编辑任务" if task else "新建任务")
        self.setMinimumSize(500, 400)
        self._initUI()
        self._loadTask()

    def _initUI(self):
        layout = QVBoxLayout(self)

        form = QFormLayout()

        # 任务名称
        self.name_edit = QLineEdit()
        form.addRow("任务名称", self.name_edit)

        # 源目录
        src_layout = QHBoxLayout()
        self.src_edit = QLineEdit()
        self.src_edit.setPlaceholderText("选择本地源目录")
        src_layout.addWidget(self.src_edit)
        src_btn = QPushButton("浏览")
        src_btn.clicked.connect(lambda: getFilePath(self, "选择源目录", "", "dir", self.src_edit))
        src_layout.addWidget(src_btn)
        form.addRow("源目录", src_layout)

        # 目标目录
        dst_layout = QHBoxLayout()
        self.dst_edit = QLineEdit()
        self.dst_edit.setPlaceholderText("OpenList 目录")
        dst_layout.addWidget(self.dst_edit)
        dst_btn = QPushButton("浏览")
        dst_btn.clicked.connect(self._browseRemote)
        dst_layout.addWidget(dst_btn)
        form.addRow("目标目录", dst_layout)

        # 模式
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("备份（只复制不删除）", MODE_BACKUP)
        self.mode_combo.addItem("同步（删除多余文件）", MODE_SYNC)
        form.addRow("模式", self.mode_combo)

        layout.addLayout(form)

        check_layout = QHBoxLayout()
        left_layout = QHBoxLayout()
        self.cache_check = QCheckBox("使用云缓存")
        self.cache_check.setToolTip("请仅在不对云文件夹进行手动操作时启用")
        left_layout.addWidget(self.cache_check)
        left_layout.addStretch()
        check_layout.addLayout(left_layout)
        right_layout = QHBoxLayout()
        self.confirm_check = QCheckBox("进行文件操作前提示")
        right_layout.addWidget(self.confirm_check)
        right_layout.addStretch()
        check_layout.addLayout(right_layout)
        layout.addLayout(check_layout)

        # 排除规则（参考 FileSelect 类的样式）
        exclude_label = QLabel("""排除规则（每行一项，支持通配符）
/file.txt - 排除单个 file.txt
*/file.txt - 排除所有 file.txt
/folder/ - 排除文件夹下的 folder 文件夹
*.pyc - 排除所有 .pyc 文件
*/.git/ - 排除所有 .git 文件夹""")
        exclude_label.setWordWrap(True)
        layout.addWidget(exclude_label)

        self.exclude_edit = QTextEdit()
        self.exclude_edit.setStyleSheet("background: #dddddd")
        self.exclude_edit.setPlaceholderText("每行一项排除规则")
        self.exclude_edit.setMaximumHeight(100)
        layout.addWidget(self.exclude_edit)

        # 打包成 tar 的文件夹
        tar_label = QLabel("打包成 tar 的文件夹（上传 tar，对文件夹本身不同步）")
        tar_label.setWordWrap(True)
        layout.addWidget(tar_label)

        self.tar_folders_edit = QTextEdit()
        self.tar_folders_edit.setStyleSheet("background: #dddddd")
        self.tar_folders_edit.setPlaceholderText("每行一个文件夹路径")
        self.tar_folders_edit.setMaximumHeight(60)
        layout.addWidget(self.tar_folders_edit)

        # 上传文件树的文件夹
        tree_label = QLabel("""上传文件树的文件夹（每行一项，绝对路径）
生成文件夹的树形结构文本文件并上传到目标目录根目录""")
        tree_label.setWordWrap(True)
        layout.addWidget(tree_label)

        self.tree_folders_edit = QTextEdit()
        self.tree_folders_edit.setStyleSheet("background: #dddddd")
        self.tree_folders_edit.setPlaceholderText("每行一个文件夹路径")
        self.tree_folders_edit.setMaximumHeight(60)
        layout.addWidget(self.tree_folders_edit)

        # 按钮
        dialogBox(layout, self, show=False)

    def _loadTask(self):
        if self.task:
            self.name_edit.setText(self.task.name)
            self.src_edit.setText(self.task.src_path)
            self.dst_edit.setText(self.task.dst_path)
            # 设置排除规则，如果没有则使用默认值
            if self.task.exclude_rules:
                self.exclude_edit.setPlainText(self.task.exclude_rules)
            else:
                self.exclude_edit.setPlainText("*.pyc\n*/__pycache__/\n*/.git/")
            # 加载 tar_folders
            if self.task.tar_folders:
                self.tar_folders_edit.setPlainText(self.task.tar_folders)
            # 加载 tree_folders
            if self.task.tree_folders:
                self.tree_folders_edit.setPlainText(self.task.tree_folders)
            index = self.mode_combo.findData(self.task.mode)
            if index >= 0:
                self.mode_combo.setCurrentIndex(index)
            self.cache_check.setChecked(self.task.use_cloud_cache)
            self.confirm_check.setChecked(self.task.confirm_before_sync)

    def _browseRemote(self):
        """浏览远程目录"""
        if not self.client.token:
            plugin = getattr(self.parent(), 'plugin', None)
            if plugin:
                self.client.login(plugin.username, plugin.password)
        dialog = RemoteDirDialog(self, self.client)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            path = dialog.getSelectedPath()
            if path:
                self.dst_edit.setText(path)

    def accept(self):
        name = self.name_edit.text().strip()
        src = self.src_edit.text().strip()
        dst = self.dst_edit.text().strip()

        if not name:
            messageBox(self, "警告", "请输入任务名称", 1)
            return
        if not src:
            messageBox(self, "警告", "请选择源目录", 1)
            return
        if not dst:
            messageBox(self, "警告", "请输入目标目录", 1)
            return
        if not os.path.isdir(src):
            messageBox(self, "警告", "源目录不存在", 1)
            return

        # 获取排除规则（每行一项）
        exclude_text = self.exclude_edit.toPlainText().strip()
        exclude_lines = [line.strip() for line in exclude_text.splitlines() if line.strip()]
        exclude_rules = "\n".join(exclude_lines)

        # 获取 tar_folders（每行一项）
        tar_text = self.tar_folders_edit.toPlainText().strip()
        tar_lines = [line.strip() for line in tar_text.splitlines() if line.strip()]
        tar_folders = "\n".join(tar_lines)

        # 获取 tree_folders（每行一项）
        tree_text = self.tree_folders_edit.toPlainText().strip()
        tree_lines = [line.strip() for line in tree_text.splitlines() if line.strip()]
        tree_folders = "\n".join(tree_lines)

        self.task = TaskConfig(
            name=name,
            src_path=src,
            dst_path=dst,
            exclude_rules=exclude_rules,
            mode=self.mode_combo.currentData(),
            confirm_before_sync=self.confirm_check.isChecked(),
            use_cloud_cache=self.cache_check.isChecked(),
            tar_folders=tar_folders,
            tree_folders=tree_folders
        )
        super().accept()

    def getTask(self) -> TaskConfig:
        return self.task

    def done(self, code):
        self.client.close()
        super().done(code)


class RemoteDirDialog(QDialog):
    """远程目录选择对话框"""

    def __init__(self, parent, client: OpenListClient):
        super().__init__(parent)
        self.client = client
        self.selected_path = "/"
        self.setWindowTitle("选择远程目录")
        self.setMinimumSize(400, 500)
        self._initUI()
        self._loadDir("/")

    def _initUI(self):
        layout = QVBoxLayout(self)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["目录"])
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.tree.itemExpanded.connect(self._onExpand)
        layout.addWidget(self.tree)

        # 当前路径
        self.path_label = QLabel("/")
        layout.addWidget(self.path_label)

        # 按钮
        dialogBox(layout, self, show=False)

        self.tree.currentItemChanged.connect(self._onSelect)

    def _loadDir(self, path: str, parent_item: QTreeWidgetItem = None):
        """加载目录"""
        items = self.client.listDir(path)
        dirs = [item for item in items if item.get("is_dir", False)]

        for d in dirs:
            item = QTreeWidgetItem([d["name"]])
            item.setData(0, Qt.ItemDataRole.UserRole, f"{path}/{d['name']}".replace("//", "/"))
            item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
            if parent_item:
                parent_item.addChild(item)
            else:
                self.tree.addTopLevelItem(item)

    def _onExpand(self, item: QTreeWidgetItem):
        """展开目录时加载子目录"""
        if item.childCount() == 0:
            path = item.data(0, Qt.ItemDataRole.UserRole)
            self._loadDir(path, item)

    def _onSelect(self, item: QTreeWidgetItem):
        """选择目录"""
        if item:
            path = item.data(0, Qt.ItemDataRole.UserRole)
            self.selected_path = path
            self.path_label.setText(f"当前选择: {path}")

    def getSelectedPath(self) -> str:
        return self.selected_path

class OpenListWidget(QWidget):
    """OpenList设置界面"""

    # 信号
    log_signal = Signal(str)

    def __init__(self, main, plugin: OpenListPlugin):
        super().__init__()
        self.main = main
        self.plugin = plugin
        self.sync_worker = None
        self._initUI()
        self._loadSettings()
        self.log_signal.connect(self._appendLog)

    def _initUI(self):
        layout = QVBoxLayout(self)

        # OpenList 路径配置
        path_layout = QHBoxLayout()
        path_layout.addWidget(QLabel("OpenList 路径"))
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("选择 openlist.exe 文件")
        path_layout.addWidget(self.path_edit)
        self.select_btn = QPushButton("选择")
        self.select_btn.setFixedWidth(70)
        self.select_btn.clicked.connect(lambda: getFilePath(self, "OpenList", "可执行文件 (*.exe)", edit=self.path_edit))
        path_layout.addWidget(self.select_btn)
        layout.addLayout(path_layout)

        # 启动/停止控制
        control_layout = QHBoxLayout()
        self.start_openlist_btn = QPushButton("启动")
        self.start_openlist_btn.clicked.connect(self.startOpenList)
        control_layout.addWidget(self.start_openlist_btn)
        self.stop_openlist_btn = QPushButton("停止")
        self.stop_openlist_btn.clicked.connect(self.stopOpenList)
        control_layout.addWidget(self.stop_openlist_btn)
        control_layout.addWidget(QLabel("运行状态"))
        self.run_status_label = QLabel("未运行")
        self.run_status_label.setStyleSheet("font-weight: bold; color: #666;")
        control_layout.addWidget(self.run_status_label)
        control_layout.addStretch()
        layout.addLayout(control_layout)

        # 分隔线
        line1 = QLabel()
        line1.setFixedHeight(1)
        line1.setStyleSheet("background-color: #ccc;")
        layout.addWidget(line1)

        # 服务器配置
        server_layout = QGridLayout()
        server_layout.addWidget(QLabel("地址"), 0, 0)

        port_layout = QHBoxLayout()
        self.port_edit = QLineEdit()
        self.port_edit.setPlaceholderText("127.0.0.1:5244")
        port_layout.addWidget(self.port_edit)
        self.open_browser_btn = QPushButton("打开")
        self.open_browser_btn.clicked.connect(self._openInBrowser)
        self.open_browser_btn.setFixedWidth(70)
        port_layout.addWidget(self.open_browser_btn)
        server_layout.addLayout(port_layout, 0, 1)

        server_layout.addWidget(QLabel("用户名"), 1, 0)
        self.username_edit = QLineEdit()
        server_layout.addWidget(self.username_edit, 1, 1)
        server_layout.addWidget(QLabel("密码"), 2, 0)
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        server_layout.addWidget(self.password_edit, 2, 1)
        server_layout.addWidget(QLabel("连接状态"), 3, 0)
        self.status_label = QLabel("未连接")
        self.status_label.setStyleSheet("font-weight: bold; color: #666;")
        server_layout.addWidget(self.status_label, 3, 1)
        layout.addLayout(server_layout)

        # 分隔线
        line2 = QLabel()
        line2.setFixedHeight(1)
        line2.setStyleSheet("background-color: #ccc;")
        layout.addWidget(line2)

        # 任务选择和操作
        task_top = QHBoxLayout()
        task_top.addWidget(QLabel("任务"))
        self.task_combo = QComboBox()
        self.task_combo.setMinimumWidth(150)
        task_top.addWidget(self.task_combo)

        self.new_btn = QPushButton("新建")
        self.new_btn.clicked.connect(self._newTask)
        task_top.addWidget(self.new_btn)

        self.edit_btn = QPushButton("编辑")
        self.edit_btn.clicked.connect(self._editTask)
        task_top.addWidget(self.edit_btn)

        self.delete_btn = QPushButton("删除")
        self.delete_btn.clicked.connect(self._deleteTask)
        task_top.addWidget(self.delete_btn)

        self.run_btn = QPushButton("运行")
        self.run_btn.clicked.connect(self._runTask)
        task_top.addWidget(self.run_btn)

        self.stop_btn = QPushButton("停止")
        self.stop_btn.clicked.connect(self._stopTask)
        self.stop_btn.setEnabled(False)
        task_top.addWidget(self.stop_btn)

        layout.addLayout(task_top)

        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.progress_label = QLabel("")
        layout.addWidget(self.progress_label)

        # 日志输出
        layout.addWidget(QLabel("执行日志"))
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(150)
        layout.addWidget(self.log_text)

        # 启动状态检测定时器
        self._startStatusTimer()

    def _loadSettings(self):
        """加载设置"""
        self.path_edit.setText(self.plugin.openlist_path)
        self.port_edit.setText(self.plugin.port)
        self.username_edit.setText(self.plugin.username)
        self.password_edit.setText(self.plugin.password)
        self._refreshTaskCombo()

    def _cleanupTimer(self):
        """清理定时器、线程、客户端连接"""
        self._stopQueuePolling()
        if hasattr(self, 'status_timer'):
            self.status_timer.stop()
        if self.sync_worker is not None:
            self.sync_worker.abort()
            self.sync_worker.wait(5000)
            self.sync_worker.deleteLater()
            self.sync_worker = None
        if self.plugin.client:
            self.plugin.client.close()
            self.plugin.client = None

    def _refreshTaskCombo(self):
        """刷新任务下拉框"""
        self.task_combo.clear()
        for task in self.plugin.tasks:
            self.task_combo.addItem(task.name)

        if self.plugin.selected_task_name:
            index = self.task_combo.findText(self.plugin.selected_task_name)
            if index >= 0:
                self.task_combo.setCurrentIndex(index)

    def _saveSettings(self):
        """保存设置"""
        self.plugin.openlist_path = self.path_edit.text().strip()
        self.plugin.port = self.port_edit.text().strip()
        self.plugin.username = self.username_edit.text().strip()
        self.plugin.password = self.password_edit.text().strip()
        self.plugin.selected_task_name = self.task_combo.currentText()
        self.plugin.saveConfig()
        self._log("配置已保存")

    def _openInBrowser(self):
        """在浏览器中打开地址"""
        port = self.port_edit.text().strip()
        url = f"http://{port}"
        webbrowser.open(url)

    def _getCurrentTask(self) -> Optional[TaskConfig]:
        """获取当前选中的任务"""
        name = self.task_combo.currentText()
        for task in self.plugin.tasks:
            if task.name == name:
                return task
        return None

    def _newTask(self):
        """新建任务"""
        self._saveSettings()

        dialog = TaskEditDialog(self, OpenListClient(f"http://{self.plugin.port}"))
        if dialog.exec() == QDialog.DialogCode.Accepted:
            task = dialog.getTask()
            self.plugin.tasks.append(task)
            self.plugin.saveConfig()
            self._refreshTaskCombo()
            self.task_combo.setCurrentText(task.name)
            self._log(f"任务 '{task.name}' 已创建")

    def _editTask(self):
        """编辑任务"""
        task = self._getCurrentTask()
        if not task:
            messageBox(self, "警告", "请先选择要编辑的任务", 1)
            return

        self._saveSettings()

        dialog = TaskEditDialog(self, OpenListClient(f"http://{self.plugin.port}"), task)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_task = dialog.getTask()
            # 更新任务
            for i, t in enumerate(self.plugin.tasks):
                if t.name == task.name:
                    self.plugin.tasks[i] = new_task
                    break
            self.plugin.saveConfig()
            self._refreshTaskCombo()
            self.task_combo.setCurrentText(new_task.name)
            self._log(f"任务 '{new_task.name}' 已更新")

    def _deleteTask(self):
        """删除任务"""
        task = self._getCurrentTask()
        if not task:
            messageBox(self, "警告", "请先选择要删除的任务", 1)
            return

        if not messageBox(self, "确认删除", tr("是否确认删除") + " " + task.name, 2):
            self.plugin.tasks = [t for t in self.plugin.tasks if t.name != task.name]
            self.plugin.saveConfig()
            self._refreshTaskCombo()
            self._log(f"任务 '{task.name}' 已删除")

    def _ensureConnected(self) -> bool:
        """确保已连接"""
        if not self.plugin.login():
            messageBox(self, "警告", "无法连接到 OpenList，请检查配置", 1)
            return False
        return True

    def _startQueuePolling(self):
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._drainQueues)
        self._poll_timer.start(50)

    def _drainQueues(self):
        worker = self.sync_worker
        if not worker:
            return
        for _ in range(100):
            try:
                self._appendLog(worker.log_queue.get_nowait())
            except queue.Empty:
                break
        latest = None
        while True:
            try:
                latest = worker.progress_queue.get_nowait()
            except queue.Empty:
                break
        if latest:
            self._onProgress(*latest)

    def _stopQueuePolling(self):
        if hasattr(self, '_poll_timer'):
            self._poll_timer.stop()

    def _runTask(self):
        """运行任务"""
        task = self._getCurrentTask()
        if not task:
            messageBox(self, "警告", "请先选择要运行的任务", 1)
            return

        self._saveSettings()
        if not self._ensureConnected():
            return

        # 禁用按钮
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.progress_label.setText("准备中...")

        # 清空日志
        self.log_text.clear()

        # 停止旧线程
        if self.sync_worker is not None:
            self.sync_worker.abort()
            self.sync_worker.wait(5000)
            self.sync_worker.deleteLater()
            self.sync_worker = None

        # 启动同步线程
        self.sync_worker = SyncWorker(self.plugin.getClient(), task)
        self.sync_worker.finished.connect(self._onFinished)
        self.sync_worker.need_confirm.connect(self._onNeedConfirm)
        self.sync_worker.start()
        self._startQueuePolling()

    def _onNeedConfirm(self, to_upload: dict, to_delete: dict):
        """需要用户确认"""
        task = self._getCurrentTask()
        mode = task.mode if task else MODE_BACKUP

        dialog = FileConfirmDialog(self, to_upload, to_delete, mode)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.sync_worker.confirm(True)
        else:
            self.sync_worker.confirm(False)

    def _stopTask(self):
        """停止任务"""
        if self.sync_worker:
            self.sync_worker.abort()
            self._log("正在停止任务...")

    def _onProgress(self, message: str, current: int, total: int):
        """进度更新"""
        self.progress_label.setText(message)
        if total > 0:
            percent = int(current / total * 100)
            self.progress_bar.setValue(percent)

    def _onFinished(self, result: dict):
        """任务完成"""
        self._stopQueuePolling()
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

        status = result.get("status", "unknown")
        duration = result.get("duration", 0)
        upload_success = result.get("upload_success", 0)
        upload_failed = result.get("upload_failed", 0)
        delete_success = result.get("delete_success", 0)
        delete_failed = result.get("delete_failed", 0)

        if status == TASK_STATUS_SUCCESS:
            self.progress_label.setText(
                f"完成 - 上传: {upload_success}, 失败: {upload_failed}, "
                f"删除: {delete_success}, 耗时: {duration:.1f}秒"
            )
        elif status == TASK_STATUS_ABORTED:
            self.progress_label.setText("任务已中止")
        else:
            self.progress_label.setText(f"任务失败: {result.get('error_msg', '')}")

        # 弹出结果对话框（仅在有实际上传或删除操作时）
        if status == TASK_STATUS_SUCCESS or status == TASK_STATUS_ABORTED:
            if (upload_success > 0 or upload_failed > 0 or
                delete_success > 0 or delete_failed > 0):
                dialog = SyncResultDialog(self, result)
                dialog.exec()
        elif status == TASK_STATUS_FAILED:
            messageBox(self, "任务失败", f"任务执行失败:\n{result.get('error_msg', '未知错误')}", 1)

        worker = self.sync_worker
        self.sync_worker = None
        if worker is not None:
            worker.deleteLater()

    def _log(self, message: str):
        """输出日志"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_signal.emit(f"[{timestamp}] {message}")

    def _appendLog(self, message: str):
        """追加日志到文本框"""
        self.log_text.append(message)
        # 自动滚动到底部
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def startOpenList(self):
        """启动OpenList"""
        self._saveSettings()
        openlist_path = self.plugin.openlist_path
        if not openlist_path:
            messageBox(self, "警告", "请先选择 OpenList 路径", 1)
            return
        try:
            work_dir = os.path.dirname(openlist_path)
            subprocess.Popen(
                [openlist_path, "server"],
                cwd=work_dir,
                creationflags=subprocess.CREATE_NEW_CONSOLE
            )
            self._log("正在启动 OpenList...")
        except Exception as e:
            messageBox(self, "错误", f"启动失败: {e}", 1)

    def stopOpenList(self):
        """停止OpenList"""
        try:
            cmd_name = os.path.basename(self.plugin.openlist_path)
            subprocess.Popen(
                ["taskkill", "/f", "/im", cmd_name],
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            self._log("正在停止 OpenList...")
        except Exception as e:
            messageBox(self, "错误", f"停止失败: {e}", 1)

    def getStatus(self) -> str:
        """获取OpenList运行状态"""
        try:
            for proc in psutil.process_iter(['name', 'exe']):
                try:
                    if proc.info['name'] and 'openlist' in proc.info['name'].lower():
                        return "运行中"
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except Exception:
            logger.exception("获取进程状态失败")
        return "未运行"

    def _startStatusTimer(self):
        """启动状态检测定时器"""
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self._updateStatus)
        self.status_timer.start(3000)
        self._updateStatus()

    def _updateStatus(self):
        """更新运行状态"""
        status = self.getStatus()
        self.run_status_label.setText(status)
        if status == "运行中":
            self.run_status_label.setStyleSheet("font-weight: bold; color: #4CAF50;")
        else:
            self.run_status_label.setStyleSheet("font-weight: bold; color: #F44336;")
