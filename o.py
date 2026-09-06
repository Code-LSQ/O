import os
import sys
import traceback

from PySide6.QtWidgets import QApplication

sys.dont_write_bytecode = True   # 禁止生成 .pyc 文件
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))   # 兼容嵌入式 Python ，编译环境下无 .py 可以不用管，不过保险起见还是保持

# 无控制台环境下（GUI 程序）标准流可能为 None，argparse 打印帮助/版本、http.server 的 log_message 写 stderr 都会崩溃，统一重定向到 devnull
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

from src.api import APP_NAME, VERSION, OSign, logger, getDevice
from src.main import MainWindow, execPython, setApp


def exceptionHook(exc_type, value, tb):
    exceptionInfo = (exc_type, value, tb)
    message = "".join(traceback.format_exception(*exceptionInfo)).rstrip()
    logger.error("未捕获异常", exc_info=exceptionInfo)

    try:
        OSign.catchException.emit(message)
    except Exception:
        logger.exception("发送程序异常信号失败")

    if "__compiled__" not in globals():
        sys.__excepthook__(*exceptionInfo)


def parseArgs():
    """解析命令行参数并返回统一的 action 字典（name + content 信封结构），为将来单实例转发预留；
    --exec 执行完脚本即退出、-h/-v 由 argparse 直接退出，均不会走到返回"""
    import argparse

    parser = argparse.ArgumentParser(
        prog=APP_NAME,
        description="启动器",
        epilog="O.exe --exec path args  内嵌解释器执行脚本后退出，参数原样透传给脚本",
    )
    parser.add_argument("targets", nargs="*", metavar="路径", help="要打开的目标，可传多个")
    parser.add_argument("-v", "--version", action="version", version=f"{APP_NAME} V{VERSION}")

    if sys.argv[1:2] == ["--exec"]:
        if len(sys.argv) < 3:
            parser.error("--exec 需要指定脚本路径")
        script_path, *extra_args = sys.argv[2:]
        sys.exit(execPython(script_path, extra_args))

    return {"name": "open", "content": parser.parse_args().targets}


def main():

    sys.excepthook = exceptionHook

    action = parseArgs()

    logger.info(f"{APP_NAME} V{VERSION} 启动")
    logger.info(sys.executable)

    # os.environ["QT_QPA_PLATFORM"] = "windows:fontengine=freetype"   # 解决 Qt6 中文锯齿，已改为在 setApp 中使用 PreferNoHinting 策略解决
    # os.environ["QT_LOGGING_RULES"] = "qt.text.font.db=false"   # 静默 Qt 字体数据库调试日志

    app = QApplication()
    app.setApplicationVersion(VERSION)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    setApp(app)
    getDevice(app)

    window = MainWindow(app, action["content"])
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
