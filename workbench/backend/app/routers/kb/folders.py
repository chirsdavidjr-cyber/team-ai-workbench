"""文件夹与工作流骨架、整理收件箱、移动文档。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from ...deps import get_current_user, get_db
from ...models import KbDoc, KbFolder, User
from .common import (
    INGEST_CATEGORIES,
    WORKFLOW_FOLDERS,
    _doc_out,
    _folder_scope_filter,
    _get_editable_doc,
    _guard_skeleton,
    _visible_filter,
)
from .helpers import _ingest

router = APIRouter()

WORKFLOW_FOLDERS = [
    "收件箱",
    "原始资料/文章", "原始资料/论文报告", "原始资料/书籍摘录", "原始资料/视频转录",
    "原始资料/会议记录", "原始资料/灵感摘录", "原始资料/数据表格",
    "来源台账",
    "知识库/概念", "知识库/常见问题", "知识库/综合分析", "知识库/项目", "知识库/主题",
]

INGEST_CATEGORIES = ["文章", "论文报告", "书籍摘录", "视频转录", "会议记录", "灵感摘录", "数据表格"]


@router.post("/init-workflow")
def init_workflow(
    body: dict,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """初始化标准知识管理工作流目录（中文命名，幂等）。"""
    scope = body.get("scope") if body.get("scope") in ("personal", "team") else "personal"
    if scope == "team" and user.role != "admin":
        raise HTTPException(status_code=403, detail="团队库需要管理员初始化")

    created_folders = 0
    for path in WORKFLOW_FOLDERS:
        exists = db.scalar(
            select(KbFolder).where(KbFolder.path == path, _folder_scope_filter(scope, user))
        )
        if not exists:
            db.add(KbFolder(owner_id=user.id, scope=scope, path=path, sort=0))
            created_folders += 1

    # 索引 / 维护日志 / 来源登记（不存在才创建）
    seeds = [
        ("索引", "索引", "# 知识库索引\n\n> 知识库首页。重要入口在这里维护，每次整理后更新。\n\n## 结构导览\n- [[来源登记]] —— 所有资料的来源台账\n- [[维护日志]] —— 每次整理与治理的记录\n- 知识库/ —— 整理后的可复用知识（概念 · 常见问题 · 综合分析 · 项目 · 主题）\n- 原始资料/ —— 按来源形态存放的原始证据\n- 收件箱/ —— 新资料先进这里，等待整理\n\n## 重要页面\n\n（待整理后补充，用 [[页面名]] 链接）\n"),
        ("维护日志", "维护日志", "# 维护日志\n\n| 日期 | 动作 | 对象 | 结果 |\n| --- | --- | --- | --- |\n"),
        ("来源台账/来源登记", "来源登记", "# 来源登记\n\n> 每条资料的来源台账：来源、状态、可信度、是否已编译。\n\n| 日期 | 标题 | 来源形态 | 来源/链接 | 状态 | 已编译 |\n| --- | --- | --- | --- | --- | --- |\n"),
    ]
    created_docs = 0
    for path, title, content in seeds:
        doc_scope = (
            or_(KbDoc.scope == "team", KbDoc.team_status == "approved")
            if scope == "team"
            else and_(KbDoc.scope == "personal", KbDoc.owner_id == user.id)
        )
        exists = db.scalar(select(KbDoc).where(KbDoc.path == f"{path}.md", doc_scope))
        if not exists:
            db.add(
                KbDoc(owner_id=user.id, scope=scope, title=title,
                      path=f"{path}.md", content=content,
                      size=len(content.encode("utf-8")))
            )
            created_docs += 1

    db.commit()
    return {"ok": True, "created_folders": created_folders, "created_docs": created_docs}


@router.post("/ingest")
async def ingest_inbox(
    body: dict,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """一键整理收件箱：AI 基于当前文件夹树动态归类 → 移动 → 登记来源台账 → 高价值内容编译为知识页。
    归类不写死：AI 从现有文件夹中选最合适的目标，也可在现有文件夹下建议新子层级（如 原始资料/湖北/公众号）。"""
    from datetime import date

    from ..kb_service import _kb_llm

    ZONES = ["原始资料", "知识库", "收件箱", "来源台账"]

    scope = body.get("scope") if body.get("scope") in ("personal", "team") else "personal"
    doc_scope = (
        or_(KbDoc.scope == "team", KbDoc.team_status == "approved")
        if scope == "team"
        else and_(KbDoc.scope == "personal", KbDoc.owner_id == user.id)
    )
    inbox_docs = db.scalars(
        select(KbDoc).where(
            or_(KbDoc.path == "收件箱", KbDoc.path.like("收件箱/%")), doc_scope
        ).order_by(KbDoc.id)
    ).all()
    inbox_docs = [d for d in inbox_docs if d.path != "收件箱.md"]  # 排除同名笔记
    if not inbox_docs:
        return {"processed": 0, "report": []}

    # 当前库完整文件夹树（供 AI 动态归类，目录结构变了归类随之变化）
    all_paths = {p for p in db.scalars(select(KbDoc.path).where(doc_scope)) if p}
    folder_set: set[str] = set()
    for p in all_paths:
        parts = p.split("/")
        for i in range(1, len(parts)):
            folder_set.add("/".join(parts[:i]))
    for f in db.scalars(select(KbFolder.path).where(_folder_scope_filter(scope, user))):
        folder_set.add(f)
    folder_tree = "\n".join(sorted(folder_set)) or "（空）"

    # 台账文档（不存在则创建）
    register = db.scalar(select(KbDoc).where(KbDoc.path == "来源台账/来源登记.md", doc_scope))
    if register is None:
        register = KbDoc(
            owner_id=user.id, scope=scope, title="来源登记",
            path="来源台账/来源登记.md",
            content="# 来源登记\n\n| 日期 | 标题 | 归档位置 | 来源/链接 | 状态 | 已编译 |\n| --- | --- | --- | --- | --- | --- |\n",
        )
        db.add(register)
        db.flush()

    today = date.today().isoformat()
    report = []
    for d in inbox_docs:
        # AI 动态归类：从现有文件夹中选目标，或建议现有文件夹下的新子层级
        try:
            raw = await _kb_llm(
                db,
                "你是知识库管理员。当前知识库文件夹结构如下：\n" + folder_tree +
                "\n\n任务：为资料选择归档文件夹并评估价值，仅返回 JSON："
                '{"target":"归档文件夹路径","worth_compiling":true|false,'
                '"source_desc":"来源一句话（从内容推断）",'
                '"compile_note":"若值得编译，给出知识页主题（10字内）；否则空字符串"}。\n'
                "规则：target 必须以「原始资料」开头（新资料一律归原始资料）；"
                "优先复用上面列出的现有文件夹；确无合适的，可在现有文件夹下扩展新子层级"
                "（如按 地区/来源 细分：原始资料/湖北/公众号）；允许新建子层级但不得超过「原始资料」下两层。"
                "worth_compiling 判断标准：内容包含可反复复用的结论/方法/口径，而非一次性事务记录。",
                f"标题：{d.title}\n摘要线索：{(d.summary or '')[:100]}\n\n{d.content[:3000]}",
            )
            m = re.search(r"\{.*\}", raw, re.S)
            parsed = json.loads(m.group(0)) if m else {}
        except Exception:
            parsed = {}

        target = str(parsed.get("target", "")).strip().strip("/")
        top = target.split("/")[0]
        depth_ok = 1 < len(target.split("/")) <= 3
        if not target or top != "原始资料" or not depth_ok:
            target = "原始资料/文章"  # 兜底：结构异常时归默认文件夹
        worth = bool(parsed.get("worth_compiling"))
        source_desc = str(parsed.get("source_desc", "未注明来源"))[:60]
        compile_note = str(parsed.get("compile_note", ""))[:20]

        # 1) 移动并规范命名：<目标>/YYYY-MM-DD-标题.md（目标文件夹随聚合自动出现，无需预建）
        new_path = f"{target}/{today}-{d.title}.md"
        d.path = new_path

        # 2) 登记
        compiled_flag = "是" if worth else "否"
        register.content += f"| {today} | [[{d.title}]] | {target} | {source_desc} | new | {compiled_flag} |\n"
        register.size = len(register.content.encode("utf-8"))

        # 3) 高价值 → 编译知识页（知识库分区下，双链回指原文）
        compiled_doc = None
        if worth and compile_note:
            page_title = f"{compile_note}（{d.title}）"[:60]
            try:
                raw2 = await _kb_llm(
                    db,
                    "你是知识编译官。基于资料提炼一页可复用知识（Markdown）："
                    "# <标题>\n## 核心结论（3条以内，可直接执行/引用）\n## 关键细节\n## 适用场景\n## 来源\n"
                    f"来源区写：- 原始资料：[[{d.title}]]。正文开头不要寒暄。",
                    f"标题：{d.title}\n\n{d.content[:4000]}",
                )
                page_content = raw2.strip()[:6000]
                page_content = re.sub(r"^#.*\n", f"# {page_title}\n", page_content, count=1)
                compiled_doc = KbDoc(
                    owner_id=user.id, scope=scope, title=page_title,
                    path=f"知识库/综合分析/{page_title}.md", content=page_content,
                )
                db.add(compiled_doc)
                db.flush()
                await _reindex_doc(db, compiled_doc)
            except Exception:
                compiled_doc = None

        await _reindex_doc(db, d)
        report.append({
            "title": d.title,
            "category": target,
            "moved_to": new_path,
            "worth_compiling": worth,
            "compiled_page": compiled_doc.title if compiled_doc else None,
        })

    # 维护日志追加
    log = db.scalar(select(KbDoc).where(KbDoc.path == "维护日志.md", doc_scope))
    if log:
        log.content += f"| {today} | 整理收件箱 | {len(inbox_docs)} 篇 | AI 动态归类并登记，编译 {sum(1 for r in report if r['compiled_page'])} 篇 |\n"
        log.size = len(log.content.encode("utf-8"))

    db.commit()
    return {"processed": len(inbox_docs), "report": report}


@router.post("/search")

class FolderIn(BaseModel):
    path: str
    scope: str = "personal"


@router.get("/folders")
def list_folders(
    scope: str = "personal",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """当前库的文件夹（含空文件夹占位），按 sort 与路径排序。"""
    rows = db.scalars(
        select(KbFolder)
        .where(_folder_scope_filter(scope if scope in ("personal", "team") else "personal", user))
        .order_by(KbFolder.sort, KbFolder.path)
    ).all()
    return [
        {"id": f.id, "path": f.path, "scope": f.scope, "sort": f.sort}
        for f in rows
    ]


@router.post("/folders")
def create_folder(
    body: FolderIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    scope = body.scope if body.scope in ("personal", "team") else "personal"
    if scope == "team" and user.role != "admin":
        raise HTTPException(status_code=403, detail="团队文件夹需要管理员创建")
    path = body.path.strip().strip("/")
    if not path:
        raise HTTPException(status_code=400, detail="文件夹路径不合法")
    exists = db.scalar(
        select(KbFolder).where(KbFolder.path == path, _folder_scope_filter(scope, user))
    )
    if not exists:
        max_sort = db.scalar(
            select(func.max(KbFolder.sort)).where(_folder_scope_filter(scope, user))
        )
        db.add(KbFolder(owner_id=user.id, scope=scope, path=path, sort=(max_sort or 0) + 1))
        db.commit()
    return {"ok": True, "path": path}


class FolderRenameIn(BaseModel):
    old_path: str
    new_path: str
    scope: str = "personal"


def _guard_skeleton(*paths: str) -> None:
    """只锁定四个一级分区本身（收件箱/原始资料/知识库/来源台账）；其下子层级任由用户增删改移。"""
    locked = {"收件箱", "原始资料", "知识库", "来源台账"}
    for p in paths:
        if p.strip("/") in locked:
            raise HTTPException(
                status_code=403,
                detail=f"「{p}」是知识工作流的固定分区，不可修改；分区内的子文件夹可自由整理",
            )


@router.patch("/folders")
def rename_folder(
    body: FolderRenameIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """重命名/移动文件夹（new_path 可含新的父层级），同步移动同库下前缀匹配的文档与子文件夹。"""
    scope = body.scope if body.scope in ("personal", "team") else "personal"
    old, new = body.old_path.strip("/"), body.new_path.strip("/")
    if not old or not new or old == new:
        raise HTTPException(status_code=400, detail="路径不合法")
    _guard_skeleton(old, new)

    doc_scope = (
        or_(KbDoc.scope == "team", KbDoc.team_status == "approved")
        if scope == "team"
        else and_(KbDoc.scope == "personal", KbDoc.owner_id == user.id)
    )
    docs = db.scalars(
        select(KbDoc).where(
            or_(KbDoc.path == old, KbDoc.path.like(f"{old}/%")), doc_scope
        )
    ).all()
    for d in docs:
        d.path = new + d.path[len(old):]

    sub_folders = db.scalars(
        select(KbFolder).where(
            or_(KbFolder.path == old, KbFolder.path.like(f"{old}/%")),
            _folder_scope_filter(scope, user),
        )
    ).all()
    exact = None
    for f in sub_folders:
        if f.path == old:
            exact = f
        else:
            f.path = new + f.path[len(old):]
    if exact:
        exact.path = new
    else:
        db.add(KbFolder(owner_id=user.id, scope=scope, path=new, sort=0))
    db.commit()
    return {"ok": True, "moved": len(docs)}


class FolderDeleteIn(BaseModel):
    path: str
    scope: str = "personal"


@router.delete("/folders")
def delete_folder(
    body: FolderDeleteIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """删除空文件夹（内有文档/子文件夹时拒绝）。"""
    scope = body.scope if body.scope in ("personal", "team") else "personal"
    path = body.path.strip("/")
    _guard_skeleton(path)
    doc_scope = (
        or_(KbDoc.scope == "team", KbDoc.team_status == "approved")
        if scope == "team"
        else and_(KbDoc.scope == "personal", KbDoc.owner_id == user.id)
    )
    has_docs = db.scalar(
        select(func.count()).select_from(KbDoc).where(
            or_(KbDoc.path == path, KbDoc.path.like(f"{path}/%")), doc_scope
        )
    )
    if has_docs:
        raise HTTPException(status_code=400, detail="文件夹内还有文档，请先移出或删除")
    has_sub = db.scalar(
        select(func.count()).select_from(KbFolder).where(
            KbFolder.path.like(f"{path}/%"), _folder_scope_filter(scope, user)
        )
    )
    if has_sub:
        raise HTTPException(status_code=400, detail="文件夹内还有子文件夹")
    targets = db.scalars(
        select(KbFolder).where(KbFolder.path == path, _folder_scope_filter(scope, user))
    ).all()
    if not targets:
        raise HTTPException(status_code=404, detail="文件夹不存在")
    for f in targets:
        db.delete(f)
    db.commit()
    return {"ok": True}


class FolderSortIn(BaseModel):
    path: str
    direction: str  # up | down
    scope: str = "personal"


@router.post("/folders/sort")
def sort_folder(
    body: FolderSortIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """同级文件夹上移/下移。"""
    scope = body.scope if body.scope in ("personal", "team") else "personal"
    rows = db.scalars(
        select(KbFolder)
        .where(_folder_scope_filter(scope, user))
        .order_by(KbFolder.sort, KbFolder.path)
    ).all()
    target = body.path.strip("/")
    same_level = [f for f in rows if f.path.count("/") == target.count("/")]
    idx = next((i for i, f in enumerate(same_level) if f.path == target), None)
    if idx is None:
        raise HTTPException(status_code=404, detail="文件夹不存在")
    swap_with = idx - 1 if body.direction == "up" else idx + 1
    if 0 <= swap_with < len(same_level):
        same_level[idx], same_level[swap_with] = same_level[swap_with], same_level[idx]
        for i, f in enumerate(same_level):
            f.sort = i + 1
        db.commit()
    return {"ok": True}


class MoveDocIn(BaseModel):
    folder: str  # 目标文件夹（空=根目录）


@router.put("/docs/{doc_id}/move")
def move_doc(
    doc_id: int,
    body: MoveDocIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    d = _get_editable_doc(doc_id, user, db)
    folder = body.folder.strip().strip("/")
    d.path = f"{folder}/{d.title}.md" if folder else f"{d.title}.md"
    db.commit()
    return _doc_out(d)

