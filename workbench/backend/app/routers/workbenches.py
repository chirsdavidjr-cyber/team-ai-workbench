import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..deps import get_current_user, get_db
from ..models import (
    BatchTask,
    Conversation,
    Dataset,
    KbDoc,
    KbReview,
    Message,
    DataMessage,
    Tool,
    User,
    Workbench,
)

router = APIRouter(prefix="/api/workbenches", tags=["workbenches"])

# 按角色推荐的默认卡片（与前端系统卡片清单 key 对齐；FR-WORK-01）
DEFAULT_LAYOUT_MEMBER = [
    {"widget": "recent_chats", "span": 1},
    {"widget": "batch_tasks", "span": 1},
    {"widget": "kb_activity", "span": 1},
    {"widget": "my_tools", "span": 1},
    {"widget": "todos", "span": 1},
]
DEFAULT_LAYOUT_ADMIN = DEFAULT_LAYOUT_MEMBER[:2] + [
    {"widget": "review_queue", "span": 1},
    {"widget": "team_usage", "span": 1},
] + DEFAULT_LAYOUT_MEMBER[2:]


def default_layout(role: str) -> list[dict]:
    layout = DEFAULT_LAYOUT_ADMIN if role == "admin" else DEFAULT_LAYOUT_MEMBER
    return [dict(item) for item in layout]


def _wb_out(w: Workbench) -> dict:
    return {
        "id": w.id,
        "name": w.name,
        "layout": w.layout,
        "is_default": w.is_default,
        "updated_at": w.updated_at.isoformat() if w.updated_at else None,
    }


def _own_workbench(wb_id: int, user: User, db: Session) -> Workbench:
    w = db.get(Workbench, wb_id)
    if w is None or w.owner_id != user.id:
        raise HTTPException(status_code=404, detail="工作台不存在")
    return w


def _seed_default(db: Session, user: User) -> Workbench:
    """首次访问：按角色生成默认工作台。"""
    w = Workbench(
        owner_id=user.id,
        name="我的工作台",
        layout_json=json.dumps(default_layout(user.role), ensure_ascii=False),
        is_default=True,
    )
    db.add(w)
    db.commit()
    db.refresh(w)
    return w


class WorkbenchIn(BaseModel):
    name: str = Field(default="新工作台", max_length=64)


class WorkbenchUpdateIn(BaseModel):
    name: str | None = Field(default=None, max_length=64)
    # 卡片布局：[{widget, span}]，widget 必须在前端系统卡片清单内（后端不做强校验，渲染端兜底）
    layout: list[dict] | None = None


@router.get("")
def list_workbenches(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(Workbench).where(Workbench.owner_id == user.id).order_by(Workbench.id)
    ).all()
    if not rows:
        rows = [_seed_default(db, user)]
    return [_wb_out(w) for w in rows]


@router.post("")
def create_workbench(
    body: WorkbenchIn | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    name = (body.name if body and body.name else "新工作台").strip() or "新工作台"
    w = Workbench(owner_id=user.id, name=name, layout_json="[]")
    db.add(w)
    db.commit()
    db.refresh(w)
    return _wb_out(w)


@router.patch("/{wb_id}")
def update_workbench(
    wb_id: int,
    body: WorkbenchUpdateIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    w = _own_workbench(wb_id, user, db)
    if body.name is not None:
        w.name = body.name.strip() or w.name
    if body.layout is not None:
        # 只保留合法结构，防御前端脏数据
        clean = [
            {"widget": str(it.get("widget", "")), "span": 2 if it.get("span") == 2 else 1}
            for it in body.layout
            if isinstance(it, dict) and it.get("widget")
        ]
        w.layout_json = json.dumps(clean, ensure_ascii=False)
    db.commit()
    return _wb_out(w)


@router.post("/{wb_id}/reset")
def reset_workbench(
    wb_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """恢复默认布局（按当前角色）。"""
    w = _own_workbench(wb_id, user, db)
    w.layout_json = json.dumps(default_layout(user.role), ensure_ascii=False)
    db.commit()
    return _wb_out(w)


@router.delete("/{wb_id}")
def delete_workbench(
    wb_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    w = _own_workbench(wb_id, user, db)
    remaining = db.execute(
        select(Workbench.id).where(Workbench.owner_id == user.id, Workbench.id != wb_id).limit(1)
    ).first()
    if remaining is None:
        raise HTTPException(status_code=400, detail="至少保留一个工作台")
    db.delete(w)
    db.commit()
    return {"ok": True}


def _count(db: Session, stmt) -> int:
    return db.scalar(stmt) or 0


@router.get("/summary")
def workbench_summary(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """统计卡聚合（一次请求供多张卡使用）：数据进展 / 行研动态；管理员另有审核队列与团队用量。"""
    week_ago = datetime.now() - timedelta(days=7)

    # 数据进展（FR-WORK-01：近 7 天问数次数 / 新增数据集）
    asks_7d = _count(
        db,
        select(func.count()).select_from(DataMessage).where(
            DataMessage.role == "user", DataMessage.created_at >= week_ago
        ),
    )
    new_datasets_7d = _count(
        db,
        select(func.count()).select_from(Dataset).where(
            Dataset.created_at >= week_ago,
            (Dataset.scope == "team") | (Dataset.owner_id == user.id),
        ),
    )

    # 行研动态：以行研类工具发起的近期会话（标题级，团队可见）
    research_rows = db.execute(
        select(Conversation, User.name)
        .join(Tool, Conversation.tool_id == Tool.id)
        .join(User, Conversation.user_id == User.id)
        .where(Tool.category == "行研")
        .order_by(Conversation.updated_at.desc())
        .limit(5)
    ).all()
    research = [
        {
            "id": c.id,
            "title": c.title,
            "owner": name,
            "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        }
        for c, name in research_rows
    ]

    out: dict = {
        "data": {"asks_7d": asks_7d, "new_datasets_7d": new_datasets_7d},
        "research": research,
    }

    if user.role == "admin":
        kb_pending = _count(db, select(func.count()).select_from(KbReview).where(KbReview.status == "pending"))
        data_pending = _count(
            db, select(func.count()).select_from(Dataset).where(Dataset.team_status == "pending")
        )
        msgs_7d = _count(
            db, select(func.count()).select_from(Message).where(Message.created_at >= week_ago)
        )
        active_users_7d = _count(
            db,
            select(func.count(func.distinct(Conversation.user_id))).select_from(Conversation).where(
                Conversation.updated_at >= week_ago
            ),
        )
        tasks_7d = _count(
            db,
            select(func.count()).select_from(BatchTask).where(BatchTask.created_at >= week_ago),
        )
        team_tools = _count(db, select(func.count()).select_from(Tool).where(Tool.scope == "team"))
        team_docs = _count(db, select(func.count()).select_from(KbDoc).where(KbDoc.scope == "team"))
        team_datasets = _count(
            db, select(func.count()).select_from(Dataset).where(Dataset.scope == "team")
        )
        out["reviews"] = {"kb": kb_pending, "data": data_pending}
        out["usage"] = {
            "msgs_7d": msgs_7d,
            "active_users_7d": active_users_7d,
            "tasks_7d": tasks_7d,
            "team_tools": team_tools,
            "team_docs": team_docs,
            "team_datasets": team_datasets,
        }

    return out
