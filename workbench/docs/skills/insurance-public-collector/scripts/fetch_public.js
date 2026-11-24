// 通用公开网页抓取（无需登录）：本机 Chrome + playwright-core，不下载 Chromium。
// 适用：JS 渲染列表、需要点击"加载更多"翻页的公开页面。静态页优先用 WebFetch。
//
// 步骤：1) 抓列表页拿详情链接  2) 逐条打开详情页抓正文  3) 输出标准 JSONL
//
// 运行：
//   NODE_PATH=/Users/luyawen/.workbuddy/binaries/node/workspace/node_modules \
//   /Users/luyawen/.workbuddy/binaries/node/versions/22.23.4/bin/node fetch_public.js
//
// 环境变量：
//   LIST_URL     列表页地址（必填）
//   LINK_SELECTOR 详情链接 CSS 选择器，默认 a[href]
//   TITLE_SELECTOR 标题选择器，默认 h1, h2, .title, [class*="title"]
//   BODY_SELECTOR 正文选择器，默认 article, .content, [class*="content"], main
//   BASE_URL     相对链接补全用的站点根地址
//   OUT          输出 JSONL 路径，默认 ./items.jsonl
//   SITE         站点名，默认"公开站点"
//   DIRECTION    方向，默认"自定义"
//   COLUMN       栏目名，默认空
//   KEYWORDS     关键词，分号分隔，默认空
//   DELAY        每条间隔毫秒，默认 2000（遵守 ≥2 秒限速）
//   MAX          最多条数，默认 50
//   HEADLESS     0 有界面，默认 1 无头

const fs = require('fs');
const { chromium } = require('playwright-core');

const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const LIST_URL = process.env.LIST_URL;
const OUT = process.env.OUT || './items.jsonl';
const SITE = process.env.SITE || '公开站点';
const DIRECTION = process.env.DIRECTION || '自定义';
const COLUMN = process.env.COLUMN || '';
const KEYWORDS = (process.env.KEYWORDS || '').split(';').filter(Boolean);
const DELAY = parseInt(process.env.DELAY || '2000', 10);
const MAX = parseInt(process.env.MAX || '50', 10);
const BASE_URL = process.env.BASE_URL || '';
const LINK_SEL = process.env.LINK_SELECTOR || 'a[href]';
const TITLE_SEL = process.env.TITLE_SELECTOR || 'h1, h2, .title, [class*="title"]';
const BODY_SEL = process.env.BODY_SELECTOR || 'article, .content, [class*="content"], main';
const headless = process.env.HEADLESS !== '0';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function absUrl(href) {
  if (!href) return '';
  if (/^https?:\/\//i.test(href)) return href;
  if (BASE_URL) return new URL(href, BASE_URL).toString();
  return href;
}

function hitsKeywords(text) {
  return KEYWORDS.filter((k) => text && text.includes(k));
}

(async () => {
  if (!LIST_URL) throw new Error('LIST_URL 必填');
  const browser = await chromium.launch({ executablePath: CHROME, headless });
  const page = await browser.newPage();
  await page.goto(LIST_URL, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(3000);

  // 列表页：拿详情链接
  const links = await page.evaluate((sel) => {
    const seen = new Set();
    const out = [];
    document.querySelectorAll(sel).forEach((a) => {
      const href = a.getAttribute('href') || '';
      if (!href || href.startsWith('#') || /javascript:/i.test(href)) return;
      if (seen.has(href)) return;
      seen.add(href);
      out.push({ href, anchor: (a.innerText || '').trim().slice(0, 200) });
    });
    return out;
  }, LINK_SEL);

  console.log('LIST_LINKS:', links.length);
  const items = [];
  for (const link of links.slice(0, MAX)) {
    const url = absUrl(link.href);
    if (!url.startsWith('http')) continue;
    try {
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 20000 });
      await page.waitForTimeout(1200);
      const detail = await page.evaluate(({ titleSel, bodySel }) => {
        const titleEl = document.querySelector(titleSel);
        const bodyEl = document.querySelector(bodySel);
        const title = (titleEl && titleEl.innerText || '').trim() || document.title || '';
        const content = (bodyEl && bodyEl.innerText || '').trim() || document.body.innerText.trim();
        const timeMatch = document.body.innerText.match(/20\d{2}[-年/.]\d{1,2}[-月/.]\d{1,2}/);
        return { title, content, published_at: timeMatch ? timeMatch[0] : '' };
      }, { titleSel: TITLE_SEL, bodySel: BODY_SEL });

      const text = detail.title + ' ' + detail.content;
      const matched = hitsKeywords(text);
      if (KEYWORDS.length && matched.length === 0) {
        await sleep(DELAY);
        continue;
      }
      items.push({
        source: SITE,
        column: COLUMN,
        url,
        title: detail.title,
        content: detail.content,
        published_at: detail.published_at,
        author: '',
        keywords: matched,
        direction: DIRECTION,
        collected_at: new Date().toISOString().slice(0, 19),
        extra: {},
      });
      console.log('GOT:', detail.title.slice(0, 40));
    } catch (e) {
      console.log('SKIP:', url, e.message.slice(0, 60));
    }
    await sleep(DELAY);
  }

  fs.writeFileSync(OUT, items.map((x) => JSON.stringify(x)).join('\n') + '\n', 'utf8');
  console.log('EXTRACTED:', items.length, '->', OUT);
  await browser.close();
})().catch((e) => { console.error('ERR:', e.message); process.exit(1); });
