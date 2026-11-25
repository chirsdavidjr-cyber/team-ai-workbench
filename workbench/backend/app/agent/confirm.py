"""删除类操作的两步确认机制。"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..kb_service import visible_docs
from ..models import PendingDelete, User

DELETE_OPS = {"delete_tool", "kb_delete_doc", "kb_delete_folder"}  # 破坏性操作：以用户对话确认为准
CONFIRM_KW = ("确认", "确定", "同意", "删吧", "是的", "执行")


def _user_confirmed(text: str) -> bool:
    """用户是否在本轮消息中明确确认（短消息 + 确认词）。模型的 confirm 参数不算数。"""
    t = text.strip()
    return 0 < len(t) <= 40 and any(k in t for k in CONFIRM_KW)


# ---- 删除确认单：确认必须绑定到具体对象（防偷换对象/重复删） ----

class PendingConfirmError(ValueError):
    """删除未获用户对【同一对象】的确认；agent_stream 捕获后登记该对象的确认单。"""


_PENDING_TTL = 600  # 确认单有效期（秒）


def _delete_target_key(name: str, args: dict, db: Session | None = None, user: User | None = None) -> str:
    """删除对象的指纹：用户确认 A 不能用来删 B，故确认必须能对上同一对象。
    kb_delete_doc 优先解析为文档 ID（模型复述标题时可能带路径/缺 .md 后缀，按 ID 判定同一性最稳）。"""
    if name == "delete_tool":
        return f"tool:{args.get('tool_id')}"
    if name == "kb_delete_doc":
        title = str(args.get("title", "")).strip()
        scope = args.get("scope", "personal")
        if db is not None and user is not None and title:
            docs = visible_docs(db, user.id, scope if scope in ("personal", "team") else None)
            hit = next(
                (d for d in docs if d.title == title or d.path == title or d.path == f"{title}.md"),
                None,
            ) or next(
                (
                    d for d in docs
                    if title.lower() in d.title.lower()
                    or title.lower() in d.path.lower()
                    or d.title.lower() in title.lower()
                ),
                None,
            )
            if hit is not None:
                return f"doc:{hit.scope}:{hit.id}"
        return f"doc:{scope}:{title.lower()}"
    if name == "kb_delete_folder":
        return f"folder:{args.get('scope', 'personal')}:{str(args.get('path', '')).strip().strip('/').lower()}"
    return f"{name}:{json.dumps(args, ensure_ascii=False, sort_keys=True)}"


def _register_pending(db: Session, conv_id: int, name: str, args: dict, user: User | None = None) -> None:
    """登记对象确认单（DB 持久化，重启不丢）；顺带清理过期单。"""
    from sqlalchemy import func as _func

    from ..models import PendingDelete

    db.query(PendingDelete).filter(
        PendingDelete.created_at < _func.datetime("now", f"-{_PENDING_TTL} seconds")
    ).delete(synchronize_session=False)
    key = _delete_target_key(name, args, db, user)
    if not db.scalar(
        select(PendingDelete).where(PendingDelete.conv_id == conv_id, PendingDelete.target_key == key)
    ):
        db.add(PendingDelete(conv_id=conv_id, target_key=key))
    db.commit()


def _match_pending(db: Session, conv_id: int, name: str, args: dict, user: User | None = None) -> bool:
    """用户的确认词只对已登记的同一对象放行；一次性消费，防重复删。"""
    from sqlalchemy import func as _func

    from ..models import PendingDelete

    key = _delete_target_key(name, args, db, user)
    row = db.query(PendingDelete).filter(
        PendingDelete.conv_id == conv_id,
        PendingDelete.target_key == key,
        PendingDelete.created_at >= _func.datetime("now", f"-{_PENDING_TTL} seconds"),
    ).first()
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True

