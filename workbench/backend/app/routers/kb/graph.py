"""知识图谱 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...deps import get_current_user, get_db
from ...models import KbDoc, KbLink, User
from .common import _visible_filter

router = APIRouter()

@router.get("/graph")
def graph(
    scope: str = "all",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """图谱数据：节点=文档，边=双链。前端力导向渲染（限 500 文档防爆）。"""
    stmt = (
        select(KbDoc)
        .where(KbDoc.status == "ready")
        .where(_visible_filter(user))
        .options(
            load_only(
                KbDoc.id, KbDoc.title, KbDoc.scope, KbDoc.chunk_count,
                KbDoc.owner_id, KbDoc.team_status,
            )
        )
    )
    if scope == "personal":
        stmt = stmt.where(KbDoc.scope == "personal", KbDoc.owner_id == user.id)
    elif scope == "team":
        stmt = stmt.where(or_(KbDoc.scope == "team", KbDoc.team_status == "approved"))
    docs = db.scalars(stmt.limit(500)).all()
    id_set = {d.id for d in docs}
    links = db.scalars(
        select(KbLink).where(KbLink.source_id.in_(id_set))
    ).all()
    return {
        "nodes": [
            {"id": d.id, "title": d.title, "scope": d.scope, "chunk_count": d.chunk_count}
            for d in docs
        ],
        "edges": [
            {"source": l.source_id, "target": l.target_doc_id, "title": l.target_title}
            for l in links
        ],
    }


# ---------------------------------------------------------------- 知识库管家
# 供对话中的 AI 指令调用：把「当前库结构」喂给模型、执行模型给出的文件操作。



