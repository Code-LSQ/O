"""更新模块，包含更新包的下载、校验、解压、更新脚本等逻辑，以及更新对话框。"""

import zipfile
import shutil
import subprocess

import requests
from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout, QTextBrowser, QLabel, QPushButton, QProgressBar, QDialog, QApplication
from PySide6.QtCore import Qt, QTimer

from src.api import root, data_dir, logger, UPDATE, VERSION, arch, tr, messageBox, download, compareVersions, runAsync
from src.core.md import renderMarkdown

# GitHub API 对未认证的匿名请求存在频率限制，需要留意，手动更新无需额外线程


UPDATE_ZIP = data_dir / "update.zip"
UPDATE_PART = UPDATE_ZIP.with_name(UPDATE_ZIP.name + ".part")  # 下载断点续传的临时文件，需一并清理
UPDATE_DIR = data_dir / "update"


def getReleaseInfo(url=None):
    """获取最新版本信息，返回 {"version": str, "body": str, "assets": [...]} 或 None"""
    if url is None:
        url = UPDATE
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        return {
            "version": data["tag_name"].lstrip("vV"),
            "body": data.get("body", ""),
            "assets": data.get("assets", []),
        }
    except requests.exceptions.Timeout:
        logger.exception("检查更新超时")
        raise TimeoutError("check_timeout")
    except requests.exceptions.RequestException:
        logger.exception("检查更新时发生网络错误")
    except KeyError:
        logger.exception("解析API响应时出错，未找到预期字段")
    except Exception:
        logger.exception("发生未知错误")
    return None


def extractUpdate(zip_path, extract_dir):
    """解压 zip 到目标目录"""
    try:
        if extract_dir.exists():
            shutil.rmtree(extract_dir)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)
        logger.info(f"已解压更新包到 {extract_dir}")
        _flattenSingleDir(extract_dir)
        return True
    except zipfile.BadZipFile:
        logger.exception("更新包损坏")
    except Exception:
        logger.exception("解压更新包时出错")
    return False


def _flattenSingleDir(extract_dir):
    """若解压目录顶层只有一个目录且无散落文件，则将其内容上移一层。

    发布包为保留 O/ 顶层目录（用户手动解压时便于辨认），内部结构会嵌套一层；
    而 update.cmd 的复制命令期望平铺结构，故在此归一化，使两种打包方式都能正确更新。"""
    entries = list(extract_dir.iterdir()) if extract_dir.exists() else []
    dirs = [e for e in entries if e.is_dir()]
    if len(dirs) == 1 and len(dirs) == len(entries):
        inner = dirs[0]
        for item in inner.iterdir():
            shutil.move(str(item), str(extract_dir / item.name))
        shutil.rmtree(inner)
        logger.info(f"已将更新包内容从 {inner.name} 上移一层")


def writeUpdateScript():
    """在 root 生成 update.cmd"""
    update_cmd = root / "update.cmd"
    # 更新失败时必须有回退，不能删光旧文件后复制失败导致程序无法启动，故采用「先备份、再替换、失败还原」策略。
    # 备份与还原都用 robocopy，因为 xcopy 无法排除目录，会把 data 递归拷进备份目录造成死循环；robocopy 的 /XD data /XF 脚本自身 可干净排除。
    # robocopy 退出码 0-7 为成功，>=8 为失败。删除旧文件时须排除 update.cmd 自身，否则脚本在执行中途被删会导致后续复制/启动失败，自删只保留在末尾一条命令。
    content = r"""@echo off
chcp 65001 >nul
if exist "data\update_error.txt" del /f /q "data\update_error.txt" >nul 2>nul
:wait
tasklist /fi "imagename eq O.exe" 2>nul | find /i "O.exe" >nul
if not errorlevel 1 (
    timeout /t 2 /nobreak >nul
    goto wait
)
cd /d "%~dp0"
if not exist "data\update\O.exe" (
    echo 更新包不完整，已中止更新 > "data\update_error.txt"
    goto finish
)
if exist "data\update_backup" rmdir /s /q "data\update_backup" >nul 2>nul
robocopy "." "data\update_backup" /E /XD data /XF "%~nx0" /R:1 /W:1 /NFL /NDL /NJH /NJS >nul
if errorlevel 8 (
    echo 备份旧文件失败，已中止更新 > "data\update_error.txt"
    goto finish
)
for /f "delims=" %%i in ('dir /b /a-d 2^>nul') do if /i not "%%i"=="data" if /i not "%%i"=="%~nx0" del /f /q "%%i" 2>nul
for /f "delims=" %%i in ('dir /b /ad 2^>nul') do if /i not "%%i"=="data" rmdir /s /q "%%i" 2>nul
robocopy "data\update" "." /E /R:1 /W:1 /NFL /NDL /NJH /NJS >nul
if errorlevel 8 (
    echo 复制新文件失败，正在还原旧版本 > "data\update_error.txt"
    for /f "delims=" %%i in ('dir /b /a-d 2^>nul') do if /i not "%%i"=="data" if /i not "%%i"=="%~nx0" del /f /q "%%i" 2>nul
    for /f "delims=" %%i in ('dir /b /ad 2^>nul') do if /i not "%%i"=="data" rmdir /s /q "%%i" 2>nul
    robocopy "data\update_backup" "." /E /R:1 /W:1 /NFL /NDL /NJH /NJS >nul
)
rmdir /s /q "data\update" >nul 2>nul
if exist "data\update.zip" del /f /q "data\update.zip" >nul 2>nul
:finish
rmdir /s /q "data\update_backup" >nul 2>nul
start "" "O.exe"
del /f /q "%~f0" >nul 2>nul
exit
"""
    update_cmd.write_text(content, encoding="utf-8")
    logger.info(f"已生成更新脚本 {update_cmd}")
    return update_cmd


def cleanTemp():
    """清理临时文件"""
    try:
        if UPDATE_ZIP.exists():
            UPDATE_ZIP.unlink()
        if UPDATE_PART.exists():
            UPDATE_PART.unlink()
        if UPDATE_DIR.exists():
            shutil.rmtree(UPDATE_DIR)
    except Exception:
        logger.exception("清理临时文件时出错")


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
