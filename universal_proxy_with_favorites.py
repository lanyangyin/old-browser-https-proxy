#!/usr/bin/env python3
"""
通用HTTPS代理服务 + 收藏夹功能
让仅支持HTTP的浏览器也能访问现代HTTPS网站。
- 支持任意HTTPS网站（重写HTML链接）
- 收藏常用网址（通过 favorites.txt）
- 显示所有局域网IP
"""

import os
import socket
import urllib.parse
from flask import Flask, request, Response, stream_with_context, render_template_string
import requests
from bs4 import BeautifulSoup

# 尝试导入netifaces获取多IP
try:
    import netifaces

    HAS_NETIFACES = True
except ImportError:
    HAS_NETIFACES = False

app = Flask(__name__)

# 配置
LISTEN_PORT = 5001
FAVORITES_FILE = 'favorites.txt'  # 收藏文件（每行一个URL）
PROXY_HOST = None  # 将在运行时自动检测

# 会话对象，保持连接和Cookie
session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
})


# ---------- 辅助函数 ----------
def get_all_local_ips():
    """获取本机所有IPv4地址（排除回环地址）"""
    ips = []
    if HAS_NETIFACES:
        for iface in netifaces.interfaces():
            addrs = netifaces.ifaddresses(iface)
            if netifaces.AF_INET in addrs:
                for addr in addrs[netifaces.AF_INET]:
                    ip = addr['addr']
                    if not ip.startswith('127.'):
                        ips.append(ip)
    else:
        try:
            hostname = socket.gethostname()
            ip_list = socket.gethostbyname_ex(hostname)[2]
            ips = [ip for ip in ip_list if not ip.startswith('127.')]
        except:
            pass
        if not ips:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(('8.8.8.8', 80))
                ip = s.getsockname()[0]
                s.close()
                if ip and not ip.startswith('127.'):
                    ips.append(ip)
            except:
                pass
    return ips


def load_favorites():
    """从收藏文件读取URL列表"""
    favorites = []
    if os.path.exists(FAVORITES_FILE):
        with open(FAVORITES_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    favorites.append(line)
    return favorites


def save_favorite(url_input):
    """添加新的收藏（支持直接输入URL或部分网址）"""
    url_input = url_input.strip()
    if not url_input:
        return False
    # 如果没带协议，默认添加 https://
    if not url_input.startswith(('http://', 'https://')):
        url_input = 'https://' + url_input
    with open(FAVORITES_FILE, 'a', encoding='utf-8') as f:
        f.write(url_input + '\n')
    return True


def rewrite_html(content, base_url, proxy_base):
    """重写HTML内容，将资源链接改为通过代理访问"""
    if not content:
        return content

    soup = BeautifulSoup(content, 'html.parser')

    # 需要重写的标签和属性
    tags_attrs = [
        ('a', 'href'),
        ('img', 'src'),
        ('script', 'src'),
        ('link', 'href'),
        ('iframe', 'src'),
        ('frame', 'src'),
        ('form', 'action'),
        ('source', 'src'),
        ('video', 'src'),
        ('audio', 'src'),
        ('embed', 'src'),
    ]

    for tag_name, attr_name in tags_attrs:
        for tag in soup.find_all(tag_name):
            if tag.has_attr(attr_name):
                original_url = tag[attr_name]
                if original_url and not original_url.startswith('data:'):
                    # 转换为绝对URL
                    absolute_url = urllib.parse.urljoin(base_url, original_url)
                    # 重写为代理URL
                    tag[attr_name] = f"/proxy?url={urllib.parse.quote(absolute_url, safe='')}"

    return str(soup)


# ---------- 路由 ----------
@app.route('/proxy')
def handle_proxy():
    """核心处理函数：获取目标URL并返回内容"""
    target_url = request.args.get('url')
    if not target_url:
        return '缺少 url 参数', 400

    try:
        # 处理可能缺少协议的情况（理论上不应发生，但保险起见）
        if not target_url.startswith(('http://', 'https://')):
            target_url = 'https://' + target_url

        method = request.method
        headers = {key: value for key, value in request.headers if key.lower() not in ['host', 'accept-encoding']}
        data = request.get_data() if request.method in ['POST', 'PUT', 'PATCH'] else None

        resp = session.request(
            method=method,
            url=target_url,
            headers=headers,
            data=data,
            params=request.args if method == 'GET' else None,
            allow_redirects=True,
            timeout=30,
            stream=True
        )

        content_type = resp.headers.get('Content-Type', '')
        is_html = 'text/html' in content_type

        if is_html:
            # HTML内容：重写链接
            content = resp.content
            proxy_base = f"http://{PROXY_HOST}:{LISTEN_PORT}"
            rewritten_content = rewrite_html(content, target_url, proxy_base)

            response = Response(rewritten_content, status=resp.status_code)
            response.headers['Content-Type'] = 'text/html; charset=utf-8'
        else:
            # 非HTML内容：直接流式传输
            def generate():
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        yield chunk

            response = Response(stream_with_context(generate()), status=resp.status_code)
            if content_type:
                response.headers['Content-Type'] = content_type

        # 复制部分有用的响应头
        for header in ['Content-Disposition', 'Content-Language', 'Cache-Control']:
            if header in resp.headers:
                response.headers[header] = resp.headers[header]

        return response

    except requests.exceptions.SSLError as e:
        return f"SSL错误：{str(e)}", 502
    except requests.exceptions.ConnectionError as e:
        return f"连接错误：{str(e)}", 502
    except requests.exceptions.Timeout:
        return "超时：目标服务器响应太慢。", 504
    except Exception as e:
        return f"代理请求失败：{str(e)}", 500


@app.route('/add_favorite', methods=['POST'])
def add_favorite():
    """处理添加收藏的表单提交"""
    url_input = request.form.get('url', '')
    if save_favorite(url_input):
        return '', 204
    else:
        return '无效输入', 400


@app.route('/')
def index():
    """主页：显示使用说明、收藏夹和服务器IP"""
    ips = get_all_local_ips()
    global PROXY_HOST
    PROXY_HOST = ips[0] if ips else '127.0.0.1'

    favorites = load_favorites()

    html = '''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>通用HTTPS代理 + 收藏夹</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 20px; line-height: 1.6; }
            .ip-list { background: #f0f0f0; padding: 10px; border-radius: 5px; }
            code { background: #e0e0e0; padding: 2px 5px; border-radius: 3px; }
            .example { margin: 20px 0; padding: 10px; border-left: 4px solid #06c; background: #f9f9f9; }
            .favorites { margin-top: 20px; }
            .favorites ul { list-style-type: none; padding: 0; }
            .favorites li { margin: 5px 0; }
            .favorites a { text-decoration: none; color: #06c; }
            .favorites a:hover { text-decoration: underline; }
            .add-form { margin-top: 20px; }
            input[type=text] { width: 300px; padding: 5px; }
            button { padding: 5px 10px; }
            .warning { color: #c00; font-weight: bold; }
        </style>
    </head>
    <body>
        <h1>🌐 通用HTTPS代理 + 收藏夹</h1>
        <p>让老旧设备也能浏览现代HTTPS网站！</p>

        <h2>📡 服务器地址</h2>
        <div class="ip-list">
            <ul>
            {% for ip in ips %}
                <li><code>http://{{ ip }}:{{ port }}/</code></li>
            {% endfor %}
            </ul>
        </div>

        <h2>📌 我的收藏</h2>
        <div class="favorites">
            {% if favorites %}
                <ul>
                {% for fav in favorites %}
                    <li>
                        <a href="/proxy?url={{ fav | urlencode }}" target="_blank">{{ fav }}</a>
                    </li>
                {% endfor %}
                </ul>
            {% else %}
                <p>暂无收藏。您可以通过下面的表单添加，或直接编辑 <code>favorites.txt</code> 文件（每行一个网址）。</p>
            {% endif %}
        </div>

        <div class="add-form">
            <h3>➕ 添加新收藏</h3>
            <form action="/add_favorite" method="post" onsubmit="alert('添加成功！');">
                <input type="text" name="url" placeholder="输入网址（如 example.com 或 https://...）" required>
                <button type="submit">添加</button>
            </form>
        </div>

        <h2>📖 使用方法</h2>
        <p>在您的老旧设备浏览器中，直接访问以下格式的URL：</p>
        <div class="example">
            <code>http://服务器IP:{{ port }}/proxy?url=https://目标网站.com</code>
        </div>

        <h3>✨ 快速测试</h3>
        <ul>
            <li><a href="/proxy?url=https://example.com" target="_blank">Example.com</a></li>
            <li><a href="/proxy?url=https://www.wikipedia.org" target="_blank">Wikipedia</a></li>
            <li><a href="/proxy?url=https://www.bilibili.com" target="_blank">Bilibili（实验性）</a></li>
        </ul>

        <h3>⚠️ 注意事项</h3>
        <ul>
            <li>本服务会<strong>重写HTML内容</strong>，使所有链接也通过代理访问。</li>
            <li>复杂的JavaScript网站（如B站、YouTube）可能无法完美工作，因为JS动态生成的请求无法被重写。</li>
            <li>对于视频网站，建议使用我们之前开发的<a href="/">专用视频代理</a>。</li>
            <li>本服务仅供个人在局域网内使用，请勿滥用。</li>
        </ul>
    </body>
    </html>
    '''
    from jinja2 import Template
    template = Template(html)
    return template.render(ips=ips, port=LISTEN_PORT, favorites=favorites)


@app.route('/favicon.ico')
def favicon():
    return '', 404


if __name__ == '__main__':
    ips = get_all_local_ips()
    PROXY_HOST = ips[0] if ips else '127.0.0.1'

    print("=" * 60)
    print("🚀 通用HTTPS代理服务 + 收藏夹 启动")
    print("=" * 60)
    print(f"监听地址: http://0.0.0.0:{LISTEN_PORT}")
    print("\n局域网访问地址：")
    for ip in ips:
        print(f"  📍 http://{ip}:{LISTEN_PORT}/")
    print("\n收藏文件：", FAVORITES_FILE)
    print("使用方法：")
    print(f"  在老旧设备浏览器中输入：http://<服务器IP>:{LISTEN_PORT}/proxy?url=https://目标网站.com")
    print("=" * 60)
    print("按 Ctrl+C 停止服务")
    print("=" * 60)

    app.run(host='0.0.0.0', port=LISTEN_PORT, debug=False, threaded=True)