#!/usr/bin/env python3
"""
B站视频代理服务（增强版）
- 显示所有局域网IP地址
- 支持收藏BV号（通过 favorites.txt 文件）
- 实时转码视频为PSP兼容格式
- 提供内嵌播放页面（/player）尝试直接播放
"""

import os
import subprocess
import socket
import urllib.parse
from flask import Flask, request, Response, stream_with_context, render_template_string
import yt_dlp

# 尝试导入netifaces获取多IP，若失败则使用后备方法
try:
    import netifaces
    HAS_NETIFACES = True
except ImportError:
    HAS_NETIFACES = False

app = Flask(__name__)

# 收藏文件路径
FAVORITES_FILE = 'favorites.txt'

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
    """从收藏文件读取BV号列表"""
    favorites = []
    if os.path.exists(FAVORITES_FILE):
        with open(FAVORITES_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    favorites.append(line)
    return favorites

def save_favorite(bv_input):
    """添加新的收藏（支持BV号或完整URL）"""
    bv_input = bv_input.strip()
    if not bv_input:
        return False
    if bv_input.startswith('BV'):
        url = f'https://www.bilibili.com/video/{bv_input}'
    elif bv_input.startswith('https://'):
        url = bv_input
    else:
        url = bv_input
    with open(FAVORITES_FILE, 'a', encoding='utf-8') as f:
        f.write(url + '\n')
    return True

def stream_with_ffmpeg(bilibili_url):
    """使用yt-dlp+ffmpeg实时转码输出MP4流"""
    cmd_get_info = [
        'yt-dlp',
        '-f', 'bv*+ba/b',
        '--get-url',
        '--get-title',
        bilibili_url
    ]
    try:
        result = subprocess.run(cmd_get_info, capture_output=True, text=True, check=True, timeout=30)
        output_lines = result.stdout.strip().split('\n')
        if len(output_lines) < 2:
            raise Exception("无法解析视频信息")
        title = output_lines[0]
        video_url = output_lines[1]

        ffmpeg_cmd = [
            'ffmpeg',
            '-i', video_url,
            '-c:v', 'libx264',
            '-profile:v', 'baseline',
            '-level', '3.0',
            '-vf', 'scale=480:272',
            '-c:a', 'aac',
            '-b:a', '128k',
            '-f', 'mp4',
            '-movflags', '+frag_keyframe+empty_moov',
            '-y',
            'pipe:1'
        ]

        process = subprocess.Popen(
            ffmpeg_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0
        )

        def generate():
            try:
                while True:
                    chunk = process.stdout.read(8192)
                    if not chunk:
                        break
                    yield chunk
            finally:
                process.terminate()

        return generate(), title

    except subprocess.TimeoutExpired:
        return None, "获取视频信息超时"
    except subprocess.CalledProcessError as e:
        return None, f"yt-dlp执行失败: {e.stderr}"
    except Exception as e:
        return None, str(e)

@app.route('/bilibili')
def proxy_bilibili():
    """返回视频流（直接下载/播放）"""
    url = request.args.get('url')
    if not url:
        return '缺少 url 参数', 400
    if not url.startswith(('https://www.bilibili.com/', 'https://b23.tv/')):
        return '只支持B站视频链接', 400

    video_stream, title_or_error = stream_with_ffmpeg(url)
    if video_stream is None:
        return f'获取视频失败: {title_or_error}', 500

    response = Response(stream_with_context(video_stream), mimetype='video/mp4')
    response.headers['Content-Disposition'] = f'inline; filename="bilibili_video.mp4"'
    return response

@app.route('/player')
def player():
    """返回内嵌视频的HTML页面，尝试在浏览器内播放"""
    url = request.args.get('url')
    if not url:
        return '缺少 url 参数', 400
    if not url.startswith(('https://www.bilibili.com/', 'https://b23.tv/')):
        return '只支持B站视频链接', 400

    # 对url进行URL编码，以便放入src属性
    encoded_url = urllib.parse.quote(url, safe='')
    html = f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>B站视频内嵌播放</title>
    <style>
        body {{ background: black; text-align: center; margin-top: 20px; color: white; }}
        video {{ max-width: 100%; height: auto; }}
    </style>
</head>
<body>
    <video src="/bilibili?url={encoded_url}" controls autoplay width="480" height="272">
        您的浏览器不支持 video 标签，请尝试<a href="/bilibili?url={encoded_url}">下载后播放</a>。
    </video>
</body>
</html>'''
    return html

@app.route('/add_favorite', methods=['POST'])
def add_favorite():
    bv_input = request.form.get('bv', '')
    if save_favorite(bv_input):
        return '', 204
    else:
        return '无效输入', 400

@app.route('/')
def index():
    ips = get_all_local_ips()
    favorites = load_favorites()
    html = '''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>B站视频代理</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 20px; }
            .ip-list { background: #f0f0f0; padding: 10px; border-radius: 5px; }
            .favorites { margin-top: 20px; }
            .favorites ul { list-style-type: none; padding: 0; }
            .favorites li { margin: 5px 0; }
            .favorites a { text-decoration: none; color: #06c; margin-right: 10px; }
            .favorites a:hover { text-decoration: underline; }
            .download-link { color: green; }
            .play-link { color: blue; }
            .add-form { margin-top: 20px; }
            input[type=text] { width: 300px; padding: 5px; }
            button { padding: 5px 10px; }
        </style>
    </head>
    <body>
        <h1>B站视频转码代理</h1>
        <p>本服务将B站视频实时转码为PSP等老旧设备兼容的MP4格式。</p>

        <h2>服务器地址</h2>
        <div class="ip-list">
            <ul>
            {% for ip in ips %}
                <li><code>http://{{ ip }}:{{ port }}/</code></li>
            {% endfor %}
            </ul>
        </div>

        <h2>我的收藏</h2>
        <div class="favorites">
            {% if favorites %}
                <ul>
                {% for fav in favorites %}
                    <li>
                        <span>{{ fav }}</span>
                        <a href="/player?url={{ fav | urlencode }}" class="play-link" target="_blank">[播放]</a>
                        <a href="/bilibili?url={{ fav | urlencode }}" class="download-link" target="_blank">[下载]</a>
                    </li>
                {% endfor %}
                </ul>
                <p>提示：点击“播放”尝试在浏览器内直接播放；如果不行，请用“下载”保存后播放。</p>
            {% else %}
                <p>暂无收藏。您可以通过下面的表单添加，或直接编辑 <code>favorites.txt</code> 文件。</p>
            {% endif %}
        </div>

        <div class="add-form">
            <h3>添加新收藏</h3>
            <form action="/add_favorite" method="post" onsubmit="alert('添加成功！');">
                <input type="text" name="bv" placeholder="输入BV号（如 BV1sVFQzvEL2）或完整视频URL" required>
                <button type="submit">添加</button>
            </form>
        </div>

        <h2>使用说明</h2>
        <p>直接访问 <code>/player?url=视频链接</code> 尝试内嵌播放。</p>
        <p>直接访问 <code>/bilibili?url=视频链接</code> 可下载视频。</p>
    </body>
    </html>
    '''
    from jinja2 import Template
    template = Template(html)
    return template.render(ips=ips, port=5001, favorites=favorites)

if __name__ == '__main__':
    port = 5001
    ips = get_all_local_ips()
    print("服务启动，监听地址: http://0.0.0.0:{}".format(port))
    if ips:
        print("局域网访问地址：")
        for ip in ips:
            print(f"  http://{ip}:{port}")
    else:
        print("无法获取局域网IP，请手动查看本机IP")
    print("确保已安装 ffmpeg (brew install ffmpeg) 和 netifaces (可选，用于显示多IP)")
    print("收藏文件：favorites.txt (与脚本同目录)")
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)