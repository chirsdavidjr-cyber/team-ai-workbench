#!/usr/bin/env python3
"""公开网页采集落盘：脱敏、去重、原文存档、汇总 CSV、manifest。

安全边界：不联网、不执行外部命令、不删文件，只读写 --output-dir。
限速由抓取端控制，本脚本 --delay 仅用于写盘节流，默认 0。

用法：
  python3 collect.py --input items.jsonl --output-dir /path/to/output \
    --site 站点名 --direction 方向 --theme 主题 --column 栏目
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

FIELDS = ["source", "column", "url", "title", "content", "published_at", "author", "keywords", "direction", "collected_at"]
CSV_HEADER = ["序号", "方向", "站点", "栏目", "标题", "发布时间", "作者/署名", "命中关键词", "链接", "正文摘要", "正文字数", "脱敏", "采集时间", "本地存档"]

# 脱敏规则：按顺序替换，记录命中类型
DESENSITIZE_RULES = [
    ("手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("身份证号", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
    ("银行卡号", re.compile(r"(?<!\d)\d{16,19}(?!\d)")),
    ("订单保单号", re.compile(r"(?:订单号|保单号|客户编号)[\s:：]*[A-Za-z0-9*\-]{6,}")),
    ("详细住址", re.compile(r"(?:住址|地址)[\s:：]*[^\s，,。；;]{6,}(?:号|室|栋|单元)")),
]


def norm(value: object) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def desensitize(text: str) -> tuple[str, list[str]]:
    hits: list[str] = []
    out = text
    for label, pattern in DESENSITIZE_RULES:
        if pattern.search(out):
            hits.append(label)
            out = pattern.sub(f"[{label}已脱敏]", out)
    return out, hits


def normalize_url(value: object) -> str:
    text = norm(value)
    text = re.sub(r"^https?://", "", text, flags=re.I)
    text = re.sub(r"^www\.", "", text, flags=re.I)
    return text.rstrip("/")


def fingerprint(item: dict) -> tuple[str, str]:
    url_fp = hashlib.sha1(normalize_url(item.get("url", "")).encode("utf-8")).hexdigest()
    content_fp = hashlib.sha1(norm(item.get("content", "")).encode("utf-8")).hexdigest()
    return url_fp, content_fp


def safe_name(value: str, fallback: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]', "_", norm(value))
    return cleaned[:40] or fallback


def load_seen(path: Path) -> dict:
    if not path.exists():
        return {"urls": {}, "contents": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {"urls": data.get("urls", {}), "contents": data.get("contents", {})}
    except (json.JSONDecodeError, OSError):
        return {"urls": {}, "contents": {}}


def keywords_of(item: dict) -> str:
    kw = item.get("keywords", [])
    if isinstance(kw, list):
        return ";".join(str(x) for x in kw)
    return norm(kw)


def archive_filename(title: str, index: int) -> str:
    """用标题做文件名：清洗非法字符、压缩空白、限长，保留辨识度。"""
    name = norm(title)
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    name = name[:60] or f"无标题{index:04d}"
    return f"{name}.md"


def write_archive(directory: Path, index: int, item: dict, desens_label: str) -> str:
    name = archive_filename(norm(item.get("title")), index)
    # 同名冲突时加序号，不覆盖
    if (directory / name).exists():
        stem = name[:-3]
        seq = 2
        while (directory / f"{stem}_{seq}.md").exists():
            seq += 1
        name = f"{stem}_{seq}.md"
    body = norm(item.get("content", "")) or "（无正文）"
    lines = [
        f"# {norm(item.get('title')) or '无标题'}",
        "",
        f"- 来源：{norm(item.get('source'))} / {norm(item.get('column'))}",
        f"- 链接：{norm(item.get('url'))}",
        f"- 发布时间：{norm(item.get('published_at')) or '未标注'}",
        f"- 作者/署名：{norm(item.get('author')) or '未标注'}",
        f"- 命中关键词：{keywords_of(item) or '未标注'}",
        f"- 采集时间：{norm(item.get('collected_at'))}",
        f"- 脱敏：{desens_label}",
        "",
        "## 正文",
        "",
        body,
        "",
    ]
    (directory / name).write_text("\n".join(lines), encoding="utf-8")
    return name


def main() -> int:
    parser = argparse.ArgumentParser(description="公开网页采集落盘")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--site", default="未命名站点")
    parser.add_argument("--column", default="")
    parser.add_argument("--direction", default="自定义")
    parser.add_argument("--theme", default="未命名主题")
    parser.add_argument("--delay", type=float, default=0.0)
    parser.add_argument("--max", type=int, default=200)
    parser.add_argument("--no-desensitize", action="store_true", help="默认脱敏；仅在确认无隐私时用")
    parser.add_argument("--blocked", action="append", default=[])
    args = parser.parse_args()

    source_path = Path(args.input).expanduser().resolve()
    output_root = Path(args.output_dir).expanduser().resolve()
    if not source_path.exists():
        print(f"输入文件不存在：{source_path}", file=sys.stderr)
        return 2

    stamp = datetime.now().strftime("%Y%m%d")
    label = f"{stamp}_{safe_name(args.direction, 'dir')}_{safe_name(args.theme, 'theme')}"
    target = output_root / "批次" / label
    archive_dir = target / "原文存档"
    archive_dir.mkdir(parents=True, exist_ok=True)

    seen = load_seen(output_root / ".collector_seen.json")
    kept: list[dict] = []
    skipped_duplicate = 0
    skipped_bad = 0
    total_input = 0
    desensitized_count = 0
    desens_types: set[str] = set()

    for raw in source_path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        total_input += 1
        if len(kept) >= args.max:
            break
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            skipped_bad += 1
            continue
        if not isinstance(item, dict):
            skipped_bad += 1
            continue
        if not norm(item.get("url")) or not norm(item.get("content")):
            skipped_bad += 1
            continue

        url_fp, content_fp = fingerprint(item)
        if url_fp in seen["urls"] or content_fp in seen["contents"]:
            skipped_duplicate += 1
            continue

        item.setdefault("source", args.site)
        item.setdefault("column", args.column)
        item.setdefault("direction", args.direction)
        item.setdefault("collected_at", datetime.now().isoformat(timespec="seconds"))

        if not args.no_desensitize:
            clean, hits = desensitize(norm(item.get("content", "")))
            item["content"] = clean
            if hits:
                desensitized_count += 1
                desens_types.update(hits)
        for key in FIELDS:
            item.setdefault(key, "")
        kept.append(item)
        seen["urls"][url_fp] = stamp
        seen["contents"][content_fp] = stamp
        if args.delay > 0:
            time.sleep(args.delay)

    rows: list[list[str]] = []
    for index, item in enumerate(kept, 1):
        content = norm(item.get("content", ""))
        archive_name = write_archive(archive_dir, index, item, "已处理" if not args.no_desensitize else "未处理")
        item["archive_file"] = f"原文存档/{archive_name}"
        rows.append([
            str(index), norm(item.get("direction")), norm(item.get("source")), norm(item.get("column")),
            norm(item.get("title")), norm(item.get("published_at")), norm(item.get("author")),
            keywords_of(item), norm(item.get("url")), content[:200], str(len(content)),
            "是" if not args.no_desensitize else "否", norm(item.get("collected_at")), f"原文存档/{archive_name}",
        ])

    with (target / "items.jsonl").open("w", encoding="utf-8") as handle:
        for item in kept:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    with (target / "summary.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_HEADER)
        writer.writerows(rows)

    manifest = {
        "batch_id": label,
        "site": args.site,
        "column": args.column,
        "direction": args.direction,
        "theme": args.theme,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "fields": FIELDS,
        "total_input": total_input,
        "kept": len(kept),
        "skipped_duplicate": skipped_duplicate,
        "skipped_bad": skipped_bad,
        "desensitized": not args.no_desensitize,
        "desensitized_count": desensitized_count,
        "desensitized_types": sorted(desens_types),
        "blocked": [norm(x) for x in args.blocked if norm(x)],
        "output_dir": str(target),
    }
    (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output_root / ".collector_seen.json").write_text(json.dumps(seen, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({"batch_id": label, "output_dir": str(target), **{k: manifest[k] for k in
          ("total_input", "kept", "skipped_duplicate", "skipped_bad", "desensitized_count", "desensitized_types", "blocked")}},
          ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
