import io
import json

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from ..data_service import (
    CATALOG_MAX_TABLES,
    build_profile,
    build_metric_sql,
    catalog_text,
    drop_table,
    fewshot_examples,
    gen_semantic_layer,
    interpret_result,
    match_metric,
    merge_hints_into_semantic,
    retrieve_datasets,
    run_readonly_sql,
    sandbox,
    select_tables,
    text2sql,
    text2sql_multi,
    validate_result_shape,
    write_dataframe,
)
from ..deps import get_current_user, get_db
from ..models import DataMessage, Dataset, DatasetFolder, User

router = APIRouter(prefix="/api/data", tags=["datasets"])


def _ds_out(ds: Dataset) -> dict:
    return {
        "id": ds.id,
        "name": ds.name,
        "filename": ds.filename,
        "scope": ds.scope,
        "folder": ds.folder or "",
        "row_count": ds.row_count,
        "profile": ds.profile,
        "semantic": ds.semantic,
        "status": ds.status,
        "summary": ds.summary or "",
        "team_status": ds.team_status or "",
        "created_at": ds.created_at.isoformat() if ds.created_at else None,
    }


def _own_ds(ds_id: int, user: User, db: Session, need_admin_for_team: bool = False) -> Dataset:
    ds = db.get(Dataset, ds_id)
    if ds is None:
        raise HTTPException(status_code=404, detail="数据集不存在")
    if ds.scope == "personal" and ds.owner_id != user.id:
        raise HTTPException(status_code=404, detail="数据集不存在")
    if need_admin_for_team and ds.scope == "team" and user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return ds


@router.post("/upload")
async def upload(
    file: UploadFile = File(...),
    scope: str = Form("personal"),
    name: str = Form(""),
    folder: str = Form(""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if scope not in ("personal", "team"):
        raise HTTPException(status_code=400, detail="scope 参数不合法")
    if scope == "team" and user.role != "admin":
        raise HTTPException(status_code=403, detail="团队数据集上传需要管理员权限")

    filename = file.filename or "dataset"
    data = await file.read()
    try:
        if filename.lower().endswith(".csv"):
            df = pd.read_csv(io.BytesIO(data))
        else:
            xls = pd.read_excel(io.BytesIO(data), sheet_name=0)
            df = xls if isinstance(xls, pd.DataFrame) else xls[0]
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"文件解析失败：{e}")

    if df.empty or df.shape[1] == 0:
        raise HTTPException(status_code=400, detail="表格为空或没有数据列")

    # 列名兜底（空列名按位置补齐）
    df.columns = [str(c).strip() or f"col_{i+1}" for i, c in enumerate(df.columns)]

    target = folder.strip().strip("/")
    ds = Dataset(
        owner_id=user.id,
        scope=scope,
        name=(name.strip() or filename.rsplit(".", 1)[0])[:120],
        filename=filename,
        table_name="ds_pending",
        folder=target,
        row_count=int(len(df)),
        profile_json=json.dumps(build_profile(df), ensure_ascii=False),
        semantic_json="{}",  # 语义层不在上传时生成：由用户上传口径文档或问数时 AI 推断沉淀
    )
    db.add(ds)
    db.flush()
    ds.table_name = f"ds_{ds.id}"
    write_dataframe(ds.table_name, df)
    db.commit()
    return _ds_out(ds)


@router.get("/datasets")
def list_datasets(
    scope: str = "all",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = select(Dataset)
    if scope == "personal":
        q = q.where(Dataset.owner_id == user.id, Dataset.scope == "personal")
    elif scope == "team":
        q = q.where(Dataset.scope == "team")
    else:
        q = q.where((Dataset.scope == "team") | (Dataset.owner_id == user.id))
    rows = db.scalars(q.order_by(Dataset.id.desc())).all()
    return [_ds_out(d) for d in rows]


@router.get("/datasets/{ds_id}")
def get_dataset(
    ds_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    ds = _own_ds(ds_id, user, db)
    out = _ds_out(ds)
    out["messages"] = [
        {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "payload": json.loads(m.payload_json) if m.payload_json else None,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in db.scalars(
            select(DataMessage).where(DataMessage.dataset_id == ds.id).order_by(DataMessage.id)
        ).all()
    ]
    return out


@router.delete("/datasets/{ds_id}")
def delete_dataset(
    ds_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    ds = _own_ds(ds_id, user, db, need_admin_for_team=True)
    drop_table(ds.table_name)
    db.query(DataMessage).where(DataMessage.dataset_id == ds.id).delete()
    db.delete(ds)
    db.commit()
    return {"ok": True}


class SemanticIn(BaseModel):
    semantic: dict


class SemanticImportIn(BaseModel):
    content: str  # 口径文档内容（md/txt 文本，或 json 字符串）


@router.get("/datasets/{ds_id}/preview")
def preview_rows(
    ds_id: int,
    limit: int = 50,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """数据预览：真实前 N 行（表格上传后"先看到我的数据"）。"""
    ds = _own_ds(ds_id, user, db)
    limit = max(1, min(limit, 200))
    try:
        with sandbox() as conn:
            cur = conn.execute(f'SELECT * FROM "{ds.table_name}" LIMIT ?', (limit,))
            columns = [d[0] for d in cur.description]
            rows = [
                [
                    "" if v is None else (v if isinstance(v, (int, float)) else str(v))
                    for v in r
                ]
                for r in cur.fetchall()
            ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取数据失败：{e}")
    return {
        "columns": columns,
        "rows": rows,
        "row_count": ds.row_count,
        "truncated": ds.row_count > len(rows),
    }


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


# ---------------- 全局问数（Trae 式两段式：目录 → 选表 → 多表生成 → 执行解读） ----------------


def _visible_datasets(user: User, db: Session) -> list[Dataset]:
    return db.scalars(
        select(Dataset)
        .where(or_(Dataset.owner_id == user.id, Dataset.scope == "team"))
        .order_by(Dataset.id)
    ).all()


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


# ---------------- 数据集分组（与知识库文件夹同构：占位表 ∪ 数据集聚合） ----------------


def _folder_scope_filter(scope: str, user: User):
    if scope == "team":
        return DatasetFolder.scope == "team"
    return and_(DatasetFolder.scope == "personal", DatasetFolder.owner_id == user.id)


def _ds_scope_filter(scope: str, user: User):
    if scope == "team":
        return Dataset.scope == "team"
    return and_(Dataset.scope == "personal", Dataset.owner_id == user.id)


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
        select(DatasetFolder)
        .where(_folder_scope_filter(scope if scope in ("personal", "team") else "personal", user))
        .order_by(DatasetFolder.sort, DatasetFolder.path)
    ).all()
    return [{"id": f.id, "path": f.path, "scope": f.scope, "sort": f.sort} for f in rows]


@router.post("/folders")
def create_folder(
    body: FolderIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    scope = body.scope if body.scope in ("personal", "team") else "personal"
    if scope == "team" and user.role != "admin":
        raise HTTPException(status_code=403, detail="团队分组需要管理员创建")
    path = body.path.strip().strip("/")
    if not path:
        raise HTTPException(status_code=400, detail="分组路径不合法")
    exists = db.scalar(
        select(DatasetFolder).where(DatasetFolder.path == path, _folder_scope_filter(scope, user))
    )
    if not exists:
        max_sort = db.scalar(
            select(func.max(DatasetFolder.sort)).where(_folder_scope_filter(scope, user))
        )
        db.add(DatasetFolder(owner_id=user.id, scope=scope, path=path, sort=(max_sort or 0) + 1))
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
    """重命名/移动分组，组内数据集与子分组同步移动。"""
    scope = body.scope if body.scope in ("personal", "team") else "personal"
    if scope == "team" and user.role != "admin":
        raise HTTPException(status_code=403, detail="团队分组需要管理员操作")
    old, new = body.old_path.strip("/"), body.new_path.strip("/")
    if not old or not new or old == new:
        raise HTTPException(status_code=400, detail="路径不合法")

    dss = db.scalars(
        select(Dataset).where(
            or_(Dataset.folder == old, Dataset.folder.like(f"{old}/%")), _ds_scope_filter(scope, user)
        )
    ).all()
    for d in dss:
        d.folder = new + d.folder[len(old):]

    subs = db.scalars(
        select(DatasetFolder).where(
            or_(DatasetFolder.path == old, DatasetFolder.path.like(f"{old}/%")),
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
        db.add(DatasetFolder(owner_id=user.id, scope=scope, path=new, sort=0))
    db.commit()
    return {"ok": True, "moved": len(dss)}


class FolderDeleteIn(BaseModel):
    path: str
    scope: str = "personal"


@router.delete("/folders")
def delete_folder(
    body: FolderDeleteIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    scope = body.scope if body.scope in ("personal", "team") else "personal"
    if scope == "team" and user.role != "admin":
        raise HTTPException(status_code=403, detail="团队分组需要管理员操作")
    path = body.path.strip("/")
    has_ds = db.scalar(
        select(func.count()).select_from(Dataset).where(
            or_(Dataset.folder == path, Dataset.folder.like(f"{path}/%")), _ds_scope_filter(scope, user)
        )
    )
    if has_ds:
        raise HTTPException(status_code=400, detail=f"分组内还有 {has_ds} 个数据集，请先移出或删除")
    subs = db.scalars(
        select(DatasetFolder).where(
            DatasetFolder.path.like(f"{path}/%"), _folder_scope_filter(scope, user)
        )
    ).all()
    if subs:
        raise HTTPException(status_code=400, detail="分组内还有子分组，请先删除子级")
    targets = db.scalars(
        select(DatasetFolder).where(DatasetFolder.path == path, _folder_scope_filter(scope, user))
    ).all()
    if not targets:
        raise HTTPException(status_code=404, detail="分组不存在")
    for f in targets:
        db.delete(f)
    db.commit()
    return {"ok": True}


class MoveDsIn(BaseModel):
    folder: str  # 目标分组（空 = 未分组）


@router.patch("/datasets/{ds_id}/move")
def move_dataset(
    ds_id: int,
    body: MoveDsIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ds = _own_ds(ds_id, user, db, need_admin_for_team=True)
    ds.folder = body.folder.strip().strip("/")
    db.commit()
    return _ds_out(ds)


@router.post("/datasets/{ds_id}/summarize")
async def summarize_dataset(
    ds_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """AI 数据速读（对标知识库 AI 摘要）：基于画像与前几行数据，生成一段表内容速览，生成后缓存。"""
    from sqlalchemy import select as _sel

    from ..llm import complete
    from ..models import Provider

    ds = _own_ds(ds_id, user, db, need_admin_for_team=True)  # 团队数据集速读仅管理员可生成
    # 前几行真实数据作样本
    sample_rows: list[list] = []
    try:
        with sandbox() as conn:
            cur = conn.execute(f'SELECT * FROM "{ds.table_name}" LIMIT 3')
            cols = [d[0] for d in cur.description]
            sample_rows = [list(r) for r in cur.fetchall()]
    except Exception:
        cols = [c["name"] for c in ds.profile]
    profile_text = "\n".join(
        f"- {c['name']}（{c['dtype']}，唯一值 {c['distinct']}，样本：{'、'.join(map(str, c['samples'][:3]))}）"
        for c in ds.profile
    )
    sample_text = " | ".join(cols) + "\n" + "\n".join(" | ".join(map(str, r)) for r in sample_rows)

    provider = db.scalar(
        _sel(Provider).where(Provider.owner_id.is_(None), Provider.enabled == True).order_by(Provider.id)  # noqa: E712
    ) or db.scalar(_sel(Provider).order_by(Provider.id))
    if provider is None:
        raise HTTPException(status_code=400, detail="没有可用模型，请先配置")
    try:
        models = json.loads(provider.models_json or "[]")
        text = await complete(
            provider,
            models[0]["id"] if models else "glm-5.3",
            [{"role": "user", "content": (
                "你是数据分析助手。根据数据集画像与样本，用中文写一段 120 字以内的速读：这张表是什么、"
                "关键字段与口径注意点、值得先问的 2 个问题（用「」包住问题）。不要寒暄、不要列表。\n\n"
                f"数据集：{ds.name}（{ds.row_count} 行）\n字段画像：\n{profile_text}\n样本：\n{sample_text}"
            )}],
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"生成失败：{e}")
    ds.summary = text.strip()[:600]
    db.commit()
    return _ds_out(ds)


# ---------------- 入团队库申请（对标知识库审核流） ----------------


@router.post("/datasets/{ds_id}/request-team")
def request_team(
    ds_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """个人数据集申请加入团队库（默认不入；管理员审核）。"""
    ds = db.get(Dataset, ds_id)
    if ds is None or ds.owner_id != user.id or ds.scope != "personal":
        raise HTTPException(status_code=404, detail="数据集不存在")
    if ds.team_status == "pending":
        raise HTTPException(status_code=400, detail="已在申请中，请等待管理员审核")
    ds.team_status = "pending"
    db.commit()
    return _ds_out(ds)


class TeamReviewIn(BaseModel):
    approve: bool
    reason: str = ""


@router.post("/datasets/{ds_id}/review-team")
def review_team(
    ds_id: int,
    body: TeamReviewIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    ds = db.get(Dataset, ds_id)
    if ds is None or ds.team_status != "pending":
        raise HTTPException(status_code=404, detail="没有待审核的申请")
    if body.approve:
        ds.scope = "team"
        ds.team_status = "approved"
    else:
        ds.team_status = "rejected"
    db.commit()
    return _ds_out(ds)


@router.get("/team-requests")
def list_team_requests(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """待审核的入团队库申请（管理员）。"""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    rows = db.scalars(
        select(Dataset).where(Dataset.team_status == "pending").order_by(Dataset.id.desc())
    ).all()
    return [_ds_out(ds) for ds in rows]


# ---------------- 粘贴 CSV 直接建表 ----------------


class CreateCsvIn(BaseModel):
    name: str
    csv: str = Field(min_length=1)
    folder: str = ""
    scope: str = "personal"


@router.post("/create-csv")
def create_from_csv(
    body: CreateCsvIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """粘贴 CSV 文本直接创建数据集（轻量接入方式）。"""
    if body.scope == "team" and user.role != "admin":
        raise HTTPException(status_code=403, detail="团队数据集需要管理员创建")
    try:
        df = pd.read_csv(io.StringIO(body.csv.strip()))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"CSV 解析失败：{e}")
    if df.empty or df.shape[1] == 0:
        raise HTTPException(status_code=400, detail="表格为空或没有数据列")
    df.columns = [str(c).strip() or f"col_{i+1}" for i, c in enumerate(df.columns)]

    ds = Dataset(
        owner_id=user.id,
        scope=body.scope if body.scope in ("personal", "team") else "personal",
        name=(body.name.strip() or "粘贴的数据")[:120],
        filename="pasted.csv",
        table_name="ds_pending",
        folder=body.folder.strip().strip("/"),
        row_count=int(len(df)),
        profile_json=json.dumps(build_profile(df), ensure_ascii=False),
        semantic_json="{}",
    )
    db.add(ds)
    db.flush()
    ds.table_name = f"ds_{ds.id}"
    write_dataframe(ds.table_name, df)
    db.commit()
    return _ds_out(ds)


# ---------------- 数据图谱（数据集 ↔ 字段 ↔ 跨表同名字段） ----------------


@router.get("/graph")
def data_graph(
    scope: str = "all",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """图谱数据：节点=数据集/字段（有语义定义高亮），边=数据集含字段、跨数据集同名字段。"""
    from sqlalchemy import or_

    stmt = select(Dataset).where(
        or_(Dataset.owner_id == user.id, Dataset.scope == "team")
    )
    if scope == "personal":
        stmt = stmt.where(Dataset.scope == "personal", Dataset.owner_id == user.id)
    elif scope == "team":
        stmt = stmt.where(Dataset.scope == "team")
    rows = db.scalars(stmt.order_by(Dataset.id.desc()).limit(200)).all()

    nodes: list[dict] = []
    edges: list[dict] = []
    for ds in rows:
        nodes.append({
            "id": f"ds_{ds.id}", "kind": "dataset", "title": ds.name,
            "scope": ds.scope, "rows": ds.row_count,
        })
        sem = ds.semantic or {}
        defined = set((sem.get("columns") or {}).keys())
        for c in ds.profile:
            fname = c["name"]
            fid = f"f_{fname}"
            # 字段节点只加一次：同名字段跨数据集共享同一个节点，即天然形成跨表关联
            if not any(n["id"] == fid for n in nodes):
                nodes.append({
                    "id": fid, "kind": "field", "title": fname,
                    "defined": fname in defined,
                    "dtype": c["dtype"],
                })
            edges.append({"source": f"ds_{ds.id}", "target": fid})
    return {"nodes": nodes, "edges": edges}
