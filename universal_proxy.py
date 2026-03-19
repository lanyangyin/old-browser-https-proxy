#!/usr/bin/env python3
"""
通用 HTTP/HTTPS 代理服务器
支持浏览器配置代理后访问任意网站
（增强版：显示类似 Flask 的启动信息，根路径返回使用说明）
"""

import socket
import threading
import urllib.parse
import netifaces
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

def get_local_ips():
    """获取本机所有 IPv4 地址（包括 127.0.0.1 和局域网 IP）"""
    ips = []
    try:
        # 获取所有网络接口的 IP
        for iface in netifaces.interfaces():
            addrs = netifaces.ifaddresses(iface)
            if netifaces.AF_INET in addrs:
                for addr in addrs[netifaces.AF_INET]:
                    ip = addr['addr']
                    if ip not in ips:
                        ips.append(ip)
    except ImportError:
        # 如果没有 netifaces 库，使用备用方法
        hostname = socket.gethostname()
        try:
            ips = list(set([ip for ip in socket.gethostbyname_ex(hostname)[2]]))
        except:
            ips = ['127.0.0.1']
    return ips

class ProxyHandler(BaseHTTPRequestHandler):
    """处理 HTTP 和 CONNECT 请求"""

    def do_GET(self):
        if self.path == '/':
            self._send_help_page()
        else:
            self._handle_http_request()

    def do_POST(self):
        self._handle_http_request()

    def do_PUT(self):
        self._handle_http_request()

    def do_DELETE(self):
        self._handle_http_request()

    def do_HEAD(self):
        self._handle_http_request()

    def do_CONNECT(self):
        """处理 HTTPS 隧道请求"""
        host, port = self.path.split(':')
        port = int(port)
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((host, port))
            self.send_response(200, 'Connection established')
            self.end_headers()
        except Exception as e:
            self.send_error(500, f'Tunnel establishment failed: {e}')
            return
        self._forward_tunnel(sock)

    def _send_help_page(self):
        """返回代理使用说明页面"""
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        help_html = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>通用代理服务器</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 40px; line-height: 1.6; }
        h1 { color: #333; }
        code { background: #f4f4f4; padding: 2px 5px; border-radius: 3px; }
        pre { background: #f4f4f4; padding: 10px; border-radius: 5px; }
    </style>
</head>
<body>
    <h1>🚀 通用代理服务器已启动</h1>
    <p>这是一个支持 HTTP 和 HTTPS 的通用代理服务。</p>
    <h2>📌 使用方法：</h2>
    <ol>
        <li>在浏览器或系统网络设置中，将 <strong>HTTP 代理</strong> 配置为本服务器的 IP 和端口（默认 8080）。</li>
        <li>确保勾选 <strong>“对 HTTPS 使用相同代理”</strong>（或单独设置 HTTPS 代理为相同地址）。</li>
        <li>之后访问任意网站，流量将通过此代理转发。</li>
    </ol>
    <h2>🔧 代理地址示例：</h2>
    <ul>
        <li><code>http://127.0.0.1:8080</code> (本机访问)</li>
        <li><code>http://192.168.x.x:8080</code> (局域网访问)</li>
    </ul>
    <p>📡 当前代理服务器正在运行，您可以通过代理浏览网页。</p>
    <hr>
    <p><em>注意：直接访问此页面不会通过代理，如需测试代理，请配置代理后访问外部网站。</em></p>
</body>
</html>"""
        self.wfile.write(help_html.encode('utf-8'))

    def _handle_http_request(self):
        """处理普通 HTTP 请求（绝对 URI 形式）"""
        url = self.path
        if not url.startswith('http'):
            self.send_error(400, 'Absolute URI required (check proxy settings)')
            return

        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        path = parsed.path or '/'
        if parsed.query:
            path += '?' + parsed.query

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((host, port))

            request_line = f"{self.command} {path} {self.request_version}\r\n"
            sock.send(request_line.encode())

            for header, value in self.headers.items():
                if header.lower() not in ('proxy-connection', 'connection', 'keep-alive'):
                    sock.send(f"{header}: {value}\r\n".encode())
            sock.send(b"Connection: close\r\n")
            sock.send(b"\r\n")

            content_length = self.headers.get('Content-Length')
            if content_length:
                body = self.rfile.read(int(content_length))
                sock.send(body)

            self._forward_response(sock)
        except Exception as e:
            self.send_error(502, f'Proxy request failed: {e}')

    def _forward_tunnel(self, remote_sock):
        self.close_connection = False
        client_sock = self.connection

        def forward(source, dest):
            try:
                while True:
                    data = source.recv(4096)
                    if not data:
                        break
                    dest.sendall(data)
            except:
                pass
            finally:
                try:
                    remote_sock.close()
                except:
                    pass
                try:
                    client_sock.close()
                except:
                    pass

        t1 = threading.Thread(target=forward, args=(client_sock, remote_sock))
        t2 = threading.Thread(target=forward, args=(remote_sock, client_sock))
        t1.daemon = True
        t2.daemon = True
        t1.start()
        t2.start()
        t1.join()
        t2.join()

    def _forward_response(self, remote_sock):
        try:
            while True:
                data = remote_sock.recv(4096)
                if not data:
                    break
                self.connection.sendall(data)
        except:
            pass
        finally:
            remote_sock.close()

    def log_message(self, format, *args):
        print(f"[{self.client_address[0]}] {format % args}")

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

def run_proxy(port=8080):
    """启动代理服务器，并显示类似 Flask 的启动信息"""
    # 获取本机 IP 列表
    ips = get_local_ips()
    # 筛选出 127.0.0.1 和局域网 IP（简单过滤，不包含回环和链路本地）
    local_ips = [ip for ip in ips if ip.startswith('127.') or ip.startswith('192.168.') or ip.startswith('10.') or ip.startswith('172.')]
    if not local_ips:
        local_ips = ['127.0.0.1']

    print("\n正在启动通用代理服务...")
    print(f"监听地址：http://0.0.0.0:{port}")
    for ip in sorted(set(local_ips)):
        print(f" * Running on http://{ip}:{port}")
    print("代理配置说明：")
    print("   客户端设置 HTTP 代理为上述任一地址和端口，同时勾选“对 HTTPS 使用相同代理”。")
    print("   直接访问本地址（如 http://127.0.0.1:8080）可查看帮助页面。")
    print("Press CTRL+C to quit\n")

    server_address = ('0.0.0.0', port)
    httpd = ThreadedHTTPServer(server_address, ProxyHandler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n代理服务器已停止")
        httpd.shutdown()

if __name__ == '__main__':
    # 默认端口 8080，可通过命令行参数修改（简单处理）
    import sys
    port = 8080
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except:
            pass
    run_proxy(port)