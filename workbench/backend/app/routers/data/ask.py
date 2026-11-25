"""单表问数（同步 + SSE）。"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ...data_service import (
    build_metric_sql,
    fewshot_examples,
    interpret_result,
    match_metric,
    run_readonly_sql,
    text2sql,
    validate_result_shape,
)
from ...deps import get_current_user, get_db
from ...models import DataMessage, Dataset, User
from .common import _own_ds

router = APIRouter()

class AskIn(BaseModel):
    question: str


@router.post("/datasets/{ds_id}/ask")
async def ask(
    ds_id: int,
    body: AskIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """问数主流程（非流式，agent/程序调用）：指标模板/few-shot/形态校验全量生效。"""
    ds = _own_ds(ds_id, user, db)
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="问题不能为空")
    return await _ask_flow(ds, question, user, db)


async def _ask_flow(ds: Dataset, question: str, user: User, db: Session,
                    llm: tuple | None = None) -> dict:
    """问数核心流程（非流式壳）。llm=(provider, model_id)：agent 对话内调用时跟随当前对话模型。"""
    final: dict = {}
    async for ev in _ask_events(ds, question, user, db, llm=llm):
        if ev.get("type") == "result":
            final = ev
    return {"id": final.get("id"), "content": final.get("content", ""), "payload": final.get("payload")}


async def _ask_events(ds: Dataset, question: str, user: User, db: Session,
                      llm: tuple | None = None):
    """单表问数事件流（SSE 与非流式共用）：
    指标注册表受控执行（命中即确定性生成，失败回退模型）→ few-shot 注入生成 → 执行（失败自修正一次）
    → 结果形态校验（可疑二次修正一次）→ 解读；消息落库。"""
    db.add(DataMessage(dataset_id=ds.id, role="user", content=question))
    db.commit()

    notes: list[str] = []
    sql, chart_type, title, value_col, result, error, hints = "", "table", question[:30], "", None, None, {}
    metric_used = False
    examples: list[tuple[str, str]] = []
    try:
        examples = fewshot_examples(db, [ds], user.id)
        hit = match_metric(question, [ds])
        if hit:
            _, metric, dim = hit
            yield {"type": "stage", "stage": "sql",
                   "detail": f"命中注册指标「{metric['name']}」，按固定口径模板生成 SQL（不经模型）…"}
            sql = build_metric_sql(ds, metric, dim)
            chart_type = "bar" if dim else "table"
            value_col = str(metric["name"])
            metric_used = True
            yield {"type": "stage", "stage": "run", "detail": "正在执行查询…", "sql": sql}
            try:
                result = run_readonly_sql(sql)
                notes.append(f"已按注册指标「{metric['name']}」固定口径受控执行")
            except Exception as e:
                # 口径表达式与表结构不符等 → 回退模型生成
                notes.append(f"指标「{metric['name']}」模板执行失败（{e}），已回退模型生成")
                sql, result, metric_used = "", None, False
        if result is None:
            yield {"type": "stage", "stage": "sql", "detail": "正在理解问题并生成 SQL…"}
            plan = await text2sql(ds, question, db, llm=llm, examples=examples)
            sql, chart_type, title = plan["sql"], plan["chart_type"], plan["title"]
            value_col = plan.get("value_column") or ""
            hints = plan.get("semantic_hints") or {}
            yield {"type": "stage", "stage": "run", "detail": "正在执行查询…", "sql": sql}
            try:
                result = run_readonly_sql(sql)
            except Exception as e1:
                yield {"type": "stage", "stage": "sql", "detail": "执行出错，正在自动修正 SQL…"}
                plan = await text2sql(ds, question, db, failed_sql=sql, failed_err=str(e1),
                                      llm=llm, examples=examples)
                sql, chart_type, title = plan["sql"], plan["chart_type"], plan["title"]
                value_col = plan.get("value_column") or value_col
                hints = plan.get("semantic_hints") or hints
                result = run_readonly_sql(sql)
        # 结果形态校验（受控执行的结果是确定性的，不再触发）
        if result is not None and not metric_used:
            issue = validate_result_shape(result, value_col)
            if issue:
                yield {"type": "stage", "stage": "sql",
                       "detail": f"结果形态可疑（{issue}），正在二次校验修正…"}
                try:
                    plan = await text2sql(ds, question, db, llm=llm, examples=examples,
                                          shape_feedback=issue, prev_sql=sql)
                    r2 = run_readonly_sql(plan["sql"])
                    sql, chart_type, title = plan["sql"], plan["chart_type"], plan["title"]
                    value_col = plan.get("value_column") or value_col
                    hints = plan.get("semantic_hints") or hints
                    result = r2
                except Exception:
                    notes.append(f"首次结果{issue}，二次修正未成功，已保留首次结果")
    except Exception as e:
        error = str(e)

    interpretation, payload = "", None
    if result is not None:
        # SQL 执行成功才沉淀字段推断，避免错误推断污染语义层
        if hints:
            new_sem, merged = merge_hints_into_semantic(ds.semantic, hints)
            if merged:
                ds.semantic_json = json.dumps(new_sem, ensure_ascii=False)
                db.commit()
        yield {"type": "stage", "stage": "interpret", "detail": "查询成功，AI 正在解读结果…"}
        try:
            interpretation = await interpret_result(question, sql, result, db, llm=llm)
        except Exception:
            interpretation = ""
        payload = {
            "sql": sql,
            "chart_type": chart_type,
            "title": title,
            "value_column": value_col or None,
            "columns": result["columns"],
            "rows": result["rows"],
            "truncated": result["truncated"],
            "error": None,
            "notes": notes or None,
        }
    else:
        payload = {"sql": sql or None, "chart_type": None, "title": None,
                   "columns": [], "rows": [], "truncated": False, "error": error,
                   "notes": notes or None}
    msg = DataMessage(
        dataset_id=ds.id, role="assistant", content=interpretation,
        payload_json=json.dumps(payload, ensure_ascii=False),
    )
    db.add(msg)
    db.commit()
    yield {"type": "result", "id": msg.id, "content": interpretation, "payload": payload}


@router.post("/datasets/{ds_id}/ask-stream")
async def ask_stream(
    ds_id: int,
    body: AskIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """问数 SSE：stage（指标命中/生成SQL→执行→形态校验→解读）分阶段事件 + result 终态；失败可整段重试。"""
    from fastapi.responses import StreamingResponse

    ds = _own_ds(ds_id, user, db)
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="问题不能为空")

    def sse(ev: dict) -> str:
        return f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

    async def flow():
        async for ev in _ask_events(ds, question, user, db):
            yield sse(ev)

    return StreamingResponse(
        flow(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )



