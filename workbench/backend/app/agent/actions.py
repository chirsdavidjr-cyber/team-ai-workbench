"""工作台动作执行器：run_tool_action 及辅助函数。"""

import json

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..data_service import sandbox
from ..kb_service import search_kb, visible_docs
from ..models import BatchTask, CollectTask, Dataset, Provider, Todo, Tool, User
from ..routers.kb import apply_kb_op, kb_context_text
from .confirm import DELETE_OPS, PendingConfirmError

def _tool_out(t: Tool) -> dict:
    return {
        "id": t.id,
        "name": t.name,
        "description": t.description,
        "icon": t.icon,
        "category": t.category,
        "kind": t.kind,
        "scope": t.scope,
        "openings": t.openings,
        "allowed_actions": t.allowed_actions,
        "use_count": t.use_count,
    }


# ---------------- 能力面（动作白名单）：工具 = 提示词 + 能力面 ----------------


def _can_edit(t: Tool, user: User) -> bool:
    return t.owner_id == user.id or user.role == "admin"


def _kb_scope(user: User, scope, name: str = "") -> str:
    """知识库动作的目标库：团队库全员可读；写操作（建/改/删/移/整理）仅管理员。
    成员贡献团队内容的正道：先存个人库，再 kb_apply_team 走审核通道。"""
    if scope == "team":
        if user.role != "admin" and name != "list_kb":
            raise ValueError(
                "团队库由管理员统一维护，成员不能直接写入。"
                "请先把内容存入个人库（kb_create_doc，默认收件箱），"
                "再调用 kb_apply_team 申请入团队库，通过管理员审核后全员可见。"
            )
        return "team"
    return "personal"


def _clean_openings(raw) -> str:
    if not isinstance(raw, list):
        return "[]"
    return json.dumps(
        [str(o).strip()[:60] for o in raw if str(o).strip()][:3], ensure_ascii=False
    )


async def run_tool_action(
    db: Session, user: User, name: str, args: dict, allow_delete: bool = False,
    llm: tuple[Provider, str] | None = None,
) -> dict:
    """执行一个工作台动作并返回 JSON 可序列化结果；失败抛 ValueError（回灌给模型自行修正）。
    allow_delete：删除放行（用户确认词 + 服务端确认单对同一对象匹配，由 agent_stream 判定）。
    llm=(provider, model_id)：问数类动作跟随当前对话模型；None 用 env 默认。"""
    if name in DELETE_OPS and not allow_delete:
        raise PendingConfirmError(
            "删除操作尚未获得用户确认：请先向用户复述将删除的对象并询问是否确认；"
            "待用户在对话中明确回复（如「确认」「确定删除」）后，再次删除**同一对象**即可执行。"
            "注意：用户对其他对象的确认不能用于本对象。"
        )
    if name == "list_tools":
        scope = args.get("scope", "all")
        stmt = select(Tool).where((Tool.scope == "team") | (Tool.owner_id == user.id))
        if scope == "personal":
            stmt = stmt.where(Tool.owner_id == user.id, Tool.scope == "personal")
        elif scope == "team":
            stmt = stmt.where(Tool.scope == "team")
        rows = db.scalars(stmt.order_by(Tool.id)).all()
        return {"tools": [_tool_out(t) for t in rows], "count": len(rows)}

    if name == "create_tool":
        tool_name = str(args.get("name", "")).strip()
        system_prompt = str(args.get("system_prompt", "")).strip()
        if not tool_name or not system_prompt:
            raise ValueError("name 与 system_prompt 必填")
        kind = args.get("kind") if args.get("kind") in ("agent", "skill", "template") else "agent"
        t = Tool(
            owner_id=user.id,
            scope="personal",
            name=tool_name[:64],
            description=str(args.get("description", "")).strip()[:255],
            icon=str(args.get("icon", "🧰")).strip()[:16] or "🧰",
            category=str(args.get("category", "通用")).strip()[:32] or "通用",
            kind=kind,
            system_prompt=system_prompt,
            openings_json=_clean_openings(args.get("openings")),
            allowed_actions_json=clean_allowed_actions(args.get("allowed_actions")),
        )
        db.add(t)
        db.commit()
        return {"ok": True, "tool": _tool_out(t), "message": f"已创建个人工具「{t.name}」(id={t.id})"}

    if name == "update_tool":
        t = db.get(Tool, args.get("tool_id"))
        if t is None or not _can_edit(t, user):
            raise ValueError("工具不存在或无权修改")
        if str(args.get("name", "")).strip():
            t.name = str(args["name"]).strip()[:64]
        if args.get("description") is not None:
            t.description = str(args["description"]).strip()[:255]
        if str(args.get("system_prompt", "")).strip():
            t.system_prompt = str(args["system_prompt"]).strip()
        if str(args.get("icon", "")).strip():
            t.icon = str(args["icon"]).strip()[:16]
        if str(args.get("category", "")).strip():
            t.category = str(args["category"]).strip()[:32]
        if args.get("kind") in ("agent", "skill", "template"):
            t.kind = args["kind"]
        if args.get("openings") is not None:
            t.openings_json = _clean_openings(args["openings"])
        if args.get("allowed_actions") is not None:
            t.allowed_actions_json = clean_allowed_actions(args.get("allowed_actions"))
        db.commit()
        return {"ok": True, "tool": _tool_out(t), "message": f"已更新工具「{t.name}」"}

    if name == "delete_tool":
        t = db.get(Tool, args.get("tool_id"))
        if t is None or not _can_edit(t, user):
            raise ValueError("工具不存在或无权删除")
        name_before = t.name
        db.delete(t)
        db.commit()
        return {"ok": True, "message": f"已删除工具「{name_before}」"}

    if name == "search_kb":
        query = str(args.get("query", "")).strip()
        if not query:
            raise ValueError("query 必填")
        scope = args.get("scope") if args.get("scope") in ("all", "team", "personal") else None
        try:
            top_k = max(1, min(int(args.get("top_k", 5)), 10))
        except (TypeError, ValueError):
            top_k = 5
        results = await search_kb(db, user.id, query, scope=scope, top_k=top_k)
        if not results:
            return {"results": [], "count": 0,
                    "message": f"知识库中未检索到与「{query}」相关的内容"}
        return {
            "results": [
                {
                    "doc_id": r["doc_id"],
                    "title": r["doc_title"],
                    "scope": "team" if r["scope"] in ("team", "approved") else "personal",
                    "snippet": r["snippet"],
                }
                for r in results
            ],
            "count": len(results),
        }

    if name == "read_kb_doc":
        title = str(args.get("title", "")).strip()
        if not title:
            raise ValueError("title 必填")
        docs = visible_docs(db, user.id, None)
        hit = next((d for d in docs if d.title == title), None) or next(
            (d for d in docs if title.lower() in d.title.lower()), None
        )
        if hit is None:
            raise ValueError(f"未找到标题包含「{title}」的文档，请先 search_kb 确认标题")
        return {
            "doc_id": hit.id,
            "title": hit.title,
            "scope": hit.scope,
            "path": hit.path,
            "content": hit.content[:TOOL_RESULT_MAX - 500],
        }

    # ---- 知识库管理（复用管家执行器 apply_kb_op） ----
    if name == "list_kb":
        scope = _kb_scope(user, args.get("scope"), name="list_kb")
        return {"scope": scope, "context": kb_context_text(db, user, scope)}

    KB_OPS = {
        "kb_create_folder": lambda a: {"action": "create_folder", "path": a.get("path")},
        "kb_rename_folder": lambda a: {"action": "rename_folder", "old": a.get("old"), "new": a.get("new")},
        "kb_delete_folder": lambda a: {"action": "delete_folder", "path": a.get("path")},
        "kb_move_doc": lambda a: {"action": "move_doc", "title": a.get("title"), "to": a.get("to")},
        "kb_rename_doc": lambda a: {"action": "rename_doc", "title": a.get("title"), "new_title": a.get("new_title")},
        "kb_delete_doc": lambda a: {"action": "delete_doc", "title": a.get("title")},
        "kb_create_doc": lambda a: {"action": "create_doc", "title": a.get("title"), "folder": a.get("folder"), "content": a.get("content")},
        "kb_organize_inbox": lambda a: {"action": "organize_inbox"},
    }
    if name in KB_OPS:
        scope = _kb_scope(user, args.get("scope"), name=name)
        result = await apply_kb_op(db, user, KB_OPS[name](args), scope)
        result = str(result)
        return {"ok": not result.startswith("❌"), "message": result}

    if name == "kb_apply_team":
        title = str(args.get("title", "")).strip()
        if not title:
            raise ValueError("title 必填")
        docs = visible_docs(db, user.id, "personal")
        hit = next((d for d in docs if d.title == title), None) or next(
            (d for d in docs if title.lower() in d.title.lower()), None
        )
        if hit is None:
            raise ValueError(f"个人库未找到《{title}》，请先用 kb_create_doc 存入个人库再申请")
        if hit.team_status == "pending":
            raise ValueError(f"《{title}》已在审核中，请耐心等待管理员终审")
        if hit.team_status == "approved":
            raise ValueError(f"《{title}》已在团队库中")
        from ..routers.kb import request_team
        try:
            res = await request_team(doc_id=hit.id, user=user, db=db)
        except HTTPException as e:
            raise ValueError(str(e.detail))
        ai = res.get("ai_review") or {}
        return {
            "ok": True,
            "message": (
                f"《{hit.title}》已提交入团队库申请（审核单 #{res.get('review_id')}），"
                "AI 预审完成，等待管理员终审"
            ),
            "ai_review": {
                "查重": ai.get("duplicate"),
                "敏感扫描": ai.get("sensitive"),
                "质量评估": ai.get("quality"),
            },
        }

    # ---- 数据集 / 问数 ----

    def _find_dataset(key: str) -> Dataset:
        q = select(Dataset).where((Dataset.scope == "team") | (Dataset.owner_id == user.id))
        if key.isdigit():
            d = db.scalar(q.where(Dataset.id == int(key)))
        else:
            d = db.scalar(q.where(Dataset.name == key))
        if d is None:
            raise ValueError(f"未找到数据集「{key}」，请先 list_datasets 确认名称或 ID")
        return d

    if name == "list_datasets":
        scope = args.get("scope", "all")
        q = select(Dataset).where((Dataset.scope == "team") | (Dataset.owner_id == user.id))
        if scope == "personal":
            q = q.where(Dataset.owner_id == user.id, Dataset.scope == "personal")
        elif scope == "team":
            q = q.where(Dataset.scope == "team")
        rows = db.scalars(q.order_by(Dataset.id.desc())).all()
        return {
            "datasets": [
                {
                    "id": d.id,
                    "name": d.name,
                    "scope": d.scope,
                    "row_count": d.row_count,
                    "columns": [c.get("name") for c in d.profile],
                    "semantic_fields": len(d.semantic.get("columns", {})),
                    "metrics": [m.get("name") for m in d.semantic.get("metrics", [])],
                }
                for d in rows
            ],
            "count": len(rows),
        }

    if name == "query_dataset":
        key = str(args.get("dataset", "")).strip()
        question = str(args.get("question", "")).strip()
        if not key or not question:
            raise ValueError("dataset 与 question 必填")
        ds = _find_dataset(key)
        from ..routers.data import _ask_flow
        try:
            res = await _ask_flow(ds, question, user, db, llm=llm)
        except HTTPException as e:
            raise ValueError(str(e.detail))
        p = res.get("payload") or {}
        err = p.get("error")
        if err:
            return {"ok": False, "dataset": ds.name, "sql": p.get("sql"),
                    "error": f"SQL 执行失败：{err}"}
        return {
            "ok": True,
            "dataset": ds.name,
            "question": question,
            "sql": p.get("sql"),
            "value_column": p.get("value_column"),
            "columns": p.get("columns"),
            "rows": (p.get("rows") or [])[:20],
            "row_count": len(p.get("rows") or []),
            "interpretation": res.get("content") or "",
        }

    if name == "query_data_global":
        question = str(args.get("question", "")).strip()
        if not question:
            raise ValueError("question 必填")
        from ..routers.data import _global_ask_events

        res: dict = {}
        async for ev in _global_ask_events(question, user, db, llm=llm):
            if ev.get("type") in ("result", "error"):
                res = ev
        if not res:
            return {"ok": False, "error": "全局问数流程异常终止"}
        p = res.get("payload") or {}
        tables = [t.get("name", "") for t in (p.get("tables") or [])]
        if res["type"] == "error":
            return {"ok": False, "tables": tables, "sql": p.get("sql"),
                    "error": res.get("detail") or p.get("error") or "查询失败"}
        return {
            "ok": True,
            "tables": tables,
            "question": question,
            "sql": p.get("sql"),
            "value_column": p.get("value_column"),
            "columns": p.get("columns"),
            "rows": (p.get("rows") or [])[:20],
            "row_count": len(p.get("rows") or []),
            "interpretation": res.get("content") or "",
        }

    if name == "preview_dataset":
        ds = _find_dataset(str(args.get("dataset", "")).strip())
        with sandbox() as conn:
            cur = conn.execute(f'SELECT * FROM "{ds.table_name}" LIMIT 5')
            columns = [d[0] for d in cur.description]
            rows = [
                ["" if v is None else str(v)[:40] for v in r]
                for r in cur.fetchall()
            ]
        return {"dataset": ds.name, "columns": columns, "sample_rows": rows}

    if name == "create_dataset":
        # 与上传入库同一管线（profile + 沙库建表），存好即可问数
        import io

        import pandas as pd

        from ..data_service import build_profile, write_dataframe

        csv_text = str(args.get("csv", "")).strip()
        ds_name = str(args.get("name", "")).strip()
        if not csv_text:
            raise ValueError("csv 必填：传完整 CSV 文本（首行表头、逗号分隔；Markdown 表格先转 CSV）")
        try:
            df = pd.read_csv(io.StringIO(csv_text))
        except Exception as e:
            raise ValueError(f"CSV 解析失败：{e}；请确认首行是表头、逗号分隔、每行一条")
        if df.empty or df.shape[1] == 0:
            raise ValueError("表格为空或没有数据列")
        df.columns = [str(c).strip() or f"col_{i + 1}" for i, c in enumerate(df.columns)]
        ds = Dataset(
            owner_id=user.id,
            scope="personal",
            name=(ds_name or "对话数据集")[:120],
            filename=f"{ds_name or 'chat'}.csv",
            table_name="ds_pending",
            folder=str(args.get("folder", "")).strip().strip("/"),
            row_count=int(len(df)),
            profile_json=json.dumps(build_profile(df), ensure_ascii=False),
            semantic_json="{}",  # 语义层由问数时 AI 推断沉淀
        )
        db.add(ds)
        db.flush()
        ds.table_name = f"ds_{ds.id}"
        write_dataframe(ds.table_name, df)
        db.commit()
        return {
            "ok": True,
            "dataset": {
                "id": ds.id, "name": ds.name, "row_count": ds.row_count,
                "columns": [c.get("name") for c in ds.profile],
            },
            "message": (
                f"已创建个人数据集「{ds.name}」(id={ds.id}，{ds.row_count} 行 × {len(df.columns)} 列)，"
                "可直接 query_dataset 问数，或在数据模块查看"
            ),
        }

    # ---- 批量任务（复用 tasks.py 执行管线） ----
    if name == "create_task":
        from ..routers.tasks import MAX_ITEMS, _out as _task_out

        tool = db.get(Tool, args.get("tool_id")) if isinstance(args.get("tool_id"), int) else None
        if tool is None:
            key = str(args.get("tool", "")).strip()
            tool = db.scalar(select(Tool).where(Tool.name == key))
        if tool is None or (tool.scope != "team" and tool.owner_id != user.id):
            raise ValueError("工具不存在，请先 list_tools 确认名称或 id")
        raw = args.get("items")
        if not isinstance(raw, list):
            raise ValueError("items 必须是字符串数组")
        valid = [str(s).strip() for s in raw if str(s).strip()]
        if not valid:
            raise ValueError("没有有效输入条目")
        if len(valid) > MAX_ITEMS:
            raise ValueError(f"单次任务最多 {MAX_ITEMS} 条（收到 {len(valid)} 条），请分批提交")
        task = BatchTask(
            owner_id=user.id,
            tool_id=tool.id,
            name=(str(args.get("name", "")).strip() or f"{tool.name} · {len(valid)}条")[:120],
            total=len(valid),
            items_json=json.dumps(
                [{"input": s, "output": "", "status": "pending", "error": ""} for s in valid],
                ensure_ascii=False,
            ),
        )
        db.add(task)
        db.commit()
        import asyncio

        from ..routers.tasks import _run_task

        asyncio.create_task(_run_task(task.id))  # 后台逐条执行（与任务页创建等效）
        out = _task_out(task)
        return {
            "ok": True,
            "task": out,
            "message": (
                f"批量任务「{out['name']}」已创建（id={task.id}，共 {out['total']} 条），"
                "后台逐条处理中；可稍后调用 get_task 查询进度，或到批量任务页查看完整结果"
            ),
        }

    if name == "list_tasks":
        from ..routers.tasks import _out as _task_out

        rows = db.scalars(
            select(BatchTask)
            .where(BatchTask.owner_id == user.id)
            .order_by(BatchTask.id.desc())
            .limit(10)
        ).all()
        return {
            "tasks": [_task_out(t) for t in rows],
            "count": len(rows),
            "message": f"共 {len(rows)} 个任务" if rows else "暂无批量任务",
        }

    if name == "get_task":
        from ..routers.tasks import _out as _task_out

        t = db.get(BatchTask, args.get("task_id")) if isinstance(args.get("task_id"), int) else None
        if t is None:
            key = str(args.get("name", "")).strip()
            if key:
                t = db.scalar(
                    select(BatchTask)
                    .where(BatchTask.owner_id == user.id, BatchTask.name.like(f"%{key}%"))
                    .order_by(BatchTask.id.desc())
                )
        if t is None or t.owner_id != user.id:
            raise ValueError("任务不存在，请先 list_tasks 确认 id 或名称")
        items = t.items
        sample = [
            {
                "input": (it.get("input") or "")[:80],
                "status": it.get("status"),
                "output": (it.get("output") or "")[:200],
                "error": (it.get("error") or "")[:120],
            }
            for it in items[:5]
        ]
        return {
            **_task_out(t),
            "sample_items": sample,
            "message": (
                f"任务「{t.name}」状态 {t.status}，进度 {t.done}/{t.total}"
                + (f"，失败 {t.failed} 条" if t.failed else "")
                + "；完整结果在批量任务详情页查看"
            ),
        }

    # ---- 后台采集（复用 collect 管线；对话只创建/查询，不在线抓取） ----
    if name == "create_collect_task":
        from ..collect_service import MAX_ITEMS_HARD, run_collect
        from ..collect_sites import SITES, get_site

        key = str(args.get("site", "")).strip()
        site = get_site(key) or next(
            (s for s in SITES if key in (s.name, s.name.replace("国家", "")) or s.name in key), None
        )
        if site is None:
            known = "、".join(f"{s.key}({s.name})" for s in SITES)
            raise ValueError(f"站点不在白名单：{key or '（空）'}。可选：{known}")
        if not site.static_fetch:
            raise ValueError(
                f"{site.name}暂不支持平台内采集：{site.note}。"
                "请改用白名单内可直接采集的站点（iachina/pingan/epicc），"
                "或按外部执行流程出配方与指令"
            )
        want_cols = {str(c).strip() for c in args.get("columns") or [] if str(c).strip()}
        col_urls = [c.url for c in site.columns if not want_cols or c.name in want_cols] or [
            c.url for c in site.columns
        ]
        try:
            max_items = max(1, min(int(args.get("max_items", 20) or 20), MAX_ITEMS_HARD))
        except (TypeError, ValueError):
            max_items = 20
        keywords = [str(k).strip() for k in args.get("keywords") or [] if str(k).strip()]
        task = CollectTask(
            owner_id=user.id,
            name=(str(args.get("name", "")).strip() or f"{site.name}·{'、'.join(c.name for c in site.columns if c.url in col_urls)}")[:128],
            site_key=site.key,
            columns_json=json.dumps(col_urls, ensure_ascii=False),
            keywords_json=json.dumps(keywords, ensure_ascii=False),
            max_items=max_items,
            to_kb=bool(args.get("to_kb", False)),
        )
        db.add(task)
        db.commit()
        import asyncio

        asyncio.create_task(run_collect(task.id))  # 后台限速执行（与采集页创建等效）
        cols_zh = "、".join(c.name for c in site.columns if c.url in col_urls)
        return {
            "ok": True,
            "task": {"id": task.id, "site": site.name, "columns": cols_zh,
                     "max_items": max_items, "keywords": keywords, "to_kb": task.to_kb},
            "message": (
                f"采集任务 #{task.id} 已创建（{site.name}·{cols_zh}，上限 {max_items} 条），"
                "后台限速执行中（约每条 2.5 秒）；可稍后调用 get_collect_task 查进度，"
                "或到采集任务页查看完整结果。完成后自动生成台账数据集可直接问数"
            ),
        }

    if name == "list_collect_tasks":
        from ..routers.collect import _out as _collect_out

        rows = db.scalars(
            select(CollectTask)
            .where(CollectTask.owner_id == user.id)
            .order_by(CollectTask.id.desc())
            .limit(10)
        ).all()
        return {
            "tasks": [{k: v for k, v in _collect_out(t).items()
                       if k in ("id", "name", "site_name", "status", "done", "total",
                                "hindered", "dataset_id", "kb_doc_title")} for t in rows],
            "count": len(rows),
            "message": f"共 {len(rows)} 个采集任务" if rows else "暂无采集任务",
        }

    if name == "get_collect_task":
        from ..routers.collect import _out as _collect_out

        t = db.get(CollectTask, args.get("task_id")) if isinstance(args.get("task_id"), int) else None
        if t is None:
            key = str(args.get("name", "")).strip()
            if key:
                t = db.scalar(
                    select(CollectTask)
                    .where(CollectTask.owner_id == user.id, CollectTask.name.like(f"%{key}%"))
                    .order_by(CollectTask.id.desc())
                )
        if t is None or t.owner_id != user.id:
            raise ValueError("采集任务不存在，请先 list_collect_tasks 确认 id 或名称")
        items = [i for i in t.items if i.get("status") == "success"]
        sample = [
            {
                "title": (it.get("title") or "")[:60],
                "url": it.get("url"),
                "published_at": it.get("published_at"),
                "content_len": it.get("content_len"),
                "desensitized": it.get("desensitized", []),
            }
            for it in items[:5]
        ]
        return {
            **{k: v for k, v in _collect_out(t).items() if k != "items"},
            "success_items": len(items),
            "sample_items": sample,
            "message": (
                f"采集任务「{t.name}」状态 {t.status}，进度 {t.done}/{t.total}"
                + (f"，受阻 {t.hindered} 条" if t.hindered else "")
                + (f"；已生成数据集 #{t.dataset_id} 可 query_dataset 问数" if t.dataset_id else "")
                + (f"；已入库《{t.kb_doc_title}》" if t.kb_doc_title else "")
                + "；完整条目在采集任务详情页查看"
            ),
        }

    # ---- 待办（与工作台待办模块同库同构） ----
    if name == "create_todo":
        title = str(args.get("title", "")).strip()
        if not title:
            raise ValueError("title 必填")
        due = str(args.get("due_date", "")).strip() or None
        if due and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", due):
            raise ValueError("due_date 需为 YYYY-MM-DD 格式")
        t = Todo(owner_id=user.id, title=title[:256], due_date=due)
        db.add(t)
        db.commit()
        return {
            "ok": True,
            "todo": {"id": t.id, "title": t.title, "due_date": t.due_date},
            "message": f"已创建待办「{t.title}」" + (f"（截止 {due}）" if due else ""),
        }

    if name == "list_todos":
        rows = db.scalars(
            select(Todo)
            .where(Todo.owner_id == user.id)
            .order_by(Todo.done, Todo.id.desc())
            .limit(100)
        ).all()
        undone = sum(1 for t in rows if not t.done)
        return {
            "todos": [
                {"id": t.id, "title": t.title, "due_date": t.due_date, "done": t.done}
                for t in rows
            ],
            "count": len(rows),
            "message": f"共 {len(rows)} 条待办，未完成 {undone} 条" if rows else "暂无待办",
        }

    if name == "complete_todo":
        t = db.get(Todo, args.get("todo_id")) if isinstance(args.get("todo_id"), int) else None
        if t is None:
            key = str(args.get("title", "")).strip()
            if key:
                t = db.scalar(
                    select(Todo)
                    .where(Todo.owner_id == user.id, Todo.title.like(f"%{key}%"))
                    .order_by(Todo.id.desc())
                )
        if t is None or t.owner_id != user.id:
            raise ValueError("待办不存在，请先 list_todos 确认 id 或标题")
        t.done = True
        db.commit()
        return {"ok": True, "message": f"已完成待办「{t.title}」"}

    raise ValueError(f"未知动作：{name}")


