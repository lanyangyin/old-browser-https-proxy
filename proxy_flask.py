#!/usr/bin/env python3
"""
简易网页代理服务（Flask 版本）
启动后访问：http://127.0.0.1:5000/search?w=https://example.com
"""

from flask import Flask, request, Response
import requests
import sys

app = Flask(__name__)

@app.route('/search')
def proxy():
    """处理 /search 请求，获取 w 参数指定的 URL 并返回其内容"""
    target_url = request.args.get('w')
    if not target_url:
        return '错误：缺少 "w" 参数', 400

    try:
        # 服务端发起 GET 请求，设置超时和 User-Agent 避免被拦截
        headers = {'User-Agent': 'Mozilla/5.0 (compatible; ProxyBot/1.0)'}
        resp = requests.get(target_url, headers=headers, timeout=10)

        # 构造响应，保留原始状态码和 Content-Type
        return Response(
            resp.content,
            status=resp.status_code,
            content_type=resp.headers.get('Content-Type', 'text/html; charset=utf-8')
        )
    except requests.exceptions.Timeout:
        return '错误：请求目标超时', 504
    except requests.exceptions.ConnectionError:
        return '错误：无法连接到目标服务器', 502
    except Exception as e:
        return f'错误：{str(e)}', 500

@app.route('/')
def index():
    return '代理服务已启动。用法：/search?w=目标网址'

if __name__ == '__main__':
    print('正在启动 Flask 代理服务...')
    print('监听地址：http://0.0.0.0:5000')
    print('示例请求：http://127.0.0.1:5000/search?w=https://example.com')
    # 关闭调试模式可减少输出，生产环境建议 debug=False
    app.run(host='0.0.0.0', port=5001, debug=False)