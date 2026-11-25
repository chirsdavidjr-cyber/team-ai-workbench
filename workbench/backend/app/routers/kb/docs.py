"""文档 CRUD、上传、下载、个人/团队切换、重索引。"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, load_only

from ...deps import get_current_user, get_db
from ...models import KbDoc, User
from .common import SUPPORTED_EXTS, TEXT_EXTS, UPLOAD_DIR, _clean_title, _doc_out, _get_editable_doc, _visible_filter
from .extract import extract_file
from .helpers import _gen_summary_bg, _ingest, _reindex_doc, _save_source

router = APIRouter()

@router.post("/upload")
async def upload(
    background: BackgroundTasks,
    files: list[UploadFile] = File(...),
    scope: str = Form("personal"),
    target: str = Form("收件箱"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """上传知识文件。target：目标文件夹（默认收件箱，符合"先进收件箱再整理"的工作流）。"""
    if scope not in ("personal", "team"):
        raise HTTPException(status_code=400, detail="scope 参数不合法")
    if scope == "team" and user.role != "admin":
        raise HTTPException(status_code=403, detail="团队库上传需要管理员权限")

    target = target.strip().strip("/")
    if target in ("原始资料", "知识库", "收件箱", "来源台账"):
        pass  # 一级分区允许
    elif target and target.split("/")[0] not in ("原始资料", "知识库", "收件箱", "来源台账", ""):
        pass  # 自由文件夹也允许（未初始化工作流的库）
    prefix = f"{target}/" if target else ""

    created, skipped = [], []
    for f in files:
        name = f.filename or ""
        data = await f.read()

        if name.lower().endswith(".zip"):
            try:
                zf = zipfile.ZipFile(io.BytesIO(data))
            except zipfile.BadZipFile:
                skipped.append({"name": name, "reason": "zip 文件损坏"})
                continue
            for info in zf.infolist():
                if info.is_dir():
                    continue
                if not (info.flag_bits & 0x800):
                    info.filename = _fix_zip_name(info.filename)
                rel = info.filename
                parts = rel.split("/")
                if any(p in IGNORE_DIRS for p in parts[:-1]):
                    continue
                if not rel.lower().endswith(SUPPORTED_EXTS):
                    continue
                raw = zf.read(info)
                try:
                    text = await extract_file(db, user, rel, raw)
                except ValueError as e:
                    skipped.append({"name": rel, "reason": str(e)})
                    continue
                except Exception:
                    skipped.append({"name": rel, "reason": "解析失败"})
                    continue
                base = parts[-1]
                doc = await _ingest(
                    db, user, scope, _clean_title(base), prefix + rel, text,
                    source_name=base, source_data=raw,
                )
                created.append(_doc_out(doc))
        elif name.lower().endswith(SUPPORTED_EXTS):
            try:
                text = await extract_file(db, user, name, data)
            except ValueError as e:
                skipped.append({"name": name, "reason": str(e)})
                continue
            except Exception:
                skipped.append({"name": name, "reason": "解析失败（文件可能损坏或加密）"})
                continue
            doc = await _ingest(
                db, user, scope, _clean_title(name), prefix + name, text,
                source_name=name, source_data=data,
            )
            created.append(_doc_out(doc))
        else:
            skipped.append({
                "name": name,
                "reason": "支持 .md / .txt / .pdf / .docx / .pptx / .doc / .xls / .zip",
            })

    db.commit()
    for d in created:
        background.add_task(_gen_summary_bg, d["id"])
    return {"docs": created, "skipped": skipped}


@router.get("/docs")
def list_docs(
    scope: str = "personal",
    q: str = "",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if scope not in ("personal", "team", "all"):
        scope = "personal"
    stmt = select(KbDoc).where(_visible_filter(user))
    if scope == "personal":
        stmt = stmt.where(KbDoc.scope == "personal", KbDoc.owner_id == user.id)
    elif scope == "team":
        stmt = stmt.where(or_(KbDoc.scope == "team", KbDoc.team_status == "approved"))
    q = q.strip()
    if q:
        like = f"%{q}%"
        stmt = stmt.where(KbDoc.title.like(like) | KbDoc.content.like(like))
    # 列表只需元数据：排除 content 全文（_doc_out 未使用 content），大库首屏瘦身
    stmt = stmt.options(
        load_only(
            KbDoc.id, KbDoc.owner_id, KbDoc.scope, KbDoc.team_status, KbDoc.title,
            KbDoc.path, KbDoc.size, KbDoc.tags_json, KbDoc.summary, KbDoc.source_file,
            KbDoc.chunk_count, KbDoc.status, KbDoc.created_at, KbDoc.updated_at,
        )
    )
    rows = db.scalars(stmt.order_by(KbDoc.updated_at.desc())).all()
    link_counts = dict(
        db.execute(
            select(KbLink.source_id, func.count()).group_by(KbLink.source_id)
        ).all()
    )
    return [_doc_out(d, link_counts.get(d.id, 0)) for d in rows]


class DocCreateIn(BaseModel):
    title: str
    folder: str = ""
    content: str = ""
    scope: str = "personal"


@router.post("/docs")
async def create_doc(
    body: DocCreateIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if body.scope == "team" and user.role != "admin":
        raise HTTPException(status_code=403, detail="写入团队库需要管理员权限")
    title = body.title.strip()[:120] or "未命名"
    folder = body.folder.strip().strip("/")
    path = f"{folder}/{title}.md" if folder else f"{title}.md"
    doc = KbDoc(
        owner_id=user.id, scope=body.scope, title=title, path=path, content=body.content
    )
    db.add(doc)
    db.flush()
    await _reindex_doc(db, doc)
    db.commit()
    return _doc_out(doc)


class DocUpdateIn(BaseModel):
    title: str | None = None
    content: str | None = None


@router.put("/docs/{doc_id}")
async def update_doc(
    doc_id: int,
    body: DocUpdateIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    d = db.get(KbDoc, doc_id)
    if d is None or (d.scope == "personal" and d.owner_id != user.id):
        raise HTTPException(status_code=404, detail="文档不存在")
    if d.scope == "team" and user.role != "admin" and d.owner_id != user.id:
        raise HTTPException(status_code=403, detail="仅创建者或管理员可编辑团队库文档")
    if body.title is not None and body.title.strip():
        d.title = body.title.strip()[:120]
        folder = d.path.rsplit("/", 1)[0] if "/" in d.path else ""
        d.path = f"{folder}/{d.title}.md" if folder else f"{d.title}.md"
    if body.content is not None:
        d.content = body.content
        await _reindex_doc(db, d)
    db.commit()
    return _doc_out(d)


@router.get("/docs/{doc_id}")
def get_doc(
    doc_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    d = db.get(KbDoc, doc_id)
    if d is None or (d.scope == "personal" and d.owner_id != user.id):
        raise HTTPException(status_code=404, detail="文档不存在")
    out = _doc_out(d)
    out["content"] = d.content
    # 双链关系：outgoing（引用了谁）/ incoming（被谁引用）
    out_links = db.scalars(select(KbLink).where(KbLink.source_id == d.id)).all()
    in_links = db.scalars(select(KbLink).where(KbLink.target_doc_id == d.id)).all()
    # 反链标题一次取齐，避免逐条 db.get 的 N+1
    src_titles = {
        d.id: d.title
        for d in db.scalars(
            select(KbDoc).where(KbDoc.id.in_([l.source_id for l in in_links]))
        ).all()
    } if in_links else {}
    out["links"] = {
        "outgoing": [
            {"title": l.target_title, "doc_id": l.target_doc_id} for l in out_links
        ],
        "incoming": [
            {"title": src_titles.get(l.source_id, "?"), "doc_id": l.source_id}
            for l in in_links
        ],
    }
    return out


@router.get("/docs/{doc_id}/file")
def download_source(
    doc_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """下载上传时的原文件。"""
    d = db.get(KbDoc, doc_id)
    # 个人库仅本人；team_status=approved 的文档已入团队库，全员可看原文件
    if d is None or (d.scope == "personal" and d.owner_id != user.id and d.team_status != "approved"):
        raise HTTPException(status_code=404, detail="文档不存在")
    if not d.source_file:
        raise HTTPException(status_code=404, detail="该文档没有保存原文件（早期上传或纯手写笔记）")
    path = UPLOAD_DIR / d.source_file
    if not path.exists():
        raise HTTPException(status_code=404, detail="原文件已丢失")
    orig_name = d.source_file.split("_", 2)[2] if d.source_file.count("_") >= 2 else d.title
    return FileResponse(path, filename=orig_name)


@router.delete("/docs/{doc_id}")
def delete_doc(
    doc_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    d = db.get(KbDoc, doc_id)
    if d is None or (d.scope == "personal" and d.owner_id != user.id):
        raise HTTPException(status_code=404, detail="文档不存在")
    if d.scope == "team" and user.role != "admin":
        raise HTTPException(status_code=403, detail="删除团队库文档需要管理员权限")
    db.query(KbChunk).where(KbChunk.doc_id == d.id).delete()
    db.query(KbLink).where((KbLink.source_id == d.id) | (KbLink.target_doc_id == d.id)).delete()
    if d.source_file:
        try:
            (UPLOAD_DIR / d.source_file).unlink(missing_ok=True)
        except OSError:
            pass
    db.delete(d)
    db.commit()
    return {"ok": True}



@router.post("/docs/{doc_id}/personal")
async def toggle_personal(
    doc_id: int,
    body: dict,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """enabled=true 入个人库（索引、AI 可检索）；false 移出转草稿。"""
    d = _get_editable_doc(doc_id, user, db)
    enabled = bool(body.get("enabled", True))
    if d.scope == "team" and d.owner_id != user.id:
        raise HTTPException(status_code=403, detail="团队库文档由创建者管理个人副本")
    if enabled and d.scope == "team":
        d.scope = "personal"  # 管理员直传团队库的文档也可拉回个人使用
        d.team_status = "none" if d.team_status == "none" else d.team_status
    d.status = "ready" if enabled else "draft"
    await _reindex_doc(db, d)
    db.commit()
    return _doc_out(d)


@router.post("/docs/{doc_id}/reindex")
async def reindex_doc(
    doc_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    d = _get_editable_doc(doc_id, user, db)
    await _reindex_doc(db, d)
    db.commit()
    return _doc_out(d)



