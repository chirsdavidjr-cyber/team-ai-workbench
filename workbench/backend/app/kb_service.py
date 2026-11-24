"""知识库服务：切块、向量化（可降级）、混合检索。"""

import json
import math
import os
import re

import numpy as np
from openai import AsyncOpenAI
from sqlalchemy import select, or_, and_
from sqlalchemy.orm import Session

from .models import KbChunk, KbDoc, Provider

# 向量模式配置：为空或调用失败自动降级为关键词检索
EMBED_BASE_URL = os.getenv("KB_EMBEDDING_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
EMBED_API_KEY = os.getenv("KB_EMBEDDING_API_KEY", "")
EMBED_MODEL = os.getenv("KB_EMBEDDING_MODEL", "embedding-3")

CHUNK_TARGET = 500  # 目标块大小（字符）
CHUNK_MAX = 800


# ---------- 切块 ----------

def chunk_markdown(text: str) -> list[str]:
    """按标题/空行切段落，合并到目标大小。"""
    sections = re.split(r"\n(?=#{1,3}\s)", text)
    chunks: list[str] = []
    buf = ""
    for sec in sections:
        sec = sec.strip()
        if not sec:
            continue
        if len(buf) + len(sec) + 1 <= CHUNK_TARGET:
            buf = f"{buf}\n{sec}" if buf else sec
        else:
            if buf:
                chunks.append(buf)
            if len(sec) <= CHUNK_MAX:
                buf = sec
            else:
                # 超长段按句号/换行二次切分
                parts = re.split(r"(?<=[。！？\n])", sec)
                buf = ""
                for p in parts:
                    if len(buf) + len(p) <= CHUNK_TARGET:
                        buf += p
                    else:
                        if buf:
                            chunks.append(buf)
                        if len(p) <= CHUNK_MAX:
                            buf = p
                        else:
                            # 单句超长（长 URL/代码行）：按 CHUNK_MAX 滑窗切多块，不丢内容
                            for k in range(0, len(p), CHUNK_MAX):
                                chunks.append(p[k:k + CHUNK_MAX])
                            buf = ""
    if buf:
        chunks.append(buf)
    return chunks or [text[:CHUNK_MAX]]


# ---------- 向量化（可降级） ----------

async def embed_texts(texts: list[str]) -> list[list[float]] | None:
    if not EMBED_API_KEY:
        return None
    try:
        client = AsyncOpenAI(base_url=EMBED_BASE_URL.rstrip("/"), api_key=EMBED_API_KEY)
        res = await client.embeddings.create(model=EMBED_MODEL, input=texts)
        return [d.embedding for d in res.data]
    except Exception:
        return None  # 降级：不写向量，检索走关键词


# ---------- 检索 ----------

def _bigrams(s: str) -> set[str]:
    s = re.sub(r"[\s。，、；：？！,.:;?!()\[\]【】《》\"'`#*_\-|>]+", "", s)
    return {s[i : i + 2] for i in range(len(s) - 1)} if len(s) > 1 else {s}


def keyword_score(query: str, text: str) -> float:
    q, t = _bigrams(query), _bigrams(text)
    if not q or not t:
        return 0.0
    return len(q & t) / math.sqrt(len(q) * len(t))


def visible_docs(db: Session, user_id: int, scope: str | None) -> list[KbDoc]:
    """检索可见范围：团队池 = scope=team 或 team_status=approved；个人池 = 本人 ready 文档。"""
    q = select(KbDoc).where(KbDoc.status == "ready")
    if scope == "team":
        q = q.where(or_(KbDoc.scope == "team", KbDoc.team_status == "approved"))
    elif scope == "personal":
        q = q.where(KbDoc.scope == "personal", KbDoc.owner_id == user_id)
    else:  # all：团队池 + 自己的个人池
        q = q.where(
            or_(
                KbDoc.scope == "team",
                KbDoc.team_status == "approved",
                and_(KbDoc.owner_id == user_id, KbDoc.scope == "personal"),
            )
        )
    return list(db.scalars(q))


# 进程级向量缓存：chunk_id -> (embedding_json 指纹, 归一化向量/None)，避免每次检索重复 loads+normalize
_VEC_CACHE: dict[int, tuple[str, "np.ndarray | None"]] = {}


def _get_cached_vec(chunk_id: int, embedding_json: str) -> "np.ndarray | None":
    hit = _VEC_CACHE.get(chunk_id)
    if hit is not None and hit[0] == embedding_json:
        return hit[1]
    try:
        v = np.array(json.loads(embedding_json), dtype=np.float32)
        n = float(np.linalg.norm(v))
        v = (v / n) if n else None
    except (json.JSONDecodeError, ValueError):
        v = None
    if len(_VEC_CACHE) > 100_000:  # 防膨胀：超限整体重建（冷启动代价可接受）
        _VEC_CACHE.clear()
    _VEC_CACHE[chunk_id] = (embedding_json, v)
    return v


async def search_kb(
    db: Session, user_id: int, query: str, scope: str | None = None, top_k: int = 5
) -> list[dict]:
    """混合检索：优先向量余弦（矩阵化一次计算 + 进程级向量缓存），向量缺失的 chunk 走关键词得分。"""
    docs = visible_docs(db, user_id, scope)
    if not docs:
        return []
    doc_map = {d.id: d for d in docs}
    chunks = db.scalars(
        select(KbChunk).where(KbChunk.doc_id.in_(list(doc_map)))
    ).all()
    if not chunks:
        return []

    q_vec = await embed_texts([query])
    q_vec = q_vec[0] if q_vec else None

    scored: list[tuple[float, KbChunk]] = []
    if q_vec:
        q = np.array(q_vec, dtype=np.float32)
        qn = float(np.linalg.norm(q)) or 1.0
        q = q / qn
        vec_idx: list[int] = []          # 走向量打分的 chunk 下标
        mat_rows: list[np.ndarray] = []
        kw_idx: list[int] = []           # 无向量（或向量损坏）走关键词的 chunk 下标
        for i, c in enumerate(chunks):
            if c.embedding_json:
                v = _get_cached_vec(c.id, c.embedding_json)
                if v is not None:
                    mat_rows.append(v)
                    vec_idx.append(i)
                    continue
            kw_idx.append(i)
        if mat_rows:
            sims = np.vstack(mat_rows) @ q  # 一次矩阵乘得到全部余弦（行向量已归一化）
            for sim, i in zip(sims.tolist(), vec_idx):
                scored.append((float(sim), chunks[i]))
        for i in kw_idx:
            scored.append((keyword_score(query, chunks[i].text), chunks[i]))
    else:
        for c in chunks:
            scored.append((keyword_score(query, c.text), c))

    scored.sort(key=lambda x: x[0], reverse=True)

    results: list[dict] = []
    seen_docs: set[int] = set()
    for score, c in scored:
        if score <= 0.01:
            continue
        d = doc_map[c.doc_id]
        # 每篇文档最多取 2 块，避免单篇霸屏
        if sum(1 for r in results if r["doc_id"] == d.id) >= 2:
            continue
        results.append(
            {
                "doc_id": d.id,
                "doc_title": d.title,
                "scope": d.scope,
                "snippet": c.text[:600],
                "score": round(score, 4),
                "mode": "vector" if (c.embedding_json and q_vec) else "keyword",
            }
        )
        if len(results) >= top_k:
            break
    return results


def build_kb_prompt(results: list[dict]) -> str:
    """把检索结果组装成带编号的 system 提示。"""
    blocks = []
    for i, r in enumerate(results, 1):
        scope_label = "团队库" if r["scope"] in ("team", "approved") else "个人库"
        blocks.append(f"[{i}] 来源：{r['doc_title']}（{scope_label}）\n{r['snippet']}")
    context = "\n\n".join(blocks)
    return (
        "你是团队 AI 工作台的助手。以下是知识库检索到的参考片段：\n\n"
        f"{context}\n\n"
        "回答要求：\n"
        "1. 优先依据参考片段回答，引用时在句末标注编号，如 [1] [2]；\n"
        "2. 若参考片段与问题无关或不足以回答，请明确说明\"知识库中未找到充分依据\"，再基于通用知识补充并注明；\n"
        "3. 不要编造参考片段中不存在的内容。"
    )


# ---------- 入库团队库 AI 预审 ----------

SENSITIVE_PATTERNS = {
    "手机号": re.compile(r"1[3-9]\d{9}"),
    "身份证号": re.compile(r"\d{17}[\dXx]"),
    "银行卡号": re.compile(r"\d{16,19}"),
}

KB_LLM_PROVIDER_ID = int(os.getenv("KB_LLM_PROVIDER_ID", "1"))
KB_LLM_MODEL = os.getenv("KB_LLM_MODEL", "glm-5.3")


async def _kb_llm(db: Session, system: str, user: str) -> str:
    import httpx2

    provider = db.get(Provider, KB_LLM_PROVIDER_ID)
    if provider is None:
        raise ValueError("知识库审核模型未配置")
    client = AsyncOpenAI(
        base_url=provider.base_url.rstrip("/"),
        api_key=provider.api_key,
        http_client=httpx2.AsyncClient(trust_env=False, timeout=60),
    )
    res = await client.chat.completions.create(
        model=KB_LLM_MODEL,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.2,
    )
    return res.choices[0].message.content or ""


async def ai_preliminary_review(db: Session, doc: KbDoc) -> dict:
    """AI 预审：查重（确定性打分）+ 敏感扫描（规则）+ 质量评估（LLM）。"""
    # 1. 查重：与团队池已有文档的 bigram 相似度 top1
    team_docs = visible_docs(db, doc.owner_id, "team")
    best_title, best_score = None, 0.0
    for t in team_docs:
        if t.id == doc.id:
            continue
        score = max(
            keyword_score(doc.title, t.title) * 0.6
            + keyword_score(doc.content[:2000], t.content[:2000]) * 0.4,
            1.0 if doc.title == t.title else 0.0,
        )
        if score > best_score:
            best_title, best_score = t.title, score

    # 2. 敏感扫描
    hits: list[dict] = []
    for kind, pat in SENSITIVE_PATTERNS.items():
        for m in pat.finditer(doc.content):
            frag = doc.content[max(0, m.start() - 8) : m.end() + 8].replace("\n", " ")
            hits.append({"kind": kind, "match": m.group(), "context": frag})
            if len(hits) >= 8:
                break

    # 3. 质量评估（LLM，失败不阻断）
    quality = {"score": "", "comment": "模型评估不可用，请人工判断", "suggestion": ""}
    try:
        raw = await _kb_llm(
            db,
            "你是知识质量评审员。对以下知识文档给出简短评估，仅返回 JSON："
            '{"score":"A|B|C","comment":"一句话评价","suggestion":"一条改进建议，可空"}。'
            "评分标准：A=结构完整、信息密度高；B=基本可用；C=过于简略或混乱。",
            f"标题：{doc.title}\n\n{doc.content[:3000]}",
        )
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            parsed = json.loads(m.group(0))
            quality = {
                "score": parsed.get("score", ""),
                "comment": parsed.get("comment", ""),
                "suggestion": parsed.get("suggestion", ""),
            }
    except Exception:
        pass

    return {
        "duplicate": {
            "most_similar": best_title,
            "score": round(best_score, 3),
            "flag": best_score >= 0.75,
        },
        "sensitive": {"count": len(hits), "hits": hits[:5], "flag": len(hits) > 0},
        "quality": quality,
    }
