import os
import sys
import traceback

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

sys.dont_write_bytecode = True   # 禁止生成 .pyc 文件
sys.path.insert(0, os.path.dirname(os.path.abspath(sys.argv[0])))   # 兼容嵌入式 Python ，编译环境下无 .py 可以不用管，不过保险起见还是保持

# 无控制台环境下（GUI 程序）标准流可能为 None，argparse 打印帮助/版本、http.server 的 log_message 写 stderr 都会崩溃，统一重定向到 devnull
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

from src.api import APP_NAME, VERSION, app_path, root, logger, getDevice, OSign


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


def parseArgs(paths):
    """解析裸路径与 -h/-v，返回统一的 action 字典（name + content 信封结构）。

    open 只是裸路径的显式写法，命令词在这里剥掉，两者走同一条路。"""
    import argparse

    parser = argparse.ArgumentParser(
        prog=APP_NAME,
        description="Launcher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="o.exe open <path> [<path>…]        Open in the built-in editor, same as 'Open with O' in Explorer\n"
               "o.exe exec <script> [args…]        Run a script with the embedded interpreter, arguments are passed through\n"
               "o.exe list                         List groups, tools and runtime settings (JSON)\n"
               "o.exe add --name <name> …          Add a launcher tool (JSON)\n"
               "o.exe run <name> [options…]        Run a tool by name (JSON)\n"
               "\nRun 'o.exe <command> -h' for the options of each command",
    )
    parser.add_argument("paths", nargs="*", metavar="PATH", help="Targets to open, more than one is allowed")
    parser.add_argument("-v", "--version", action="version", version=f"{APP_NAME} V{VERSION}")

    paths = paths[1:] if paths[:1] == ["open"] else paths
    parsed = parser.parse_args(paths)

    # open 的 content 就是路径列表本身，不另套一层：parseArgs 的产物可原样转发给实例
    return {"name": "open", "content": parsed.paths}


def handOver(paths):
    """把本次启动交给已运行的实例，成功返回 True；没有实例、或实例没在超时内回执则返回 False。

    paths 已由 parseArgs 剥掉「open」：有路径请实例在编辑器里打开，无参数请它显示窗口。
    相对路径一律转绝对，否则会按实例的工作目录解析；abspath 只做规范化、不展开环境变量，
    文件名里长成 %VAR% 的部分不会被误伤，而 cmd 早在传进来之前就展开过了。
    这里刻意不加文件锁，竞态是有意接受的"""
    from src.cli import sendAction

    if paths:
        action = {"name": "open", "content": [os.path.abspath(p) for p in paths]}
    else:
        action = {"name": "show"}

    try:
        result = sendAction(action)
    except TimeoutError:
        # 实例在、但没在超时内回执：宁可多开一个窗口，也别让用户双击图标后什么都没发生
        logger.warning("已有实例但未返回回执，仍启动新窗口")
        return False
    return bool(result and result.get("ok"))


def main():

    sys.excepthook = exceptionHook

    argv = sys.argv[1:]

    # 不显示窗口的子命令在这里分流掉，省得为它们加载主窗口、编辑器与插件
    from src.cli import COMMANDS, runCli
    if argv[:1] and argv[0] in COMMANDS:
        sys.exit(runCli(argv))
    action = parseArgs(argv)

    from src.main import MainWindow, setApp

    logger.info(APP_NAME + " V " + VERSION)
    logger.info(root)
    logger.info(app_path)
    logger.info(sys.executable)
    logger.info(f"环境变量 PATH 长度 - {len(os.environ.get('PATH', ''))}")

    # os.environ["QT_QPA_PLATFORM"] = "windows:fontengine=freetype"   # 解决 Qt6 中文锯齿，已改为在 setApp 中使用 PreferNoHinting 策略解决
    # os.environ["QT_LOGGING_RULES"] = "qt.text.font.db=false"   # 静默 Qt 字体数据库调试日志

    app = QApplication()
    app.setApplicationVersion(VERSION)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    setApp(app)
    getDevice(app)

    # 已有实例就把这次启动交给它
    if handOver(action["content"]):
        sys.exit(0)

    window = MainWindow(app, action["content"])
    window.show()

    # 本机通道延后一个事件循环再监听：MainWindow 排的 singleShot（全局监听、插件）就在前面，
    # 转发来的动作可能指向插件动作，落在插件初始化之前会找不到。服务端由 cli 持有引用并接了退出清理
    from src.cli import startServer
    QTimer.singleShot(0, lambda: startServer(window.handleChannelAction))

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
