import json
import re

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..agent import WORKBENCH_HINT, agent_stream, expand_actions, scoped_hint
from ..deps import get_current_user, get_db
from ..kb_service import build_kb_prompt, search_kb
from ..models import Conversation, Message, Provider, Tool, User
from .kb import extract_file

router = APIRouter(prefix="/api/chat", tags=["chat"])

MAX_CONTEXT_MESSAGES = 40  # 最近 N 条作为上下文


class ChatIn(BaseModel):
    conversation_id: int
    provider_id: int
    model_id: str = Field(min_length=1, max_length=64)
    content: str = Field(min_length=1, max_length=32_000)
    use_kb: bool = False  # 开启后检索知识库（个人库 + 团队库）作为参考上下文（带引用）
    tool_id: int | None = None  # 使用工具（agent/skill）时注入其系统提示词


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


# 函数调用轨迹行（> 🔧 调用 / > ✅ `..` / > ❌ `..`）：仅用于前端展示，
# 回喂模型前剥离，防止模型模仿轨迹格式伪造调用
_TRACE_RE = re.compile(r"^> (🔧 调用 |✅ `|❌ `).*\n?", re.M)


def _strip_trace(text: str) -> str:
    return _TRACE_RE.sub("", text)


def _can_use_provider(p: Provider, user: User) -> bool:
    return p.owner_id is None or p.owner_id == user.id


@router.post("/upload")
async def chat_upload(
    files: list[UploadFile] = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """对话附件：解析文本/识别图片供 AI 阅读，不写知识库。
    用户在对话中要求入库时，AI 通过 kb_create_doc 等函数直接执行（从收件箱开始走整理链路）。"""
    out, skipped = [], []
    for f in files[:5]:  # 单次最多 5 个
        name = f.filename or ""
        data = await f.read()
        try:
            text = await extract_file(db, user, name, data)
        except ValueError as e:
            skipped.append({"name": name, "reason": str(e)})
            continue
        except Exception:
            skipped.append({"name": name, "reason": "解析失败（文件可能损坏或加密）"})
            continue
        out.append({"name": name, "text": text[:12000], "size": len(data)})
    return {"files": out, "skipped": skipped}


@router.post("")
async def chat(
    body: ChatIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conv = db.get(Conversation, body.conversation_id)
    if conv is None or conv.user_id != user.id:
        raise HTTPException(status_code=404, detail="会话不存在")
    provider = db.get(Provider, body.provider_id)
    if provider is None or not _can_use_provider(provider, user):
        raise HTTPException(status_code=404, detail="模型供应商不存在")
    if provider.owner_id is None and not provider.enabled:
        raise HTTPException(status_code=400, detail="该供应商已被管理员停用")

    # 保存用户消息；首条消息即时生成会话标题（与模型调用成败无关）
    user_msg = Message(
        conversation_id=conv.id, role="user", content=body.content
    )
    db.add(user_msg)
    if conv.title == "新对话":
        conv.title = body.content.strip().replace("\n", " ")[:24] or "新对话"
    db.commit()

    # 组装上下文
    history = (
        db.scalars(
            select(Message)
            .where(Message.conversation_id == conv.id)
            .order_by(Message.id.desc())
            .limit(MAX_CONTEXT_MESSAGES)
        )
        .all()[::-1]
    )
    llm_messages = [
        {
            "role": m.role,
            "content": _strip_trace(m.content) if m.role == "assistant" else m.content,
        }
        for m in history
    ]

    # 工具（agent/skill）：注入系统提示词；声明了能力面白名单时收窄可调用动作
    tool_system = ""
    tool_actions: list[str] | None = None
    tool = db.get(Tool, body.tool_id) if body.tool_id else None
    if tool is not None:
        if tool.scope != "team" and tool.owner_id != user.id:
            raise HTTPException(status_code=404, detail="工具不存在")
        tool_system = tool.system_prompt
        tool_actions = expand_actions(tool.allowed_actions)
        tool.use_count += 1
        if conv.tool_id is None:  # 会话首次使用的工具固定为其发起工具（供行研动态等聚合）
            conv.tool_id = tool.id
        db.commit()

    # 知识库检索（use_kb 开关）：命中则注入带编号的参考上下文
    citations: list[dict] = []
    kb_system = ""
    if body.use_kb:
        results = await search_kb(db, user.id, body.content, scope="all", top_k=5)
        if results:
            citations = [
                {
                    "n": i,
                    "doc_id": r["doc_id"],
                    "title": r["doc_title"],
                    "snippet": r["snippet"],
                    "mode": r["mode"],
                }
                for i, r in enumerate(results, 1)
            ]
            kb_system = build_kb_prompt(results)

    # 恒定 harness：始终提供工作台函数能力，由模型自动判断是否调用（普通问答不调用）；
    # 带能力面的工具：系统说明同步收窄（只介绍白名单内动作），避免模型被全量清单误导。
    # 身份行：告知模型当前用户角色，避免其猜测权限（管理员被误判成成员→该直写团队库时却走申请审核）
    base_hint = scoped_hint(tool.name, tool_actions) if (tool is not None and tool_actions) else WORKBENCH_HINT
    identity = (
        f"当前用户：{user.name or user.email}"
        f"（{'管理员，可直接操作团队库' if user.role == 'admin' else '普通成员'}）。"
    )
    system_parts = [p for p in (base_hint, identity, tool_system, kb_system) if p]
    llm_messages = [
        {"role": "system", "content": "\n\n".join(system_parts)}
    ] + llm_messages

    async def agent_event_stream():
        try:
            async for ev in agent_stream(
                db, user, conv, provider, body.model_id, llm_messages,
                user_text=body.content, allowed_actions=tool_actions,
            ):
                yield _sse(ev)
        except Exception as e:  # 网络错误 / 鉴权失败 / 限流等
            detail = getattr(e, "message", None) or str(e)
            yield _sse({"type": "error", "detail": f"模型调用失败：{detail}"})

    return StreamingResponse(
        agent_event_stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
