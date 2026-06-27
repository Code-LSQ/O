# 需要额外安装 OpenCC 库，如果删除此插件并移除构建脚本内的 '--hidden-import=opencc', 可以使程序大小减少 5M。
import opencc

from PySide6.QtWidgets import QMenu

from src.plugin import PluginBase

PluginLib = ["OpenCC==1.2.0"]  # 额外的第三方库

class OpenCCPlugin(PluginBase):
    version = "1.0.0"
    description = "简繁中文转换"

    def __init__(self, main_window):
        super().__init__(main_window)

    def initialize(self):
        if not super().initialize():
            return
        self._t2s = opencc.OpenCC('t2s')
        self._s2t = opencc.OpenCC('s2t')

    def getAction(self):
        menu = QMenu(self.description, self.main_window)
        menu.addAction("繁体转简体", self.cht_to_chs)
        menu.addAction("简体转繁体", self.chs_to_cht)
        return menu

    def cht_to_chs(self):
        self.initialize()
        editor = getattr(self.main_window, 'get_current_editor', lambda: None)()
        if not editor:
            return
        editor.setPlainText(self._t2s.convert(editor.toPlainText()))
        self.main_window.statusBar().showMessage("已转换为简体", 2000)

    def chs_to_cht(self):
        self.initialize()
        editor = getattr(self.main_window, 'get_current_editor', lambda: None)()
        if not editor:
            return
        editor.setPlainText(self._s2t.convert(editor.toPlainText()))
        self.main_window.statusBar().showMessage("已转换为繁体", 2000)
