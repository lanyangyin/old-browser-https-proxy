const express = require('express');
const axios = require('axios');
const puppeteer = require('puppeteer');
const app = express();
const PORT = 3000;

let browser;
let browserWSEndpoint = null;

async function initBrowser() {
  if (!browser) {
    browser = await puppeteer.launch({
      headless: true,
      args: ['--no-sandbox', '--disable-setuid-sandbox']
    });
    browserWSEndpoint = browser.wsEndpoint();
    console.log('Puppeteer 浏览器已启动');
  }
  return browser;
}

async function closeBrowser() {
  if (browser) {
    await browser.close();
    browser = null;
    browserWSEndpoint = null;
    console.log('Puppeteer 浏览器已关闭');
  }
}

async function fetchWithPuppeteer(url) {
  const browserInstance = await initBrowser();
  const page = await browserInstance.newPage();
  try {
    await page.setUserAgent('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36');
    await page.setViewport({ width: 1280, height: 800 });
    await page.goto(url, { waitUntil: 'networkidle2', timeout: 30000 });
    const content = await page.content();
    return content;
  } catch (error) {
    console.error('Puppeteer 请求失败:', error.message);
    throw error;
  } finally {
    await page.close();
  }
}

function isCaptchaPage(html, url) {
  const captchaKeywords = ['验证码', 'captcha', '请输入验证码', '请完成安全验证', '安全检查', '请滑动验证', '人机验证', '请刷新'];
  const lowerHtml = html.toLowerCase();
  if (captchaKeywords.some(keyword => lowerHtml.includes(keyword.toLowerCase()))) return true;
  if (html.length < 500) return true;
  if (url.includes('bilibili.com') && lowerHtml.includes('验证')) return true;
  return false;
}

app.use(express.urlencoded({ extended: true }));

app.get('/', (req, res) => {
  res.send(`
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"><title>搜索代理 (增强版)</title></head>
    <body>
      <h1>搜索代理 (支持反爬网站)</h1>
      <form id="searchForm">
        <input type="text" id="searchInput" placeholder="输入搜索词或网址" style="width: 300px;">
        <br><br>
        <button type="button" onclick="submitSearch('url')">网址搜索</button>
        <button type="button" onclick="submitSearch('bing')">搜索</button>
      </form>
      <script>
        function submitSearch(type) {
          const query = document.getElementById('searchInput').value;
          if (!query) { alert('请输入内容'); return; }
          let url;
          if (type === 'url') {
            url = '/web?info=' + encodeURIComponent(query);
          } else {
            url = '/web?info=' + encodeURIComponent('https://cn.bing.com/search?q=' + encodeURIComponent(query));
          }
          window.location.href = url;
        }
      </script>
    </body>
    </html>
  `);
});

app.get('/web', async (req, res) => {
  const info = req.query.info;
  if (!info) return res.status(400).send('缺少 info 参数');

  if (info.startsWith('https://cn.bing.com/search?q=')) {
    try {
      const bingResponse = await axios.get(info, {
        headers: { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0' }
      });
      res.set('Content-Type', 'text/html; charset=UTF-8');
      return res.send(bingResponse.data);
    } catch (error) {
      console.error('Bing 请求失败:', error.message);
      return res.status(500).send('搜索服务暂时不可用');
    }
  }

  let targetUrl = info;
  if (!/^https?:\/\//i.test(targetUrl)) {
    targetUrl = 'http://' + targetUrl;
  }

  try {
    const axiosResponse = await axios.get(targetUrl, {
      headers: { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0' },
      maxRedirects: 5,
      timeout: 10000
    });

    const html = axiosResponse.data;
    if (!isCaptchaPage(html, targetUrl)) {
      const contentType = axiosResponse.headers['content-type'] || 'text/html';
      res.set('Content-Type', contentType);
      return res.send(html);
    }

    console.log(`检测到可能反爬，使用 Puppeteer 重试: ${targetUrl}`);
    const puppeteerHtml = await fetchWithPuppeteer(targetUrl);
    res.set('Content-Type', 'text/html; charset=UTF-8');
    res.send(puppeteerHtml);
  } catch (error) {
    console.log(`Axios 请求失败 (${error.message})，尝试 Puppeteer: ${targetUrl}`);
    try {
      const puppeteerHtml = await fetchWithPuppeteer(targetUrl);
      res.set('Content-Type', 'text/html; charset=UTF-8');
      res.send(puppeteerHtml);
    } catch (puppeteerError) {
      console.error('Puppeteer 也失败:', puppeteerError.message);
      res.status(500).send('无法访问该网址（经过两种方式尝试均失败）');
    }
  }
});

app.listen(PORT, '0.0.0.0', async () => {
  console.log(`代理服务器运行在 http://0.0.0.0:${PORT}`);
  await initBrowser();
});

process.on('SIGINT', async () => { await closeBrowser(); process.exit(); });
process.on('SIGTERM', async () => { await closeBrowser(); process.exit(); });