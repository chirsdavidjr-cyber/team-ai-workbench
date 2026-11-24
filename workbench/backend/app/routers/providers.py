import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import get_admin_user, get_current_user, get_db
from ..models import Provider, User

router = APIRouter(prefix="/api/providers", tags=["providers"])


class ProviderIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    base_url: str = Field(min_length=1, max_length=255)
    api_key: str = Field(min_length=8, max_length=512)
    models: list[dict] = Field(default_factory=list)  # [{"id","label"}]
    scope: str = "personal"  # team | personal


def _out(p: Provider) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "base_url": p.base_url,
        "api_key_masked": p.mask_key(),
        "models": p.models,
        "enabled": p.enabled,
        "scope": "team" if p.owner_id is None else "personal",
        "owner_id": p.owner_id,
    }


@router.get("")
def list_providers(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """返回当前用户可用的供应商：团队全局 + 本人个人。"""
    rows = db.scalars(
        select(Provider).where(
            (Provider.owner_id.is_(None)) | (Provider.owner_id == user.id)
        )
    ).all()
    visible = [p for p in rows if p.owner_id is not None or p.enabled]
    # 团队的排在前面
    visible.sort(key=lambda p: (p.owner_id is not None, p.id))
    return [_out(p) for p in visible]


@router.get("/{provider_id}/models")
async def remote_models(
    provider_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from ..llm import list_remote_models

    p = db.get(Provider, provider_id)
    if p is None or (p.owner_id is not None and p.owner_id != user.id):
        raise HTTPException(status_code=404, detail="供应商不存在")
    if p.owner_id is None and not p.enabled:
        raise HTTPException(status_code=400, detail="该供应商已被管理员停用")
    return await list_remote_models(p)


class ModelsPreviewIn(BaseModel):
    base_url: str = Field(min_length=1, max_length=255)
    api_key: str = Field(min_length=8, max_length=512)


@router.post("/models-preview")
async def preview_models(body: ModelsPreviewIn, user: User = Depends(get_current_user)):
    """保存前按 base_url + api_key 试拉模型列表（OpenAI 兼容 /models）。"""
    from ..llm import list_remote_models

    probe = Provider(base_url=body.base_url.strip(), api_key=body.api_key.strip())
    models = await list_remote_models(probe)
    if not models:
        raise HTTPException(status_code=502, detail="拉取失败：请检查接口地址与 API Key")
    return models


@router.post("")
def create_provider(
    body: ProviderIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    models = [
        {"id": str(m.get("id", "")).strip(), "label": str(m.get("label") or m.get("id", "")).strip()}
        for m in body.models
        if str(m.get("id", "")).strip()
    ]
    if not models:
        raise HTTPException(status_code=400, detail="至少配置一个模型 ID")
    if body.scope == "team":
        get_admin_user(user)  # 校验管理员
        owner_id = None
    else:
        owner_id = user.id
    p = Provider(
        owner_id=owner_id,
        name=body.name.strip(),
        base_url=body.base_url.strip(),
        api_key=body.api_key.strip(),
        models_json=json.dumps(models, ensure_ascii=False),
    )
    db.add(p)
    db.commit()
    return _out(p)


@router.delete("/{provider_id}")
def delete_provider(
    provider_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    p = db.get(Provider, provider_id)
    if p is None:
        raise HTTPException(status_code=404, detail="供应商不存在")
    # 团队供应商（owner_id=None）仅管理员可删；个人供应商仅本人或管理员
    if (p.owner_id is None and user.role != "admin") or (
        p.owner_id is not None and p.owner_id != user.id and user.role != "admin"
    ):
        raise HTTPException(status_code=403, detail="无权删除该供应商")
    db.delete(p)
    db.commit()
    return {"ok": True}
