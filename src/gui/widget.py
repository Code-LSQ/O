"""GUI 组件模块，谨慎导入本地模块"""
import subprocess

from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout, QTextEdit, QLabel, QPushButton, QProgressBar, QDialog, QApplication
from PySide6.QtCore import Qt, QTimer

from src.util import logger, tr, messageBox, arch, root, VERSION, download, compareVersions, runAsync
from src.core.update import getReleaseInfo, extractUpdate, writeUpdateScript, cleanTemp, UPDATE_ZIP, UPDATE_DIR


class UpdateDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._release_info = None
        self._downloading = False
        self._installing = False
        self._checking = False
        self._download_url = None
        self._initUI()

    def _initUI(self):
        self.setWindowTitle(tr("检查更新"))
        self.setMinimumWidth(500)
        self.setAttribute(Qt.WA_DeleteOnClose)

        layout = QVBoxLayout(self)

        self._version_label = QLabel()
        layout.addWidget(self._version_label)

        self._notes_label = QLabel(tr("更新说明"))
        self._notes_label.hide()
        layout.addWidget(self._notes_label)

        self._notes_text = QTextEdit()
        self._notes_text.setReadOnly(True)
        self._notes_text.setMaximumHeight(250)
        self._notes_text.hide()
        layout.addWidget(self._notes_text)

        self._progress = QProgressBar()
        self._progress.hide()
        layout.addWidget(self._progress)

        btn_layout = QHBoxLayout()

        self._check_btn = QPushButton(tr("检查更新"))
        self._check_btn.clicked.connect(self._check)
        btn_layout.addWidget(self._check_btn)

        self._download_btn = QPushButton(tr("下载更新"))
        self._download_btn.clicked.connect(self._download)
        self._download_btn.setEnabled(False)
        btn_layout.addWidget(self._download_btn)

        self._close_btn = QPushButton(tr("取消"))
        self._close_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self._close_btn)

        layout.addLayout(btn_layout)

    def closeEvent(self, event):
        if self._downloading:
            messageBox(self, tr("提示"), tr("正在下载更新，请等待下载完成"), 1)
            event.ignore()
            return
        if self._installing:
            event.ignore()
            return
        cleanTemp()
        event.accept()

    @staticmethod
    def checkAndUpdate(parent):
        dialog = UpdateDialog(parent)
        dialog._check()
        dialog.exec()

    def _setCheckEnabled(self, enabled):
        self._check_btn.setEnabled(enabled)

    def _check(self):
        if self._checking:
            return
        self._checking = True
        self._release_info = None
        self._download_btn.setEnabled(False)
        self._notes_text.hide()
        self._notes_label.hide()
        self._version_label.setText(tr("检查中..."))
        self._setCheckEnabled(False)

        runAsync(getReleaseInfo, on_done=self._onCheckResult)

    def _onCheckResult(self, info):
        self._checking = False
        self._setCheckEnabled(True)
        if info is None:
            messageBox(self, tr("错误"), tr("检查更新失败"), 1)
            self._version_label.setText(tr("检查更新失败"))
            return

        version = info["version"]
        if compareVersions(version, VERSION) <= 0:
            self._version_label.setText(tr("当前已是最新版本"))
            return

        self._release_info = info
        self._version_label.setText(tr("发现新版本") + f" {version}")
        body = info.get("body", "").strip()
        if body:
            self._notes_text.setText(body)
            self._notes_label.show()
            self._notes_text.show()

        asset_name = f"Windows_{arch}.zip"
        for asset in info["assets"]:
            if asset["name"] == asset_name:
                self._download_url = asset["browser_download_url"]
                self._download_btn.setEnabled(True)
                return

        messageBox(self, tr("警告"), tr("没有找到适用于当前平台的更新包"), 1)

    def _download(self):
        if not self._download_url:
            return
        self._downloading = True
        self._download_btn.setEnabled(False)
        self._check_btn.setEnabled(False)

        self._progress.setValue(0)
        self._progress.show()

        runAsync(
            lambda report: download(self._download_url, UPDATE_ZIP, report),
            on_done=self._onDownloadFinished,
            on_error=lambda _: self._onDownloadFinished(False),
            on_progress=self._onProgress,
        )

    def _onProgress(self, current, total):
        if total > 0:
            self._progress.setMaximum(total)
            self._progress.setValue(current)

    def _onDownloadFinished(self, success):
        self._downloading = False
        self._progress.hide()
        if success:
            self._download_btn.setText(tr("重启更新"))
            self._download_btn.setEnabled(True)
            self._download_btn.clicked.disconnect()
            self._download_btn.clicked.connect(self._install)
        else:
            cleanTemp()
            messageBox(self, tr("错误"), tr("下载失败"), 1)
            self._download_btn.setText(tr("下载更新"))
            self._download_btn.setEnabled(True)
            self._check_btn.setEnabled(True)

    def _install(self):
        if not messageBox(self, tr("提示"), tr("确认重启并安装更新？")):
            return

        self._installing = True
        self._setCheckEnabled(False)
        self._close_btn.setEnabled(False)
        self._version_label.setText(tr("准备更新..."))

        if not extractUpdate(UPDATE_ZIP, UPDATE_DIR):
            messageBox(self, tr("错误"), tr("解压更新包失败"), 1)
            self._installing = False
            self._close_btn.setEnabled(True)
            self._setCheckEnabled(True)
            return

        try:
            writeUpdateScript()
            script = str(root / "update.cmd")
            subprocess.Popen(
                [script],
                creationflags=subprocess.CREATE_NO_WINDOW,
                cwd=str(root),
            )
        except Exception:
            logger.exception("启动更新脚本失败")
            messageBox(self, tr("错误"), tr("启动更新脚本失败"), 1)
            self._installing = False
            self._close_btn.setEnabled(True)
            self._setCheckEnabled(True)
            return

        QTimer.singleShot(500, QApplication.quit)
