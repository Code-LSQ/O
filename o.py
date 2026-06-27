import os
import sys
import traceback

from PySide6.QtWidgets import QApplication

# sys.dont_write_bytecode = True   # 禁止生成 .pyc 文件
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # 兼容嵌入版 Python

from src.main import MainWindow, setApp
from src.util import APP_NAME, ExceptSign, logger, getScreen

AUTHOR = "Code-LSQ"
VERSION = "0.5.0"

def exceptionHook(exc_type, value, tb):
    exceptionInfo = (exc_type, value, tb)
    message = "".join(traceback.format_exception(*exceptionInfo)).rstrip()
    logger.error("未捕获异常", exc_info=exceptionInfo)

    try:
        ExceptSign.catchException.emit(message)
    except Exception:
        logger.exception("发送程序异常信号失败")

    if "__compiled__" not in globals():
        sys.__excepthook__(*exceptionInfo)

def main():

    sys.excepthook = exceptionHook
    logger.info(f"V{VERSION} 版本程序启动")

    # 解决 Qt6 中文锯齿，静默字体数据库调试日志
    os.environ["QT_QPA_PLATFORM"] = "windows:fontengine=freetype"
    os.environ["QT_LOGGING_RULES"] = "qt.text.font.db=false"

    # 设置程序属性
    app = QApplication()
    app.setApplicationVersion(VERSION)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    setApp(app)
    getScreen(app)

    window = MainWindow(app, sys.argv[1] if len(sys.argv) > 1 else None)
    window.show()

    sys.exit(app.exec())

if __name__ == "__main__":
    main()
