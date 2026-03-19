from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import urllib.request

class ProxyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        # 只处理 /search 路径
        if not self.path.startswith('/search'):
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'Not Found')
            return

        # 解析查询参数
        query = urlparse(self.path).query
        params = parse_qs(query)
        urls = params.get('w', [])
        if not urls:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'Missing "w" parameter')
            return

        target_url = urls[0]
        try:
            # 服务端请求目标 URL
            with urllib.request.urlopen(target_url, timeout=10) as response:
                content = response.read()
                # 获取目标响应的 Content-Type，默认为 text/html
                content_type = response.headers.get('Content-Type', 'text/html')
                self.send_response(200)
                self.send_header('Content-Type', content_type)
                self.end_headers()
                self.wfile.write(content)
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(f'Error: {str(e)}'.encode())

if __name__ == '__main__':
    server = HTTPServer(('0.0.0.0', 8000), ProxyHandler)
    print('Proxy server running on port 8000...')
    server.serve_forever()