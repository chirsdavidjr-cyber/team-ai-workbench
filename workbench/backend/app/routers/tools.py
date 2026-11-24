from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, or_
from sqlalchemy.orm import Session

import json
import re

from ..deps import get_current_user, get_db
from ..llm import complete
from ..agent import clean_allowed_actions
from ..models import Conversation, Message, Provider, Tool, ToolFolder, User

router = APIRouter(prefix="/api/tools", tags=["tools"])


class ToolIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=255)
    icon: str = Field(default="🧰", max_length=16)
    category: str = Field(default="通用", max_length=32)
    kind: str = Field(default="agent", pattern="^(agent|skill|template)$")
    system_prompt: str = Field(min_length=1)
    openings: list[str] = Field(default_factory=list)
    allowed_actions: list[str] = Field(default_factory=list)  # 能力面白名单（空=全部）
    scope: str = "personal"
    folder: str = Field(default="", max_length=512)  # 所属分组路径（与知识库/数据模块同构）


def _clean_folder(raw: str | None) -> str:
    return (raw or "").strip().strip("/")[:512]


def _out(t: Tool) -> dict:
    return {
        "id": t.id,
        "name": t.name,
        "description": t.description,
        "icon": t.icon,
        "category": t.category,
        "kind": t.kind,
        "system_prompt": t.system_prompt,
        "openings": t.openings,
        "allowed_actions": t.allowed_actions,
        "scope": t.scope,
        "team_status": t.team_status,
        "folder": t.folder,
        "owner_id": t.owner_id,
        "use_count": t.use_count,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }


@router.get("")
def list_tools(
    scope: str = "all",
    q: str = "",
    sort: str = "use",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """scope: all（团队+本人个人，默认，向后兼容）| personal | team。
    q：按名称/描述/分类/提示词模糊搜索；sort：use（默认，使用次数）| updated | name。"""
    stmt = select(Tool).where(
        or_(Tool.scope == "team", Tool.owner_id == user.id)
    )
    if scope == "personal":
        stmt = stmt.where(Tool.owner_id == user.id, Tool.scope == "personal")
    elif scope == "team":
        stmt = stmt.where(Tool.scope == "team")
    kw = q.strip()
    if kw:
        like = f"%{kw}%"
        stmt = stmt.where(
            or_(
                Tool.name.like(like),
                Tool.description.like(like),
                Tool.category.like(like),
                Tool.system_prompt.like(like),
            )
        )
    if sort == "updated":
        stmt = stmt.order_by(Tool.updated_at.desc(), Tool.id.desc())
    elif sort == "name":
        rows = db.scalars(stmt).all()
        return sorted((_out(t) for t in rows), key=lambda x: x["name"])  # 中文按码点近似 localeCompare
    else:
        stmt = stmt.order_by(Tool.use_count.desc(), Tool.id.desc())  # 默认排序与旧版一致
    rows = db.scalars(stmt).all()
    return [_out(t) for t in rows]


@router.post("")
def create_tool(
    body: ToolIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if body.scope == "team" and user.role != "admin":
        raise HTTPException(status_code=403, detail="发布团队工具需要管理员权限")
    t = Tool(
        owner_id=user.id,
        scope=body.scope if body.scope in ("team", "personal") else "personal",
        name=body.name.strip(),
        description=body.description.strip(),
        icon=body.icon or "🧰",
        category=body.category.strip() or "通用",
        kind=body.kind,
        system_prompt=body.system_prompt,
        folder=_clean_folder(body.folder),
        openings_json=json.dumps(
            [o.strip() for o in body.openings if o.strip()], ensure_ascii=False
        ),
        allowed_actions_json=clean_allowed_actions(body.allowed_actions),
    )
    db.add(t)
    db.commit()
    return _out(t)


class ToolUpdateIn(BaseModel):
    name: str | None = None
    description: str | None = None
    icon: str | None = None
    category: str | None = None
    kind: str | None = None
    system_prompt: str | None = None
    openings: list[str] | None = None
    allowed_actions: list[str] | None = None
    folder: str | None = None
    scope: str | None = None  # personal→team 需管理员；成员请走 request-team 审核流


@router.put("/{tool_id}")
def update_tool(
    tool_id: int,
    body: ToolUpdateIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    t = db.get(Tool, tool_id)
    if t is None or (t.owner_id != user.id and user.role != "admin"):
        raise HTTPException(status_code=404, detail="工具不存在")
    if body.scope is not None and body.scope in ("team", "personal") and body.scope != t.scope:
        if body.scope == "team" and user.role != "admin":
            raise HTTPException(status_code=403, detail="转为团队工具需要管理员权限，请先申请入团队库")
        t.scope = body.scope
        if body.scope == "team":
            t.team_status = "approved"  # 管理员直接转团队 = 视为已审核
    if body.name is not None and body.name.strip():
        t.name = body.name.strip()
    if body.description is not None:
        t.description = body.description.strip()
    if body.icon:
        t.icon = body.icon
    if body.category:
        t.category = body.category
    if body.kind in ("agent", "skill", "template"):
        t.kind = body.kind
    if body.system_prompt:
        t.system_prompt = body.system_prompt
    if body.folder is not None:
        t.folder = _clean_folder(body.folder)
    if body.openings is not None:
        t.openings_json = json.dumps(
            [o.strip() for o in body.openings if o.strip()], ensure_ascii=False
        )
    if body.allowed_actions is not None:
        t.allowed_actions_json = clean_allowed_actions(body.allowed_actions)
    db.commit()
    return _out(t)


# ---------------- 分组（与知识库/数据模块 folders 同构） ----------------


def _folder_scope_filter(scope: str, user: User):
    from sqlalchemy import and_

    if scope == "team":
        return ToolFolder.scope == "team"
    return and_(ToolFolder.scope == "personal", ToolFolder.owner_id == user.id)


def _tool_scope_filter(scope: str, user: User):
    from sqlalchemy import and_

    if scope == "team":
        return Tool.scope == "team"
    return and_(Tool.scope == "personal", Tool.owner_id == user.id)


class FolderIn(BaseModel):
    path: str
    scope: str = "personal"


@router.get("/folders")
def list_folders(
    scope: str = "personal",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = db.scalars(
        select(ToolFolder)
        .where(_folder_scope_filter(scope if scope in ("personal", "team") else "personal", user))
        .order_by(ToolFolder.sort, ToolFolder.path)
    ).all()
    return [{"id": f.id, "path": f.path, "scope": f.scope, "sort": f.sort} for f in rows]


@router.post("/folders")
def create_folder(
    body: FolderIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from sqlalchemy import func

    scope = body.scope if body.scope in ("personal", "team") else "personal"
    if scope == "team" and user.role != "admin":
        raise HTTPException(status_code=403, detail="团队分组需要管理员创建")
    path = body.path.strip().strip("/")
    if not path:
        raise HTTPException(status_code=400, detail="分组路径不合法")
    exists = db.scalar(
        select(ToolFolder).where(ToolFolder.path == path, _folder_scope_filter(scope, user))
    )
    if not exists:
        max_sort = db.scalar(
            select(func.max(ToolFolder.sort)).where(_folder_scope_filter(scope, user))
        )
        db.add(ToolFolder(owner_id=user.id, scope=scope, path=path, sort=(max_sort or 0) + 1))
        db.commit()
    return {"ok": True, "path": path}


class FolderRenameIn(BaseModel):
    old_path: str
    new_path: str
    scope: str = "personal"


@router.patch("/folders")
def rename_folder(
    body: FolderRenameIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """重命名/移动分组，组内工具与子分组同步移动。"""
    scope = body.scope if body.scope in ("personal", "team") else "personal"
    if scope == "team" and user.role != "admin":
        raise HTTPException(status_code=403, detail="团队分组需要管理员操作")
    old, new = body.old_path.strip("/"), body.new_path.strip("/")
    if not old or not new or old == new:
        raise HTTPException(status_code=400, detail="路径不合法")

    tools = db.scalars(
        select(Tool).where(
            or_(Tool.folder == old, Tool.folder.like(f"{old}/%")), _tool_scope_filter(scope, user)
        )
    ).all()
    for t in tools:
        t.folder = new + t.folder[len(old):]

    subs = db.scalars(
        select(ToolFolder).where(
            or_(ToolFolder.path == old, ToolFolder.path.like(f"{old}/%")),
            _folder_scope_filter(scope, user),
        )
    ).all()
    exact = None
    for f in subs:
        if f.path == old:
            exact = f
        else:
            f.path = new + f.path[len(old):]
    if exact:
        exact.path = new
    else:
        db.add(ToolFolder(owner_id=user.id, scope=scope, path=new, sort=0))
    db.commit()
    return {"ok": True, "moved": len(tools)}


class FolderDeleteIn(BaseModel):
    path: str
    scope: str = "personal"


@router.delete("/folders")
def delete_folder(
    body: FolderDeleteIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from sqlalchemy import func

    scope = body.scope if body.scope in ("personal", "team") else "personal"
    if scope == "team" and user.role != "admin":
        raise HTTPException(status_code=403, detail="团队分组需要管理员操作")
    path = body.path.strip("/")
    has_tool = db.scalar(
        select(func.count()).select_from(Tool).where(
            or_(Tool.folder == path, Tool.folder.like(f"{path}/%")), _tool_scope_filter(scope, user)
        )
    )
    if has_tool:
        raise HTTPException(status_code=400, detail=f"分组内还有 {has_tool} 个工具，请先移出或删除")
    subs = db.scalars(
        select(ToolFolder).where(
            ToolFolder.path.like(f"{path}/%"), _folder_scope_filter(scope, user)
        )
    ).all()
    if subs:
        raise HTTPException(status_code=400, detail="分组内还有子分组，请先删除子级")
    targets = db.scalars(
        select(ToolFolder).where(ToolFolder.path == path, _folder_scope_filter(scope, user))
    ).all()
    if not targets:
        raise HTTPException(status_code=404, detail="分组不存在")
    for f in targets:
        db.delete(f)
    db.commit()
    return {"ok": True}


@router.delete("/{tool_id}")
def delete_tool(
    tool_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    t = db.get(Tool, tool_id)
    if t is None or (t.owner_id != user.id and user.role != "admin"):
        raise HTTPException(status_code=404, detail="工具不存在")
    db.delete(t)
    db.commit()
    return {"ok": True}


class MoveToolIn(BaseModel):
    folder: str  # 目标分组（空 = 未分组）


@router.patch("/{tool_id}/move")
def move_tool(
    tool_id: int,
    body: MoveToolIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    t = db.get(Tool, tool_id)
    if t is None or (t.owner_id != user.id and user.role != "admin"):
        raise HTTPException(status_code=404, detail="工具不存在")
    t.folder = _clean_folder(body.folder)
    db.commit()
    return _out(t)


# ---------------- 入团队库申请（对标知识库/数据模块审核流） ----------------


@router.post("/{tool_id}/request-team")
def request_team(
    tool_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """个人工具申请加入团队库（成员不能直接发布团队工具；管理员审核）。"""
    t = db.get(Tool, tool_id)
    if t is None or t.owner_id != user.id or t.scope != "personal":
        raise HTTPException(status_code=404, detail="工具不存在")
    if t.team_status == "pending":
        raise HTTPException(status_code=400, detail="已在申请中，请等待管理员审核")
    t.team_status = "pending"
    db.commit()
    return _out(t)


class TeamReviewIn(BaseModel):
    approve: bool
    reason: str = ""


@router.post("/{tool_id}/review-team")
def review_team(
    tool_id: int,
    body: TeamReviewIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    t = db.get(Tool, tool_id)
    if t is None or t.team_status != "pending":
        raise HTTPException(status_code=404, detail="没有待审核的申请")
    if body.approve:
        t.scope = "team"
        t.team_status = "approved"
    else:
        t.team_status = "rejected"
    db.commit()
    return _out(t)


@router.get("/team-requests")
def list_team_requests(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """待审核的入团队库申请（管理员）。"""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    rows = db.scalars(
        select(Tool).where(Tool.team_status == "pending").order_by(Tool.id.desc())
    ).all()
    return [_out(t) for t in rows]


@router.get("/{tool_id}")
def get_tool(
    tool_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """单个工具详情（团队工具全员可见；个人工具仅本人与管理员）。"""
    t = db.get(Tool, tool_id)
    if t is None or (t.scope != "team" and t.owner_id != user.id and user.role != "admin"):
        raise HTTPException(status_code=404, detail="工具不存在")
    return _out(t)


class FromConvIn(BaseModel):
    conversation_id: int


DRAFT_PROMPT = """你是工作台的工具设计师。根据下面这段对话，提炼出一个可复用工具的草案。
判断 kind：持续性角色/人格 → agent；固定流程/方法论 → skill；固定格式产出 → template。
system_prompt 要吸收对话中验证有效的做法，写成可直接使用的角色指令（不含对话本身的具体内容）。

只返回 JSON：
{"name":"工具名（8字内）","icon":"一个emoji","category":"分类（4字内）",
"kind":"agent|skill|template","description":"一句话说明（30字内）",
"system_prompt":"完整系统提示词","openings":["开场问题1","开场问题2"]}

对话内容：
"""


@router.post("/draft-from-conversation")
async def draft_from_conversation(
    body: FromConvIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """把一段对话沉淀为工具草案（不落库，用户在前端确认后走 POST /api/tools 保存）。"""
    conv = db.get(Conversation, body.conversation_id)
    if conv is None or conv.user_id != user.id:
        raise HTTPException(status_code=404, detail="会话不存在")
    msgs = db.scalars(
        select(Message).where(Message.conversation_id == conv.id).order_by(Message.id)
    ).all()
    if not msgs:
        raise HTTPException(status_code=400, detail="会话为空，无法提炼")

    provider = db.scalar(
        select(Provider)
        .where(or_(Provider.owner_id.is_(None), Provider.owner_id == user.id))
        .order_by(Provider.id)
    )
    if provider is None:
        raise HTTPException(status_code=400, detail="没有可用模型，请先配置 API Key")

    transcript = "\n".join(
        f"[{'用户' if m.role == 'user' else 'AI'}] {m.content[:1500]}" for m in msgs[-30:]
    )[:24000]
    try:
        models = json.loads(provider.models_json or "[]")
    except Exception:
        models = []
    model_id = models[0]["id"] if models else "glm-4.6"
    try:
        raw = await complete(
            provider, model_id,
            [{"role": "user", "content": DRAFT_PROMPT + transcript}],
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"模型调用失败：{e}")

    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise HTTPException(status_code=502, detail="模型未返回有效草案，请重试")
    try:
        draft = json.loads(m.group(0))
    except json.JSONDecodeError:
        raise HTTPException(status_code=502, detail="草案解析失败，请重试")

    kind = draft.get("kind") if draft.get("kind") in ("agent", "skill", "template") else "agent"
    openings = [str(o)[:60] for o in draft.get("openings", []) if str(o).strip()][:3]
    return {
        "name": str(draft.get("name", ""))[:32] or "未命名工具",
        "icon": str(draft.get("icon", "🧰"))[:4] or "🧰",
        "category": str(draft.get("category", "通用"))[:16] or "通用",
        "kind": kind,
        "description": str(draft.get("description", ""))[:120],
        "system_prompt": str(draft.get("system_prompt", "")).strip()[:6000] or "你是一个专业助手。",
        "openings": openings,
    }
