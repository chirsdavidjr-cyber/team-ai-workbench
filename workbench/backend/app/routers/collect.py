"""后台采集任务 API：站点台账 / 创建 / 进度 / 取消 / 删除。"""

from __future__ import annotations

import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..collect_service import MAX_ITEMS_HARD, run_collect
from ..collect_sites import get_site, sites_payload
from ..deps import get_current_user, get_db
from ..models import CollectTask, User

router = APIRouter(prefix="/api/collect", tags=["collect"])


class CollectIn(BaseModel):
    site_key: str = Field(min_length=1, max_length=32)
    columns: list[str] = Field(default_factory=list)  # 栏目 URL；空 = 该站全部栏目
    keywords: list[str] = Field(default_factory=list)  # 空 = 不过滤
    max_items: int = Field(default=20, ge=1, le=MAX_ITEMS_HARD)
    name: str = Field(default="", max_length=128)
    to_kb: bool = False


def _out(t: CollectTask, with_items: bool = False) -> dict:
    site = get_site(t.site_key)
    data = {
        "id": t.id,
        "name": t.name,
        "site_key": t.site_key,
        "site_name": site.name if site else t.site_key,
        "status": t.status,
        "total": t.total,
        "done": t.done,
        "failed": t.failed,
        "hindered": t.hindered,
        "keywords": json.loads(t.keywords_json or "[]"),
        "max_items": t.max_items,
        "to_kb": t.to_kb,
        "dataset_id": t.dataset_id,
        "kb_doc_title": t.kb_doc_title,
        "error": t.error,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }
    if with_items:
        data["items"] = t.items
    return data


@router.get("/sites")
def list_sites(user: User = Depends(get_current_user)):
    """采集站点台账（域名白名单，供前端表单与 agent 动作使用）。"""
    return sites_payload()


@router.post("")
def create_collect(
    body: CollectIn,
    background: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    site = get_site(body.site_key)
    if site is None:
        raise HTTPException(status_code=400, detail="站点未注册（只允许采集白名单内站点）")
    if not site.static_fetch:
        raise HTTPException(status_code=400, detail=f"{site.name}：{site.note}")
    valid_urls = {c.url for c in site.columns}
    columns = [u for u in body.columns if u in valid_urls]
    if not columns:
        columns = list(valid_urls)
    name = body.name.strip() or f"{site.name}·{'、'.join(sorted({c.name for c in site.columns if c.url in columns}))}"
    t = CollectTask(
        owner_id=user.id,
        name=name[:128],
        site_key=site.key,
        columns_json=json.dumps(columns, ensure_ascii=False),
        keywords_json=json.dumps([k.strip() for k in body.keywords if k.strip()], ensure_ascii=False),
        max_items=body.max_items,
        to_kb=body.to_kb,
    )
    db.add(t)
    db.commit()
    background.add_task(run_collect, t.id)
    return _out(t)


@router.get("")
def list_collects(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = db.scalars(
        select(CollectTask)
        .where(CollectTask.owner_id == user.id)
        .order_by(CollectTask.id.desc())
        .limit(50)
    ).all()
    return [_out(t) for t in rows]


@router.get("/{task_id}")
def get_collect(
    task_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    t = db.get(CollectTask, task_id)
    if t is None or t.owner_id != user.id:
        raise HTTPException(status_code=404, detail="采集任务不存在")
    return _out(t, with_items=True)


@router.post("/{task_id}/cancel")
def cancel_collect(
    task_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    t = db.get(CollectTask, task_id)
    if t is None or t.owner_id != user.id:
        raise HTTPException(status_code=404, detail="采集任务不存在")
    if t.status in ("queued", "running"):
        t.status = "cancelled"
        db.commit()
    return _out(t)


@router.delete("/{task_id}")
def delete_collect(
    task_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    t = db.get(CollectTask, task_id)
    if t is None or t.owner_id != user.id:
        raise HTTPException(status_code=404, detail="采集任务不存在")
    if t.status in ("queued", "running"):
        raise HTTPException(status_code=400, detail="任务进行中，请先取消再删除")
    db.delete(t)
    db.commit()
    return {"ok": True}
