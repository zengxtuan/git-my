// iTEST 网页考试自动化 · 可复用浏览器控制服务
//
// 用途：用独立 profile 启动 Edge（保留你的登录态），暴露一个本地 HTTP 控制接口，
// 供 CLI / 脚本 / Agent 读取页面、执行 JS、截图、切换标签页。
//
// 前置：
//   1) 安装 puppeteer-core（不要装进本仓库依赖，保持零依赖）：
//        cd %TEMP%\itest && pnpm add puppeteer-core
//      或全局可用后在本目录执行。
//   2) 运行：node tools/itest-cdp-server.mjs
//      首次启动后，在弹出的 Edge 窗口里登录 iTEST（只需一次，登录态保存在独立 profile）。
//
// 环境变量：
//   ITEST_EDGE      Edge 可执行文件路径
//   ITEST_PROFILE   独立 profile 目录（不要指向默认 profile）
//   ITEST_START_URL 启动后打开的地址
//   ITEST_PORT      控制服务端口（默认 17890）
//
// 端点：
//   GET  /state              当前活动页 URL / 标题 / 标签页数
//   GET  /pages              列出全部标签页
//   GET  /use?i=2            切换活动页
//   GET  /dump               当前页 innerText
//   GET  /html               当前页 outerHTML
//   GET  /goto?url=...       当前页导航
//   POST /eval  {code}       在页面上下文执行 JS（返回 {ok,value}）
//   GET  /shot               截图到 %TEMP%\itest\shot.png

import http from 'node:http';

const EDGE =
  process.env.ITEST_EDGE ||
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const PROFILE =
  process.env.ITEST_PROFILE ||
  `${process.env.TEMP}\\itest-profile`;
const START_URL =
  process.env.ITEST_START_URL ||
  'https://itestcloud.unipus.cn/utest/itest/s/exam';
const PORT = Number(process.env.ITEST_PORT || 17890);
const SHOT_PATH = `${process.env.TEMP}\\itest\\shot.png`;

let puppeteer;
try {
  puppeteer = (await import('puppeteer-core')).default;
} catch {
  console.error('缺少 puppeteer-core：请先 `pnpm add puppeteer-core`（可装在临时目录，不要让本仓库产生依赖）。');
  process.exit(1);
}

const browser = await puppeteer.launch({
  executablePath: EDGE,
  userDataDir: PROFILE,
  headless: false,
  pipe: false,
  timeout: 30000,
  args: ['--no-first-run', '--no-default-browser-check', '--start-maximized'],
  defaultViewport: null,
});

let activePage = (await browser.pages())[0] || (await browser.newPage());

browser.on('targetcreated', async (target) => {
  if (target.type() !== 'page') return;
  try {
    const page = await target.page();
    if (page) activePage = page;
  } catch {
    // 忽略已关闭的 target
  }
});

browser.on('targetdestroyed', async () => {
  const pages = await browser.pages();
  if (pages.length && !pages.includes(activePage)) activePage = pages[pages.length - 1];
});

await activePage
  .goto(START_URL, { waitUntil: 'domcontentloaded', timeout: 45000 })
  .catch(() => {});

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1');
  const send = (code, obj) => {
    res.writeHead(code, { 'content-type': 'application/json; charset=utf-8' });
    res.end(JSON.stringify(obj));
  };
  try {
    if (url.pathname === '/state') {
      return send(200, {
        url: activePage.url(),
        title: await activePage.title().catch(() => ''),
        count: (await browser.pages()).length,
      });
    }
    if (url.pathname === '/pages') {
      const pages = [];
      for (const p of await browser.pages()) {
        pages.push({
          url: p.url(),
          title: await p.title().catch(() => ''),
          active: p === activePage,
        });
      }
      return send(200, { pages });
    }
    if (url.pathname === '/use') {
      const i = Number(url.searchParams.get('i') || 0);
      const pages = await browser.pages();
      if (!pages[i]) return send(400, { ok: false, error: 'bad index', count: pages.length });
      activePage = pages[i];
      return send(200, { ok: true, url: activePage.url(), title: await activePage.title().catch(() => '') });
    }
    if (url.pathname === '/dump') {
      const text = await activePage.evaluate(() => (document.body ? document.body.innerText : ''));
      return send(200, { url: activePage.url(), title: await activePage.title().catch(() => ''), text });
    }
    if (url.pathname === '/html') {
      const html = await activePage.evaluate(() => document.documentElement.outerHTML);
      return send(200, { url: activePage.url(), html });
    }
    if (url.pathname === '/goto') {
      const target = url.searchParams.get('url') || START_URL;
      await activePage.goto(target, { waitUntil: 'domcontentloaded', timeout: 45000 }).catch(() => {});
      return send(200, { ok: true, url: activePage.url() });
    }
    if (url.pathname === '/eval' && req.method === 'POST') {
      let body = '';
      for await (const chunk of req) body += chunk;
      const { code } = JSON.parse(body);
      const result = await activePage.evaluate((c) => {
        try {
          return { ok: true, value: eval(c) };
        } catch (e) {
          return { ok: false, error: String(e) };
        }
      }, code);
      return send(200, { page: activePage.url(), ...result });
    }
    if (url.pathname === '/shot') {
      await activePage.screenshot({ path: SHOT_PATH, fullPage: false });
      return send(200, { ok: true, path: SHOT_PATH, url: activePage.url() });
    }
    return send(404, { error: 'unknown endpoint' });
  } catch (e) {
    return send(500, { error: String((e && e.stack) || e) });
  }
});

server.listen(PORT, '127.0.0.1', () => {
  console.log(`CONTROL_SERVER_READY on http://127.0.0.1:${PORT}`);
  console.log(`profile: ${PROFILE}`);
  console.log('首次使用请在弹出的 Edge 窗口里登录 iTEST。');
});
