import os
import socket
import time
import threading
import webbrowser
from urllib import parse
from http.server import HTTPServer, SimpleHTTPRequestHandler
from socketserver import ThreadingMixIn

from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QLineEdit, QTextEdit, QWidget, QFrame, QCheckBox
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction

from src.util import logger, getFilePath, messageBox
from src.plugin import PluginBase

PluginLib = ["http.server"]

MAX_RECV_DATA = 1000
MAX_MULTIPART_BYTES = 64 * 1024 * 1024
MAX_STREAM_BYTES = 16 * 1024 * 1024 * 1024

def getIP():
    """获取本机局域网 IP 地址"""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        except Exception:
            logger.exception("检测网络连接失败")
            return "127.0.0.1"

class HttpsPlugin(PluginBase):

    version = "1.0.0"
    description = "局域网通信"

    def initialize(self):
        if not super().initialize():
            return
        self.http_tool = HTTPTool(main=self.main, settings=self.settings, plugin=self)

    def getAction(self):
        action = QAction(self.description, self.main)
        action.triggered.connect(self.showDialog)
        return action

    def showDialog(self):
        self.initialize()
        self.http_tool.showDialog()

    def cleanup(self):
        if not self._initialized:
            return
        self.saveConfig()
        self.http_tool.stopServer()

class HTTPTool:
    def __init__(self, main=None, settings=None, plugin=None):
        self.main = main
        self.plugin = plugin
        self.settings = settings if settings else {
            "port": 8000,
            "shared_folder": "",
            "upload_folder": "",
            "background_run": False
        }
        self.local_ip = getIP()
        self.server_thread = None
        self.server = None
        self.running = False
        self._data_lock = threading.Lock()
        self._server_shutdown = threading.Event()

    def showDialog(self):
        dialog = HTTPDialog(self.main, self)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.setModal(False)
        dialog.show()

    def stopServer(self):
        """停止 HTTP 服务器，清理线程和端口"""
        if not self.running:
            return

        self._server_shutdown.set()
        self.running = False

        if self.server_thread and self.server_thread.is_alive():
            self.server_thread.join(timeout=2)

        if self.server:
            try:
                self.server.server_close()
            except Exception:
                logger.exception("关闭HTTP服务器失败")
            self.server = None

    def startServer(self):
        """启动 HTTP 服务器（新线程）"""
        if self.running and self.server:
            return

        self.shared_folder = os.path.abspath(self.settings.get("shared_folder", "")) or ""
        if self.shared_folder and not os.path.exists(self.shared_folder):
            os.makedirs(self.shared_folder, exist_ok=True)

        upload_folder = self.settings.get("upload_folder", "")
        self.upload_folder = os.path.abspath(upload_folder) if upload_folder else ""
        if self.upload_folder and not os.path.exists(self.upload_folder):
            os.makedirs(self.upload_folder, exist_ok=True)

        self._server_shutdown.clear()
        self.running = True
        self.server_thread = threading.Thread(target=self._runServer, daemon=True)
        self.server_thread.start()

    def _runServer(self):
        """服务器线程入口：创建 ThreadedHTTPServer 并启动"""
        shared_folder = self.shared_folder
        shared_folder = os.path.abspath(shared_folder)
        upload_folder = os.path.abspath(self.upload_folder) if self.upload_folder else ""
        port = self.settings.get("port", 8000)
        http_tool = self
        data_lock = self._data_lock
        shutdown_event = self._server_shutdown

        class CustomHandler(SimpleHTTPRequestHandler):
            """自定义 HTTP 请求处理器：文件浏览、上传、流式上传、文本收发"""
            def __init__(self, *args, **kwargs):
                try:
                    super().__init__(*args, directory=shared_folder, **kwargs)
                except Exception:
                    logger.exception("处理器初始化失败")
                    raise

            def setup(self):
                try:
                    super().setup()
                except Exception:
                    logger.exception("处理器安装失败")

            def handle(self):
                try:
                    super().handle()
                except Exception:
                    logger.exception("处理器handle失败")

            def finish(self):
                try:
                    super().finish()
                except Exception:
                    logger.exception("处理器完成失败")

            def log_message(self, format, *args):
                # 覆盖默认实现，改为写入项目日志。默认写入 sys.stderr，无控制台环境（打包 exe / pythonw）下 stderr 为 None 会崩溃
                logger.info("%s - - [%s] %s",
                            self.address_string(), self.log_date_time_string(), format % args)

            def do_GET(self):
                path = parse.unquote(self.path)
                if path == "/":
                    self._sendCustomIndex()
                else:
                    try:
                        self._serveFileDownload()
                    except ConnectionResetError:
                        pass
                    except Exception:
                        logger.exception(f"GET请求失败 {path}")
                        self.send_error(500)

            def _serveFileDownload(self):
                translated_path = self.translate_path(self.path)
                if os.path.isdir(translated_path):
                    super().do_GET()
                    return
                if not os.path.isfile(translated_path):
                    self.send_error(404)
                    return
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    filename = os.path.basename(translated_path)
                    ascii_fallback = filename.encode("ascii", errors="replace").decode("ascii")
                    url_encoded = parse.quote(filename, safe='')
                    disposition = f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{url_encoded}'
                    self.send_header("Content-Disposition", disposition)
                    file_size = os.path.getsize(translated_path)
                    self.send_header("Content-Length", str(file_size))
                    self.end_headers()
                    with open(translated_path, "rb") as f:
                        self.copyfile(f, self.wfile)
                except ConnectionResetError:
                    raise
                except Exception:
                    logger.exception(f"文件下载失败 {parse.unquote(self.path)}")
                    self.send_error(500)

            def do_POST(self):
                if self.path == "/send_data":
                    try:
                        content_length = int(self.headers.get('Content-Length', 0))
                        body = self.rfile.read(content_length).decode("utf-8")
                        self._handleSendData(body)
                    except Exception:
                        logger.exception("处理send_data请求失败")
                        self.send_error(500)
                elif self.path == "/upload":
                    self._handleUpload()
                else:
                    self.send_error(404)

            @staticmethod
            def _parseBoundary(content_type: str) -> str:
                """从 Content-Type 中提取 boundary"""
                if 'boundary=' not in content_type:
                    return ""
                boundary = content_type.split('boundary=')[1].strip()
                if boundary.startswith('"') and boundary.endswith('"'):
                    boundary = boundary[1:-1]
                return boundary

            @staticmethod
            def _parseMultipartFile(part: bytes, boundary: str) -> dict:
                """解析单个 multipart part，提取 filename + file_data"""
                if b'Content-Disposition: form-data' not in part or b'filename="' not in part:
                    return None
                fn_start = part.find(b'filename="') + 10
                fn_end = part.find(b'"', fn_start)
                if fn_end <= fn_start:
                    return None
                filename = part[fn_start:fn_end].decode("utf-8", errors="replace")
                fs = part.find(b'\r\n\r\n')
                if fs <= 0:
                    return None
                file_data = part[fs + 4:]
                bp = b'--' + (boundary.encode() if isinstance(boundary, str) else boundary)
                if bp in file_data:
                    pos = file_data.rfind(bp)
                    if pos > 0:
                        file_data = file_data[:pos]
                        if file_data.endswith(b'\r\n'):
                            file_data = file_data[:-2]
                if not filename or not file_data:
                    return None
                return {"filename": filename, "data": file_data}

            @staticmethod
            def _saveUploadedFile(filename: str, file_data: bytes, target_folder: str) -> str:
                """保存上传文件，返回文件名或 None"""
                safe_name = os.path.normpath(os.path.basename(filename)).replace('\\', '/')
                if safe_name in ('.', '..', ''):
                    return None
                base_dir = os.path.abspath(target_folder)
                filepath = os.path.abspath(os.path.join(base_dir, safe_name))
                if not filepath.startswith(base_dir + os.sep) and filepath != base_dir:
                    return None
                try:
                    with open(filepath, 'wb') as f:
                        f.write(file_data)
                    return safe_name
                except Exception:
                    logger.exception(f"写入文件失败 {safe_name}")
                    return None

            @staticmethod
            def _appendReceivedData(http_tool, data_lock, key: str, val: str):
                if key != "data":
                    return
                data = parse.unquote_plus(val)
                if not data:
                    return
                with data_lock:
                    if not hasattr(http_tool, 'received_data'):
                        http_tool.received_data = []
                    http_tool.received_data.append(data)
                    if len(http_tool.received_data) > MAX_RECV_DATA:
                        http_tool.received_data.pop(0)

            def _handleUpload(self):
                content_type = self.headers.get('Content-Type', '')
                if content_type == 'application/octet-stream':
                    self._handleUploadStream()
                    return

                if 'multipart/form-data' not in content_type:
                    self.send_error(400)
                    return

                content_length = int(self.headers.get('Content-Length', 0))
                target_folder = upload_folder if upload_folder else self.directory
                boundary = self._parseBoundary(content_type)
                if not boundary:
                    self.send_error(400)
                    return

                try:
                    data = self.rfile.read(content_length)
                except Exception:
                    logger.exception("读取上传数据失败")
                    self.send_error(400)
                    return

                uploaded = []
                for part in data.split(('--' + boundary).encode()):
                    parsed = self._parseMultipartFile(part, boundary)
                    if parsed is None:
                        continue
                    saved = self._saveUploadedFile(parsed["filename"], parsed["data"], target_folder)
                    if saved:
                        uploaded.append(saved)

                if not uploaded:
                    self.send_error(400)
                    return

                with data_lock:
                    if not hasattr(http_tool, 'received_data'):
                        http_tool.received_data = []
                    for f in uploaded:
                        msg = f"上传文件 {f} 到 {target_folder}"
                        http_tool.received_data.append(msg)
                        logger.info(msg)
                    if len(http_tool.received_data) > MAX_RECV_DATA:
                        http_tool.received_data.pop(0)

                self.send_response(200)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.send_header('Content-Length', '2')
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(b'OK')
                self.close_connection = True
                logger.info("form 连接关闭")
                self.wfile.flush()

            def _handleUploadStream(self):
                target_folder = upload_folder if upload_folder else self.directory

                content_length = self.headers.get('Content-Length')
                size = 0
                if content_length:
                    try:
                        size = int(content_length)
                        if size > MAX_STREAM_BYTES:
                            self.send_error(413)
                            return
                    except ValueError:
                        logger.exception("Content-Length 解析失败")

                content_disposition = self.headers.get('Content-Disposition', '')
                filename = None
                if 'filename=' in content_disposition:
                    parts = content_disposition.split('filename=')
                    if len(parts) > 1:
                        filename = parts[1].strip().strip('"').strip("'")
                        filename = parse.unquote(filename)

                if not filename:
                    filename = f"upload_{int(time.time())}.bin"

                safe_name = os.path.basename(filename).replace('\\', '/')
                if safe_name in ('.', '..', ''):
                    self.send_error(400)
                    return

                base_dir = os.path.abspath(target_folder)
                filepath = os.path.abspath(os.path.join(base_dir, safe_name))
                if os.path.normpath(filepath) != filepath or not filepath.startswith(base_dir + os.sep):
                    self.send_error(403)
                    return

                try:
                    with open(filepath, 'wb') as f:
                        remaining = size
                        while remaining > 0:
                            chunk_size = min(1024 * 1024, remaining)
                            chunk = self.rfile.read(chunk_size)
                            if not chunk:
                                break
                            f.write(chunk)
                            remaining -= len(chunk)
                except Exception:
                    logger.exception("流式上传失败")
                    self.send_error(500)
                    return

                with data_lock:
                    if not hasattr(http_tool, 'received_data'):
                        http_tool.received_data = []
                    msg = f"流式上传文件 {safe_name} 到 {target_folder}"
                    http_tool.received_data.append(msg)
                    logger.info(msg)
                    if len(http_tool.received_data) > MAX_RECV_DATA:
                        http_tool.received_data.pop(0)

                # 返回纯文本 "OK" 并强制关闭连接
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.send_header('Content-Length', '2')
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(b'OK')
                self.close_connection = True
                logger.info("stream 连接关闭")
                self.wfile.flush()

            def _sendCustomIndex(self):
                items = []
                try:
                    for item in os.listdir(self.directory):
                        full_path = os.path.join(self.directory, item)
                        try:
                            is_dir = os.path.isdir(full_path)
                            size = "" if is_dir else f"{os.path.getsize(full_path) / 1024:.1f} KB"
                            items.append({"name": item, "is_dir": is_dir, "size": size})
                        except Exception as e:
                            logger.info(f"获取文件信息失败 {item}: {e}")
                            continue
                except Exception:
                    logger.exception("读取目录失败")

                html = '''<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
    <title>局域网通信</title>
    <style>
        * { box-sizing: border-box; }
        body { font-family: Arial, sans-serif; margin: 0; padding: 10px; background: #f5f5f5; }
        .container { max-width: 900px; margin: 0 auto; background: white; padding: 15px; border-radius: 8px; }
        h1 { color: #333; text-align: center; font-size: 1.5rem; margin: 10px 0; }
        h3 { color: #555; font-size: 1.1rem; margin: 10px 0; }
        .section { margin: 15px 0; padding: 12px; background: #f9f9f9; border-radius: 4px; }
        textarea { width: 100%; height: 80px; padding: 10px; border: 1px solid #ddd; border-radius: 4px; font-size: 16px; }
        button { background: white; color: black; padding: 10px 20px; border: 1px solid #999; border-radius: 4px; cursor: pointer; font-size: 16px; }
        button:hover { background: #eee; }
        .file-item { padding: 8px; margin: 5px 0; background: white; border: 1px solid #ddd; border-radius: 4px; word-wrap: break-word; }
        .file-item a { text-decoration: none; color: #2196F3; }
        .dir { font-weight: bold; color: #FF9800; }
        #progressInfo { margin-top: 10px; color: #2196F3; word-wrap: break-word; }
        @media (max-width: 480px) {
            body { padding: 5px; }
            .container { padding: 10px; }
            h1 { font-size: 1.3rem; }
            h3 { font-size: 1rem; }
            .section { padding: 10px; margin: 10px 0; }
            textarea { height: 100px; font-size: 14px; }
            button { padding: 8px 16px; font-size: 14px; width: 100%; }
            .file-item { padding: 10px; font-size: 14px; }
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>局域网通信</h1>

        <div class="section">
            <h3>发送数据</h3>
            <iframe name="send_data_frame" style="display:none"></iframe>
            <form method="post" action="/send_data" target="send_data_frame" onsubmit="setTimeout(() => location.reload(), 100)">
                <textarea name="data" placeholder="输入要发送的数据..."></textarea>
                <br><button type="submit">发送数据</button>
            </form>
        </div>

        <div class="section">
            <h3>接收数据</h3>
            <div>
'''
                with data_lock:
                    received = http_tool.received_data[-10:] if hasattr(http_tool, 'received_data') else []
                    for item in received:
                        html += f'<div class="file-item">{item}</div>'
                    if not received:
                        html += '<div class="file-item">暂无数据</div>'
                html += '''
            </div>
            <button onclick="location.reload()">刷新</button>
        </div>

        <div class="section">
            <h3>上传文件</h3>
            <input type="file" id="fileInput" multiple onchange="uploadFiles()">
            <div id="progressInfo"></div>
        </div>

        <div class="section">
            <h3>共享文件夹</h3>
            <div>
'''
                for item in items:
                    if item["is_dir"]:
                        href = f"/{parse.quote(item['name'])}/"
                        html += f'<div class="file-item"><a href="{href}" class="dir">{item["name"]}/</a></div>'
                    else:
                        href = f"/{parse.quote(item['name'])}"
                        html += f'<div class="file-item"><a href="{href}">{item["name"]}</a> ({item["size"]})</div>'
                if not items:
                    html += '<div class="file-item">共享文件夹为空</div>'

                html += '''
            </div>
        </div>
    </div>

    <script>
        const MAX_MULTIPART_MB = 64;
        const MAX_STREAM_GB = 16;

        async function uploadFiles() {
            const fileInput = document.getElementById('fileInput');
            const files = fileInput.files;
            if (files.length === 0) return;
            const progressDiv = document.getElementById('progressInfo');
            try {
                for (let i = 0; i < files.length; i++) {
                    const file = files[i];
                    const sizeMB = file.size / (1024 * 1024);
                    const sizeGB = file.size / (1024 * 1024 * 1024);
                    if (sizeGB > MAX_STREAM_GB) {
                        alert(`${file.name} 超过 16GB，无法上传`);
                        continue;
                    }
                    if (sizeMB < MAX_MULTIPART_MB) {
                        await uploadViaMultipart(file);
                    } else {
                        await uploadViaStream(file);
                    }
                }
                location.reload();
            } catch (err) {
                alert('上传失败: ' + err.message);
            } finally {
                fileInput.value = '';
            }
        }

        function uploadViaMultipart(file) {
            return new Promise((resolve, reject) => {
                const formData = new FormData();
                formData.append('file', file);
                const xhr = new XMLHttpRequest();
                xhr.open('POST', '/upload', true);
                xhr.onload = () => {
                    if (xhr.status === 200 && xhr.responseText === "OK") {
                        resolve();
                    } else {
                        reject(new Error('服务器返回错误: ' + xhr.status));
                    }
                };
                xhr.onerror = () => reject(new Error('网络错误'));
                xhr.send(formData);
            });
        }

        function uploadViaStream(file) {
            return new Promise((resolve, reject) => {
                const xhr = new XMLHttpRequest();
                xhr.open('POST', '/upload', true);
                xhr.setRequestHeader('Content-Disposition', `attachment; filename="${encodeURIComponent(file.name)}"`);
                xhr.setRequestHeader('Content-Type', 'application/octet-stream');
                const progressDiv = document.getElementById('progressInfo');
                xhr.upload.onprogress = (e) => {
                    if (e.lengthComputable) {
                        const percent = (e.loaded / e.total * 100).toFixed(2);
                        progressDiv.innerText = `${file.name}: ${percent}%`;
                    }
                };
                xhr.onload = () => {
                    progressDiv.innerText = '';
                    if (xhr.status === 200 && xhr.responseText === "OK") {
                        resolve();
                    } else {
                        reject(new Error('服务器返回错误: ' + xhr.status));
                    }
                };
                xhr.onerror = () => reject(new Error('网络错误'));
                xhr.send(file);
            });
        }
    </script>
</body>
</html>'''
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(html.encode("utf-8"))

            def _handleSendData(self, body):
                try:
                    for pair in body.split('&'):
                        if '=' in pair:
                            key, val = pair.split('=', 1)
                            self._appendReceivedData(http_tool, data_lock, key, val)
                except Exception:
                    logger.exception("解析发送数据失败")

                self.send_response(200)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.send_header('Content-Length', '2')
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(b'OK')
                self.wfile.flush()

        try:
            port = self.settings.get("port", 8000)

            class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
                daemon_threads = True

            self.server = ThreadedHTTPServer(("", port), CustomHandler)
            self.server.timeout = 1
            logger.info(f"HTTP服务器启动: {self.local_ip}:{port}")

            while not shutdown_event.is_set():
                try:
                    self.server.handle_request()
                except Exception as e:
                    if shutdown_event.is_set():
                        break
                    logger.info(f"处理请求时出错: {e}")

            if self.server:
                self.server.server_close()
            logger.info("HTTP服务器已停止")
        except Exception:
            logger.exception("HTTP服务器启动失败")
        finally:
            self.running = False
            self.server = None


class HTTPDialog(QDialog):
    def __init__(self, parent, tool: HTTPTool):
        super().__init__(parent)
        self.tool = tool
        self.setWindowTitle("局域网通信")
        self.setMinimumSize(600, 500)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.Dialog)
        self._initUI()
        self._updateStatus()
        if not self.tool.running:
            self.tool.startServer()
            self._updateStatus()

    def showEvent(self, event):
        super().showEvent(event)

    def _initUI(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        title_bar = QFrame()
        title_bar.setObjectName("title_bar")
        title_layout = QHBoxLayout(title_bar)
        title_layout.setContentsMargins(10, 5, 5, 5)

        title_layout.addWidget(QLabel("局域网通信"))
        title_layout.addStretch()

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(10, 10, 10, 10)

        info_layout = QVBoxLayout()

        ip_port_layout = QHBoxLayout()
        ip_port_layout.addWidget(QLabel("本机 IP: "))
        self.ip_label = QLabel(self.tool.local_ip)
        ip_port_layout.addWidget(self.ip_label)
        ip_port_layout.addSpacing(20)
        ip_port_layout.addWidget(QLabel("端口: "))
        self.port_edit = QLineEdit()
        self.port_edit.setText(str(self.tool.settings.get("port", 8000)))
        self.port_edit.setMaximumWidth(80)
        ip_port_layout.addWidget(self.port_edit)
        self.browser_btn = QPushButton("浏览器打开")
        self.browser_btn.clicked.connect(self._openBrowser)
        ip_port_layout.addWidget(self.browser_btn)
        ip_port_layout.addStretch()
        info_layout.addLayout(ip_port_layout)

        folder_layout = QHBoxLayout()
        folder_layout.addWidget(QLabel("共享文件夹: "))
        self.folder_edit = QLineEdit()
        self.folder_edit.setText(self.tool.settings.get("shared_folder", ""))
        self.folder_edit.setReadOnly(True)
        folder_layout.addWidget(self.folder_edit)
        folder_btn = QPushButton("选择")
        folder_btn.clicked.connect(lambda: getFilePath(self, "选择共享文件夹", "", "dir", self.folder_edit))
        folder_layout.addWidget(folder_btn)
        info_layout.addLayout(folder_layout)

        upload_folder_layout = QHBoxLayout()
        upload_folder_layout.addWidget(QLabel("上传文件夹: "))
        self.upload_folder_edit = QLineEdit()
        self.upload_folder_edit.setText(self.tool.settings.get("upload_folder", ""))
        self.upload_folder_edit.setReadOnly(True)
        upload_folder_layout.addWidget(self.upload_folder_edit)
        upload_folder_btn = QPushButton("选择")
        upload_folder_btn.clicked.connect(lambda: getFilePath(self, "选择上传文件夹", "", "dir", self.upload_folder_edit))
        upload_folder_layout.addWidget(upload_folder_btn)
        info_layout.addLayout(upload_folder_layout)

        content_layout.addLayout(info_layout)

        send_header_layout = QHBoxLayout()
        send_header_layout.addWidget(QLabel("发送数据"))
        send_header_layout.addStretch()

        self.send_edit = QTextEdit()
        self.send_edit.setMaximumHeight(100)

        self.send_btn = QPushButton("发送")
        self.send_btn.clicked.connect(self._sendData)
        send_header_layout.addWidget(self.send_btn)

        clear_send_btn = QPushButton("清空")
        clear_send_btn.clicked.connect(lambda: self.send_edit.clear())
        send_header_layout.addWidget(clear_send_btn)

        content_layout.addLayout(send_header_layout)
        content_layout.addWidget(self.send_edit)

        recv_header_layout = QHBoxLayout()
        recv_header_layout.addWidget(QLabel("接收数据"))
        recv_header_layout.addStretch()

        self.recv_edit = QTextEdit()
        self.recv_edit.setMaximumHeight(150)
        self.recv_edit.setReadOnly(True)

        clear_recv_btn = QPushButton("清空")
        clear_recv_btn.clicked.connect(self._clearRecv)
        recv_header_layout.addWidget(clear_recv_btn)

        content_layout.addLayout(recv_header_layout)
        content_layout.addWidget(self.recv_edit)

        self.recv_timer = QTimer()
        self.recv_timer.timeout.connect(self._updateRecvData)
        self.recv_timer.start(1000)

        layout.addWidget(content)

        bottom_bar = QFrame()
        bottom_bar.setObjectName("bottom_bar")
        bottom_layout = QHBoxLayout(bottom_bar)

        self.start_btn = QPushButton("启动服务")
        self.start_btn.clicked.connect(self._toggleServer)
        bottom_layout.addWidget(self.start_btn)

        self.status_label = QLabel("未启动")
        self.status_label.setStyleSheet("color: #666;")
        bottom_layout.addWidget(self.status_label)

        bottom_layout.addStretch()

        self.background_check = QCheckBox("后台运行")
        self.background_check.setChecked(self.tool.settings.get("background_run", False))
        bottom_layout.addWidget(self.background_check)

        layout.addWidget(bottom_bar)

    def _sendData(self):
        """向共享数据缓冲写入文本"""
        data = self.send_edit.toPlainText().strip()
        if not data:
            messageBox(self, "提示", "请输入要发送的数据", 1)
            return
        with self.tool._data_lock:
            if not hasattr(self.tool, 'received_data'):
                self.tool.received_data = []
            self.tool.received_data.append(data)
        self.send_edit.clear()
        logger.info("数据已发送")

    def _clearRecv(self):
        with self.tool._data_lock:
            if hasattr(self.tool, 'received_data'):
                self.tool.received_data.clear()
        self.recv_edit.clear()

    def _updateRecvData(self):
        """定时刷新接收数据显示"""
        with self.tool._data_lock:
            new_data = self.tool.received_data[-10:] if hasattr(self.tool, 'received_data') else []
            new_text = "\n".join(new_data)
            if new_text != self.recv_edit.toPlainText():
                self.recv_edit.setPlainText(new_text)

    def _openBrowser(self):
        """用系统默认浏览器打开 HTTP 服务页面"""
        port = self.port_edit.text().strip() or self.tool.settings.get("port", 8000)
        webbrowser.open(f"http://{self.tool.local_ip}:{port}/")

    def _toggleServer(self):
        """切换服务器启动 / 停止状态"""
        if self.tool.running:
            self.tool.stopServer()
            self.start_btn.setText("启动服务")
            self.status_label.setText("已停止")
        else:
            self._saveToSettings(self.tool.settings)
            self.tool.startServer()
            self._updateStatus()

    def _saveToSettings(self, settings: dict):
        try:
            port = int(self.port_edit.text())
            if 1 <= port <= 65535:
                settings["port"] = port
        except ValueError:
            logger.exception("端口号解析失败")
        settings["shared_folder"] = self.folder_edit.text()
        settings["upload_folder"] = self.upload_folder_edit.text()
        settings["background_run"] = self.background_check.isChecked()

    def _updateStatus(self):
        if self.tool.running:
            self.start_btn.setText("停止服务")
            port = self.tool.settings.get("port", 8000)
            self.status_label.setText(f"运行中: {self.tool.local_ip}:{port}")

    def closeEvent(self, event):
        self._saveToSettings(self.tool.settings)
        if self.tool.plugin:
            self.tool.plugin.settings.update(self.tool.settings)
            self.tool.plugin.saveConfig()
        self.recv_timer.stop()
        if self.tool.running and not self.background_check.isChecked():
            self.tool.stopServer()
        super().closeEvent(event)
