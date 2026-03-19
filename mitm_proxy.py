#!/usr/bin/env python3
"""
MITM代理服务器 - 解决老旧设备SSL/TLS版本不兼容问题
自动生成CA证书，动态签发域名证书，支持TLS降级。
"""

import os
import sys
import socket
import threading
import ssl
import urllib.parse
import tempfile
import shutil
import subprocess
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from datetime import datetime, timedelta, timezone

# 尝试导入cryptography，用于动态生成证书
try:
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.backends import default_backend
except ImportError:
    print("错误：需要cryptography库。请运行：pip install cryptography")
    sys.exit(1)

# 配置
CA_CERT_FILE = "ca.crt"
CA_KEY_FILE = "ca.key"
CERT_VALID_DAYS = 365
LISTEN_PORT = 8080  # 可修改

# 全局CA证书和密钥（从文件加载或生成）
ca_cert = None
ca_key = None
ca_cert_pem = None
ca_key_pem = None

def ensure_ca_exists():
    """检查CA证书是否存在，若不存在则生成"""
    global ca_cert, ca_key, ca_cert_pem, ca_key_pem
    if os.path.exists(CA_CERT_FILE) and os.path.exists(CA_KEY_FILE):
        # 加载现有CA
        with open(CA_CERT_FILE, "rb") as f:
            ca_cert_pem = f.read()
            ca_cert = x509.load_pem_x509_certificate(ca_cert_pem, default_backend())
        with open(CA_KEY_FILE, "rb") as f:
            ca_key_pem = f.read()
            ca_key = serialization.load_pem_private_key(ca_key_pem, password=None, backend=default_backend())
        print("已加载现有CA证书")
    else:
        print("未找到CA证书，正在生成新的CA证书...")
        # 生成CA私钥
        ca_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
            backend=default_backend()
        )
        # 生成CA证书
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COUNTRY_NAME, u"CN"),
            x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, u"Beijing"),
            x509.NameAttribute(NameOID.LOCALITY_NAME, u"Beijing"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, u"MITM Proxy"),
            x509.NameAttribute(NameOID.COMMON_NAME, u"MITM Proxy CA"),
        ])
        ca_cert = x509.CertificateBuilder().subject_name(
            subject
        ).issuer_name(
            issuer
        ).public_key(
            ca_key.public_key()
        ).serial_number(
            x509.random_serial_number()
        ).not_valid_before(
            datetime.now(timezone.utc)
        ).not_valid_after(
            datetime.now(timezone.utc) + timedelta(days=CERT_VALID_DAYS * 10)  # CA长期有效
        ).add_extension(
            x509.BasicConstraints(ca=True, path_length=None), critical=True
        ).sign(ca_key, hashes.SHA256(), default_backend())

        # 保存PEM
        ca_cert_pem = ca_cert.public_bytes(serialization.Encoding.PEM)
        ca_key_pem = ca_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()
        )
        with open(CA_CERT_FILE, "wb") as f:
            f.write(ca_cert_pem)
        with open(CA_KEY_FILE, "wb") as f:
            f.write(ca_key_pem)
        print(f"CA证书已生成：{CA_CERT_FILE}")
        print("请将此CA证书安装到您的老旧设备上并信任它，否则浏览器将显示证书警告。")

# 缓存生成的域名证书
cert_cache = {}

def get_cert_for_domain(domain):
    """为指定域名动态生成证书（使用CA签名），返回(cert_pem, key_pem)"""
    if domain in cert_cache:
        return cert_cache[domain]

    # 生成域名私钥
    key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend()
    )
    # 生成证书请求
    subject = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, u"CN"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, u"Beijing"),
        x509.NameAttribute(NameOID.LOCALITY_NAME, u"Beijing"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, u"MITM Proxy"),
        x509.NameAttribute(NameOID.COMMON_NAME, domain),
    ])
    cert = x509.CertificateBuilder().subject_name(
        subject
    ).issuer_name(
        ca_cert.subject  # 使用CA签发
    ).public_key(
        key.public_key()
    ).serial_number(
        x509.random_serial_number()
    ).not_valid_before(
        datetime.now(timezone.utc)
    ).not_valid_after(
        datetime.now(timezone.utc) + timedelta(days=CERT_VALID_DAYS)
    ).add_extension(
        x509.SubjectAlternativeName([x509.DNSName(domain)]),
        critical=False
    ).sign(ca_key, hashes.SHA256(), default_backend())

    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption()
    )
    cert_cache[domain] = (cert_pem, key_pem)
    return cert_pem, key_pem


def get_local_ips():
    """获取本机所有IPv4地址"""
    ips = []
    try:
        import netifaces
        for iface in netifaces.interfaces():
            addrs = netifaces.ifaddresses(iface)
            if netifaces.AF_INET in addrs:
                for addr in addrs[netifaces.AF_INET]:
                    ip = addr['addr']
                    if ip not in ips:
                        ips.append(ip)
    except ImportError:
        # 备用方法
        hostname = socket.gethostname()
        try:
            ips = list(set(socket.gethostbyname_ex(hostname)[2]))
        except:
            ips = ['127.0.0.1']
    return ips


class MITMProxyHandler(BaseHTTPRequestHandler):
    """处理HTTP和HTTPS请求（支持MITM）"""

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
        """处理HTTPS隧道请求，如果是443端口则进行MITM"""
        host, port = self.path.split(':')
        port = int(port)
        if port != 443:
            # 非HTTPS端口，使用普通隧道
            self._handle_tunnel(host, port)
        else:
            # HTTPS端口，进行MITM
            self._handle_mitm(host)

    def _handle_tunnel(self, host, port):
        """普通TCP隧道（用于非HTTPS或不想MITM的端口）"""
        try:
            remote = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            remote.connect((host, port))
            self.send_response(200, 'Connection established')
            self.end_headers()
        except Exception as e:
            self.send_error(500, f'Tunnel establishment failed: {e}')
            return

        # 双向转发原始数据
        self._forward_tunnel(self.connection, remote)

    def _handle_mitm(self, host):
        """MITM处理：与客户端建立TLS（支持旧版本），与远程建立TLS（现代版本）"""
        # 获取为该域名签发的证书
        try:
            cert_pem, key_pem = get_cert_for_domain(host)
        except Exception as e:
            self.send_error(500, f'Certificate generation failed: {e}')
            return

        # 响应CONNECT成功
        try:
            self.send_response(200, 'Connection established')
            self.end_headers()
        except Exception as e:
            # 可能客户端已断开
            return

        client_sock = self.connection

        # 创建服务器端SSL上下文（与客户端握手）
        server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        # 为了兼容老旧设备，启用所有可能版本（包括TLS1.0/1.1）
        server_ctx.minimum_version = ssl.TLSVersion.MINIMUM_SUPPORTED
        server_ctx.maximum_version = ssl.TLSVersion.MAXIMUM_SUPPORTED
        # 加载证书和私钥（内存中）
        with tempfile.NamedTemporaryFile(delete=False) as cert_file:
            cert_file.write(cert_pem)
            cert_file.close()
            with tempfile.NamedTemporaryFile(delete=False) as key_file:
                key_file.write(key_pem)
                key_file.close()
                try:
                    server_ctx.load_cert_chain(cert_file.name, key_file.name)
                except Exception as e:
                    self.log_error(f"SSL server cert load failed: {e}")
                    return
                finally:
                    os.unlink(cert_file.name)
                    os.unlink(key_file.name)

        # 将客户端socket包装为SSL
        try:
            ssl_client = server_ctx.wrap_socket(client_sock, server_side=True)
        except ssl.SSLError as e:
            self.log_error(f"SSL handshake with client failed: {e}")
            return

        # 连接远程服务器并建立TLS（客户端模式）
        remote_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            remote_sock.connect((host, 443))
        except Exception as e:
            self.log_error(f"Connect to remote {host} failed: {e}")
            ssl_client.close()
            return

        # 创建客户端SSL上下文（与远程握手）
        client_ctx = ssl.create_default_context()
        # 可选：设置远程TLS版本（默认使用系统最高）
        try:
            ssl_remote = client_ctx.wrap_socket(remote_sock, server_hostname=host)
        except ssl.SSLError as e:
            self.log_error(f"SSL handshake with remote {host} failed: {e}")
            ssl_client.close()
            remote_sock.close()
            return

        # 双向转发解密后的数据（此时双方都是明文HTTP）
        self._forward_tunnel(ssl_client, ssl_remote)

    def _forward_tunnel(self, sock1, sock2):
        """在两个socket之间双向转发数据，直到一方关闭"""
        sock1.settimeout(None)
        sock2.settimeout(None)
        stop_event = threading.Event()

        def forward(src, dst):
            try:
                while not stop_event.is_set():
                    data = src.recv(4096)
                    if not data:
                        break
                    dst.sendall(data)
            except (ConnectionResetError, BrokenPipeError, OSError):
                pass
            finally:
                stop_event.set()
                try:
                    src.close()
                except:
                    pass
                try:
                    dst.close()
                except:
                    pass

        t1 = threading.Thread(target=forward, args=(sock1, sock2))
        t2 = threading.Thread(target=forward, args=(sock2, sock1))
        t1.daemon = True
        t2.daemon = True
        t1.start()
        t2.start()
        t1.join()
        t2.join()

    def _handle_http_request(self):
        """处理HTTP绝对URI请求（非隧道）"""
        url = self.path
        if not url.startswith('http'):
            self.send_error(400, 'Absolute URI required')
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

            # 接收响应并转发
            while True:
                data = sock.recv(4096)
                if not data:
                    break
                self.connection.sendall(data)
        except Exception as e:
            self.send_error(502, f'Proxy request failed: {e}')
        finally:
            sock.close()

    def _send_help_page(self):
        """返回帮助页面"""
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        help_html = f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><title>MITM代理服务器</title></head>
<body>
<h1>MITM代理服务器已启动</h1>
<p>此代理可帮助老旧设备访问现代HTTPS网站。</p>
<h2>使用说明：</h2>
<ol>
<li>将生成的CA证书 <code>{CA_CERT_FILE}</code> 安装到您的设备并信任。</li>
<li>在设备网络设置中配置HTTP代理为：本机IP，端口 {LISTEN_PORT}。</li>
<li>访问任意HTTPS网站，代理将动态生成匹配的证书。</li>
</ol>
<h3>本机可用IP：</h3>
<ul>
"""
        for ip in get_local_ips():
            help_html += f"<li><code>{ip}:{LISTEN_PORT}</code></li>\n"
        help_html += """
</ul>
<p>注意：如果证书未安装，浏览器会显示警告，请点击继续。</p>
</body>
</html>"""
        self.wfile.write(help_html.encode('utf-8'))

    def log_message(self, format, *args):
        """自定义日志输出"""
        print(f"[{self.client_address[0]}] {format % args}")


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def run_proxy(port=8080):
    # 确保CA存在
    ensure_ca_exists()

    server_address = ('0.0.0.0', port)
    httpd = ThreadedHTTPServer(server_address, MITMProxyHandler)

    print("\n正在启动MITM代理服务...")
    print(f"监听地址：http://0.0.0.0:{port}")
    for ip in sorted(set(get_local_ips())):
        print(f" * Running on http://{ip}:{port}")
    print("\n代理配置说明：")
    print(f"   客户端设置HTTP代理为上述任一地址和端口。")
    print(f"   必须将生成的CA证书（{CA_CERT_FILE}）安装到设备并信任，否则TLS握手会失败。")
    print("Press CTRL+C to quit\n")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n代理服务器已停止")
        httpd.shutdown()


if __name__ == '__main__':
    port = LISTEN_PORT
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except:
            pass
    run_proxy(port)