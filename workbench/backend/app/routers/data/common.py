"""数据集路由共享工具。"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session
from sqlalchemy import select

from ...models import Dataset, DatasetFolder, User


def _ds_out(ds: Dataset) -> dict:
    return {
        "id": ds.id,
        "name": ds.name,
        "filename": ds.filename,
        "scope": ds.scope,
        "folder": ds.folder or "",
        "row_count": ds.row_count,
        "profile": ds.profile,
        "semantic": ds.semantic,
        "status": ds.status,
        "summary": ds.summary or "",
        "team_status": ds.team_status or "",
        "created_at": ds.created_at.isoformat() if ds.created_at else None,
    }


def _own_ds(ds_id: int, user: User, db: Session, need_admin_for_team: bool = False) -> Dataset:
    ds = db.get(Dataset, ds_id)
    if ds is None:
        raise HTTPException(status_code=404, detail="数据集不存在")
    if ds.scope == "personal" and ds.owner_id != user.id:
        raise HTTPException(status_code=404, detail="数据集不存在")
    if need_admin_for_team and ds.scope == "team" and user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return ds


def _visible_datasets(user: User, db: Session) -> list[Dataset]:
    return db.scalars(
        select(Dataset)
        .where(or_(Dataset.owner_id == user.id, Dataset.scope == "team"))
        .order_by(Dataset.id)
    ).all()


def _folder_scope_filter(scope: str, user: User):
    if scope == "team":
        return DatasetFolder.scope == "team"
    return and_(DatasetFolder.scope == "personal", DatasetFolder.owner_id == user.id)


def _ds_scope_filter(scope: str, user: User):
    if scope == "team":
        return Dataset.scope == "team"
    return and_(Dataset.scope == "personal", Dataset.owner_id == user.id)



