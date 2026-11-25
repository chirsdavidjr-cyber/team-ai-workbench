"""全局问数（多表选表 → 生成 → 执行解读）与历史消息。"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...data_service import (
    CATALOG_MAX_TABLES,
    build_metric_sql,
    catalog_text,
    interpret_result,
    match_metric,
    retrieve_datasets,
    run_readonly_sql,
    select_tables,
    text2sql_multi,
    validate_result_shape,
)
from ...deps import get_current_user, get_db
from ...models import DataMessage, Dataset, User
from .common import _visible_datasets

router = APIRouter()

async def _global_ask_events(
    question: str, user: User, db: Session, llm: tuple | None = None
):
    """全局问数事件流：select（目录超限先检索）→ selected → sql（指标受控|模型生成）→ run(→修正)
    → 形态校验(→二次修正) → interpret → result | error。消息落库（dataset_id 为空 + owner_id 归属）。"""
    datasets = _visible_datasets(user, db)
    if not datasets:
        yield {"type": "error", "detail": "还没有可问的数据集，请先上传或加入团队库"}
        return

    db.add(DataMessage(dataset_id=None, owner_id=user.id, role="user", content=question))
    db.commit()

    notes: list[str] = []
    _, catalog_truncated = catalog_text(datasets)
    if catalog_truncated or len(datasets) > CATALOG_MAX_TABLES:
        # 目录放不下：先按问题检索出候选表再组目录
        yield {"type": "stage", "stage": "select",
               "detail": f"可见数据表较多（{len(datasets)} 张），正在按问题检索相关表…"}
        candidates, how = await retrieve_datasets(question, datasets)
        notes.append(
            f"可见数据表 {len(datasets)} 张超出目录上限，已按{how}检索出最相关的 "
            f"{len(candidates)} 张表组成候选目录"
        )
    else:
        candidates = datasets
    tables_meta = lambda ds_list: [{"id": d.id, "name": d.name} for d in ds_list]  # noqa: E731

    sql, chart_type, title, value_col, result, error, hints = "", "table", question[:30], "", None, None, {}
    picked: list[Dataset] = []
    metric_used = False
    examples: list[tuple[str, str]] = []
    try:
        yield {"type": "stage", "stage": "select",
               "detail": f"正在从 {len(candidates)} 张表中选择相关数据表…"}
        picked = await select_tables(question, candidates, db, llm=llm)
        yield {"type": "stage", "stage": "selected", "tables": tables_meta(picked),
               "detail": "已选定数据表：" + "、".join(d.name for d in picked)}
        examples = fewshot_examples(db, picked, user.id)
        hit = match_metric(question, picked)
        if hit:
            ds_m, metric, dim = hit
            yield {"type": "stage", "stage": "sql",
                   "detail": f"命中注册指标「{metric['name']}」，按固定口径模板生成 SQL（不经模型）…"}
            sql = build_metric_sql(ds_m, metric, dim)
            chart_type = "bar" if dim else "table"
            value_col = str(metric["name"])
            metric_used = True
            yield {"type": "stage", "stage": "run", "detail": "正在执行查询…", "sql": sql}
            try:
                result = run_readonly_sql(sql)
                notes.append(f"已按注册指标「{metric['name']}」固定口径受控执行")
            except Exception as e:
                notes.append(f"指标「{metric['name']}」模板执行失败（{e}），已回退模型生成")
                sql, result, metric_used = "", None, False
        if result is None:
            yield {"type": "stage", "stage": "sql", "detail": "正在理解问题并生成 SQL…"}
            plan = await text2sql_multi(picked, question, db, llm=llm, examples=examples)
            sql, chart_type, title = plan["sql"], plan["chart_type"], plan["title"]
            value_col = plan.get("value_column") or ""
            hints = plan.get("semantic_hints") or {}
            yield {"type": "stage", "stage": "run", "detail": "正在执行查询…", "sql": sql}
            try:
                result = run_readonly_sql(sql)
            except Exception as e1:
                yield {"type": "stage", "stage": "sql", "detail": "执行出错，正在自动修正 SQL…"}
                plan = await text2sql_multi(
                    picked, question, db, failed_sql=sql, failed_err=str(e1), llm=llm,
                    examples=examples,
                )
                sql, chart_type, title = plan["sql"], plan["chart_type"], plan["title"]
                value_col = plan.get("value_column") or value_col
                hints = plan.get("semantic_hints") or hints
                result = run_readonly_sql(sql)
        if result is not None and not metric_used:
            issue = validate_result_shape(result, value_col)
            if issue:
                yield {"type": "stage", "stage": "sql",
                       "detail": f"结果形态可疑（{issue}），正在二次校验修正…"}
                try:
                    plan = await text2sql_multi(
                        picked, question, db, llm=llm, examples=examples,
                        shape_feedback=issue, prev_sql=sql,
                    )
                    r2 = run_readonly_sql(plan["sql"])
                    sql, chart_type, title = plan["sql"], plan["chart_type"], plan["title"]
                    value_col = plan.get("value_column") or value_col
                    hints = plan.get("semantic_hints") or hints
                    result = r2
                except Exception:
                    notes.append(f"首次结果{issue}，二次修正未成功，已保留首次结果")
    except Exception as e:
        error = str(e)

    interpretation = ""
    if result is not None:
        # 仅单表时沉淀字段推断（多表字段含义有歧义风险）
        if hints and len(picked) == 1:
            new_sem, merged = merge_hints_into_semantic(picked[0].semantic, hints)
            if merged:
                picked[0].semantic_json = json.dumps(new_sem, ensure_ascii=False)
                db.commit()
        yield {"type": "stage", "stage": "interpret", "detail": "查询成功，AI 正在解读结果…"}
        try:
            interpretation = await interpret_result(question, sql, result, db, llm=llm)
        except Exception:
            interpretation = ""
        payload = {
            "sql": sql, "chart_type": chart_type, "title": title,
            "value_column": value_col or None,
            "columns": result["columns"], "rows": result["rows"],
            "truncated": result["truncated"], "error": None,
            "dataset_ids": [d.id for d in picked],
            "tables": tables_meta(picked),
            "notes": notes or None,
        }
    else:
        payload = {
            "sql": sql or None, "chart_type": None, "title": None,
            "columns": [], "rows": [], "truncated": False, "error": error,
            "dataset_ids": [d.id for d in picked],
            "tables": tables_meta(picked),
            "notes": notes or None,
        }
    msg = DataMessage(
        dataset_id=None, owner_id=user.id, role="assistant",
        content=interpretation, payload_json=json.dumps(payload, ensure_ascii=False),
    )
    db.add(msg)
    db.commit()
    if result is not None:
        yield {"type": "result", "id": msg.id, "content": interpretation, "payload": payload}
    else:
        yield {"type": "error", "id": msg.id, "detail": error or "查询失败", "payload": payload}


class GlobalAskIn(BaseModel):
    question: str


@router.post("/ask")
async def global_ask(
    body: GlobalAskIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """全局问数（非流式，agent/程序调用）：自动选表 → 生成执行 → 解读。失败不抛 5xx，payload.error 携带原因。"""
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="问题不能为空")
    final: dict = {}
    async for ev in _global_ask_events(question, user, db):
        if ev["type"] in ("result", "error"):
            final = ev
    if not final:
        final = {"type": "error", "detail": "问数流程异常终止", "payload": None}
    return {"id": final.get("id"), "content": final.get("content", ""), "payload": final.get("payload")}


@router.post("/ask-stream")
async def global_ask_stream(
    body: GlobalAskIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """全局问数 SSE：select/selected/sql/run/interpret 分阶段 + result/error 终态。"""
    from fastapi.responses import StreamingResponse

    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="问题不能为空")

    def sse(ev: dict) -> str:
        return f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

    async def flow():
        async for ev in _global_ask_events(question, user, db):
            yield sse(ev)

    return StreamingResponse(
        flow(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/ask/messages")
def global_ask_messages(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """本人全局问数历史（时间正序，最近 20 组问答）。"""
    rows = db.scalars(
        select(DataMessage)
        .where(DataMessage.dataset_id.is_(None), DataMessage.owner_id == user.id)
        .order_by(DataMessage.id.desc())
        .limit(40)
    ).all()
    return [
        {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "payload": json.loads(m.payload_json) if m.payload_json else None,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in reversed(rows)
    ]



