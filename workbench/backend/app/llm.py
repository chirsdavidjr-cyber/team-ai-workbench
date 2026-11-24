"""模型调用层：统一走 OpenAI 兼容协议。"""

from typing import AsyncIterator

import httpx2
from openai import AsyncOpenAI

from .models import Provider


def client_for(provider: Provider) -> AsyncOpenAI:
    # trust_env=False：显式绕过环境代理变量，避免沙箱/内网代理不稳定导致偶发超时
    http_client = httpx2.AsyncClient(trust_env=False, timeout=120)
    return AsyncOpenAI(
        base_url=provider.base_url.rstrip("/"),
        api_key=provider.api_key,
        http_client=http_client,
    )


async def stream_completion(
    provider: Provider, model_id: str, messages: list[dict]
) -> AsyncIterator[str]:
    """流式返回增量文本；异常向上抛出由调用方处理。"""
    client = client_for(provider)
    stream = await client.chat.completions.create(
        model=model_id, messages=messages, stream=True
    )
    async for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        text = getattr(delta, "content", None)
        if text:
            yield text


async def complete(
    provider: Provider, model_id: str, messages: list[dict]
) -> str:
    """非流式一次性返回完整文本（用于 JSON 指令/草案生成等内部调用）。"""
    client = client_for(provider)
    res = await client.chat.completions.create(
        model=model_id, messages=messages, stream=False
    )
    return res.choices[0].message.content or ""


async def list_remote_models(provider: Provider) -> list[dict]:
    """拉取供应商的可用模型列表（OpenAI 兼容 /models）。失败返回空列表。"""
    try:
        client = client_for(provider)
        res = await client.models.list()
        return [{"id": m.id, "label": m.id} for m in res.data]
    except Exception:
        return []
