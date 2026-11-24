"""问数服务：沙库执行、数据画像、语义层生成、Text2SQL、解读。"""

import io
import json
import math
import os
import re
import sqlite3
from pathlib import Path

import pandas as pd
import httpx2
from openai import AsyncOpenAI
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .config import BASE_DIR
from .models import DataMessage, Dataset, Provider

SANDBOX_DB = Path(os.getenv("DATASETS_DB", BASE_DIR / "datasets.db"))

# 问数所用模型（providers 表 id + 模型 id）
DATA_LLM_PROVIDER_ID = int(os.getenv("DATA_LLM_PROVIDER_ID", "1"))
DATA_LLM_MODEL = os.getenv("DATA_LLM_MODEL", "glm-5.3")

MAX_RESULT_ROWS = 500


# ---------- 沙库 ----------

def sandbox() -> sqlite3.Connection:
    conn = sqlite3.connect(SANDBOX_DB)
    conn.row_factory = sqlite3.Row
    # 关闭双引号字符串回退：引号内的不存在列名报错（而非被当作字符串字面量静默返回垃圾值），
    # 坏口径/坏字段才能触发自修正回路（Python 3.12+ 提供 setconfig，失败则忽略保持默认）
    for cfg in ("SQLITE_DBCONFIG_DQS_DML", "SQLITE_DBCONFIG_DQS_DDL"):
        try:
            conn.setconfig(getattr(sqlite3, cfg), False)
        except (AttributeError, ValueError):
            pass
    return conn


def write_dataframe(table_name: str, df: pd.DataFrame) -> None:
    with sandbox() as conn:
        df.to_sql(table_name, conn, if_exists="replace", index=False)


def drop_table(table_name: str) -> None:
    with sandbox() as conn:
        conn.execute(f'DROP TABLE IF EXISTS "{table_name}"')


def _strip_sql_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|attach|detach|pragma|vacuum|reindex)\b",
    re.IGNORECASE,
)


def run_readonly_sql(sql: str, limit: int = MAX_RESULT_ROWS) -> dict:
    """只读执行：仅允许单条 SELECT/WITH；返回 {columns, rows}。异常抛出 ValueError。"""
    sql = _strip_sql_comments(sql or "").strip().rstrip(";").strip()
    if not sql:
        raise ValueError("SQL 为空")
    if ";" in sql:
        raise ValueError("仅允许单条 SQL 语句")
    head = sql.split(None, 1)[0].lower() if sql else ""
    if head not in ("select", "with"):
        raise ValueError("仅允许 SELECT 查询")
    if FORBIDDEN.search(sql):
        raise ValueError("检测到写操作关键字，已拦截")

    with sandbox() as conn:
        cur = conn.execute(f"SELECT * FROM ({sql}) LIMIT {limit + 1}")
        columns = [d[0] for d in cur.description]
        rows = [list(r) for r in cur.fetchall()]
    truncated = len(rows) > limit
    if truncated:
        rows = rows[:limit]

    def _clean(v):
        if v is None or isinstance(v, (int, float, str, bool)):
            return v
        return str(v)

    return {
        "columns": columns,
        "rows": [[_clean(v) for v in r] for r in rows],
        "truncated": truncated,
    }


# ---------- 数据画像 ----------

def build_profile(df: pd.DataFrame) -> list[dict]:
    total = len(df)
    cols = []
    for c in df.columns:
        s = df[c]
        non_null = int(s.notna().sum())
        distinct = int(s.nunique(dropna=True))
        samples = s.dropna().astype(str).unique()[:5].tolist()
        cols.append(
            {
                "name": str(c),
                "dtype": str(s.dtype),
                "non_null": non_null,
                "null_rate": round(1 - non_null / total, 4) if total else 0,
                "distinct": distinct,
                "samples": samples,
            }
        )
    return cols


# ---------- LLM ----------

async def _data_llm(db: Session, system: str, user: str, llm: tuple[Provider, str] | None = None) -> str:
    """llm=(provider, model_id)：调用方显式指定模型（如对话内问数跟随当前对话模型）；
    None 时用环境变量默认（后台任务/定时任务等无对话上下文的场景）。"""
    provider, model = llm if llm else (db.get(Provider, DATA_LLM_PROVIDER_ID), DATA_LLM_MODEL)
    if provider is None:
        raise ValueError("问数模型未配置（DATA_LLM_PROVIDER_ID）")
    model = model or DATA_LLM_MODEL
    client = AsyncOpenAI(
        base_url=provider.base_url.rstrip("/"),
        api_key=provider.api_key,
        http_client=httpx2.AsyncClient(trust_env=False, timeout=120),
    )
    res = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.1,
    )
    return res.choices[0].message.content or ""


def _extract_json(text: str) -> dict | None:
    """从 LLM 回复中提取 JSON（容忍 ```json 包裹与前后说明文字）。"""
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    else:
        s, e = text.find("{"), text.rfind("}")
        if s >= 0 and e > s:
            text = text[s : e + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _schema_text(ds: Dataset, with_semantic: bool = True) -> str:
    lines = [f'表 "{ds.table_name}"（数据集：{ds.name}，{ds.row_count} 行）字段：']
    sem = ds.semantic if with_semantic else {}
    col_sem = sem.get("columns", {}) if sem else {}
    for c in ds.profile:
        name = c["name"]
        s = col_sem.get(name, {})
        desc = s.get("desc", "")
        enum = s.get("enum", {})
        enum_str = (
            "；枚举：" + "，".join(f"{k}={v}" for k, v in list(enum.items())[:12])
            if enum
            else ""
        )
        samples = "，".join(c["samples"][:3])
        lines.append(
            f'- "{name}"（{c["dtype"]}，空值率{c["null_rate"]:.0%}，示例：{samples}）'
            + (f" 业务含义：{desc}" if desc else "")
            + enum_str
        )
    metrics = sem.get("metrics", []) if sem else []
    for m in metrics:
        lines.append(f'- 指标「{m.get("name")}」= {m.get("expr")}（{m.get("desc","")}）')
    return "\n".join(lines)


async def gen_semantic_layer(ds: Dataset, db: Session) -> dict:
    """AI 生成语义层草稿：每列中文含义 + 枚举翻译 + 常用指标。"""
    system = (
        "你是数据语义层专家。根据表结构与样本值，为每个字段推断业务含义。"
        '仅返回 JSON：{"columns":{"字段名":{"desc":"中文业务含义","enum":{"值":"中文标签"}}},'
        '"metrics":[{"name":"指标名","expr":"SQL 表达式","desc":"口径说明"}]}。'
        "枚举仅对取值少且明显是编码的字段给出；指标最多 5 个；没有把握的字段 desc 留空字符串。"
    )
    raw = await _data_llm(db, system, _schema_text(ds, with_semantic=False))
    parsed = _extract_json(raw)
    if not parsed or "columns" not in parsed:
        return {"columns": {}, "metrics": []}
    parsed.setdefault("metrics", [])
    return parsed


_T2SQL_RULES = (
    '仅返回 JSON：{"sql":"...","chart_type":"bar|line|pie|table","title":"结果标题",'
    '"value_column":"指标列名","semantic_hints":{"字段名":"中文业务含义"}}。\n'
    "要求：单条 SELECT；字符串日期可用 strftime/substr 处理；聚合结果列用 AS 命名为可读中文；"
    "图表类型按数据形态选（趋势选 line，对比选 bar，占比选 pie，明细或 3 列以上维度选 table）；"
    "只使用给出的字段，不要编造字段。\n"
    "value_column 必须是 SELECT 结果中直接回答用户问题的那个指标列的列名（AS 后的名字），"
    "它是前端画图的数值列，务必准确；明细/全字段类问题没有单一指标时返回空字符串。\n"
    "列顺序约定（聚合类问题时遵守；明细/全字段类问题可多列平铺，不受此限）：第 1 列为分组维度"
    "（如渠道/月份），第 2 列为核心指标值（即 value_column 指向的列）；"
    "辅助列（如分子分母）放在其后，非必要不返回。核心指标列用 AS 命名为指标名（如 AS \"接通率\"）。\n"
    "semantic_hints：结合你的通用行业知识与字段名/样本值，对**语义层中还没有业务含义的字段**给出推断"
    "（仅这些字段，已有含义的不用重复）；没有可推断的就返回空对象。"
)


def _examples_block(examples: list[tuple[str, str]] | None) -> str:
    """few-shot 历史示例 prompt 块（近期成功问答对，供参考写法与口径）。"""
    if not examples:
        return ""
    lines = ["历史成功问答示例（仅供参考写法与口径，当前问题未必相同，不要照抄数字）："]
    for i, (q, s) in enumerate(examples, 1):
        lines.append(f"例{i} 问题：{q[:80]}\nSQL：{s[:500]}")
    return "\n".join(lines) + "\n\n"


def _shape_feedback_block(shape_feedback: str | None, prev_sql: str | None) -> str:
    """结果形态可疑的二次修正提示块。"""
    if not shape_feedback:
        return ""
    return (
        f"\n\n注意：上一条 SQL 执行成功但结果可疑：{shape_feedback}。\n"
        f"上一条 SQL：{prev_sql}\n"
        "请检查筛选条件与字段口径后重新生成；若你确信原结果本身合理（如答案确为 0），可返回相同 SQL。"
    )


async def text2sql(ds: Dataset, question: str, db: Session, failed_sql: str | None = None,
                   failed_err: str | None = None, llm: tuple[Provider, str] | None = None,
                   examples: list[tuple[str, str]] | None = None,
                   shape_feedback: str | None = None, prev_sql: str | None = None) -> dict:
    """问题 → {sql, chart_type, title, value_column, semantic_hints}。
    value_column：回答用户问题的指标列名（前端图表据此选数值列，不靠猜）。
    semantic_hints：模型对语义层中尚无定义字段的现场推断，执行成功后沉淀回语义层。
    examples：few-shot 历史成功示例（问题→SQL）；shape_feedback/prev_sql：结果形态可疑时的二次修正。"""
    system = (
        "你是 SQLite 数据分析 SQL 专家。根据表结构与已有语义层，把用户问题翻译成一条 SELECT 语句。\n"
        + _T2SQL_RULES
    )
    user = _examples_block(examples) + f"{_schema_text(ds)}\n\n用户问题：{question}"
    if failed_sql:
        user += (
            f"\n\n注意：之前生成的 SQL 执行失败，请修正后重试。\n"
            f"失败 SQL：{failed_sql}\n错误信息：{failed_err}"
        )
    user += _shape_feedback_block(shape_feedback, prev_sql)
    raw = await _data_llm(db, system, user, llm=llm)
    parsed = _extract_json(raw)
    if not parsed or not parsed.get("sql"):
        raise ValueError("模型未能生成有效 SQL，请换个问法或补充语义层")
    parsed.setdefault("chart_type", "table")
    parsed.setdefault("title", question[:30])
    parsed.setdefault("value_column", "")
    parsed.setdefault("semantic_hints", {})
    return parsed


def merge_hints_into_semantic(semantic: dict, hints: dict) -> tuple[dict, int]:
    """把字段含义合并进语义层（只填空，不覆盖用户定义）。
    hints 兼容两种值：{"字段": "含义"} 或 {"字段": {"desc": "...", "enum": {...}}}。"""
    columns = dict(semantic.get("columns", {}))
    merged = 0
    for col, val in (hints or {}).items():
        if isinstance(val, dict):
            desc = str(val.get("desc", "")).strip()
            enum = val.get("enum") or None
        else:
            desc, enum = str(val).strip(), None
        if not desc:
            continue
        existing = columns.get(col)
        if existing and existing.get("desc"):
            if enum and not existing.get("enum"):  # 补充枚举但不覆盖 desc
                columns[col] = {**existing, "enum": enum}
            continue  # 用户或先前已定义，不覆盖
        columns[col] = {**(existing or {}), "desc": desc, **({"enum": enum} if enum else {})}
        merged += 1
    new_sem = {**semantic, "columns": columns}
    return new_sem, merged


async def interpret_result(
    question: str, sql: str, result: dict, db: Session, llm: tuple[Provider, str] | None = None
) -> str:
    system = (
        "你是数据解读专家。根据用户问题、SQL 与查询结果，用中文给出简明解读："
        "关键数值结论、明显的趋势或异常、需要留意的口径。不超过 150 字，直接输出文字。"
    )
    preview = result["rows"][:20]
    user = (
        f"问题：{question}\nSQL：{sql}\n结果列：{result['columns']}\n前 {len(preview)} 行：\n"
        + "\n".join(str(r) for r in preview)
        + (f"\n（共 {len(result['rows'])} 行{'，已截断' if result.get('truncated') else ''}）")
    )
    try:
        return await _data_llm(db, system, user, llm=llm)
    except Exception:
        return ""


# ---------- 全局问数（Trae 式两段式：目录 → 选表 → 多表生成） ----------

CATALOG_MAX_TABLES = 60
CATALOG_MAX_CHARS = 12000


def catalog_text(datasets: list[Dataset]) -> tuple[str, bool]:
    """可见数据集压成目录页（选表用）。返回 (目录文本, 是否截断)。"""
    lines = ["数据表目录（[数据集id] 名称 | 物理表名 | 行数 | 用途摘要，下一行为字段摘要）："]
    truncated = len(datasets) > CATALOG_MAX_TABLES
    total = 0
    for ds in datasets[:CATALOG_MAX_TABLES]:
        sem = ds.semantic or {}
        col_sem = sem.get("columns", {})
        fields = []
        for c in ds.profile[:10]:
            name = c["name"]
            s = col_sem.get(name, {})
            desc = str(s.get("desc", "")).strip()[:20]
            enum = s.get("enum") or {}
            tail = f"（{'/'.join(str(k) for k in list(enum)[:5])}）" if enum else ""
            fields.append(f"{name}:{desc}{tail}" if desc else f"{name}{tail}")
        more = f" 等共{len(ds.profile)}列" if len(ds.profile) > 10 else ""
        metrics = [str(m.get("name")) for m in sem.get("metrics", []) if m.get("name")]
        summary = (ds.summary or "").strip()[:40]
        block = (
            f"[{ds.id}] {ds.name} | 表 {ds.table_name} | {ds.row_count}行"
            + (f" | {summary}" if summary else "")
            + (f" | 指标：{'、'.join(metrics[:5])}" if metrics else "")
            + "\n  字段: " + "，".join(fields) + more
        )
        if total + len(block) > CATALOG_MAX_CHARS and len(lines) > 1:
            truncated = True
            break
        lines.append(block)
        total += len(block)
    if len(datasets) > CATALOG_MAX_TABLES:
        lines.append(f"（表过多，仅列前 {CATALOG_MAX_TABLES} 张，共 {len(datasets)} 张）")
    return "\n".join(lines), truncated


async def select_tables(
    question: str, datasets: list[Dataset], db: Session, llm: tuple[Provider, str] | None = None
) -> list[Dataset]:
    """目录 + 问题 → LLM 选 1~3 张最相关表（id 必须来自目录，天然过滤幻觉 id）。"""
    catalog, _ = catalog_text(datasets)
    system = (
        "你是数据表路由专家。根据数据表目录，为用户问题选出回答它所需的表。\n"
        "规则：按表的用途摘要与字段语义匹配；选 1~3 张，宁少勿多——能用一张表回答就只选一张；"
        "id 必须来自目录中真实存在的数据集 id。\n"
        '仅返回 JSON：{"tables":[数据集id, ...],"reason":"一句话理由"}；'
        "目录中没有能回答问题的表时返回空数组。"
    )
    user = f"{catalog}\n\n用户问题：{question}"
    by_id = {d.id: d for d in datasets}
    prompt = user
    for _attempt in range(2):  # 输出非法/无效 id 时重试一次
        raw = await _data_llm(db, system, prompt, llm=llm)
        parsed = _extract_json(raw)
        ids = parsed.get("tables") if isinstance(parsed, dict) else None
        if isinstance(ids, list):
            picked: list[Dataset] = []
            for i in ids:
                try:
                    key = int(i)
                except (TypeError, ValueError):
                    continue
                if key in by_id and key not in {p.id for p in picked}:
                    picked.append(by_id[key])
            if picked:
                return picked[:3]
        prompt = user + "\n\n注意：上一次输出无法解析或 id 不在目录中，请严格只返回 JSON，且只使用目录内的数据集 id。"
    raise ValueError("未能从数据表目录中选出相关表，请换个问法，或进入具体数据集提问")


def _join_hints(datasets: list[Dataset]) -> list[str]:
    """候选表两两同名字段 → join 候选键提示。"""
    hints = []
    for a in range(len(datasets)):
        for b in range(a + 1, len(datasets)):
            da, other = datasets[a], datasets[b]
            names_a = {c["name"] for c in da.profile}
            common = [c["name"] for c in other.profile if c["name"] in names_a]
            if common:
                hints.append(
                    f'表 "{da.table_name}" 与 "{other.table_name}" 同名字段：'
                    + "、".join(f'"{n}"' for n in common[:8])
                )
    return hints


async def text2sql_multi(
    datasets: list[Dataset], question: str, db: Session,
    failed_sql: str | None = None, failed_err: str | None = None,
    llm: tuple[Provider, str] | None = None,
    examples: list[tuple[str, str]] | None = None,
    shape_feedback: str | None = None, prev_sql: str | None = None,
) -> dict:
    """多表版 text2sql：单表时直接走原 text2sql；多表给 join 候选键并约束 JOIN 行为。"""
    if len(datasets) == 1:
        return await text2sql(datasets[0], question, db,
                              failed_sql=failed_sql, failed_err=failed_err, llm=llm,
                              examples=examples, shape_feedback=shape_feedback, prev_sql=prev_sql)
    schema_parts = [_schema_text(d) for d in datasets]
    joins = _join_hints(datasets)
    join_block = (
        "候选 JOIN 键（两表同名字段，须结合业务语义确认后再用）：\n"
        + "\n".join(f"- {h}" for h in joins)
        if joins
        else "这些表之间没有同名字段；若确需关联，连接条件中的字段必须真实存在于对应表中。"
    )
    system = (
        "你是 SQLite 数据分析 SQL 专家。下面给出多张表的结构与语义层，把用户问题翻译成一条 SELECT 语句。\n"
        "多表规则：先判断能否只用其中一张表回答，能则不要 JOIN；确需多表时优先使用候选 JOIN 键，"
        '并给引用列加表名前缀（如 "ds_1"."渠道"）；多表结果中的同名列必须用 AS 重命名区分；'
        "不要编造字段。\n"
        + _T2SQL_RULES
    )
    user = (
        _examples_block(examples)
        + "\n\n".join(schema_parts) + "\n\n" + join_block + f"\n\n用户问题：{question}"
    )
    if failed_sql:
        user += (
            f"\n\n注意：之前生成的 SQL 执行失败，请修正后重试。\n"
            f"失败 SQL：{failed_sql}\n错误信息：{failed_err}"
        )
    user += _shape_feedback_block(shape_feedback, prev_sql)
    raw = await _data_llm(db, system, user, llm=llm)
    parsed = _extract_json(raw)
    if not parsed or not parsed.get("sql"):
        raise ValueError("模型未能生成有效 SQL，请换个问法或补充语义层")
    parsed.setdefault("chart_type", "table")
    parsed.setdefault("title", question[:30])
    parsed.setdefault("value_column", "")
    parsed.setdefault("semantic_hints", {})
    return parsed


# ---------- ① 指标注册表 + 模板受控执行 ----------
# 注册表即语义层 semantic.metrics（口径文档导入 / AI 草稿 / 手工编辑三来源统一沉淀）。
# 问题命中注册指标名时按固定口径确定性生成 SQL（不经模型），杜绝固定口径指标的生成偏差。


def _detect_dim(question: str, ds: Dataset) -> str | None:
    """从问题中识别分组维度：该表字段名或其业务含义出现在问题里。"""
    col_sem = (ds.semantic or {}).get("columns", {})
    for c in ds.profile:
        name = c["name"]
        if name and name in question:
            return name
        desc = str(col_sem.get(name, {}).get("desc", "")).strip()
        if desc and len(desc) >= 2 and desc in question:
            return name
    return None


def match_metric(question: str, datasets: list[Dataset]):
    """问题命中注册指标 → (数据集, 指标dict, 分组维度|None)；未命中返回 None。
    规则：指标名（≥2 字）出现在问题中；带 expr 才可受控执行。"""
    for ds in datasets:
        for m in (ds.semantic or {}).get("metrics", []):
            name = str(m.get("name", "")).strip()
            if len(name) >= 2 and name in question and str(m.get("expr", "")).strip():
                return ds, m, _detect_dim(question, ds)
    return None


def build_metric_sql(ds: Dataset, metric: dict, dim: str | None) -> str:
    """按注册口径确定性拼装 SQL：有维度则分组聚合，无维度则整体单值。"""
    name, expr = metric["name"], metric["expr"]
    if dim:
        return (
            f'SELECT "{dim}" AS "{dim}", {expr} AS "{name}" '
            f'FROM "{ds.table_name}" GROUP BY "{dim}" ORDER BY 2 DESC'
        )
    return f'SELECT {expr} AS "{name}" FROM "{ds.table_name}"'


# ---------- ② few-shot 历史示例 ----------


def fewshot_examples(
    db: Session, datasets: list[Dataset], user_id: int, limit: int = 3
) -> list[tuple[str, str]]:
    """近期成功问答对（问题→SQL），注入 prompt 作 few-shot。
    来源：这些数据集内问数记录（团队数据集共享口径）+ 本人全局问数中涉及这些表的记录。"""
    ids = [d.id for d in datasets]
    if not ids:
        return []
    rows = db.scalars(
        select(DataMessage)
        .where(or_(DataMessage.dataset_id.in_(ids), DataMessage.owner_id == user_id))
        .order_by(DataMessage.id.desc())
        .limit(400)
    ).all()
    idset = set(ids)
    by_id = {m.id: m for m in rows}
    picked: list[tuple[str, str]] = []
    seen_q: set[str] = set()
    for m in reversed(rows):  # 时间正序遍历，取最近的
        if m.role != "assistant" or not m.payload_json:
            continue
        try:
            payload = json.loads(m.payload_json)
        except json.JSONDecodeError:
            continue
        sql = (payload or {}).get("sql") or ""
        if not sql or (payload or {}).get("error"):
            continue
        if m.dataset_id is None:  # 全局问数：须涉及当前表
            if not idset.intersection(payload.get("dataset_ids") or []):
                continue
        # 配对的用户问题：assistant 前最近一条 user 消息（只在本次取回窗口内找）
        q = ""
        for pid in range(m.id - 1, 0, -1):
            prev = by_id.get(pid)
            if prev is not None and prev.role == "user":
                q = prev.content.strip()
                break
            if prev is not None and prev.role == "assistant":
                break  # 越过上一组问答仍未找到，放弃配对
        if not q or q in seen_q:
            continue
        seen_q.add(q)
        picked.append((q, sql))
        if len(picked) >= limit:
            break
    return picked


# ---------- ③ 结果形态校验 ----------


def validate_result_shape(result: dict, value_column: str = "") -> str | None:
    """SQL 执行成功后校验结果形态，可疑返回问题描述（触发一次二次修正），正常返回 None。"""
    rows, cols = result.get("rows") or [], result.get("columns") or []
    if not rows:
        return "结果为 0 行（可能筛选条件过严或字段口径不符）"
    if all(v is None for r in rows for v in r):
        return "所有字段值全为空"
    if value_column and value_column in cols:
        i = cols.index(value_column)
        if all(r[i] is None for r in rows):
            return f"指标列「{value_column}」全为空"
    return None


# ---------- ④ 表多目录超限时的候选检索（先关键词后 embedding） ----------
# 说明：FTS5 的 unicode61/trigram 对中文长句分词支持差（MATCH 过严或漏召回），
# 改用「词汇倒排」：以表名/字段名/语义含义/枚举/指标名为词表对问题计分，天然适配中文；
# 全未命中时用 embedding 语义兜底（复用知识库嵌入配置），仍失败则退回按 id 截断。


def _ds_score(question: str, ds: Dataset) -> int:
    """问题与数据集元数据的词汇命中计分（名字/指标权重高，含义/枚举权重低）。"""
    score = 0
    if len(ds.name) >= 2 and ds.name in question:
        score += 3
    sem = ds.semantic or {}
    for m in sem.get("metrics", []):
        n = str(m.get("name", ""))
        if len(n) >= 2 and n in question:
            score += 3
    col_sem = sem.get("columns", {})
    for c in ds.profile:
        name = c["name"]
        if name and name in question:
            score += 2
        s = col_sem.get(name, {})
        desc = str(s.get("desc", "")).strip()
        if desc and len(desc) >= 2 and desc in question:
            score += 1
        for k in list((s.get("enum") or {}).keys()):
            if len(str(k)) >= 2 and str(k) in question:
                score += 1
                break
    if ds.summary and len(ds.summary) >= 2 and any(
        w in question for w in re.split(r"[\s，。；、,.;]", ds.summary) if len(w) >= 2
    ):
        score += 1
    return score


async def retrieve_datasets(
    question: str, datasets: list[Dataset], k: int = 40
) -> tuple[list[Dataset], str]:
    """目录放不下时按问题检索最相关候选表。返回 (候选列表, 检索方式说明)。"""
    scored = [(_ds_score(question, d), d) for d in datasets]
    hits = [(s, d) for s, d in scored if s > 0]
    if hits:
        hits.sort(key=lambda x: (-x[0], x[1].id))
        return [d for _, d in hits[:k]], "关键词"
    # embedding 语义兜底
    try:
        from .kb_service import embed_texts

        cards = []
        for d in datasets:
            sem = d.semantic or {}
            fields = "、".join(
                f"{c['name']}({str((sem.get('columns', {}).get(c['name'], {}) or {}).get('desc', ''))[:12]})"
                for c in d.profile[:15]
            )
            metrics = "、".join(str(m.get("name")) for m in sem.get("metrics", []) if m.get("name"))
            cards.append(f"数据表：{d.name}。摘要：{d.summary or ''} 字段：{fields} 指标：{metrics}")
        vecs = await embed_texts([question] + cards)
        if vecs:
            qv = vecs[0]

            def _cos(a: list[float], b: list[float]) -> float:
                na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
                if not na or not nb:
                    return 0.0
                return sum(x * y for x, y in zip(a, b)) / (na * nb)

            ranked = sorted(zip(datasets, vecs[1:]), key=lambda x: -_cos(qv, x[1]))
            return [d for d, _ in ranked[:k]], "语义"
    except Exception:
        pass
    return datasets[:k], "截断"
