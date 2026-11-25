"""数据图谱（数据集 ↔ 字段）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ...deps import get_current_user, get_db
from ...models import Dataset, User

router = APIRouter()

@router.get("/graph")
def data_graph(
    scope: str = "all",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """图谱数据：节点=数据集/字段（有语义定义高亮），边=数据集含字段、跨数据集同名字段。"""
    from sqlalchemy import or_

    stmt = select(Dataset).where(
        or_(Dataset.owner_id == user.id, Dataset.scope == "team")
    )
    if scope == "personal":
        stmt = stmt.where(Dataset.scope == "personal", Dataset.owner_id == user.id)
    elif scope == "team":
        stmt = stmt.where(Dataset.scope == "team")
    rows = db.scalars(stmt.order_by(Dataset.id.desc()).limit(200)).all()

    nodes: list[dict] = []
    edges: list[dict] = []
    for ds in rows:
        nodes.append({
            "id": f"ds_{ds.id}", "kind": "dataset", "title": ds.name,
            "scope": ds.scope, "rows": ds.row_count,
        })
        sem = ds.semantic or {}
        defined = set((sem.get("columns") or {}).keys())
        for c in ds.profile:
            fname = c["name"]
            fid = f"f_{fname}"
            # 字段节点只加一次：同名字段跨数据集共享同一个节点，即天然形成跨表关联
            if not any(n["id"] == fid for n in nodes):
                nodes.append({
                    "id": fid, "kind": "field", "title": fname,
                    "defined": fname in defined,
                    "dtype": c["dtype"],
                })
            edges.append({"source": f"ds_{ds.id}", "target": fid})
    return {"nodes": nodes, "edges": edges}

