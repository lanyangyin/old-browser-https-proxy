import warnings
from urllib3.exceptions import NotOpenSSLWarning
warnings.filterwarnings("ignore", category=NotOpenSSLWarning)

# 其余导入和代码保持不变
import requests
from bs4 import BeautifulSoup
import warnings
warnings.filterwarnings("ignore", category=UserWarning, module='urllib3')
print("Script started...")
# 在 /search 路由中替换 API 调用部分
def bing_search(query):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    url = "https://www.bing.com/search"
    params = {"q": query, "count": 10}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        results = []
        # Bing 结果通常包含在 <li class="b_algo"> 中
        for item in soup.find_all('li', class_='b_algo'):
            title_tag = item.find('h2')
            if not title_tag:
                continue
            link_tag = title_tag.find('a')
            if not link_tag:
                continue
            title = link_tag.get_text()
            url = link_tag.get('href')
            # 摘要可能在 <p> 中
            snippet_tag = item.find('p')
            snippet = snippet_tag.get_text() if snippet_tag else ""
            results.append({"title": title, "url": url, "snippet": snippet})
        return results
    except Exception as e:
        return []