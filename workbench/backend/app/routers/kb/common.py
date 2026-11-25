"""知识库路由共享常量与工具。"""

from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ...models import KbDoc, KbFolder, User

UPLOAD_DIR = Path(__file__).resolve().parent.parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

TEXT_EXTS = (".md", ".markdown", ".txt")
DOC_EXTS = (".pdf", ".docx", ".pptx", ".doc", ".xls")
SUPPORTED_EXTS = TEXT_EXTS + DOC_EXTS
PLAIN_EXTS = (
    ".csv", ".tsv", ".log", ".json", ".xml", ".yaml", ".yml", ".toml", ".ini", ".conf",
    ".py", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".java", ".c", ".h", ".cpp", ".hpp",
    ".cs", ".go", ".rs", ".rb", ".php", ".sh", ".bat", ".sql", ".css", ".scss", ".vue",
    ".swift", ".kt", ".r", ".m",
)
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")

IGNORE_DIRS = {".git", ".obsidian", "__MACOSX", "node_modules", ".trash", ".Trash"}
WIKI_LINK_RE = re.compile(r"\[\[([^\]\|]+)(?:\|[^\]]+)?\]\]")
TAG_RE = re.compile(r"(?<!\w)#([\w\u4e00-\u9fff/-]+)")

WORKFLOW_FOLDERS = ["收件箱", "原始资料", "来源台账", "知识库"]
INGEST_CATEGORIES = ["文章", "论文报告", "书籍摘录", "视频转录", "会议记录", "灵感摘录", "数据表格"]


def _folder_scope_filter(scope: str, user_id: int):
    if scope == "team":
        return KbFolder.scope == "team"
    return and_(KbFolder.scope == "personal", KbFolder.owner_id == user_id)


def _visible_filter(user_id: int, scope: str | None = None):
    if scope == "team":
        return or_(KbDoc.scope == "team", KbDoc.team_status == "approved")
    if scope == "personal":
        return and_(KbDoc.scope == "personal", KbDoc.owner_id == user_id)
    return or_(
        KbDoc.scope == "team",
        KbDoc.team_status == "approved",
        and_(KbDoc.owner_id == user_id, KbDoc.scope == "personal"),
    )


def _doc_out(d: KbDoc, link_count: int | None = None) -> dict:
    return {
        "id": d.id,
        "title": d.title,
        "path": d.path,
        "scope": d.scope,
        "team_status": d.team_status,
        "owner_id": d.owner_id,
        "size": d.size,
        "tags": d.tags,
        "summary": d.summary,
        "has_source_file": bool(d.source_file),
        "chunk_count": d.chunk_count,
        "status": d.status,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "updated_at": d.updated_at.isoformat() if d.updated_at else None,
        "link_count": link_count,
    }



def _clean_title(filename: str) -> str:
    return re.sub(r"\.(md|markdown|txt|pdf|docx)$", "", filename, flags=re.I)[:120] or "未命名"


def _guard_skeleton(*paths: str) -> None:
    """只锁定四个一级分区本身（收件箱/原始资料/知识库/来源台账）；其下子层级任由用户增删改移。"""
    locked = {"收件箱", "原始资料", "知识库", "来源台账"}
    for p in paths:
        if p.strip("/") in locked:
            raise HTTPException(
                status_code=403,
                detail=f"「{p}」是知识工作流的固定分区，不可修改；分区内的子文件夹可自由整理",
            )


def _get_editable_doc(doc_id: int, user: User, db: Session) -> KbDoc:
    d = db.get(KbDoc, doc_id)
    if d is None or (d.scope == "personal" and d.owner_id != user.id):
        raise HTTPException(status_code=404, detail="文档不存在")
    if d.scope == "team" and user.role != "admin" and d.owner_id != user.id:
        raise HTTPException(status_code=403, detail="仅创建者或管理员可操作该文档")
    return d



