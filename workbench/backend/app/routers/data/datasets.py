"""数据集 CRUD、上传、预览、移动、粘贴建表、摘要。"""

from __future__ import annotations

import io
import json

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...data_service import build_profile, drop_table, sandbox, write_dataframe
from ...deps import get_current_user, get_db
from ...models import Dataset, User
from .common import _ds_out, _own_ds
from .folders import MoveDsIn

router = APIRouter()

@router.post("/upload")
async def upload(
    file: UploadFile = File(...),
    scope: str = Form("personal"),
    name: str = Form(""),
    folder: str = Form(""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if scope not in ("personal", "team"):
        raise HTTPException(status_code=400, detail="scope 参数不合法")
    if scope == "team" and user.role != "admin":
        raise HTTPException(status_code=403, detail="团队数据集上传需要管理员权限")

    filename = file.filename or "dataset"
    data = await file.read()
    try:
        if filename.lower().endswith(".csv"):
            df = pd.read_csv(io.BytesIO(data))
        else:
            xls = pd.read_excel(io.BytesIO(data), sheet_name=0)
            df = xls if isinstance(xls, pd.DataFrame) else xls[0]
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"文件解析失败：{e}")

    if df.empty or df.shape[1] == 0:
        raise HTTPException(status_code=400, detail="表格为空或没有数据列")

    # 列名兜底（空列名按位置补齐）
    df.columns = [str(c).strip() or f"col_{i+1}" for i, c in enumerate(df.columns)]

    target = folder.strip().strip("/")
    ds = Dataset(
        owner_id=user.id,
        scope=scope,
        name=(name.strip() or filename.rsplit(".", 1)[0])[:120],
        filename=filename,
        table_name="ds_pending",
        folder=target,
        row_count=int(len(df)),
        profile_json=json.dumps(build_profile(df), ensure_ascii=False),
        semantic_json="{}",  # 语义层不在上传时生成：由用户上传口径文档或问数时 AI 推断沉淀
    )
    db.add(ds)
    db.flush()
    ds.table_name = f"ds_{ds.id}"
    write_dataframe(ds.table_name, df)
    db.commit()
    return _ds_out(ds)


@router.get("/datasets")
def list_datasets(
    scope: str = "all",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = select(Dataset)
    if scope == "personal":
        q = q.where(Dataset.owner_id == user.id, Dataset.scope == "personal")
    elif scope == "team":
        q = q.where(Dataset.scope == "team")
    else:
        q = q.where((Dataset.scope == "team") | (Dataset.owner_id == user.id))
    rows = db.scalars(q.order_by(Dataset.id.desc())).all()
    return [_ds_out(d) for d in rows]


@router.get("/datasets/{ds_id}")
def get_dataset(
    ds_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    ds = _own_ds(ds_id, user, db)
    out = _ds_out(ds)
    out["messages"] = [
        {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "payload": json.loads(m.payload_json) if m.payload_json else None,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in db.scalars(
            select(DataMessage).where(DataMessage.dataset_id == ds.id).order_by(DataMessage.id)
        ).all()
    ]
    return out


@router.delete("/datasets/{ds_id}")
def delete_dataset(
    ds_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    ds = _own_ds(ds_id, user, db, need_admin_for_team=True)
    drop_table(ds.table_name)
    db.query(DataMessage).where(DataMessage.dataset_id == ds.id).delete()
    db.delete(ds)
    db.commit()
    return {"ok": True}


@router.get("/datasets/{ds_id}/preview")
def preview_rows(
    ds_id: int,
    limit: int = 50,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """数据预览：真实前 N 行（表格上传后"先看到我的数据"）。"""
    ds = _own_ds(ds_id, user, db)
    limit = max(1, min(limit, 200))
    try:
        with sandbox() as conn:
            cur = conn.execute(f'SELECT * FROM "{ds.table_name}" LIMIT ?', (limit,))
            columns = [d[0] for d in cur.description]
            rows = [
                [
                    "" if v is None else (v if isinstance(v, (int, float)) else str(v))
                    for v in r
                ]
                for r in cur.fetchall()
            ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取数据失败：{e}")
    return {
        "columns": columns,
        "rows": rows,
        "row_count": ds.row_count,
        "truncated": ds.row_count > len(rows),
    }



@router.patch("/datasets/{ds_id}/move")
def move_dataset(
    ds_id: int,
    body: MoveDsIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ds = _own_ds(ds_id, user, db, need_admin_for_team=True)
    ds.folder = body.folder.strip().strip("/")
    db.commit()
    return _ds_out(ds)


@router.post("/datasets/{ds_id}/summarize")
async def summarize_dataset(
    ds_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """AI 数据速读（对标知识库 AI 摘要）：基于画像与前几行数据，生成一段表内容速览，生成后缓存。"""
    from sqlalchemy import select as _sel

    from ..llm import complete
    from ..models import Provider

    ds = _own_ds(ds_id, user, db, need_admin_for_team=True)  # 团队数据集速读仅管理员可生成
    # 前几行真实数据作样本
    sample_rows: list[list] = []
    try:
        with sandbox() as conn:
            cur = conn.execute(f'SELECT * FROM "{ds.table_name}" LIMIT 3')
            cols = [d[0] for d in cur.description]
            sample_rows = [list(r) for r in cur.fetchall()]
    except Exception:
        cols = [c["name"] for c in ds.profile]
    profile_text = "\n".join(
        f"- {c['name']}（{c['dtype']}，唯一值 {c['distinct']}，样本：{'、'.join(map(str, c['samples'][:3]))}）"
        for c in ds.profile
    )
    sample_text = " | ".join(cols) + "\n" + "\n".join(" | ".join(map(str, r)) for r in sample_rows)

    provider = db.scalar(
        _sel(Provider).where(Provider.owner_id.is_(None), Provider.enabled == True).order_by(Provider.id)  # noqa: E712
    ) or db.scalar(_sel(Provider).order_by(Provider.id))
    if provider is None:
        raise HTTPException(status_code=400, detail="没有可用模型，请先配置")
    try:
        models = json.loads(provider.models_json or "[]")
        text = await complete(
            provider,
            models[0]["id"] if models else "glm-5.3",
            [{"role": "user", "content": (
                "你是数据分析助手。根据数据集画像与样本，用中文写一段 120 字以内的速读：这张表是什么、"
                "关键字段与口径注意点、值得先问的 2 个问题（用「」包住问题）。不要寒暄、不要列表。\n\n"
                f"数据集：{ds.name}（{ds.row_count} 行）\n字段画像：\n{profile_text}\n样本：\n{sample_text}"
            )}],
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"生成失败：{e}")
    ds.summary = text.strip()[:600]
    db.commit()
    return _ds_out(ds)



class CreateCsvIn(BaseModel):
    name: str
    csv: str = Field(min_length=1)
    folder: str = ""
    scope: str = "personal"


@router.post("/create-csv")
def create_from_csv(
    body: CreateCsvIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """粘贴 CSV 文本直接创建数据集（轻量接入方式）。"""
    if body.scope == "team" and user.role != "admin":
        raise HTTPException(status_code=403, detail="团队数据集需要管理员创建")
    try:
        df = pd.read_csv(io.StringIO(body.csv.strip()))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"CSV 解析失败：{e}")
    if df.empty or df.shape[1] == 0:
        raise HTTPException(status_code=400, detail="表格为空或没有数据列")
    df.columns = [str(c).strip() or f"col_{i+1}" for i, c in enumerate(df.columns)]

    ds = Dataset(
        owner_id=user.id,
        scope=body.scope if body.scope in ("personal", "team") else "personal",
        name=(body.name.strip() or "粘贴的数据")[:120],
        filename="pasted.csv",
        table_name="ds_pending",
        folder=body.folder.strip().strip("/"),
        row_count=int(len(df)),
        profile_json=json.dumps(build_profile(df), ensure_ascii=False),
        semantic_json="{}",
    )
    db.add(ds)
    db.flush()
    ds.table_name = f"ds_{ds.id}"
    write_dataframe(ds.table_name, df)
    db.commit()
    return _ds_out(ds)



