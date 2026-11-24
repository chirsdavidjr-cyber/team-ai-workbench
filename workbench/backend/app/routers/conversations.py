from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.orm import Session

import json

from ..deps import get_current_user, get_db
from ..models import Conversation, Message, User

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


class ConversationIn(BaseModel):
    title: str = Field(default="新对话", max_length=128)


def _conv_out(c: Conversation, message_count: int = 0, with_messages: bool = False) -> dict:
    data = {
        "id": c.id,
        "title": c.title,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        "message_count": message_count or len(c.messages),
    }
    if with_messages:
        data["messages"] = [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "model": m.model,
                "citations": (
                    json.loads(m.citations_json) if m.citations_json else None
                ),
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in c.messages
        ]
    return data


def _own_conversation(conv_id: int, user: User, db: Session) -> Conversation:
    c = db.get(Conversation, conv_id)
    if c is None or c.user_id != user.id:
        raise HTTPException(status_code=404, detail="会话不存在")
    return c


@router.get("")
def list_conversations(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # 消息数用子查询统计，避免逐会话懒加载全部消息（N+1）
    counts = dict(
        db.execute(
            select(Message.conversation_id, func.count())
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(Conversation.user_id == user.id)
            .group_by(Message.conversation_id)
        ).all()
    )
    rows = db.scalars(
        select(Conversation)
        .where(Conversation.user_id == user.id)
        .order_by(Conversation.updated_at.desc())
        .limit(100)
    ).all()
    return [_conv_out(c, counts.get(c.id, 0)) for c in rows]


@router.post("")
def create_conversation(
    body: ConversationIn | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    title = (body.title if body and body.title else "新对话").strip() or "新对话"
    c = Conversation(user_id=user.id, title=title)
    db.add(c)
    db.commit()
    return _conv_out(c)


@router.get("/{conv_id}")
def get_conversation(
    conv_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    c = _own_conversation(conv_id, user, db)
    return _conv_out(c, with_messages=True)


@router.patch("/{conv_id}")
def rename_conversation(
    conv_id: int,
    body: ConversationIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    c = _own_conversation(conv_id, user, db)
    c.title = body.title.strip() or c.title
    db.commit()
    return _conv_out(c)


@router.delete("/{conv_id}")
def delete_conversation(
    conv_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    c = _own_conversation(conv_id, user, db)
    db.delete(c)
    db.commit()
    return {"ok": True}
