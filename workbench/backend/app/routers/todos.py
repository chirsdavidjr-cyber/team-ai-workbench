from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import get_current_user, get_db
from ..models import Todo, User

router = APIRouter(prefix="/api/todos", tags=["todos"])


class TodoIn(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    due_date: str | None = Field(default=None, max_length=10)  # YYYY-MM-DD


class TodoUpdateIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=256)
    due_date: str | None = Field(default=None, max_length=10)
    done: bool | None = None


def _out(t: Todo) -> dict:
    return {
        "id": t.id,
        "title": t.title,
        "due_date": t.due_date,
        "done": t.done,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


def _own(todo_id: int, user: User, db: Session) -> Todo:
    t = db.get(Todo, todo_id)
    if t is None or t.owner_id != user.id:
        raise HTTPException(status_code=404, detail="待办不存在")
    return t


@router.get("")
def list_todos(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(Todo)
        .where(Todo.owner_id == user.id)
        .order_by(Todo.done, Todo.id.desc())
        .limit(100)
    ).all()
    return [_out(t) for t in rows]


@router.post("")
def create_todo(body: TodoIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    t = Todo(owner_id=user.id, title=body.title.strip(), due_date=body.due_date or None)
    db.add(t)
    db.commit()
    db.refresh(t)
    return _out(t)


@router.patch("/{todo_id}")
def update_todo(
    todo_id: int,
    body: TodoUpdateIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    t = _own(todo_id, user, db)
    if body.title is not None:
        t.title = body.title.strip()
    if body.due_date is not None:
        t.due_date = body.due_date or None
    if body.done is not None:
        t.done = body.done
    db.commit()
    return _out(t)


@router.delete("/{todo_id}")
def delete_todo(
    todo_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    t = _own(todo_id, user, db)
    db.delete(t)
    db.commit()
    return {"ok": True}
