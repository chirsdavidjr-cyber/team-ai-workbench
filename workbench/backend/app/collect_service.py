"""后台采集引擎：注册站点（域名白名单）→ 列表提取 → 详情正文 → 脱敏去重 → 产出注册。

合规约束（服务端强制，来自 docs/skills/insurance-public-collector/references/compliance.md）：
- 域名白名单：只允许 collect_sites 注册域名的 URL，拒绝任意地址
- SSRF 防护：解析 IP 命中私网/保留/回环/链路本地即拒绝
- 限速：每次请求间隔 ≥2.5 秒；响应截断 ≤2MB；单批 ≤200 条
- 受阻即停：401/403/登录跳转/空正文标记 hindered，同栏目连续受阻 ≥3 停该栏目
- 脱敏落库：手机号/身份证/银行卡/订单保单号/住址 正则替换后才入库
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import re
import socket
from datetime import datetime
from urllib.parse import urlparse

import httpx2
import pandas as pd
from lxml import html as lhtml

from .collect_sites import Column, Site, get_site, site_of_url
from .data_service import build_profile, write_dataframe
from .db import SessionLocal
from .models import CollectTask, Dataset, User

RATE_LIMIT_SECONDS = 2.5
MAX_RESPONSE_BYTES = 2_000_000
MAX_ITEMS_HARD = 200
MIN_TITLE_LEN = 8
DETAIL_MIN_CHARS = 50
HINDERED_STREAK_STOP = 3
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36 WorkbenchCollector/1.0"
)

# 脱敏规则（移植自 docs/skills/insurance-public-collector/scripts/collect.py）
DESENSITIZE_RULES = [
    ("手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("身份证号", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
    ("银行卡号", re.compile(r"(?<!\d)\d{16,19}(?!\d)")),
    ("订单保单号", re.compile(r"(?:订单号|保单号|客户编号)[\s:：]*[A-Za-z0-9*\-]{6,}")),
    ("详细住址", re.compile(r"(?:住址|地址)[\s:：]*[^\s，,。；;]{6,}(?:号|室|栋|单元)")),
]

_DATE_RE = re.compile(r"\d{4}[-年./]\d{1,2}[-月./]\d{1,2}")
_JUNK_HREF_RE = re.compile(r"^(javascript:|#|mailto:|tel:)", re.I)
_LIST_PAGE_RE = re.compile(r"ItemList|/index\.html?$|/$", re.I)

LEDGER_COLUMNS = [
    "序号", "方向", "站点", "栏目", "标题", "发布时间", "作者/署名", "命中关键词",
    "链接", "正文摘要", "正文字数", "脱敏", "采集时间",
]


def _norm_text(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def desensitize(text: str) -> tuple[str, list[str]]:
    hits: list[str] = []
    out = text
    for label, pattern in DESENSITIZE_RULES:
        if pattern.search(out):
            hits.append(label)
            out = pattern.sub(f"[{label}已脱敏]", out)
    return out, hits


def normalize_url(value: str) -> str:
    """URL 归一（去 scheme/www/尾斜杠）用于指纹去重，移植自 collect.py。"""
    text = _norm_text(value)
    text = re.sub(r"^https?://", "", text, flags=re.I)
    text = re.sub(r"^www\.", "", text, flags=re.I)
    return text.rstrip("/")


def _host_matches(url: str, site: Site) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in site.domains)


def extract_links(html_text: str, base_url: str, column: Column, site: Site) -> list[dict]:
    """列表页启发式提取：同域 <a> + 文本标题 + 邻近日期 + url_pattern 过滤 + URL 指纹去重。"""
    try:
        doc = lhtml.fromstring(html_text)
        doc.make_links_absolute(base_url)
    except Exception:  # 解析失败（空页/编码异常）：按无候选处理
        return []
    pattern = re.compile(column.url_pattern) if column.url_pattern else None
    out: list[dict] = []
    seen: set[str] = set()
    for a in doc.xpath("//a[@href]"):
        href = (a.get("href") or "").split("#", 1)[0]
        if not href or _JUNK_HREF_RE.match(href) or not href.lower().startswith("http"):
            continue
        if not _host_matches(href, site):
            continue
        if _LIST_PAGE_RE.search(href):
            continue
        title = _norm_text(a.text_content())
        if len(title) < MIN_TITLE_LEN:
            continue
        if pattern and not pattern.search(href):
            continue
        fp = hashlib.sha1(normalize_url(href).encode("utf-8")).hexdigest()
        if fp in seen:
            continue
        seen.add(fp)
        # 日期：沿父链向上找最近文本（列表行的 td/li 常带日期）
        published = ""
        node = a.getparent()
        for _ in range(3):
            if node is None:
                break
            m = _DATE_RE.search(_norm_text(node.text_content())[:300])
            if m:
                published = m.group(0)
                break
            node = node.getparent()
        out.append({"url": href, "title": title[:120], "published_at": published})
    return out


def extract_main(html_text: str) -> str:
    """详情页正文启发式：删噪音标签后取"文本量-链接文本量"得分最高的子树。"""
    try:
        doc = lhtml.fromstring(html_text)
    except Exception:
        return ""
    for bad in doc.xpath("//script|//style|//nav|//header|//footer|//aside|//form|//noscript|//iframe"):
        parent = bad.getparent()
        if parent is not None:
            parent.remove(bad)
    best_text, best_score = "", 0.0
    for node in doc.xpath("//div|//article|//td|//section"):
        text = _norm_text(node.text_content())
        if not text:
            continue
        link_len = sum(len(_norm_text(x.text_content())) for x in node.xpath(".//a"))
        score = len(text) - link_len  # 导航/页脚链接密集区被压低
        if score > best_score:
            best_text, best_score = text, score
    if best_score < 100:  # 兜底：主体不明显时取全文
        best_text = _norm_text(doc.text_content())
    return best_text[:100_000]


def _is_hindered(status_code: int, final_url: str, main_text: str) -> str | None:
    """受阻判定：返回原因字符串；None=正常。"""
    if status_code in (401, 403, 412):
        return f"HTTP {status_code}，疑似访问受限"
    low = (final_url or "").lower()
    if any(k in low for k in ("login", "passport", "captcha", "verify")):
        return "跳转到登录/验证页"
    if len(main_text) < DETAIL_MIN_CHARS:
        return "正文为空或过短（可能为 JS 渲染页）"
    return None


async def _validate_url(url: str, site: Site) -> None:
    """白名单 + SSRF 校验；不通过抛 ValueError（受阻处理，不绕过）。"""
    if not _host_matches(url, site):
        raise ValueError("链接域名不在本站点白名单内")
    host = urlparse(url).hostname
    if not host:
        raise ValueError("无效链接")
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None)
    except socket.gaierror:
        raise ValueError("域名解析失败")
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if ip.is_private or ip.is_reserved or ip.is_loopback or ip.is_link_local:
            raise ValueError("解析到内网/保留地址，已拒绝")


async def _fetch(client: httpx2.AsyncClient, url: str) -> tuple[int, str, str]:
    resp = await client.get(url)
    content = resp.content[:MAX_RESPONSE_BYTES]
    charset = resp.charset_encoding
    if not charset:  # 头里没写就从页面 meta 嗅探（GBK 站兜底）
        m = re.search(rb'charset=["\']?([\w-]+)', content[:2048], re.I)
        charset = m.group(1).decode("ascii", "ignore") if m else "utf-8"
    try:
        text = content.decode(charset, errors="ignore")
    except (LookupError, UnicodeDecodeError):
        text = content.decode("utf-8", errors="ignore")
    return resp.status_code, str(resp.url), text


def _matched_keywords(title: str, url: str, keywords: list[str]) -> list[str]:
    return [k for k in keywords if k in title or k in url]


async def run_collect(task_id: int) -> None:
    """后台采集主流程：列表扫描（定 total）→ 逐条详情（逐条 commit 进度）→ 产出注册。"""
    with SessionLocal() as db:
        task = db.get(CollectTask, task_id)
        if task is None or task.status == "cancelled":
            return
        site = get_site(task.site_key)
        owner = db.get(User, task.owner_id)
        if site is None or owner is None:
            task.status = "failed"
            task.error = "站点未注册或用户不存在"
            db.commit()
            return

        try:
            selected = json.loads(task.columns_json or "[]")
        except json.JSONDecodeError:
            selected = []
        columns = [c for c in site.columns if c.url in selected] or list(site.columns)
        keywords = [
            _norm_text(k) for k in (json.loads(task.keywords_json or "[]")) if _norm_text(k)
        ]
        max_items = max(1, min(int(task.max_items or 20), MAX_ITEMS_HARD))

        task.status = "running"
        db.commit()

        # ---- 阶段 A：列表扫描，确定候选与 total ----
        candidates: list[dict] = []
        seen_fp: set[str] = set()
        column_notes: list[str] = []
        async with httpx2.AsyncClient(
            trust_env=False, timeout=20, follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            for col in columns:
                if len(candidates) >= max_items:
                    break
                db.refresh(task)
                if task.status == "cancelled":
                    return
                try:
                    await _validate_url(col.url, site)
                    status, final_url, text = await _fetch(client, col.url)
                    await asyncio.sleep(RATE_LIMIT_SECONDS)
                    links = extract_links(text, final_url or col.url, col, site)
                except ValueError as e:
                    column_notes.append(f"{col.name}：列表受阻（{e}）")
                    continue
                except Exception as e:  # 网络异常等：如实记录，不重试
                    column_notes.append(f"{col.name}：列表请求失败（{str(e)[:120]}）")
                    continue
                for link in links:
                    if len(candidates) >= max_items:
                        break
                    fp = hashlib.sha1(normalize_url(link["url"]).encode("utf-8")).hexdigest()
                    if fp in seen_fp:
                        continue
                    if keywords and not _matched_keywords(link["title"], link["url"], keywords):
                        continue
                    seen_fp.add(fp)
                    candidates.append({**link, "column": col.name, "direction": col.direction, "fingerprint": fp})

        task.total = len(candidates)
        task.items_json = "[]"
        db.commit()
        if not candidates:
            task.status = "failed"
            task.error = "未拿到候选条目（列表为空/关键词无匹配）" + (
                "；" + "；".join(column_notes) if column_notes else ""
            )
            db.commit()
            return

        # ---- 阶段 B：逐条详情 ----
        items: list[dict] = []
        async with httpx2.AsyncClient(
            trust_env=False, timeout=20, follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            consec_hindered = 0
            for cand in candidates:
                db.refresh(task)
                if task.status == "cancelled":
                    return
                if consec_hindered >= HINDERED_STREAK_STOP:
                    column_notes.append(f"{cand['column']}：连续受阻 {consec_hindered} 条，停止该栏目剩余条目")
                    break
                item = {
                    "url": cand["url"], "title": cand["title"],
                    "published_at": cand.get("published_at", ""),
                    "column": cand["column"], "direction": cand["direction"],
                    "keywords": _matched_keywords(cand["title"], cand["url"], keywords),
                    "content": "", "content_len": 0, "desensitized": [],
                    "fingerprint": cand["fingerprint"], "status": "pending", "error": "",
                    "collected_at": datetime.now().isoformat(timespec="seconds"),
                }
                try:
                    await _validate_url(cand["url"], site)
                    status, final_url, body = await _fetch(client, cand["url"])
                    await asyncio.sleep(RATE_LIMIT_SECONDS)
                    main = extract_main(body)
                    reason = _is_hindered(status, final_url, main)
                    if reason:
                        item["status"], item["error"] = "hindered", reason
                        task.hindered += 1
                        consec_hindered += 1
                    else:
                        content, hits = desensitize(main)
                        item.update(
                            status="success", content=content,
                            content_len=len(content), desensitized=hits,
                        )
                        consec_hindered = 0
                except ValueError as e:
                    item["status"], item["error"] = "hindered", str(e)
                    task.hindered += 1
                    consec_hindered += 1
                except Exception as e:
                    item["status"], item["error"] = "failed", str(e)[:200]
                    task.failed += 1
                items.append(item)
                task.items_json = json.dumps(items, ensure_ascii=False)
                task.done = len(items)
                task.total = max(task.total, len(items))
                db.commit()

        if task.status == "cancelled":
            return
        task.total = len(items)  # 早停时 total 对齐已处理条数

        ok_items = [i for i in items if i["status"] == "success" and i.get("content")]
        if not ok_items:
            task.status = "failed"
            task.error = "全部条目受阻或失败，未产出可用数据" + (
                "；" + "；".join(column_notes) if column_notes else ""
            )
            db.commit()
            return

        try:
            await _register_outputs(db, task, site, ok_items)
            task.status = "success"
            if column_notes:
                task.error = "；".join(column_notes)[:1000]
        except Exception as e:
            task.status = "failed"
            task.error = f"产出注册失败：{str(e)[:300]}"
        db.commit()


async def _register_outputs(db, task: CollectTask, site: Site, ok_items: list[dict]) -> None:
    """产出注册（身份=任务 owner，与平台权限一致）：台账→数据集；可选→知识库文档。"""
    now = datetime.now().isoformat(timespec="seconds")
    rows = []
    for idx, i in enumerate(ok_items, start=1):
        rows.append({
            "序号": f"{idx:04d}", "方向": i["direction"], "站点": site.name, "栏目": i["column"],
            "标题": i["title"], "发布时间": i.get("published_at", ""), "作者/署名": "",
            "命中关键词": ";".join(i.get("keywords", [])), "链接": i["url"],
            "正文摘要": i["content"][:200], "正文字数": i["content_len"],
            "脱敏": "已脱敏（" + "、".join(i["desensitized"]) + "）" if i["desensitized"] else "无命中",
            "采集时间": i.get("collected_at") or now,
        })
    df = pd.DataFrame(rows, columns=LEDGER_COLUMNS)
    ds = Dataset(
        owner_id=task.owner_id, scope="personal",
        name=f"采集台账·{task.name}"[:128], filename=f"{task.name}.csv",
        table_name="ds_pending", folder="采集", row_count=len(df),
        profile_json=json.dumps(build_profile(df), ensure_ascii=False),
        semantic_json="{}",
    )
    db.add(ds)
    db.flush()
    ds.table_name = f"ds_{ds.id}"
    write_dataframe(ds.table_name, df)
    task.dataset_id = ds.id

    if task.to_kb:
        from .routers.kb import _ingest

        keywords = ";".join(_norm_text(k) for k in json.loads(task.keywords_json or "[]"))
        parts = [
            f"# 采集·{task.name}", "",
            f"- 站点：{site.name}（{site.key}）",
            f"- 栏目：{'、'.join(sorted({i['column'] for i in ok_items}))}",
            f"- 关键词：{keywords or '（未指定，全量）'}",
            f"- 条数：{len(ok_items)}（受阻 {task.hindered} 条）",
            f"- 采集时间：{now}", "",
        ]
        for i in ok_items:
            parts += [
                f"## {i['title']}", "",
                f"- 来源：{site.name} / {i['column']}",
                f"- 链接：{i['url']}",
                f"- 发布时间：{i.get('published_at') or '未知'}",
                f"- 命中关键词：{';'.join(i.get('keywords', [])) or '—'}",
                f"- 脱敏：{'已处理（' + '、'.join(i['desensitized']) + '）' if i['desensitized'] else '无命中'}", "",
                (i["content"][:500] + ("…" if len(i["content"]) > 500 else "")), "",
                f"[阅读原文]({i['url']})", "",
            ]
        doc = await _ingest(
            db, db.get(User, task.owner_id), "personal",
            title=f"采集·{task.name}",
            path=f"原始资料/采集/{task.name}.md",
            text="\n".join(parts),
        )
        task.kb_doc_id = doc.id
        task.kb_doc_title = doc.title
    db.commit()
