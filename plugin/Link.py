import os
import sys
import shutil
from typing import Optional, Tuple, List

from PySide6.QtWidgets import QPushButton, QDialog, QLineEdit, QFormLayout, QVBoxLayout, QHBoxLayout, QListWidgetItem
from PySide6.QtGui import QAction
from PySide6.QtCore import Qt

from src.plugin import PluginBase
from src.util import isAdmin, runAdmin, ManagePair, logger, checksum, messageBox, dialogBox, tr

SOURCE_NOT_EXISTS = "源路径不存在"
SOURCE_SYMLINK = "源路径是符号链接"
SOURCE_NOT_SYMLINK = "源路径是文件或文件夹"
TARGET_NOT_EXISTS = "目标路径不存在"
TARGET_SYMLINK = "目标路径是符号链接"
TARGET_NOT_SYMLINK = "目标路径是文件或文件夹"
NORMAL = "正常"
BROKEN = "符号链接已断开"

# Windows 用环境变量，适配非标准安装（系统 装 D 盘、Program Files 自定义等），Linux 没有 %WINDIR% 这类标准环境变量来指代系统目录。FHS（Filesystem Hierarchy Standard）非常统一，/etc、/usr、/bin、/boot 在所有发行版上位置固定，macOS 同理，因此用路径就行。
system_dir = set()

if sys.platform == "win32":
    env_vars = ['SystemRoot', 'WINDIR', 'ProgramFiles', 'ProgramFiles(x86)', 'CommonProgramFiles', 'CommonProgramFiles(x86)', 'ProgramData']
    for var in env_vars:
        val = os.environ.get(var)
        if val:
            system_dir.add(os.path.normpath(val).lower())

elif sys.platform == "linux":
    for p in ['/etc', '/usr', '/bin', '/sbin', '/lib', '/lib32', '/lib64', '/libx32', '/boot', '/var', '/dev', '/proc', '/sys', '/root', '/run', '/snap', '/lost+found']:
        system_dir.add(os.path.normpath(p))

elif sys.platform == "darwin":
    for p in ['/System', '/Library', '/private', '/Applications', '/Volumes', '/Network', '/cores', '/opt', '/usr', '/var', '/etc', '/bin', '/sbin', '/lib']:
        system_dir.add(os.path.normpath(p))

def _isSystemPath(path: str) -> bool:
    normalized = os.path.normpath(path)
    if sys.platform == "win32":
        normalized = normalized.lower()
    for protected in system_dir:
        if normalized == protected or normalized.startswith(protected + os.sep):
            return True
    return False

class LinkPlugin(PluginBase):
    
    version = "1.0.0"
    description = "符号链接管理"
    
    def __init__(self, main=None, editor=None):
        super().__init__(main=main, editor=editor)

    def loadConfig(self):
        super().loadConfig()
        self.settings.setdefault("links", [])

    def initialize(self):
        if not super().initialize():
            return

    def getAction(self):
        action = QAction(self.description, self.main)
        action.triggered.connect(self.showManageDialog)
        return action
    
    def showManageDialog(self, action=None):
        self.initialize()
        dialog = LinkManage(self.main, pairs=self.settings.get("links", []), on_save=self._onDialogSave)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.show()
    
    def _onDialogSave(self, pairs):
        """对话框保存回调"""
        self.settings["links"] = pairs
        self.saveConfig()
        logger.info(f"符号链接配置已保存，共 {len(self.settings.get('links', []))} 项")


class LinkManage(QDialog):

    def __init__(self, parent=None, pairs=None, on_save=None):
        super().__init__(parent)
        self.on_save = on_save
        title = "符号链接管理"
        if not isAdmin():
            title += " - 需要管理员权限"
        self.setWindowTitle(title)
        self.setMinimumSize(450, 300)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.Dialog)

        self.pair_manage = ManagePair(self, pairs=pairs, connect_signals=False)

        self.run_btn = QPushButton("运行")
        self.recover_btn = QPushButton("恢复")
        self.test_btn = QPushButton("测试")

        main_layout = QVBoxLayout(self)
        main_layout.addWidget(self.pair_manage.pair_list)

        button_layout = QHBoxLayout()
        button_layout.addWidget(self.run_btn)
        button_layout.addWidget(self.recover_btn)
        button_layout.addWidget(self.test_btn)
        button_layout.addWidget(self.pair_manage.add_btn)
        button_layout.addWidget(self.pair_manage.edit_btn)
        button_layout.addWidget(self.pair_manage.delete_btn)
        button_layout.addStretch()
        main_layout.addLayout(button_layout)

        self.run_btn.clicked.connect(self.runLink)
        self.recover_btn.clicked.connect(self.recoverLink)
        self.test_btn.clicked.connect(self.testAllLinks)
        self.pair_manage.add_btn.clicked.connect(self.add)
        self.pair_manage.edit_btn.clicked.connect(self.edit)
        self.pair_manage.delete_btn.clicked.connect(self.delete)

    def closeEvent(self, event):
        """对话框关闭时保存配置"""
        if self.on_save:
            self.on_save(self.pair_manage.getPairs())
        super().closeEvent(event)

    def _showElevationDialog(self):
        """显示提权确认对话框"""
        if messageBox(self, "需要管理员权限", "是否以管理员身份运行？是则重启程序", 2):
            runAdmin()

    def _handleSymlinkError(self, err_msg: str):
        """处理符号链接操作错误，如果是权限问题则显示提权对话框"""
        if "需要管理员权限" in err_msg:
            self._showElevationDialog()
        else:
            messageBox(self, "错误", f"操作失败: {err_msg}", 1)

    def refresh(self, pairs):
        """刷新列表数据"""
        self.pair_manage.pair_list.clear()
        for pair in pairs:
            item = QListWidgetItem(pair.get("name", ""))
            item.setData(Qt.ItemDataRole.UserRole, pair.get("value", ""))
            self.pair_manage.pair_list.addItem(item)

    def parseValue(self, value: str) -> Tuple[Optional[str], Optional[str]]:
        if not value:
            return None, None
        if "->" in value:
            parts = value.rsplit("->", 1)
            source_path = os.path.normpath(os.path.expandvars(parts[0].strip()))
            target_path = os.path.normpath(os.path.expandvars(parts[1].strip()))
        else:
            source_path = os.path.normpath(os.path.expandvars(value))
            target_path = None
        if not source_path or source_path == ".":
            source_path = None
        if not target_path or target_path == ".":
            target_path = None
        return source_path, target_path

    def _createSymlink(self, target_path: str, link_path: str, is_dir: Optional[bool] = None) -> Tuple[bool, Optional[str]]:
        """创建符号链接：在 link_path 创建指向 target_path 的符号链接"""
        is_dir_flag = is_dir if is_dir is not None else os.path.isdir(target_path)
        try:
            if sys.platform == "win32":
                os.symlink(target_path, link_path, target_is_directory=is_dir_flag)
            else:
                os.symlink(target_path, link_path)
            logger.info(f"创建符号链接: {link_path} -> {target_path}")
            return True, None
        except OSError as e:
            if sys.platform == "win32" and getattr(e, 'winerror', None) == 1314:
                return False, "创建符号链接需要管理员权限，请以管理员身份运行程序"
            logger.exception("创建符号链接失败")
            return False, str(e)

    def _safeMove(self, src: str, dst: str) -> Tuple[bool, Optional[str]]:
        src_drive = os.path.splitdrive(src)[0].lower()
        dst_drive = os.path.splitdrive(dst)[0].lower()
        if src_drive == dst_drive:
            try:
                shutil.move(src, dst)
                return True, None
            except Exception as e:
                return False, f"移动文件失败: {e}"

        try:
            if os.path.isdir(src):
                shutil.copytree(src, dst, symlinks=False)
            else:
                shutil.copy2(src, dst)
            if not checksum(src) == checksum(dst):
                return False, "复制后校验失败，源文件未删除"
            if os.path.isdir(src):
                shutil.rmtree(src)
            else:
                os.remove(src)
            return True, None
        except Exception as e:
            return False, f"跨盘复制失败: {e}"

    def _executeLinkOperation(
        self,
        source_path: str,
        target_path: str,
        source_is_symlink: bool,
        source_is_dir: Optional[bool]
    ) -> Tuple[bool, Optional[str]]:
        """移动文件并创建符号链接：
        1. 如果 source_path 是符号链接：删除链接，将其目标文件移动到 target_path
        2. 如果 source_path 是普通文件/目录：移动到 target_path
        3. 在 source_path 创建指向 target_path 的符号链接
        """
        if _isSystemPath(source_path):
            return False, f"源路径涉及系统目录，禁止操作: {source_path}"
        if _isSystemPath(target_path):
            return False, f"目标路径涉及系统目录，禁止操作: {target_path}"

        # 如果 source_path 是符号链接，先获取其目标
        original_target = None
        if source_is_symlink:
            try:
                original_target = os.readlink(source_path)
                if original_target and _isSystemPath(original_target):
                    return False, f"符号链接目标涉及系统目录，禁止操作: {original_target}"
                os.remove(source_path)
                logger.info(f"已删除旧符号链接: {source_path} -> {original_target}")
            except Exception as e:
                return False, f"删除旧符号链接失败: {e}"
        
        # 移动文件到 target_path
        need_move = False
        move_source = None
        
        if source_is_symlink and original_target:
            # 将符号链接指向的原始文件移动到 target_path
            if os.path.exists(original_target):
                move_source = original_target
                need_move = True
            else:
                # 原始文件不存在，无法移动，直接创建符号链接
                logger.warning(f"符号链接目标不存在: {original_target}")
        elif not source_is_symlink and os.path.exists(source_path):
            # source_path 是普通文件/目录，移动它
            move_source = source_path
            need_move = True
        
        if need_move and move_source:
            # 检查源和目标是否相同
            if os.path.abspath(move_source) == os.path.abspath(target_path):
                logger.info(f"源路径和目标路径相同，跳过移动: {move_source}")
            else:
                try:
                    if os.path.exists(target_path):
                        return False, f"目标路径已存在: {target_path}"
                    parent = os.path.dirname(target_path)
                    if not os.path.exists(parent):
                        return False, f"目标父目录不存在: {parent}"
                    success, err_msg = self._safeMove(move_source, target_path)
                    if not success:
                        return False, err_msg
                    logger.info(f"移动文件: {move_source} -> {target_path}")
                except Exception as e:
                    return False, f"移动文件失败: {e}"
        
        # 检查 target_path 是否存在（移动后应该存在，或者原本就存在）
        if not os.path.exists(target_path):
            return False, f"目标路径不存在: {target_path}"
        
        is_dir = source_is_dir if source_is_dir is not None else os.path.isdir(target_path)
        success, err_msg = self._createSymlink(target_path, source_path, is_dir)
        if not success and move_source:
            rollback_ok, rollback_err = self._safeMove(target_path, move_source)
            if rollback_ok:
                return False, f"创建符号链接失败，已自动回滚: {err_msg}"
            else:
                return False, (
                    f"创建符号链接失败: {err_msg}\n"
                    f"移动文件到目标路径成功，文件实际位于: {target_path}\n"
                    f"回滚失败: {rollback_err}"
                )
        return success, err_msg

    def _recoverLinkOperation(self, source_path: str, target_path: str) -> Tuple[bool, Optional[str]]:
        """恢复操作：删除符号链接，并将文件从 target_path 移回 source_path
        
        Args:
            source_path: 符号链接路径（将恢复为原始文件位置）
            target_path: 当前文件位置（将被移回 source_path）"""
        if _isSystemPath(source_path):
            return False, f"源路径涉及系统目录，禁止恢复操作: {source_path}"
        if _isSystemPath(target_path):
            return False, f"目标路径涉及系统目录，禁止恢复操作: {target_path}"

        # 1. 删除符号链接（如果存在）
        if os.path.islink(source_path):
            try:
                os.remove(source_path)
                logger.info(f"已删除符号链接: {source_path}")
            except Exception as e:
                return False, f"删除符号链接失败: {e}"
        
        # 2. 将文件从 target_path 移回 source_path（如果 target_path 存在）
        if os.path.exists(target_path):
            # 检查源和目标是否相同
            if os.path.abspath(target_path) == os.path.abspath(source_path):
                logger.info(f"目标路径和源路径相同，无需移动: {target_path}")
                return True, None
            # 检查 source_path 是否已存在（非符号链接）
            if os.path.lexists(source_path):
                return False, f"源路径已存在，无法移动恢复: {source_path}"
            try:
                shutil.move(target_path, source_path)
                logger.info(f"恢复移动文件: {target_path} -> {source_path}")
                return True, None
            except Exception as e:
                return False, f"移动文件恢复失败: {e}"
        else:
            # target_path 不存在，无法恢复
            return False, f"目标路径不存在，无法恢复: {target_path}"

    def getSelectedPair(self):
        selected = self.pair_manage.pair_list.selectedItems()
        if not selected:
            messageBox(self, "警告", "请先选择一个要操作的项", 1)
            return None, None, None
        current_item = selected[0]

        name = current_item.text()
        value = current_item.data(Qt.ItemDataRole.UserRole)
        source_path, target_path = self.parseValue(value)
        
        if target_path is None:
            messageBox(self, "警告", "无效的格式，需要 source_path->target_path 格式", 1)
            return None, None, None
        
        return name, source_path, target_path

    def checkPathLink(self, path: str, symlink_status: str, not_symlink_status: str, not_exists_status: str) -> Tuple[str, Optional[bool]]:
        if os.path.lexists(path):
            is_link = os.path.islink(path)
            if is_link:
                link_target = os.readlink(path)
                is_dir = os.path.isdir(link_target) if os.path.exists(link_target) else None
                return (symlink_status, is_dir)
            else:
                is_dir = os.path.isdir(path)
                return (not_symlink_status, is_dir)
        else:
            return (not_exists_status, None)

    def add(self):
        name, value = self.pairDialog("添加配对")
        if name is not None:
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, value)
            self.pair_manage.pair_list.addItem(item)

    def edit(self):
        selected = self.pair_manage.pair_list.selectedItems()
        if not selected:
            messageBox(self, "警告", "请先选择一个要编辑的项", 1)
            return
        current_item = selected[0]

        old_name = current_item.text()
        old_value = current_item.data(Qt.ItemDataRole.UserRole)

        name, value = self.pairDialog("编辑配对", old_name, old_value)
        if name is not None:
            current_item.setText(name)
            current_item.setData(Qt.ItemDataRole.UserRole, value)

    def delete(self):
        selected = self.pair_manage.pair_list.selectedItems()
        if not selected:
            messageBox(self, "警告", "请先选择一个要删除的项", 1)
            return
        current_item = selected[0]

        if not messageBox(self, "确认删除", tr("是否确认删除配置") + " " + current_item.text()):
            row = self.pair_manage.pair_list.row(current_item)
            self.pair_manage.pair_list.takeItem(row)

    def pairDialog(self, title: str, initial_name: str = "", initial_value: str = "") -> Tuple[Optional[str], Optional[str]]:
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setMinimumWidth(450)

        layout = QVBoxLayout(dialog)
        form_layout = QFormLayout()

        name_edit = QLineEdit()
        name_edit.setText(initial_name)
        form_layout.addRow("名称", name_edit)

        source_edit = QLineEdit()
        target_edit = QLineEdit()
        
        if initial_value and "->" in initial_value:
            parts = initial_value.rsplit("->", 1)
            source_edit.setText(parts[0].strip())
            target_edit.setText(parts[1].strip())

        form_layout.addRow("源路径 (source)", source_edit)
        form_layout.addRow("目标路径 (target)", target_edit)

        layout.addLayout(form_layout)

        if not dialogBox(layout, dialog):
            return None, None

        name = name_edit.text().strip()
        source = source_edit.text().strip()
        target = target_edit.text().strip()
        
        if not name:
            messageBox(self, "警告", "名称不能为空", 1)
            return None, None
        if not source or not target:
            messageBox(self, "警告", "源路径和目标路径不能为空", 1)
            return None, None
        
        source = os.path.normpath(os.path.expandvars(source))
        target = os.path.normpath(os.path.expandvars(target))

        if _isSystemPath(source):
            messageBox(self, "禁止操作", f"源路径涉及系统目录，禁止操作:\n{source}", 1)
            return None, None
        if _isSystemPath(target):
            messageBox(self, "禁止操作", f"目标路径涉及系统目录，禁止操作:\n{target}", 1)
            return None, None

        return name, f"{source}->{target}"

    def runLink(self):
        result = self.getSelectedPair()
        if result[0] is None:
            return
        
        name, source_path, target_path = result
        status, is_dir = self.checkPathLink(source_path, SOURCE_SYMLINK, SOURCE_NOT_SYMLINK, SOURCE_NOT_EXISTS)

        if status == SOURCE_SYMLINK:
            success, err_msg = self._executeLinkOperation(source_path, target_path, True, is_dir)
            if success:
                messageBox(self, "完成", "已删除旧符号链接并创建新符号链接", 1)
            else:
                self._handleSymlinkError(err_msg)

        elif status == SOURCE_NOT_SYMLINK:
            if messageBox(self, "确认", f"源路径存在但不是符号链接，是否将其移动到目标路径并创建符号链接？\n{source_path} -> {target_path}", 2):
                success, err_msg = self._executeLinkOperation(source_path, target_path, False, is_dir)
                if success:
                    messageBox(self, "完成", "文件已移动并创建符号链接", 1)
                else:
                    self._handleSymlinkError(err_msg)

        elif status == SOURCE_NOT_EXISTS:
            if not os.path.exists(target_path):
                messageBox(self, "无法创建", "源路径和目标路径均不存在，无法创建符号链接", 1)
                return
            is_dir = os.path.isdir(target_path)
            success, err_msg = self._executeLinkOperation(source_path, target_path, False, is_dir)
            if success:
                messageBox(self, "完成", "符号链接创建成功", 1)
            else:
                self._handleSymlinkError(err_msg)


    def recoverLink(self):
        result = self.getSelectedPair()
        if result[0] is None:
            return
        
        name, source_path, target_path = result
        status, is_dir = self.checkPathLink(source_path, SOURCE_SYMLINK, SOURCE_NOT_SYMLINK, SOURCE_NOT_EXISTS)

        if status == SOURCE_SYMLINK:
            success, err_msg = self._recoverLinkOperation(source_path, target_path)
            if success:
                messageBox(self, "完成", "符号链接已删除，文件已恢复", 1)
            else:
                self._handleSymlinkError(err_msg)
        
        elif status == SOURCE_NOT_SYMLINK:
            if not os.path.exists(target_path):
                messageBox(self, "无法恢复", f"目标路径不存在，无法恢复:\n{target_path}", 1)
                return

            if _isSystemPath(source_path):
                messageBox(self, "禁止操作", f"源路径涉及系统目录，禁止删除:\n{source_path}", 1)
                return

            if messageBox(self, "确认覆盖", f"源路径存在且不是符号链接，是否删除该文件或文件夹并从目标路径恢复？\n{source_path}", 2):
                # 先删除已存在的源路径
                try:
                    if os.path.isdir(source_path):
                        shutil.rmtree(source_path)
                    else:
                        os.remove(source_path)
                    logger.info(f"删除源路径: {source_path}")
                except Exception as e:
                    messageBox(self, "错误", f"删除源路径失败: {str(e)}", 1)
                    return
                # 调用恢复操作
                success, err_msg = self._recoverLinkOperation(source_path, target_path)
                if success:
                    messageBox(self, "完成", "文件已恢复", 1)
                else:
                    self._handleSymlinkError(err_msg)
        
        elif status == SOURCE_NOT_EXISTS:
            messageBox(self, "完成", "源路径不存在，无需恢复", 1)

    def testAllLinks(self):
        if self.pair_manage.pair_list.count() == 0:
            messageBox(self, "测试结果", "没有符号链接", 1)
            return
        
        results = []
        for i in range(self.pair_manage.pair_list.count()):
            item = self.pair_manage.pair_list.item(i)
            name = item.text()
            value = item.data(Qt.ItemDataRole.UserRole)
            
            if not value:
                results.append(f"{name}: 数据为空")
                continue
            
            source_path, target_path = self.parseValue(value)
            
            if source_path is None or target_path is None:
                results.append(f"{name}: 格式错误 (需要 source->target 格式)")
                continue
            
            source_status, source_is_dir = self.checkPathLink(source_path, SOURCE_SYMLINK, SOURCE_NOT_SYMLINK, SOURCE_NOT_EXISTS)
            target_status, target_is_dir = self.checkPathLink(target_path, TARGET_SYMLINK, TARGET_NOT_SYMLINK, TARGET_NOT_EXISTS)
            
            if source_status == SOURCE_SYMLINK:
                link_target = os.readlink(source_path)
                if os.path.exists(link_target):
                    status = NORMAL
                    source_type = "符号链接" + (" (文件夹)" if source_is_dir else " (文件)")
                else:
                    status = BROKEN
                    source_type = "符号链接 (目标不存在)"
            elif source_status == SOURCE_NOT_SYMLINK:
                status = "源路径存在但不是符号链接"
                source_type = f"{'文件夹' if source_is_dir else '文件'}"
            else:
                status = "源路径不存在"
                source_type = "不存在"
            
            if target_status == TARGET_NOT_EXISTS:
                target_type = "不存在"
            elif target_status == TARGET_SYMLINK:
                target_type = "符号链接"
            else:
                target_type = f"{'文件夹' if target_is_dir else '文件'}"
            
            results.append(f"{name}:\n  源: {source_type}\n  目标: {target_type}\n  状态: {status}")
        
        logger.info(f"测试符号链接结果: {results}")
        messageBox(self, "测试结果", "\n\n".join(results), 1)
