#!/usr/bin/env python3
"""台账生成器：从批次产出生成明细台账、采集总台账、配方六段。

用法：
  python3 ledger.py --output-dir /path/to/output --batch-id 20261120_监管_001 \
    --keywords "保险公司;交强险" --time-range "近30天" --fetch "WebFetch" \
    --status 完成 --delay 2 --max 200
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path

DETAIL_HEADER = ["序号", "批次号", "方向", "站点", "栏目", "标题", "发布时间", "作者/署名", "原文链接", "命中关键词", "正文摘要", "正文字数", "脱敏标记", "采集时间", "本地存档"]
MASTER_HEADER = ["批次号", "采集日期", "方向", "站点", "栏目", "关键词", "时间范围", "抓取方式", "输入条数", "保留条数", "去重条数", "受阻条数", "脱敏", "输出目录", "状态", "备注"]


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
            if isinstance(item, dict):
                out.append(item)
        except json.JSONDecodeError:
            continue
    return out


def kw_string(item: dict) -> str:
    kw = item.get("keywords", [])
    return ";".join(str(x) for x in kw) if isinstance(kw, list) else str(kw or "")


def main() -> int:
    parser = argparse.ArgumentParser(description="生成采集台账")
    parser.add_argument("--output-dir", required=True, help="输出根目录，与 collect.py 一致")
    parser.add_argument("--batch-id", default="", help="批次目录名；留空则取最新批次")
    parser.add_argument("--keywords", default="")
    parser.add_argument("--time-range", default="近30天")
    parser.add_argument("--fetch", default="WebFetch")
    parser.add_argument("--status", default="完成")
    parser.add_argument("--delay", type=float, default=2)
    parser.add_argument("--max", type=int, default=200)
    parser.add_argument("--note", default="")
    args = parser.parse_args()

    root = Path(args.output_dir).expanduser().resolve()
    batch_root = root / "批次"
    if args.batch_id:
        batch = batch_root / args.batch_id
    else:
        dirs = sorted([d for d in batch_root.iterdir() if d.is_dir()], key=lambda p: p.stat().st_mtime) if batch_root.exists() else []
        if not dirs:
            print("没有找到批次目录", file=sys.stderr)
            return 2
        batch = dirs[-1]

    if not batch.exists():
        print(f"批次目录不存在：{batch}", file=sys.stderr)
        return 2

    manifest_path = batch / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    items = read_jsonl(batch / "items.jsonl")
    batch_id = manifest.get("batch_id", batch.name)
    direction = manifest.get("direction", "自定义")
    site = manifest.get("site", "")
    column = manifest.get("column", "")

    # 明细台账
    detail_rows = []
    for idx, item in enumerate(items, 1):
        content = str(item.get("content", "") or "")
        detail_rows.append([
            f"{idx:04d}", batch_id, item.get("direction", direction), item.get("source", site),
            item.get("column", column), item.get("title", ""), item.get("published_at", ""),
            item.get("author", ""), item.get("url", ""), kw_string(item), content[:200],
            str(len(content)), "已处理" if manifest.get("desensitized", True) else "未处理",
            item.get("collected_at", ""), item.get("archive_file", f"原文存档/{idx:04d}.md"),
        ])
    with (batch / "明细台账.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(DETAIL_HEADER)
        writer.writerows(detail_rows)

    # 总台账（追加，去重按批次号）
    master_path = root / "采集总台账.csv"
    existing: dict[str, list[str]] = {}
    if master_path.exists():
        with master_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            next(reader, None)
            for row in reader:
                if row:
                    existing[row[0]] = row
    master_row = [
        batch_id,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        direction,
        site,
        column,
        args.keywords,
        args.time_range,
        args.fetch,
        str(manifest.get("total_input", len(items))),
        str(manifest.get("kept", len(items))),
        str(manifest.get("skipped_duplicate", 0)),
        str(len(manifest.get("blocked", []))),
        "是" if manifest.get("desensitized", True) else "否",
        str(batch),
        args.status,
        args.note or ";".join(manifest.get("blocked", [])),
    ]
    existing[batch_id] = master_row
    with master_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(MASTER_HEADER)
        for key in sorted(existing.keys()):
            writer.writerow(existing[key])

    # 配方六段
    recipe = [
        f"# 批次配方 {batch_id}",
        "",
        f"采集方向：{direction} / 主题：{manifest.get('theme', '')}",
        "",
        "站点与栏目：",
        f"- {site} / {column or '首页'}",
        "",
        f"关键词：{args.keywords or '（见 keyword-bank.md 对应方向）'}",
        "",
        "字段：标题、正文全文、发布时间、作者/署名、原文链接、命中关键词、来源栏目、采集时间",
        "",
        f"工具与版本：{args.fetch} / collector v1",
        "",
        f"时间与限速：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}；间隔 {args.delay} 秒；单站上限 {args.max} 条",
        "",
        f"备注：{args.note or '无'}；脱敏：{'是' if manifest.get('desensitized', True) else '否'}"
        + (f"；脱敏类型：{','.join(manifest.get('desensitized_types', []))}" if manifest.get("desensitized_types") else ""),
        "",
    ]
    (batch / "配方.md").write_text("\n".join(recipe), encoding="utf-8")

    # 当前区最新汇总
    current_dir = root / "当前"
    current_dir.mkdir(parents=True, exist_ok=True)
    latest = current_dir / f"{direction}_latest.csv"
    existing_latest: list[list[str]] = []
    if latest.exists():
        with latest.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            next(reader, None)
            existing_latest = [row for row in reader if row]
    with latest.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(DETAIL_HEADER)
        writer.writerows(existing_latest + detail_rows)

    print(json.dumps({
        "batch_id": batch_id,
        "detail_rows": len(detail_rows),
        "master_rows": len(existing),
        "files": ["明细台账.csv", "配方.md", str(master_path), str(latest)],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main())
