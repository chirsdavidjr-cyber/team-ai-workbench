"""语义层导入 / 保存 / 重生成。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...data_service import gen_semantic_layer, merge_hints_into_semantic
from ...deps import get_current_user, get_db
from ...models import Dataset, User
from .common import _ds_out, _own_ds

router = APIRouter()

class SemanticIn(BaseModel):
    semantic: dict


class SemanticImportIn(BaseModel):
    content: str  # 口径文档内容（md/txt 文本，或 json 字符串）



@router.post("/datasets/{ds_id}/semantic/import")
async def import_semantic(
    ds_id: int,
    body: SemanticImportIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """上传口径文档（Markdown/文本）或标准 JSON，解析合并进语义层。"""
    import re as _re

    ds = _own_ds(ds_id, user, db, need_admin_for_team=True)  # 团队数据集语义层仅管理员可改
    content = body.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="内容为空")

    # 标准 JSON 直接导入
    if content.startswith("{"):
        try:
            parsed = json.loads(content)
            if isinstance(parsed.get("columns"), dict):
                new_sem, _ = merge_hints_into_semantic(
                    ds.semantic, parsed.get("columns", {})
                )
                if parsed.get("metrics"):
                    new_sem["metrics"] = parsed["metrics"]
                ds.semantic_json = json.dumps(new_sem, ensure_ascii=False)
                db.commit()
                return _ds_out(ds)
        except json.JSONDecodeError:
            pass  # 非 JSON，走文档解析

    # 文档口径 → LLM 解析
    col_names = [c["name"] for c in ds.profile]
    system = (
        "你是数据语义层解析专家。用户上传了一份业务口径文档，请把其中与数据集字段相关的定义"
        "提取为语义层 JSON。字段清单：" + "、".join(col_names) + "\n"
        '仅返回 JSON：{"columns":{"字段名":{"desc":"含义","enum":{"值":"标签"}}},"metrics":[{"name":"指标","expr":"SQL表达式","desc":"口径"}]}。'
        "只提取文档中明确出现的字段定义与指标口径，找不到的不要编造；与字段清单无关的内容忽略。"
    )
    try:
        from ..data_service import _data_llm, _extract_json

        raw = await _data_llm(db, system, content[:8000])
        parsed = _extract_json(raw)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"解析失败：{e}")
    if not parsed or not isinstance(parsed.get("columns"), dict):
        raise HTTPException(status_code=422, detail="未能从文档中解析出字段定义，请检查文档内容")

    new_sem, merged = merge_hints_into_semantic(ds.semantic, parsed["columns"])
    if parsed.get("metrics"):
        metrics = ds.semantic.get("metrics", [])
        new_sem["metrics"] = metrics + [
            m for m in parsed["metrics"] if m not in metrics
        ]
    ds.semantic_json = json.dumps(new_sem, ensure_ascii=False)
    db.commit()
    out = _ds_out(ds)
    out["imported_fields"] = merged
    return out


@router.put("/datasets/{ds_id}/semantic")
def save_semantic(
    ds_id: int,
    body: SemanticIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ds = _own_ds(ds_id, user, db, need_admin_for_team=True)  # 团队数据集语义层仅管理员可改
    ds.semantic_json = json.dumps(body.semantic, ensure_ascii=False)
    db.commit()
    return _ds_out(ds)


@router.post("/datasets/{ds_id}/semantic/regenerate")
async def regenerate_semantic(
    ds_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ds = _own_ds(ds_id, user, db, need_admin_for_team=True)  # 团队数据集语义层仅管理员可改
    try:
        ds.semantic_json = json.dumps(
            await gen_semantic_layer(ds, db), ensure_ascii=False
        )
        db.commit()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"生成失败：{e}")
    return _ds_out(ds)



