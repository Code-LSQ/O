"""CLI 子命令与通信信道的实现。

协议（端点、消息信封、端口与 token、分权规则）见文件末尾的「通信协议」段，这里不重复描述，
免得规格与实现两处漂移。

目前只绑回环地址，并用配置里的 token 校验：网页能连回环端口，但读不到 config.json 里的 token。
无法阻挡已经能在本机执行代码的攻击者，那不是它的责任。"""
import os
import sys
import json
import time
import argparse
import subprocess

from PySide6.QtCore import QObject, QUrl, QUrlQuery, QTimer, QEventLoop, QCoreApplication
from PySide6.QtWebSockets import QWebSocket, QWebSocketServer

from src.api import APP_NAME, VERSION, Interpret, app_path, config_file, env, logger, root
from src.config import DEFAULT_CONFIG, getConfig
from src.system import Terminal

PORT_SPAN = 10          # 起始端口被占用时向后试探的个数
CHANNEL_TIMEOUT = 3.0

MODE_UNSET = object()   # 区分「没有传 --mode」与「传了 --mode none」，后者表示无控制台

_app = None             # CLI 进程里为通道创建的 QCoreApplication，必须持有引用，否则会被回收
_server = None          # GUI 进程里的通道服务端，同样必须持有引用


def ensureToken():
    """读取通道 token，没有就生成并保存。

    token 跨进程共享，故先信磁盘再信内存：内存里可能是本次启动时的旧值，而另一个进程刚生成过。
    CLI 与 GUI 也可能几乎同时首启，两个进程各写一次、后写覆盖先写，故写盘后重读磁盘，并以磁盘值
    回填内存 —— 只回填、不再写盘：重写会把对方刚写的值顶掉，而且本进程以后整份保存配置时还会
    把内存里的旧值再写回去，磁盘与服务端手里已经生效的 token 就此长期不一致。"""
    config = getConfig()

    token = config.readKey("token")
    if token:
        # 同样要回填内存：内存为空而盘上有值，说明 token 是别的进程在本进程加载配置之后写的，
        # 本进程以后整份保存配置时会把磁盘上的 token 抹成空，服务端手里已生效的值就此失配
        if token != config.get("token"):
            config.set("token", token)
        return token

    config.set("token", os.urandom(16).hex())
    config.save()
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            disk = json.load(f).get("token", "")
    except Exception:
        logger.exception("重读 token 失败")
        return config.get("token", "")

    if disk and disk != config.get("token"):
        config.set("token", disk)
    return disk or config.get("token", "")


def channelPort():
    """读取通道端口，先信磁盘再信内存。

    端口由 GUI 侧顺延之后回写配置，而本进程内存里可能还是启动时的旧值；只读磁盘还不够 ——
    本进程之后整份保存配置时会把磁盘上的新值顶回旧值（cmdAdd 就会保存），于是实例实际在
    10001、配置却写回 10000，CLI 再探 10000 探不到，白起实例。故读到的值一并回填内存，
    与 ensureToken 同一套理由。只回填不落盘：本进程没有资格替实例改写它正在用的端口。"""
    config = getConfig()
    port = config.readKey("port", DEFAULT_CONFIG["port"])
    if port != config.get("port"):
        config.set("port", port)
    return port


class ActionServer(QObject):
    """本机通道的服务端：收到动作后交给 onAction 处理，并把返回的字典回给客户端"""

    def __init__(self, onAction, parent=None):
        super().__init__(parent)
        self._onAction = onAction
        self._token = ensureToken()
        self._server = None
        self.port = 0

    def start(self) -> bool:
        """监听回环地址，起始端口被占用时向后顺延；整段都占不到返回 False。

        顺延到的端口回写配置并立即落盘：配置里的 port 从此就是「实例实际在哪」，CLI 只探它这
        一个端口，将来的移动端与扩展也按它连，都不必各自扫描。回写不改身份 —— 用户还在看的
        是这个实例，只是地址变了。"""
        from PySide6.QtNetwork import QHostAddress

        base = getConfig().get("port", DEFAULT_CONFIG["port"])

        server = QWebSocketServer(APP_NAME, QWebSocketServer.SslMode.NonSecureMode, self)
        # 在 listen 之前接上：握手早于这里到达的连接会一直躺在队列里没人取走
        server.newConnection.connect(self._onConnection)
        for port in range(base, base + PORT_SPAN + 1):
            if not server.listen(QHostAddress(QHostAddress.SpecialAddress.LocalHost), port):
                continue
            self._server = server
            self.port = port
            if port != base:
                # 只改 port 一个键（见 saveKey）：整份 save() 会把别的进程这段时间写进去的键一起顶掉
                logger.info(f"起始端口 {base} 已被占用，本机通道改用 {port}，已回写配置")
                getConfig().saveKey("port", port)
            logger.info(f"监听 ws://127.0.0.1:{port}")
            return True

        logger.warning(f"监听失败，已尝试 {base}-{base + PORT_SPAN}，本机通道未启用，转发不可用")
        server.deleteLater()
        return False

    def stop(self):
        if self._server:
            self._server.close()
            self._server = None
            logger.info("本机通道已关闭")

    def _onConnection(self):
        socket = self._server.nextPendingConnection()
        if QUrlQuery(socket.request().url()).queryItemValue("token") != self._token:
            logger.warning("本机通道收到 token 无效的连接，已断开")
            socket.close()
            socket.deleteLater()
            return
        socket.textMessageReceived.connect(lambda text, s=socket: self._onMessage(s, text))
        socket.disconnected.connect(socket.deleteLater)

    def _onMessage(self, socket, text):
        try:
            action = json.loads(text)
        except ValueError:
            logger.exception(f"本机通道收到无法解析的消息: {text[:200]}")
            socket.sendTextMessage(json.dumps({"ok": False, "error": "bad_request"}, ensure_ascii=False))
            return
        try:
            result = self._onAction(action) or {"ok": True}
        except Exception as e:
            logger.exception("处理本机通道动作失败")
            result = {"ok": False, "error": "action_failed", "detail": str(e)}
        try:
            socket.sendTextMessage(json.dumps(result, ensure_ascii=False))
        except Exception:
            logger.exception("回复本机通道失败")


def _connectPort(port, token):
    """连一次指定端口，成功返回 QWebSocket；连不上或超时返回 None。

    QWebSocket 是异步 API，没有 waitForConnected 可等，只能用事件循环加计时器。
    一次尝试自成一个函数：loop 是本次调用的局部，上一轮迟到的信号只会打到已结束的循环上。"""
    socket = QWebSocket()
    loop = QEventLoop()
    state = {"connected": False}

    def onConnected():
        state["connected"] = True
        loop.quit()

    socket.connected.connect(onConnected)
    socket.errorOccurred.connect(lambda _: loop.quit())

    # 对端接受 TCP 却不回握手时没有任何错误信号，只能靠计时器脱身（回环上正常都是瞬时）
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    timer.start(200)

    socket.open(QUrl(f"ws://127.0.0.1:{port}/?token={token}"))
    loop.exec()
    timer.stop()

    if state["connected"]:
        return socket
    socket.abort()
    socket.deleteLater()
    return None


def _openChannel():
    """连上配置端口上的本程序实例，成功返回 QWebSocket；那个端口上没有本程序的实例时返回 None。

    端口空着、端口上有人监听但不是本程序（token 没过或握手走不完），两种都由 _connectPort 归一成
    None —— 对调用方来说两者没区别：这里没有可转发的实例，该去起一个。起来之后它会顺延到空闲端口
    并把配置改过来，所以配置端口被别的程序占着也不影响，无非是多等一个实例的启动。"""
    global _app

    if QCoreApplication.instance() is None:
        # QWebSocket 依赖事件循环；CLI 里没有 QApplication，QCoreApplication 就够（不加载 GUI 平台插件）
        _app = QCoreApplication([])

    return _connectPort(channelPort(), ensureToken())


def sendAction(action, socket=None, timeout=CHANNEL_TIMEOUT):
    """把动作发给已运行的实例，返回它的回执；配置的端口上没有实例时返回 None。

    socket 传了就用它（调用方已经连上，见 waitInstance），不传就按配置的端口连（见 _openChannel）。
    已连上却等不到回执时抛 TimeoutError：调用方不该在这时回退成直接执行，否则同一次调用会启动两个进程。"""
    if socket is None:
        socket = _openChannel()
        if socket is None:
            return None

    loop = QEventLoop()
    state = {"result": None}

    def onText(text):
        try:
            state["result"] = json.loads(text)
        except ValueError:
            logger.exception("本机通道回执无法解析")
            state["result"] = {"ok": False, "error": "bad_response"}
        loop.quit()

    socket.textMessageReceived.connect(onText)
    # 用可停止的 QTimer 而不是 singleShot，否则动作已返回时留着的计时器会停掉本进程后来的某个循环
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    timer.start(int(timeout * 1000))

    socket.sendTextMessage(json.dumps(action, ensure_ascii=False))
    loop.exec()
    timer.stop()

    socket.close()
    socket.deleteLater()

    if state["result"] is None:
        raise TimeoutError("本机通道未在超时内返回")
    return state["result"]


def startServer(onAction):
    """启动本机通道服务端并返回它；端口全不可用时返回 None（GUI 照常启动，只是转发不可用）

    返回值由模块的 _server 持有，调用方不必再留引用：QObject 没有父对象时只靠 Python 引用存活，
    只交给局部变量的话，出了作用域监听会连着一起被回收。退出清理也一并接在这里。"""
    global _server
    server = ActionServer(onAction)
    if not server.start():
        return None
    app = QCoreApplication.instance()
    if app:
        app.aboutToQuit.connect(server.stop)
    _server = server
    return server


def launchInstance():
    """新起一个 O 实例（GUI），本进程不等它。

    只负责把窗口拉起来，动作随后由调用方经本机通道转发 —— 与「已有实例」走同一条转发路径，
    回执与退出码才会一致。用 Popen 不等待：本进程若自己变成 GUI，就会卡在 app.exec() 里把调用方
    一起挂住。

    三个标准流必须显式断开：新实例会长期活着并持有从 CLI 继承的管道写端，调用方
    （如 Agent 的 subprocess.run(capture_output=True)）读 stdout 要等 EOF，而 EOF 得等 GUI
    退出才来 —— Windows 上超时也救不了它，超时后 run() 还会再调一次无超时的 communicate()
    收输出，于是 o run 明明已经打出了 JSON，调用方却拿到 TimeoutExpired，或没设超时的一直挂住。
    断开不影响诊断：日志走 data/app.log，不依赖标准流。"""
    ensureToken()   # 先把 token 落到盘上：新实例启动时会读它，否则两边可能各生成一个，握手对不上
    cmd = [sys.executable, app_path] if Interpret else [app_path]
    subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def waitInstance(timeout=20.0):
    """等新起的实例把本机通道监听起来，返回已连上的 socket；始终没上线返回 None

    每一轮都重读磁盘上的端口，新起实例时等它监听，时间上限为 20 秒"""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        socket = _openChannel()
        if socket:
            return socket
        time.sleep(0.2)
    return None


def emit(payload: dict, code=0) -> int:
    """把结果以 JSON 写进 stdout 并返回退出码；写不出去时返回失败码，免得调用方把空输出当成成功。

    直接写 UTF-8 字节而不是文本层：Windows 控制台默认代码页不是 UTF-8，走文本层遇中文会抛 UnicodeEncodeError。"""
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    try:
        if hasattr(sys.stdout, "buffer"):
            sys.stdout.buffer.write(text.encode("utf-8", "replace") + b"\n")
            sys.stdout.buffer.flush()
        else:
            print(text)
    except Exception:
        logger.exception("输出结果失败")
        return code or 1
    return code


def fail(error: str, detail=None, code=1) -> int:
    """失败回执。成功时不套信封、直接输出内容本体，只有失败才用 error 区分，
    调用方按退出码判断成败即可。error 是固定英文码供程序判断，detail 给人和 Agent 看。"""
    payload = {"error": error}
    if detail is not None:
        payload["detail"] = detail
    return emit(payload, code)


def resolveMode(value):
    """把 --mode 的取值映射成配置里的运行方式，none 表示无控制台；取值非法时抛 ValueError"""
    if value.lower() == "none":
        return None
    for key in Terminal:
        if key.lower() == value.lower():
            return key
    raise ValueError(value)


def cmdList(argv) -> int:
    """list：列出分组、工具与运行时配置

    只透传配置原貌、滤掉空值，不做展开或改名 —— 「实际会用什么」（cwd 补成目标所在目录、
    mode 按类型回退、相对路径转绝对）是 run 的事。
    --group 只筛 groups 一项，版本、路径、运行时照常返回，调用方不必为它分两套解析。"""

    parser = argparse.ArgumentParser(prog=f"{APP_NAME} list", description="List groups, tools and runtime settings")
    parser.add_argument("--group", default="", help="Only list the given group, all groups when omitted")
    args = parser.parse_args(argv)

    config = getConfig()
    all_tools = config.get("Launch.tools", {}) or {}
    # 分组写错时给出可选值，比回一个空列表更容易让人和 Agent 纠正
    if args.group and args.group not in all_tools:
        return fail("group_not_found", {"group": args.group, "available": list(all_tools)})

    groups = []
    for group_name, group_tools in all_tools.items():
        if args.group and group_name != args.group:
            continue
        groups.append({
            "name": group_name,
            "tools": [{k: v for k, v in tool.items() if v} for tool in (group_tools or [])],
        })
    return emit({
        "version": VERSION,
        "app_path": str(app_path),
        "root": str(root),
        "active_group": config.get("Launch.active_group", ""),
        "path_mode": config.get("Launch.path_mode", "absolute"),
        "groups": groups,
        "runtime": config.get("Launch.Runtime", {}),
    })


def cmdAdd(argv) -> int:
    """add：添加启动器工具，字段与 EditTool.getData() 保持一致。

    类型清单取自 TYPES，不另抄一份 —— 将来新增类型时 CLI 不必跟着改。"""

    from src.main import TYPES, checkNameList, findTool

    def aliasType(value: str) -> str:
        """把 --type 的取值归一到配置里的类型名，认不出来抛 ArgumentTypeError 交给 argparse 报错。

        别名只作用于命令行入口（file→文件、url→网址，Python/Java 大小写不敏感）：配置里存的始终是
        TYPES 里的键，下游的比较与写入都不用动。预设由界面添加，命令行不提供。"""
        lower = value.lower()
        for name in TYPES:
            if name != "预设" and lower == name.lower():
                return name
        alias = {"file": "文件", "url": "网址"}
        if lower in alias:
            return alias[lower]
        raise argparse.ArgumentTypeError(f"unknown type: {value}, use file / url / Python / Java")

    parser = argparse.ArgumentParser(prog=f"{APP_NAME} add", description="Add a launcher tool")
    parser.add_argument("--name", required=True, help="Display name, unique across all groups")
    parser.add_argument("--type", default="文件", type=aliasType, help="Tool type: file / url / Python / Java")
    parser.add_argument("--path", default="", help="Target path, use --url when --type is url")
    parser.add_argument("--url", default="", help="URL, used only when --type is url")
    parser.add_argument("--args", nargs="*", default=None, help="Launch arguments, several values are joined into one fragment")
    parser.add_argument("--cwd", default="", help="Working directory, defaults to the target's folder")
    parser.add_argument("--group", default="", help="Group name, defaults to the active group")
    parser.add_argument("--hotkey", default="", help="Hotkey")
    parser.add_argument("--note", default="", help="Note")
    parser.add_argument("--icon", default="", help="Icon path")
    parser.add_argument("--mode", default=MODE_UNSET, help="Run mode: none / cmd / PowerShell")
    parser.add_argument("--admin", action="store_true", help="Run as administrator")
    parser.add_argument("--service", default="", help="Attached services, separated by |")
    parser.add_argument("--process", default="", help="Attached processes, separated by |")
    args = parser.parse_args(argv)

    config = getConfig()
    tools = config.get("Launch.tools", {})
    group = args.group or config.get("Launch.active_group", "") or "o"
    target = args.url if args.type == "网址" else args.path
    if not target:
        return fail("target_required", "--url is empty" if args.type == "网址" else "--path is empty")

    # 名称跨分组全局唯一：findTool 与 run 都只按名称定位，同名的另一个将无法指定
    conflict_group, conflict = findTool(tools, args.name)
    if conflict:
        return fail("name_exists", {"group": conflict_group, "name": args.name})

    # 与 EditTool 共用校验：进程名缺扩展名会写进永远匹配不上的配置；白名单是系统关键项，
    # GUI 要用户确认，CLI 没有交互只能拒绝
    field, msg, blocked = checkNameList(args.service, args.process)
    if field:
        return fail("bad_service" if field == "服务" else "bad_process", msg)
    if blocked:
        detail = "; ".join(field + ": " + ", ".join(hit) for field, hit in blocked)
        return fail("system_protected", "系统关键项目，请在图形界面中确认后再添加: " + detail)

    data = {
        "type": args.type,
        "name": args.name,
        "cwd": args.cwd,
        "args": " ".join(args.args or []),
        "service": args.service,
        "process": args.process,
        "note": args.note,
        "hotkey": args.hotkey,
        "icon": args.icon,
        "run_as_admin": args.admin,
    }
    data["url" if args.type == "网址" else "path"] = target
    data = {k: v for k, v in data.items() if v}     # 空值不写入，与 EditTool.getData() 一致

    if args.mode is not MODE_UNSET:
        try:
            mode = resolveMode(args.mode)
        except ValueError:
            return fail("bad_mode", args.mode)
        # 与类型默认值相同就不写，保持配置精简 —— run 时同样会回退到这个值
        if mode != ("cmd" if args.type in ("Python", "Java") else None):
            data["mode"] = mode

    tools.setdefault(group, []).append(data)
    # 整份保存会把内存里的端口一并写回去，若实例刚顺延过并回写了配置，这一下就把它的新端口顶成旧值，
    # 下次 CLI 去探旧端口就探不到了；保存前先把端口从磁盘同步进内存（见 channelPort）
    channelPort()
    config.save()

    # 已有实例时让它重读磁盘：否则它退出保存配置时会把刚加的工具覆盖掉。
    # 要看回执里的 ok，不能只判「有没有回执」：字典非空恒为真，实例回了 reload_failed
    # 也会被当成刷新成功，而那时它内存里还是旧工具，退出保存仍会把刚加的工具覆盖掉
    reloaded = False
    try:
        result = sendAction({"name": "reload"})
        reloaded = bool(result and result.get("ok"))
    except TimeoutError:
        logger.exception("通知实例刷新超时")
    return emit({"group": group, "tool": data, "reloaded": reloaded})


def cmdRun(argv) -> int:
    """run：运行指定名称的工具。有实例就交给它，没有就先起一个实例再交给它。

    两种情况都经本机通道转发，回执与退出码才一致 —— 执行始终发生在 GUI 进程里，本进程只做搬运，
    只有一条执行路径，附属服务/进程、插件动作、GUI 预设才不会时有时无。这里的 findTool
    只用来把「工具不存在」当场报清楚。"""

    parser = argparse.ArgumentParser(prog=f"{APP_NAME} run", description="Run a tool by name")
    parser.add_argument("name", help="Tool display name")
    parser.add_argument("--args", nargs="*", default=None, help="Override the configured arguments, several values are joined; nothing after it means no arguments")
    parser.add_argument("--mode", default=MODE_UNSET, help="Override the run mode: none / cmd / PowerShell")
    args = parser.parse_args(argv)

    from src.main import findTool

    tools = getConfig().get("Launch.tools", {}) or {}
    group, found = findTool(tools, args.name)
    if not found:
        names = [t.get("name", "") for group_tools in tools.values() for t in (group_tools or [])]
        return fail("tool_not_found", {"name": args.name, "available": names})

    action = {"name": "run", "content": {"tool": args.name}}
    if args.args is not None:
        action["content"]["args"] = " ".join(args.args)
    if args.mode is not MODE_UNSET:
        try:
            action["content"]["mode"] = resolveMode(args.mode)
        except ValueError:
            return fail("bad_mode", args.mode)

    try:
        result = sendAction(action)
    except TimeoutError:
        return fail("channel_timeout", "connected to an instance but got no reply in time")

    if result is None:
        # 没有实例：先起一个，等它的通道监听起来再转发。等不到就如实报错 —— 通道没起来，
        # 动作必然没执行，不能像「派活给子进程」那样回一个乐观的成功
        try:
            launchInstance()
            socket = waitInstance()
        except Exception as e:
            logger.exception("启动新实例失败")
            return fail("launch_failed", str(e))
        if socket is None:
            return fail("instance_not_ready", "started an instance but its channel never came up")
        try:
            result = sendAction(action, socket)
        except TimeoutError:
            return fail("channel_timeout", "connected to an instance but got no reply in time")

    if not result.get("ok"):
        return fail(result.get("error", "instance_failed"), result.get("detail"))
    logger.info(f"已交给实例运行: {args.name}")
    return emit({"group": group, **result.get("data", {})})


def cmdExec(argv) -> int:
    """exec：在内嵌解释器中执行脚本后退出，参数原样透传给脚本。

    四个子命令里只有它在本进程里跑代码，故只有它需要注入环境变量 —— 其余三个都把活交给 GUI 进程，
    那边 setApp() 已经注入过；少了这步，脚本里再起的子进程就看不到配置的 Python/Java 与自定义环境变量。"""
    if not argv:
        return fail("script_required", "exec requires a script path")
    env.loadConfig(getConfig())
    env.inject()
    from src.main import execPython
    return execPython(argv[0], argv[1:])


# 不显示窗口的子命令表：o.py 用它决定哪些命令行交给 CLI，runCli 也照它分发
COMMANDS = {
    "exec": cmdExec,
    "list": cmdList,
    "add": cmdAdd,
    "run": cmdRun,
}


def runCli(argv) -> int:
    """CLI 入口：按子命令分发并返回退出码。

    open（含裸路径）由 o.py 走 GUI 路径，不进这里；exec 虽然是老功能，但它同样不显示窗口，
    所以一并归到这条路径上。"""
    handler = COMMANDS.get(argv[0])
    if not handler:
        return fail("unknown_command", argv[0])
    try:
        return handler(argv[1:])
    except Exception as e:
        # SystemExit 不属于 Exception，argparse 的 -h 与参数错误会带着自己的退出码直接穿过去
        logger.exception("CLI 执行失败")
        return fail("cli_failed", str(e))


"""
通信协议

此部分处于计划中

契约与实现分家
    写在本文件（实现文件）里，不再另存 src/api.py：api.py 是所有模块共用的底层（config.py 也
    import 它），它必须与具体功能无关，且拿不到 getConfig()，实现放那里还会造成循环导入。
    规格与实现同处一文件，改动时才不会两处漂移。

    与 plugin/https.py 的关系：两者独立并存，职责不同 —— https.py 是网页版局域网文件浏览/上传，
    面向浏览器直接访问；通道面向移动端与浏览器扩展等自有客户端。后续如需统一，可考虑让
    https.py 复用通道的部分能力，但当前不耦合。


混合通信架构
HTTP REST（/api/http/*）：文件列表、上传、下载、鉴权、消息发送。大文件传输成熟，支持 Content-Length、进度、断点续传。
WebSocket（QWebSocketServer）：服务器主动推送，如消息到达、文件接收通知。QWebSocketServer 跑在 Qt 事件循环，收到消息由信号槽直接触发弹窗，无需线程桥接，对应 AGENTS.md 中的文本/文件弹窗功能。

合并：不各起一个监听。动作转发（CLI）与程序化 API（移动端/扩展）共用一个 QWebSocketServer、
一份 token、一个端口，绑通配地址。两端威胁等级不同，故按对端地址分权：只有回环对端挂
run/reload/open/show（等价于在本机执行代码），其余对端只给消息与文件。分权只能靠连接建立时的
一次判断，是本设计的薄弱处，漏一处就等于把执行能力摆到局域网上，故判断集中在一处，不要散进各分支。
回环对端发的是动作信封 {name, content}，与 o.py parseArgs() 的产物同构，可直接 JSON 序列化。
不设启停开关：通道随进程常驻（CLI 依赖它，关掉转发就没法用），所以「没在服务」不能靠关端口表达。
同一条连接上两种消息混用：动作信封带 name，事件推送带 type，分派规则待定。
端口与 token 用配置顶层的 port / token，不另设 Net.* 分组。



HTTP REST （/api/*）
    统一响应格式：
        {"ok": true, "code": 0, "message": "", "data": ...}
    ok 为 false 时 data 省略，message 为错误说明。HTTP 状态码与语义一致
    （200 成功、400 参数错误、401 未鉴权、404 不存在、500 服务端错误）。

    1. GET /api/info      免鉴权，服务信息
        响应 data：{"name": "OpenList", "version": "1.0.0",
                    "requires_auth": false, "capabilities": ["message", "file"]}
        用于客户端发现握手，判断是否需要 token。

    2. POST /api/auth/verify   验证 token
        请求体：{"token": "xxx"}
        响应 data：{"valid": true}，valid 为 false 表示 token 无效。
        也可将 token 放入 Authorization: Bearer 头，此时请求体可省略。

    3. POST /api/message/send   发送文本消息
        请求体：{"text": "你好", "device": "手机"}
        响应 data：{"id": 100}，服务端收到后弹窗显示消息。

    4. GET /api/message/list?since=0&limit=20   轮询拉取消息
        响应 data：{"messages": [{"id": 1, "text": "你好", "device": "手机",
                                  "time": "2026-08-16 10:00:00"}], "has_more": false}
        客户端以 since 记录已读消息 id，实现增量拉取。客户端主动模型下用于
        消息同步，避免服务器反向推送依赖。

    5. GET /api/file/list?path=/   列出接收目录内容
        响应 data：{"path": "/", "items": [{"name": "a.txt", "is_dir": false,
                    "size": 1024, "mtime": 1723694400}]}
        path 为相对接收根目录的路径，不传默认根目录。目录操作对根目录外
        不开放，防止越权访问任意磁盘路径。

    6. POST /api/file/upload   流式上传文件
        请求头：File-Path（相对接收根目录的目标路径，URL 编码）、
                SHA-256（客户端计算的文件 SHA-256，十六进制）、
                Content-Length、Content-Type: application/octet-stream
        请求体：文件二进制流
        响应 data：{"name": "a.txt", "size": 1024}
        服务端边收边算 SHA-256，收完与请求头比对，不一致删除文件并返回 400。

    7. GET /api/file/download?path=/a.txt   下载文件
        响应头：X-Checksum-SHA256（服务端计算的文件 SHA-256）、
                Content-Disposition、Content-Length
        响应体：文件二进制流。客户端可校验 SHA-256 确认完整性。

    8. POST /api/file/mkdir   创建目录
        请求体：{"path": "/newdir"}

    9. POST /api/file/remove   删除文件或目录
        请求体：{"path": "/a.txt"}，删除目录要求目录为空，避免误删。


WebSocket
    1. 连接：ws://ip:<port>/?token=xxx   握手时携带 token 鉴权，端口即配置顶层的 port（与回环同一条监听）。
       起始端口被占用时实例会顺延并回写配置，故客户端每次连接前重读一次 port，不要缓存。
    2. 服务器 -> 客户端事件（JSON 文本帧）：
        {"type": "message", "id": 100, "text": "你好", "device": "手机"}
        {"type": "file", "name": "a.txt", "size": 1024, "path": "/a.txt"}
        {"type": "status", "status": "ok"}
    3. 客户端 -> 服务器：心跳保活。客户端定期发送 {"type": "ping"}，
       服务端回 {"type": "pong"}。客户端断线后需重连。
    4. 浏览器扩展（Chrome MV3）使用 WebSocket 需三件套：
        - keepalive：每 20 秒发送 ping 保持 SW 活跃（Chrome 116+ 支持）
        - 自动重连：指数退避（1s 起，15s 封顶）
        - alarms 兜底：chrome.alarms 周期检查连接健康，SW 被终止后唤醒重连
        manifest 需声明 minimum_chrome_version 116 与 alarms 权限。

鉴权
    token 取配置顶层的 token，回环与局域网两端共用。实例启动时生成并落盘，之后一直复用
    （单实例转发认的也是它），故不存在「没 token」的运行状态：鉴权恒开，不能写成「空 token =
    免鉴权」—— 同一个监听上挂着 run，那等于把执行能力开放给局域网。
    移动端/扩展接入前，用户先拿到这个 token 填进去。
    1. HTTP 请求携带 Authorization: Bearer <token> 头。
    2. WebSocket 连接在 URL query 携带 token。
    3. GET /api/info 免鉴权，用于发现握手，返回 requires_auth 供客户端判断。

SHA-256 校验
    上传与下载均支持 SHA-256 完整性校验（对应 AGENTS.md 计划第 13 条）：
    上传时客户端先算一次随请求发送，服务端边收边算再比对一次，两端结果
    必须一致，不一致视为传输损坏删除文件返回错误。下载时服务端计算并放入
    响应头，客户端可选校验。



客户端实现要点
    1. 移动端（ArkTS）：
       - HTTP 使用 @ohos.net.http 模块。
       - WebSocket 使用 @ohos.net.webSocket 模块。
    2. 浏览器扩展（TypeScript, MV3）：
       - 请求使用 fetch + manifest.json 的 host_permissions 绕过 CORS。
       - 实时推送使用 WebSocket，需 keepalive + 自动重连 + alarms 三件套。
    客户端具体代码在对应项目（移动端、浏览器扩展）中实现，本段仅约束契约。
"""
