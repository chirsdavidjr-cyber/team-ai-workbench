"""采集站点注册表（域名白名单 + 实测栏目）。

来源：workbench/docs/skills/insurance-public-collector/references/site-registry.md（2026-11 实测，均无需登录）。
规则：只登记实测过"不登录可见"的公开栏目；新站点先实测再登记。
url_pattern 为 None 时用引擎默认启发式（同域、非列表页自身、文本长度达标）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Column:
    url: str  # 栏目列表页入口
    name: str  # 栏目名
    direction: str  # 行业/监管/同业/药监/医保/...
    url_pattern: str | None = None  # 详情链接过滤正则（None=默认启发式）


@dataclass
class Site:
    key: str
    name: str
    domains: list[str] = field(default_factory=list)  # 允许的域名（含子域匹配）
    columns: list[Column] = field(default_factory=list)
    note: str = ""
    static_fetch: bool = True  # 平台静态抓取可用性（False=需浏览器渲染/有WAF，走外部执行环境）


SITES: list[Site] = [
    Site(
        key="nfra",
        name="国家金融监督管理总局",
        domains=["nfra.gov.cn"],
        static_fetch=False,
        columns=[
            Column(
                url=(
                    "https://www.nfra.gov.cn/cn/view/pages/ItemList.html"
                    "?itemPId=923&itemId=925&itemUrl=ItemListRightList.html&itemName=公告通知"
                ),
                name="公告通知",
                direction="监管",
                url_pattern=r"ItemContent|ItemDetail|/gov/",
            ),
        ],
        note="列表为 Angular 动态渲染，平台静态采集拿不到条目；用 WorkBuddy/Trae 等带浏览器的执行环境采集",
    ),
    Site(
        key="nmpa",
        name="国家药品监督管理局",
        domains=["nmpa.gov.cn"],
        static_fetch=False,
        columns=[
            Column(url="https://www.nmpa.gov.cn/xxgk/ggtg/index.html", name="公告通告", direction="药监"),
            Column(url="https://www.nmpa.gov.cn/xxgk/fgwj/index.html", name="法规文件", direction="药监"),
        ],
        note="服务端直连被 WAF 拦截（412，实测 2026-11-22），按受阻即停不绕过；用带浏览器的执行环境采集",
    ),
    Site(
        key="nhsa",
        name="国家医疗保障局",
        domains=["nhsa.gov.cn"],
        static_fetch=False,
        columns=[
            Column(url="https://www.nhsa.gov.cn/col/col172/index.html", name="政策", direction="医保"),
            Column(url="https://www.nhsa.gov.cn/col/col109/index.html", name="通知公告", direction="医保"),
        ],
        note="列表由 JS 动态加载，平台静态采集拿不到条目；用带浏览器的执行环境采集",
    ),
    Site(
        key="iachina",
        name="中国保险行业协会",
        domains=["iachina.cn"],
        columns=[
            Column(url="https://www.iachina.cn/", name="首页要闻", direction="行业"),
        ],
        note="静态直读可用（平台实测 2026-11-22，3/3 成功）",
    ),
    Site(
        key="pingan",
        name="平安产险",
        domains=["property.pingan.com"],
        columns=[
            Column(url="https://property.pingan.com/", name="公示与产品", direction="同业"),
        ],
        note="静态直读可用（平台实测 2026-11-22，公示内容可提取）",
    ),
    Site(
        key="epicc",
        name="中国人保财险",
        domains=["epicc.com.cn", "picc.com.cn"],  # epicc.com.cn 会重定向到集团站 picc.com.cn
        columns=[
            Column(url="https://www.epicc.com.cn/", name="新闻中心", direction="同业"),
        ],
        note="静态直读可用；入口重定向到 picc.com.cn 集团站（平台实测 2026-11-22）",
    ),
]

_DEFAULT_JUNK = re.compile(r"javascript:|^#|^mailto:|^tel:")


def get_site(key: str) -> Site | None:
    k = (key or "").strip().lower()
    return next((s for s in SITES if s.key == k), None)


def site_of_url(url: str) -> Site | None:
    """域名反查站点（引擎抓详情前的白名单校验用）。"""
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    if not host:
        return None
    for s in SITES:
        for d in s.domains:
            if host == d or host.endswith("." + d):
                return s
    return None


def sites_payload() -> list[dict]:
    """给前端表单 / agent 动作用的站点清单。"""
    return [
        {
            "key": s.key,
            "name": s.name,
            "note": s.note,
            "static_fetch": s.static_fetch,
            "columns": [
                {"url": c.url, "name": c.name, "direction": c.direction} for c in s.columns
            ],
        }
        for s in SITES
    ]
