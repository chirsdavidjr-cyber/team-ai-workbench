"""数据集分组（文件夹）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from ...deps import get_current_user, get_db
from ...models import Dataset, DatasetFolder, User
from .common import _ds_out, _ds_scope_filter, _folder_scope_filter, _own_ds

router = APIRouter()

class FolderIn(BaseModel):
    path: str
    scope: str = "personal"


@router.get("/folders")
def list_folders(
    scope: str = "personal",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = db.scalars(
        select(DatasetFolder)
        .where(_folder_scope_filter(scope if scope in ("personal", "team") else "personal", user))
        .order_by(DatasetFolder.sort, DatasetFolder.path)
    ).all()
    return [{"id": f.id, "path": f.path, "scope": f.scope, "sort": f.sort} for f in rows]


@router.post("/folders")
def create_folder(
    body: FolderIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    scope = body.scope if body.scope in ("personal", "team") else "personal"
    if scope == "team" and user.role != "admin":
        raise HTTPException(status_code=403, detail="团队分组需要管理员创建")
    path = body.path.strip().strip("/")
    if not path:
        raise HTTPException(status_code=400, detail="分组路径不合法")
    exists = db.scalar(
        select(DatasetFolder).where(DatasetFolder.path == path, _folder_scope_filter(scope, user))
    )
    if not exists:
        max_sort = db.scalar(
            select(func.max(DatasetFolder.sort)).where(_folder_scope_filter(scope, user))
        )
        db.add(DatasetFolder(owner_id=user.id, scope=scope, path=path, sort=(max_sort or 0) + 1))
        db.commit()
    return {"ok": True, "path": path}


class FolderRenameIn(BaseModel):
    old_path: str
    new_path: str
    scope: str = "personal"


@router.patch("/folders")
def rename_folder(
    body: FolderRenameIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """重命名/移动分组，组内数据集与子分组同步移动。"""
    scope = body.scope if body.scope in ("personal", "team") else "personal"
    if scope == "team" and user.role != "admin":
        raise HTTPException(status_code=403, detail="团队分组需要管理员操作")
    old, new = body.old_path.strip("/"), body.new_path.strip("/")
    if not old or not new or old == new:
        raise HTTPException(status_code=400, detail="路径不合法")

    dss = db.scalars(
        select(Dataset).where(
            or_(Dataset.folder == old, Dataset.folder.like(f"{old}/%")), _ds_scope_filter(scope, user)
        )
    ).all()
    for d in dss:
        d.folder = new + d.folder[len(old):]

    subs = db.scalars(
        select(DatasetFolder).where(
            or_(DatasetFolder.path == old, DatasetFolder.path.like(f"{old}/%")),
            _folder_scope_filter(scope, user),
        )
    ).all()
    exact = None
    for f in subs:
        if f.path == old:
            exact = f
        else:
            f.path = new + f.path[len(old):]
    if exact:
        exact.path = new
    else:
        db.add(DatasetFolder(owner_id=user.id, scope=scope, path=new, sort=0))
    db.commit()
    return {"ok": True, "moved": len(dss)}


class FolderDeleteIn(BaseModel):
    path: str
    scope: str = "personal"


@router.delete("/folders")
def delete_folder(
    body: FolderDeleteIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    scope = body.scope if body.scope in ("personal", "team") else "personal"
    if scope == "team" and user.role != "admin":
        raise HTTPException(status_code=403, detail="团队分组需要管理员操作")
    path = body.path.strip("/")
    has_ds = db.scalar(
        select(func.count()).select_from(Dataset).where(
            or_(Dataset.folder == path, Dataset.folder.like(f"{path}/%")), _ds_scope_filter(scope, user)
        )
    )
    if has_ds:
        raise HTTPException(status_code=400, detail=f"分组内还有 {has_ds} 个数据集，请先移出或删除")
    subs = db.scalars(
        select(DatasetFolder).where(
            DatasetFolder.path.like(f"{path}/%"), _folder_scope_filter(scope, user)
        )
    ).all()
    if subs:
        raise HTTPException(status_code=400, detail="分组内还有子分组，请先删除子级")
    targets = db.scalars(
        select(DatasetFolder).where(DatasetFolder.path == path, _folder_scope_filter(scope, user))
    ).all()
    if not targets:
        raise HTTPException(status_code=404, detail="分组不存在")
    for f in targets:
        db.delete(f)
    db.commit()
    return {"ok": True}


class MoveDsIn(BaseModel):
    folder: str  # 目标分组（空 = 未分组）



