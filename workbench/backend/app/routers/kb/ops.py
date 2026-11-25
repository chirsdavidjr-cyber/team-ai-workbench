"""Agent 可调用的知识库操作入口。"""

from __future__ import annotations

from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from ...models import KbDoc, KbFolder, User
from .common import WORKFLOW_FOLDERS, _doc_out, _folder_scope_filter, _guard_skeleton, _visible_filter
from .helpers import _ingest

def kb_context_text(db: Session, user: User, scope: str = "personal") -> str:
    """当前库的文件夹树 + 文档路径清单（作为管家的操作上下文）。"""
    doc_scope = (
        or_(KbDoc.scope == "team", KbDoc.team_status == "approved")
        if scope == "team"
        else and_(KbDoc.scope == "personal", KbDoc.owner_id == user.id)
    )
    folders = sorted(
        f for f in db.scalars(select(KbFolder.path).where(_folder_scope_filter(scope, user)))
    )
    docs = db.scalars(select(KbDoc).where(doc_scope).order_by(KbDoc.updated_at.desc()).limit(300)).all()
    doc_lines = [f"- {d.path}（标题：{d.title}）" for d in docs]
    return (
        "【文件夹树】\n" + ("\n".join(folders) or "（空）")
        + "\n\n【文档清单】\n" + ("\n".join(doc_lines) or "（空）")
    )


def _find_doc(db: Session, user: User, scope: str, key: str) -> KbDoc | None:
    """按标题或路径片段定位文档（管家指令中的模糊指代）。"""
    doc_scope = (
        or_(KbDoc.scope == "team", KbDoc.team_status == "approved")
        if scope == "team"
        else and_(KbDoc.scope == "personal", KbDoc.owner_id == user.id)
    )
    like = f"%{key.strip()}%"
    return db.scalar(
        select(KbDoc)
        .where(doc_scope, or_(KbDoc.title.like(like), KbDoc.path.like(like)))
        .order_by(KbDoc.updated_at.desc())
        .limit(1)
    )


async def apply_kb_op(db: Session, user: User, op: dict, scope: str = "personal") -> str:
    """执行管家给出的单个操作，返回结果描述（出错也返回文案，不中断会话）。"""
    action = str(op.get("action", "")).strip()
    try:
        if action == "create_folder":
            path = str(op.get("path", "")).strip().strip("/")
            if not path:
                return "❌ create_folder 缺少 path"
            _guard_skeleton(path)
            exists = db.scalar(
                select(KbFolder).where(KbFolder.path == path, _folder_scope_filter(scope, user))
            )
            if not exists:
                max_sort = db.scalar(
                    select(func.max(KbFolder.sort)).where(_folder_scope_filter(scope, user))
                )
                db.add(KbFolder(owner_id=user.id, scope=scope, path=path, sort=(max_sort or 0) + 1))
                db.commit()
                return f"✅ 新建文件夹 {path}"
            return f"⏭️ 文件夹已存在：{path}"

        if action == "rename_folder":
            old, new = str(op.get("old", "")).strip("/"), str(op.get("new", "")).strip("/")
            if not old or not new or old == new:
                return "❌ rename_folder 缺少 old/new"
            _guard_skeleton(old, new)
            doc_scope = (
                or_(KbDoc.scope == "team", KbDoc.team_status == "approved")
                if scope == "team"
                else and_(KbDoc.scope == "personal", KbDoc.owner_id == user.id)
            )
            docs = db.scalars(
                select(KbDoc).where(or_(KbDoc.path == old, KbDoc.path.like(f"{old}/%")), doc_scope)
            ).all()
            for d in docs:
                d.path = new + d.path[len(old):]
            subs = db.scalars(
                select(KbFolder).where(
                    or_(KbFolder.path == old, KbFolder.path.like(f"{old}/%")),
                    _folder_scope_filter(scope, user),
                )
            ).all()
            exact = next((f for f in subs if f.path == old), None)
            for f in subs:
                if f.path != old:
                    f.path = new + f.path[len(old):]
            if exact:
                exact.path = new
            else:
                db.add(KbFolder(owner_id=user.id, scope=scope, path=new, sort=0))
            db.commit()
            return f"✅ 文件夹 {old} → {new}（同步移动 {len(docs)} 篇文档）"

        if action == "delete_folder":
            path = str(op.get("path", "")).strip("/")
            if not path:
                return "❌ delete_folder 缺少 path"
            _guard_skeleton(path)
            doc_scope = (
                or_(KbDoc.scope == "team", KbDoc.team_status == "approved")
                if scope == "team"
                else and_(KbDoc.scope == "personal", KbDoc.owner_id == user.id)
            )
            has_docs = db.scalar(
                select(func.count()).select_from(KbDoc).where(
                    or_(KbDoc.path == path, KbDoc.path.like(f"{path}/%")), doc_scope
                )
            )
            if has_docs:
                return f"❌ {path} 内还有 {has_docs} 篇文档，请先移出或删除"
            subs = db.scalars(
                select(KbFolder).where(KbFolder.path.like(f"{path}/%"), _folder_scope_filter(scope, user))
            ).all()
            if subs:
                return f"❌ {path} 内还有子文件夹（{subs[0].path} 等），请先删除子级"
            targets = db.scalars(
                select(KbFolder).where(KbFolder.path == path, _folder_scope_filter(scope, user))
            ).all()
            if not targets:
                return f"❌ 文件夹不存在：{path}"
            for f in targets:
                db.delete(f)
            db.commit()
            return f"✅ 删除文件夹 {path}"

        if action == "move_doc":
            key, to = str(op.get("title", "")).strip(), str(op.get("to", "")).strip("/")
            if not key:
                return "❌ move_doc 缺少 title"
            d = _find_doc(db, user, scope, key)
            if d is None:
                return f"❌ 未找到文档：{key}"
            d.path = f"{to}/{d.title}.md" if to else f"{d.title}.md"
            await _reindex_doc(db, d)
            db.commit()
            return f"✅ 《{d.title}》移动到 {to or '根目录'}"

        if action == "rename_doc":
            key = str(op.get("title", "")).strip()
            new_title = str(op.get("new_title", "")).strip()
            if not key or not new_title:
                return "❌ rename_doc 缺少 title/new_title"
            d = _find_doc(db, user, scope, key)
            if d is None:
                return f"❌ 未找到文档：{key}"
            old_path = d.path
            d.title = new_title
            d.path = "/".join(old_path.split("/")[:-1] + [f"{new_title}.md"])
            await _reindex_doc(db, d)
            db.commit()
            return f"✅ 重命名《{key}》→《{new_title}》"

        if action == "delete_doc":
            key = str(op.get("title", "")).strip()
            if not key:
                return "❌ delete_doc 缺少 title"
            d = _find_doc(db, user, scope, key)
            if d is None:
                return f"❌ 未找到文档：{key}"
            title = d.title
            db.query(KbChunk).where(KbChunk.doc_id == d.id).delete()
            db.query(KbLink).where(or_(KbLink.source_id == d.id, KbLink.target_doc_id == d.id)).delete()
            db.delete(d)
            db.commit()
            return f"✅ 删除文档《{title}》"

        if action == "create_doc":
            title = str(op.get("title", "")).strip() or "未命名"
            folder = str(op.get("folder", "")).strip("/")
            content = str(op.get("content", "")).strip() or f"# {title}\n"
            path = f"{folder}/{title}.md" if folder else f"{title}.md"
            doc = await _ingest(db, user, scope, title, path, content)
            db.commit()
            return f"✅ 新建文档《{title}》于 {folder or '根目录'}"

        if action == "organize_inbox":
            # 复用「整理收件箱」端点逻辑：AI 动态归类 → 登记台账 → 编译知识页
            res = await ingest_inbox(body={"scope": scope}, user=user, db=db)
            lines = [
                f"《{r['title']}》→ {r['category']}"
                + (f"，编译知识页《{r['compiled_page']}》" if r.get("compiled_page") else "")
                for r in res.get("report", [])
            ]
            summary = "\n".join(lines) if lines else "收件箱为空，无需整理"
            return f"✅ 整理收件箱完成：处理 {res.get('processed', 0)} 篇\n{summary}"

        return f"❌ 未知操作：{action}"
    except HTTPException as e:
        db.rollback()
        return f"❌ {e.detail}"
    except Exception as e:
        db.rollback()
        return f"❌ 执行失败：{e}"
