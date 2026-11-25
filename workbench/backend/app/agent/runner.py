"""Agent 流式循环：模型 ⇄ 工具调用。"""

import json
from typing import AsyncIterator

from sqlalchemy.orm import Session

from ..llm import client_for
from ..models import Conversation, Message, Provider, User
from .actions import run_tool_action
from .confirm import (
    PendingConfirmError,
    _match_pending,
    _register_pending,
    _user_confirmed,
)
from .prompts import HANDOVER_HINT
from .specs import filter_tool_specs

MAX_ROUNDS = 6
TOOL_RESULT_MAX = 4000

def _trace_call(name: str, args: dict) -> str:
    brief = "，".join(
        f"{k}={str(v)[:36]}" for k, v in list(args.items())[:3] if v not in (None, "", [])
    )
    return f"\n\n> 🔧 调用 `{name}`" + (f"（{brief}）" if brief else "") + "…\n"


def _trace_result(name: str, status: str, result: dict) -> str:
    if status == "ok":
        msg = result.get("message") or (
            f"返回 {result.get('count')} 条结果" if "count" in result else "完成"
        )
        return f"\n> ✅ `{name}` → {str(msg)[:120]}\n"
    return f"\n> ❌ `{name}` → {str(result.get('error', '失败'))[:120]}\n"


# 伪造轨迹拦截：模型若在正文里编造「> 🔧 调用 / > ✅ `.. / > ❌ `..」行，实时过滤，
# 保证未真实执行的"调用记录"永远不会展示给用户或写入消息。真实轨迹由本模块直接下发，不经过滤器。
FAKE_TRACE_PATTERNS = ("> 🔧 调用 ", "> ✅ `", "> ❌ `")


class _DeltaTraceFilter:
    """流式增量过滤器：按行拦截伪造轨迹（跨 chunk 的不完整行先暂扣，确认不是轨迹再放行）。"""

    def __init__(self) -> None:
        self.buf = ""
        self.dropped = 0

    def _emit_line(self, line: str) -> str:
        if any(line.startswith(p) for p in FAKE_TRACE_PATTERNS):
            self.dropped += 1
            return ""
        return line + "\n"

    def _holding(self) -> bool:
        """缓冲需要暂扣：正在形成某条轨迹（buf 是模式前缀），或已命中轨迹开头（模式是 buf 前缀）。"""
        return any(p.startswith(self.buf) for p in FAKE_TRACE_PATTERNS) or any(
            self.buf.startswith(p) for p in FAKE_TRACE_PATTERNS
        )

    def feed(self, text: str) -> str:
        self.buf += text
        out: list[str] = []
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            out.append(self._emit_line(line))
        # 无换行的残留：可能是不完整/进行中的轨迹行 → 暂扣；否则放行
        if self.buf and not self._holding():
            out.append(self.buf)
            self.buf = ""
        return "".join(out)

    def flush(self) -> str:
        if not self.buf:
            return ""
        if any(self.buf.startswith(p) for p in FAKE_TRACE_PATTERNS):
            self.dropped += 1
            self.buf = ""
            return ""
        out, self.buf = self.buf, ""
        return out


async def agent_stream(
    db: Session,
    user: User,
    conv: Conversation,
    provider: Provider,
    model_id: str,
    messages: list[dict],
    user_text: str = "",
    allowed_actions: list[str] | None = None,
) -> AsyncIterator[dict]:
    """function calling 循环，产出 SSE 事件 dict（delta / tool / done），结束前保存 assistant 消息。

    user_text：本轮用户原话，用于判定删除类操作是否已获用户确认（模型的 confirm 参数不算数）。
    allowed_actions：工具能力面白名单（None/空 = 全部动作）；模型只能调用白名单内的动作。

    事件流：
      delta {text}                          模型文字增量
      reasoning {text}                      推理模型思考增量（实时展示，不入库不回喂）
      tool  {name, args, status}            动作开始（status=calling）
      tool  {name, args, status, result}    动作结束（status=ok|error）
      done  {message_id, conversation_title, citations}
    """
    client = client_for(provider)
    all_text: list[str] = []
    finished = False
    had_calls = False  # 本轮消息流中是否发生过真实工具调用（用于耗尽时的交接引导）
    handed_over = False  # 是否因预算耗尽进入交接轮（决定 done.continuable）
    use_tools = True  # 模型不支持 function calling 时自动降级为普通对话
    allow_delete = _user_confirmed(user_text)  # 删除放行：仅以用户本轮原话为准
    specs = filter_tool_specs(allowed_actions)  # 能力面：白名单外的动作模型看不到

    for round_no in range(MAX_ROUNDS):
        # 最后一轮不再提供工具，强制模型收敛为最终文字回答
        kwargs: dict = {"model": model_id, "messages": messages, "stream": True}
        if round_no < MAX_ROUNDS - 1 and use_tools:
            kwargs["tools"] = specs
        if round_no == MAX_ROUNDS - 1 and had_calls:
            # 预算耗尽的收敛轮：说明情况，引导模型交代进度并告知可回复「继续」接续
            messages.append({"role": "system", "content": HANDOVER_HINT})
            handed_over = True
        try:
            stream = await client.chat.completions.create(**kwargs)
        except Exception:
            if "tools" in kwargs:  # 可能是不支持 tools 参数的模型：去掉后重试
                use_tools = False
                del kwargs["tools"]
                stream = await client.chat.completions.create(**kwargs)
            else:
                raise

        round_text: list[str] = []
        calls: list[dict] = []  # 按 index 聚合流式分片：{id, name, args}
        filt = _DeltaTraceFilter()
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            # 推理模型思考增量（DeepSeek/Qwen 系 reasoning_content，OpenAI 系 reasoning）：
            # 实时透传给前端展示"正在想什么"，消除长思考期间的静默；不入库、不回喂模型
            rc = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
            if isinstance(rc, str) and rc:
                yield {"type": "reasoning", "text": rc}
            if delta.content:
                safe = filt.feed(delta.content)
                if safe:
                    round_text.append(safe)
                    yield {"type": "delta", "text": safe}
            for tc in delta.tool_calls or []:
                i = tc.index or 0
                while len(calls) <= i:
                    calls.append({"id": "", "name": "", "args": ""})
                if tc.id:
                    calls[i]["id"] = tc.id
                if tc.function and tc.function.name:
                    calls[i]["name"] += tc.function.name
                if tc.function and tc.function.arguments:
                    calls[i]["args"] += tc.function.arguments
        tail = filt.flush()
        if tail:
            round_text.append(tail)
            yield {"type": "delta", "text": tail}
        if filt.dropped:  # 拦截到伪造轨迹：告知用户（也留在历史中，警示后续轮次）
            warn = "\n\n> ⚠️ 已拦截模型伪造的函数调用记录（未真实执行的操作不会生效）\n"
            round_text.append(warn)
            yield {"type": "delta", "text": warn}

        all_text.extend(round_text)
        if not calls:
            finished = True
            break  # 模型给出最终回答

        # 回灌 assistant 工具调用消息（OpenAI 协议必需，供模型看到自己发起了什么）
        messages.append({
            "role": "assistant",
            "content": "".join(round_text) or None,
            "tool_calls": [
                {
                    "id": c["id"] or f"call_{round_no}_{i}",
                    "type": "function",
                    "function": {"name": c["name"], "arguments": c["args"] or "{}"},
                }
                for i, c in enumerate(calls)
            ],
        })

        # 逐个执行动作 → 结果回灌（轨迹同时写入消息内容，刷新后仍可见）
        had_calls = True
        for i, c in enumerate(calls):
            try:
                args = json.loads(c["args"]) if c["args"].strip() else {}
                if not isinstance(args, dict):
                    args = {}
            except json.JSONDecodeError:
                args = {}
            trace = _trace_call(c["name"], args)
            all_text.append(trace)
            yield {"type": "delta", "text": trace}
            yield {"type": "tool", "name": c["name"], "args": args, "status": "calling"}
            try:
                # 删除放行双条件：用户确认词（本轮原话）+ 服务端确认单命中同一对象（一次性）
                allow_this = allow_delete and _match_pending(db, conv.id, c["name"], args, user)
                result = await run_tool_action(
                    db, user, c["name"], args, allow_delete=allow_this,
                    llm=(provider, model_id),
                )
                status = "ok"
            except PendingConfirmError as e:
                _register_pending(db, conv.id, c["name"], args, user)  # 登记该对象的确认单，等用户确认
                result = {"ok": False, "error": str(e)}
                status = "error"
            except Exception as e:  # 动作失败也回灌，让模型向用户说明或修正
                result = {"ok": False, "error": str(e)}
                status = "error"
            messages.append({
                "role": "tool",
                "tool_call_id": c["id"] or f"call_{round_no}_{i}",
                "content": json.dumps(result, ensure_ascii=False)[:TOOL_RESULT_MAX],
            })
            trace = _trace_result(c["name"], status, result)
            all_text.append(trace)
            yield {"type": "delta", "text": trace}
            yield {
                "type": "tool", "name": c["name"], "args": args,
                "status": status, "result": result,
            }

    if not finished and not "".join(all_text).strip():
        note = "（已达最大工具调用轮数，请精简指令后重试）"
        all_text.append(note)
        yield {"type": "delta", "text": note}

    assistant_msg = Message(
        conversation_id=conv.id,
        role="assistant",
        content="".join(all_text),
        model=f"{provider.name}/{model_id}",
    )
    db.add(assistant_msg)
    db.commit()
    yield {
        "type": "done",
        "message_id": assistant_msg.id,
        "conversation_title": conv.title,
        "citations": [],
        "continuable": handed_over,  # 轮次耗尽：回复「继续」接续，下一轮恢复全部预算
    }
