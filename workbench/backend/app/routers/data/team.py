"""数据集入团队库申请与审核。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...deps import get_current_user, get_db
from ...models import Dataset, User
from .common import _ds_out, _own_ds

router = APIRouter()

@router.post("/datasets/{ds_id}/request-team")
def request_team(
    ds_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """个人数据集申请加入团队库（默认不入；管理员审核）。"""
    ds = db.get(Dataset, ds_id)
    if ds is None or ds.owner_id != user.id or ds.scope != "personal":
        raise HTTPException(status_code=404, detail="数据集不存在")
    if ds.team_status == "pending":
        raise HTTPException(status_code=400, detail="已在申请中，请等待管理员审核")
    ds.team_status = "pending"
    db.commit()
    return _ds_out(ds)


class TeamReviewIn(BaseModel):
    approve: bool
    reason: str = ""


@router.post("/datasets/{ds_id}/review-team")
def review_team(
    ds_id: int,
    body: TeamReviewIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    ds = db.get(Dataset, ds_id)
    if ds is None or ds.team_status != "pending":
        raise HTTPException(status_code=404, detail="没有待审核的申请")
    if body.approve:
        ds.scope = "team"
        ds.team_status = "approved"
    else:
        ds.team_status = "rejected"
    db.commit()
    return _ds_out(ds)


@router.get("/team-requests")
def list_team_requests(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """待审核的入团队库申请（管理员）。"""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    rows = db.scalars(
        select(Dataset).where(Dataset.team_status == "pending").order_by(Dataset.id.desc())
    ).all()
    return [_ds_out(ds) for ds in rows]



