"""GUI 组件模块，谨慎导入本地模块"""
import subprocess

from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout, QTextBrowser, QLabel, QPushButton, QProgressBar, QDialog, QApplication
from PySide6.QtCore import Qt, QTimer

from src.util import logger, tr, messageBox, arch, root, VERSION, download, compareVersions, runAsync
from src.core.update import getReleaseInfo, extractUpdate, writeUpdateScript, cleanTemp, UPDATE_ZIP, UPDATE_DIR
from src.core.md import renderMarkdown


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
        self.setMinimumWidth(320)
        self.setAttribute(Qt.WA_DeleteOnClose)

        layout = QVBoxLayout(self)

        self._version_label = QLabel()
        layout.addWidget(self._version_label)

        self._notes_label = QLabel(tr("更新说明"))
        self._notes_label.hide()
        layout.addWidget(self._notes_label)

        self._notes_text = QTextBrowser()
        self._notes_text.setMinimumHeight(300)
        self._notes_text.setOpenExternalLinks(True)
        self._notes_text.hide()
        layout.addWidget(self._notes_text)

        self._progress = QProgressBar()
        self._progress.setObjectName("rainbow")
        self._progress.setFormat("")
        self._progress.hide()
        self._progress_percent = QLabel()
        self._progress_percent.setFixedWidth(40)
        self._progress_percent.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._progress_percent.hide()
        progress_layout = QHBoxLayout()
        progress_layout.addWidget(self._progress, 1)
        progress_layout.addWidget(self._progress_percent)
        layout.addLayout(progress_layout)

        btn_layout = QHBoxLayout()

        self._download_btn = QPushButton(tr("下载更新"))
        self._download_btn.clicked.connect(self._download)
        self._download_btn.setEnabled(False)
        self._download_btn.hide()
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

    def _check(self):
        if self._checking:
            return
        self._checking = True
        self._release_info = None
        self._download_btn.hide()
        self._download_btn.setEnabled(False)
        self._close_btn.setText(tr("取消"))
        self._notes_text.hide()
        self._notes_label.hide()
        self._version_label.setText(tr("检查中..."))

        runAsync(getReleaseInfo, on_done=self._onCheckResult, on_error=self._onCheckError)

    def showEvent(self, event):
        """窗口显示时直接居中，避免先显示在别处再移动"""
        super().showEvent(event)
        self._centerWindow()

    def _centerWindow(self):
        """将窗口移动到屏幕中央，先按内容调整大小再居中"""
        self.adjustSize()
        screen = self.screen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        self.move(geo.center().x() - self.width() // 2, geo.center().y() - self.height() // 2)

    def _showConfirmOnly(self):
        """隐藏下载按钮，仅保留确定按钮用于关闭窗口"""
        self._download_btn.hide()
        self._close_btn.setText(tr("确定"))

    def _onCheckError(self, message):
        self._checking = False
        logger.exception(f"检查更新出错: {message}")
        if message == "check_timeout":
            self._version_label.setText(tr("检查更新超时，请检查网络连接"))
        else:
            self._version_label.setText(tr("检查更新失败"))
        self._showConfirmOnly()

    def _onCheckResult(self, info):
        self._checking = False
        try:
            if info is None:
                self._version_label.setText(tr("检查更新失败"))
                self._showConfirmOnly()
                return

            version = info["version"]
            if compareVersions(version, VERSION) <= 0:
                self._version_label.setText(tr("当前已是最新版本"))
                self._showConfirmOnly()
                return

            self._release_info = info
            self._version_label.setText(tr("发现新版本") + f" {version}")
            self._download_btn.show()
            self._close_btn.setText(tr("取消"))
            self.setMinimumWidth(560)
            body = info.get("body", "").strip()
            if body:
                self._notes_text.setHtml(renderMarkdown(body) or body)
                self._notes_label.show()
                self._notes_text.show()
            # 待 Release 说明显示后再居中，否则窗口变高会向下延伸偏离屏幕中央
            self._centerWindow()

            asset_name = f"Windows_{arch}.zip"
            for asset in info["assets"]:
                if asset["name"] == asset_name:
                    self._download_url = asset["browser_download_url"]
                    self._download_btn.setEnabled(True)
                    return

            self._version_label.setText(tr("没有找到适用于当前平台的更新包"))
            self._showConfirmOnly()
        except Exception:
            logger.exception("处理更新检查结果时出错")
            self._version_label.setText(tr("检查更新失败"))
            self._showConfirmOnly()

    def _download(self):
        if not self._download_url:
            return
        self._downloading = True
        self._download_btn.setEnabled(False)

        self._progress.setValue(0)
        self._progress.show()
        self._progress_percent.setText("0%")
        self._progress_percent.show()

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
            self._progress_percent.setText(f"{int(current / total * 100)}%")

    def _onDownloadFinished(self, success):
        self._downloading = False
        self._progress.hide()
        self._progress_percent.hide()
        if success:
            if messageBox(self, tr("提示"), tr("确认重启程序并安装更新？")):
                self._install()
        else:
            cleanTemp()
            messageBox(self, tr("错误"), tr("下载失败"), 1)
            self._download_btn.setText(tr("下载更新"))
            self._download_btn.setEnabled(True)

    def _install(self):
        if self._installing:
            return

        self._installing = True
        self._close_btn.setEnabled(False)
        self._download_btn.setEnabled(False)

        if not extractUpdate(UPDATE_ZIP, UPDATE_DIR):
            messageBox(self, tr("错误"), tr("解压更新包失败"), 1)
            self._installing = False
            self._close_btn.setEnabled(True)
            return

        if not (UPDATE_DIR / "O.exe").is_file():
            # 校验暂存目录顶层是否有主程序，避免更新包内容错误（缺 O.exe）导致脚本删光旧文件后无法启动
            messageBox(self, tr("错误"), tr("更新包内容不完整"), 1)
            self._installing = False
            self._close_btn.setEnabled(True)
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
            return

        # 隐藏当前更新窗口，随后退出程序由脚本完成安装
        self.hide()
        QTimer.singleShot(500, QApplication.quit)
