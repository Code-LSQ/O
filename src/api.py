from PySide6.QtWebSockets import QWebSocket, QWebSocketServer, QWebSocketProtocol


"""
此模块处于计划中


定义程序的桌面端与移动端、桌面端与浏览器扩展、移动端与浏览器扩展之间的通信接口协议。需要能够发送、接收消息、接受文件同步。

与 plugin/https.py 的关系
    两者独立并存，职责不同：
    - plugin/https.py：网页版局域网文件浏览/上传，面向浏览器直接访问。
    - 本模块 src/api.py：程序化 API（HTTP REST + WebSocket），
      面向移动端与浏览器扩展等自有客户端。
    后续如需统一，可考虑让 https.py 复用本模块的部分能力，但当前不耦合。


混合通信架构
HTTP REST（/api/http/*）：文件列表、上传、下载、鉴权、消息发送。大文件传输成熟，支持 Content-Length、进度、断点续传。
WebSocket（QWebSocketServer）：服务器主动推送，如消息到达、文件接收通知。QWebSocketServer 跑在 Qt 事件循环，收到消息由信号槽直接触发弹窗，无需线程桥接，对应 AGENTS.md 中的文本/文件弹窗功能。
端口规划：Net.port（HTTP，默认 8000）、Net.ws_port（WebSocket，默认8001）



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
    1. 连接：ws://ip:ws_port/?token=xxx   握手时携带 token 鉴权。
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
    可选 Token。Net.token 为空表示不启用鉴权，此时所有请求免 token。
    启用后：
    1. HTTP 请求携带 Authorization: Bearer <token> 头。
    2. WebSocket 连接在 URL query 携带 token。
    3. GET /api/info 始终免鉴权，返回 requires_auth 供客户端判断。
    默认关闭，面向可信局域网。

SHA-256 校验
    上传与下载均支持 SHA-256 完整性校验（对应 AGENTS.md 计划第 13 条）：
    上传时客户端先算一次随请求发送，服务端边收边算再比对一次，两端结果
    必须一致，不一致视为传输损坏删除文件返回错误。下载时服务端计算并放入
    响应头，客户端可选校验。

配置（全局配置顶层，保持扁平）
    "Net": {
        "enabled": false,        # 是否启用通信服务
        "port": 8000,            # HTTP 端口
        "ws_port": 8001,         # WebSocket 端口
        "token": "",             # 访问 token，空表示不鉴权
        "receive_folder": ""     # 接收文件夹（手机同步落盘根目录）
    }
    由 getConfig() 读写，配置键路径为 Net.xxx。



客户端实现要点
    1. 移动端（ArkTS）：
       - HTTP 使用 @ohos.net.http 模块。
       - WebSocket 使用 @ohos.net.webSocket 模块。
    2. 浏览器扩展（TypeScript, MV3）：
       - 请求使用 fetch + manifest.json 的 host_permissions 绕过 CORS。
       - 实时推送使用 WebSocket，需 keepalive + 自动重连 + alarms 三件套。
    客户端具体代码在对应项目（移动端、浏览器扩展）中实现，本文件仅约束契约。
"""


