"""知识库检索 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...deps import get_current_user, get_db
from ...kb_service import search_kb
from ...models import User

router = APIRouter()

async def search(
    body: dict,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """知识库检索（前端预览用；对话内检索由 chat 流程内部调用 kb_service）。"""
    from ..kb_service import search_kb

    query = str(body.get("query", "")).strip()
    if not query:
        raise HTTPException(status_code=400, detail="query 不能为空")
    scope = body.get("scope") or "all"
    top_k = min(int(body.get("top_k", 5)), 10)
    return await search_kb(db, user.id, query, scope, top_k)


# ---------- 文件与文件夹管理 ----------

