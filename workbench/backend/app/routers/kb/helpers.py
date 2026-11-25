"""入库/索引辅助。"""

from __future__ import annotations

import json
import secrets
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...kb_service import chunk_markdown, embed_texts
from ...llm import client_for
from ...models import KbChunk, KbDoc, KbLink, Provider, User
from .common import TAG_RE, UPLOAD_DIR, WIKI_LINK_RE, _doc_out
from .extract import extract_file

def _save_source(doc_id: int, filename: str, data: bytes) -> str:
    """保存原文件到 uploads/，返回相对路径。"""
    safe = re.sub(r"[^\w.\u4e00-\u9fff-]+", "_", filename)[:120] or "file"
    rel = f"{doc_id}_{secrets.token_hex(4)}_{safe}"
    (UPLOAD_DIR / rel).write_bytes(data)
    return rel



def _fix_zip_name(name: str) -> str:
    """修复 zip 中文文件名：macOS/Windows 打包常不设 UTF-8 flag，zipfile 按 cp437 解成乱码。"""
    if all(ord(c) < 128 for c in name):
        return name
    for enc in ("utf-8", "gbk"):
        try:
            return name.encode("cp437").decode(enc)
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    return name


def _parse_meta(content: str) -> tuple[list[str], list[str]]:
    """解析 [[双链目标标题]] 与 #标签。"""
    links = []
    for m in WIKI_LINK_RE.finditer(content):
        t = m.group(1).strip()
        if t and t not in links:
            links.append(t)
    tags = []
    for m in TAG_RE.finditer(content):
        t = m.group(1).strip()
        if t and t not in tags:
            tags.append(t)
    return links, tags



async def _reindex_doc(db: Session, doc: KbDoc) -> None:
    """重建切块、向量与双链关系。draft（未入个人库）文档只更新元数据不索引。"""
    db.query(KbChunk).where(KbChunk.doc_id == doc.id).delete()
    if doc.status != "draft":
        chunks = chunk_markdown(doc.content)
        vectors = await embed_texts(chunks)
        for i, text in enumerate(chunks):
            db.add(
                KbChunk(
                    doc_id=doc.id,
                    idx=i,
                    text=text,
                    embedding_json=(json.dumps(vectors[i]) if vectors else None),
                )
            )
        doc.chunk_count = len(chunks)
        doc.status = "ready"
    else:
        doc.chunk_count = 0

    # 双链：目标存在则回填 target_doc_id（目标须在本文档作者可见范围内）
    db.query(KbLink).where(KbLink.source_id == doc.id).delete()
    link_titles, tags = _parse_meta(doc.content)
    doc.tags_json = json.dumps(tags, ensure_ascii=False)
    for t in link_titles:
        target = db.scalar(
            select(KbDoc).where(
                KbDoc.title == t,
                or_(
                    KbDoc.scope == "team",
                    KbDoc.team_status == "approved",
                    KbDoc.owner_id == doc.owner_id,
                ),
            )
        )
        db.add(
            KbLink(
                source_id=doc.id,
                target_title=t,
                target_doc_id=target.id if target else None,
            )
        )
    # 状态只在上方非 draft 分支设置；draft（未入个人库）保持 draft，避免"移出个人库"被覆盖失效
    doc.size = len(doc.content.encode("utf-8"))



async def _ingest(
    db: Session, user: User, scope: str, title: str, path: str, text: str,
    source_name: str | None = None, source_data: bytes | None = None,
) -> KbDoc:
    doc = KbDoc(owner_id=user.id, scope=scope, title=title, path=path, content=text)
    db.add(doc)
    db.flush()
    if source_data is not None and source_name:
        try:
            doc.source_file = _save_source(doc.id, source_name, source_data)
        except OSError:
            doc.source_file = ""  # 存盘失败不阻断入库
    await _reindex_doc(db, doc)
    return doc


async def _gen_summary_bg(doc_id: int) -> None:
    """后台生成 AI 摘要与智能标签（失败静默，不影响上传速度）。"""
    from ..db import SessionLocal
    from ..kb_service import _kb_llm

    with SessionLocal() as db:
        doc = db.get(KbDoc, doc_id)
        if doc is None or not doc.content.strip():
            return
        try:
            raw = await _kb_llm(
                db,
                "你是知识管理员。阅读文档，仅返回 JSON："
                '{"summary":"100字以内中文摘要","tags":["3-5个智能分类标签（词组，不带#）"]}。'
                "摘要提炼核心内容与适用场景；标签用于归类检索。",
                f"标题：{doc.title}\n\n{doc.content[:4000]}",
            )
            m = re.search(r"\{.*\}", raw, re.S)
            if not m:
                return
            parsed = json.loads(m.group(0))
            summary = str(parsed.get("summary", "")).strip()
            ai_tags = [str(t).strip().lstrip("#") for t in parsed.get("tags", []) if str(t).strip()][:5]
            if summary:
                doc.summary = summary
            if ai_tags:
                merged = list(dict.fromkeys(doc.tags + ai_tags))
                doc.tags_json = json.dumps(merged, ensure_ascii=False)
            db.commit()
        except Exception:
            return  # 摘要失败不影响文档本身


