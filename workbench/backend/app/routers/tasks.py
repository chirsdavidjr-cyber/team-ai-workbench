"""异步批量任务：用工具的系统提示词逐条处理文本（如批量意图分析）。"""

import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import get_current_user, get_db
from ..models import BatchTask, Tool, User

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

MAX_ITEMS = 500


class TaskIn(BaseModel):
    tool_id: int
    name: str = Field(default="", max_length=128)
    items: list[str] = Field(min_length=1)


def _out(t: BatchTask, with_items: bool = False) -> dict:
    data = {
        "id": t.id,
        "name": t.name,
        "tool_id": t.tool_id,
        "status": t.status,
        "total": t.total,
        "done": t.done,
        "failed": t.failed,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }
    if with_items:
        data["items"] = t.items
    return data


async def _run_task(task_id: int) -> None:
    """后台逐条执行；每条完成即落库（前端轮询可见进度）。"""
    import httpx2
    from openai import AsyncOpenAI

    from ..db import SessionLocal
    from ..models import Provider
    import os

    with SessionLocal() as db:
        task = db.get(BatchTask, task_id)
        if task is None or task.status == "cancelled":
            return
        tool = db.get(Tool, task.tool_id)
        provider = db.get(Provider, int(os.getenv("DATA_LLM_PROVIDER_ID", "1")))
        if provider is None:
            # 回退：任意已启用的团队供应商，避免固定 id=1 不存在时任务直接失败
            provider = db.scalar(
                select(Provider)
                .where(Provider.owner_id.is_(None), Provider.enabled == True)  # noqa: E712
                .order_by(Provider.id)
            )
        if tool is None or provider is None:
            task.status = "failed"
            items = task.items
            if items:
                items[0]["error"] = "没有可用的模型供应商，请联系管理员配置"
                task.items_json = json.dumps(items, ensure_ascii=False)
            db.commit()
            return

        client = AsyncOpenAI(
            base_url=provider.base_url.rstrip("/"),
            api_key=provider.api_key,
            http_client=httpx2.AsyncClient(trust_env=False, timeout=120),
        )
        task.status = "running"
        db.commit()

        items = task.items
        for i, item in enumerate(items):
            # 每条处理前重查状态，支持取消
            db.refresh(task)
            if task.status == "cancelled":
                return
            entry = items[i]
            try:
                res = await client.chat.completions.create(
                    model=os.getenv("DATA_LLM_MODEL", "glm-5.3"),
                    messages=[
                        {"role": "system", "content": tool.system_prompt},
                        {"role": "user", "content": item["input"]},
                    ],
                    temperature=0.2,
                )
                entry["output"] = res.choices[0].message.content or ""
                entry["status"] = "success"
            except Exception as e:
                entry["output"] = ""
                entry["status"] = "failed"
                entry["error"] = str(e)[:300]
            task.done += 1
            if entry["status"] == "failed":
                task.failed += 1
            task.items_json = json.dumps(items, ensure_ascii=False)
            db.commit()

        task.status = "cancelled" if task.status == "cancelled" else (
            "failed" if task.failed >= task.total else "success"
        )
        db.commit()


@router.post("")
def create_task(
    body: TaskIn,
    background: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    tool = db.get(Tool, body.tool_id)
    if tool is None or (tool.scope != "team" and tool.owner_id != user.id):
        raise HTTPException(status_code=404, detail="工具不存在")
    valid = [s for s in body.items if s.strip()]
    if len(valid) > MAX_ITEMS:
        raise HTTPException(
            status_code=400,
            detail=f"单次任务最多 {MAX_ITEMS} 条（收到 {len(valid)} 条），请分批提交",
        )
    items = [
        {"input": s.strip(), "output": "", "status": "pending", "error": ""}
        for s in valid
    ][:MAX_ITEMS]
    if not items:
        raise HTTPException(status_code=400, detail="没有有效输入")

    task = BatchTask(
        owner_id=user.id,
        tool_id=tool.id,
        name=(body.name.strip() or f"{tool.name} · {len(items)}条")[:120],
        total=len(items),
        items_json=json.dumps(items, ensure_ascii=False),
    )
    db.add(task)
    db.commit()
    background.add_task(_run_task, task.id)
    return _out(task)


@router.get("")
def list_tasks(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = db.scalars(
        select(BatchTask)
        .where(BatchTask.owner_id == user.id)
        .order_by(BatchTask.id.desc())
        .limit(50)
    ).all()
    return [_out(t) for t in rows]


@router.get("/{task_id}")
def get_task(
    task_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    t = db.get(BatchTask, task_id)
    if t is None or t.owner_id != user.id:
        raise HTTPException(status_code=404, detail="任务不存在")
    return _out(t, with_items=True)


@router.post("/{task_id}/cancel")
def cancel_task(
    task_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    t = db.get(BatchTask, task_id)
    if t is None or t.owner_id != user.id:
        raise HTTPException(status_code=404, detail="任务不存在")
    if t.status in ("queued", "running"):
        t.status = "cancelled"
        db.commit()
    return _out(t)


@router.delete("/{task_id}")
def delete_task(
    task_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    t = db.get(BatchTask, task_id)
    if t is None or t.owner_id != user.id:
        raise HTTPException(status_code=404, detail="任务不存在")
    if t.status in ("queued", "running"):
        t.status = "cancelled"
        db.commit()
    db.delete(t)
    db.commit()
    return {"ok": True}
