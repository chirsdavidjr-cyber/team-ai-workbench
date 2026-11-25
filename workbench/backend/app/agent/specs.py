"""工具 schema 注册表与能力面过滤。"""

import json

from .prompts import BASE_RULES

TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "list_tools",
            "description": "列出工作台工具库中的工具（团队工具 + 我的工具），返回 id、名称、类型、描述等",
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "enum": ["all", "team", "personal"],
                        "description": "all=全部可见工具（默认），team=仅团队工具，personal=仅我的工具",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_tool",
            "description": "在工作台创建一个新工具（agent=持续性角色 / skill=固定流程 / template=固定格式），保存为当前用户的个人工具",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "工具名（必填，8 字左右）"},
                    "system_prompt": {"type": "string", "description": "系统提示词（必填），工具的核心指令"},
                    "description": {"type": "string", "description": "一句话说明用途"},
                    "kind": {"type": "string", "enum": ["agent", "skill", "template"]},
                    "category": {"type": "string", "description": "分类，如 通用/文案/质检"},
                    "icon": {"type": "string", "description": "一个 emoji 图标"},
                    "openings": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "推荐开场问题（0-3 个）",
                    },
                    "allowed_actions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "能力面白名单（可选）：从组名 tool_lib/kb_read/kb_manage/data/task 或具体动作名中选择；省略或空数组 = 全部能力。工具运行时只能调用白名单内的动作",
                    },
                },
                "required": ["name", "system_prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_tool",
            "description": "修改已有工具的字段（仅限本人创建的工具或管理员）",
            "parameters": {
                "type": "object",
                "properties": {
                    "tool_id": {"type": "integer", "description": "工具 ID（先 list_tools 查询）"},
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "system_prompt": {"type": "string"},
                    "category": {"type": "string"},
                    "icon": {"type": "string"},
                    "kind": {"type": "string", "enum": ["agent", "skill", "template"]},
                    "openings": {"type": "array", "items": {"type": "string"}},
                    "allowed_actions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "能力面白名单：组名或动作名；传空数组 = 恢复全部能力",
                    },
                },
                "required": ["tool_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_tool",
            "description": "删除工具（仅限本人创建的工具或管理员）。执行前必须先向用户复述对象并获得其在对话中的明确确认，否则会被系统拒绝",
            "parameters": {
                "type": "object",
                "properties": {
                    "tool_id": {"type": "integer", "description": "工具 ID"},
                },
                "required": ["tool_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_kb",
            "description": "检索知识库（团队库+个人库），返回最相关文档的标题与内容片段。需要基于知识库素材/风格/规范做任何事之前，先调用此函数查找素材是否真实存在",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索关键词，如「PPT 风格 规范」「太保 品牌色」「话术模板」「质检标准」"},
                    "scope": {
                        "type": "string",
                        "enum": ["all", "team", "personal"],
                        "description": "all=全部可见（默认），team=仅团队库，personal=仅个人库",
                    },
                    "top_k": {"type": "integer", "description": "返回条数，默认 5"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_kb_doc",
            "description": "阅读一篇知识库文档的完整内容（先 search_kb 找到标题，再用标题读取）",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "文档标题（或标题片段）"},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_kb",
            "description": "查看知识库的文件夹树与文档路径清单。执行任何知识库管理操作（建文件夹、移动/改名/删除文档等）之前先调用此函数确认真实结构",
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "enum": ["personal", "team"],
                        "description": "personal=个人库（默认），team=团队库（需管理员）",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kb_create_folder",
            "description": "知识库：新建文件夹（仅空路径层级，不能与已有同名）",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件夹路径，如 原始资料/湖北/报告"},
                    "scope": {"type": "string", "enum": ["personal", "team"]},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kb_rename_folder",
            "description": "知识库：重命名文件夹（其下文档路径同步更新）",
            "parameters": {
                "type": "object",
                "properties": {
                    "old": {"type": "string", "description": "旧路径"},
                    "new": {"type": "string", "description": "新路径"},
                    "scope": {"type": "string", "enum": ["personal", "team"]},
                },
                "required": ["old", "new"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kb_delete_folder",
            "description": "知识库：删除文件夹（仅限空文件夹；四个一级分区不可删改）。执行前必须先向用户复述对象并获得其在对话中的明确确认，否则会被系统拒绝",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件夹路径"},
                    "scope": {"type": "string", "enum": ["personal", "team"]},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kb_move_doc",
            "description": "知识库：把文档移动到指定文件夹",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "文档标题或路径片段"},
                    "to": {"type": "string", "description": "目标文件夹路径（不含文件名）"},
                    "scope": {"type": "string", "enum": ["personal", "team"]},
                },
                "required": ["title", "to"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kb_rename_doc",
            "description": "知识库：重命名文档",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "旧标题"},
                    "new_title": {"type": "string", "description": "新标题"},
                    "scope": {"type": "string", "enum": ["personal", "team"]},
                },
                "required": ["title", "new_title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kb_delete_doc",
            "description": "知识库：删除文档（不可恢复）。执行前必须先向用户复述对象并获得其在对话中的明确确认，否则会被系统拒绝",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "文档标题"},
                    "scope": {"type": "string", "enum": ["personal", "team"]},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kb_create_doc",
            "description": "知识库：新建文档。用户交代新资料/附件/结论要入库时，用此函数存入「收件箱」文件夹（folder 固定传 收件箱）",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "文档标题"},
                    "content": {"type": "string", "description": "Markdown 正文"},
                    "folder": {"type": "string", "description": "目标文件夹，默认 收件箱"},
                    "scope": {"type": "string", "enum": ["personal", "team"]},
                },
                "required": ["title", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kb_organize_inbox",
            "description": "整理收件箱：AI 自动归类移动 → 登记来源台账 → 高价值内容编译为知识页（无参数）",
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {"type": "string", "enum": ["personal", "team"]},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "kb_apply_team",
            "description": "把个人库中的一篇文档申请入团队库：自动 AI 预审（查重/质量/敏感扫描）→ 进入管理员终审队列，通过后全员可见。成员想共享内容给团队时必须走此通道，不能直接写团队库",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "个人库中的文档标题"},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_datasets",
            "description": "列出可见的数据集（团队 + 本人）：返回名称、行数、字段清单与语义层概况。用户问数据相关问题前先调用确认数据集",
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "enum": ["all", "team", "personal"],
                        "description": "all=全部可见（默认），team=仅团队，personal=仅本人",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_dataset",
            "description": "对数据集用自然语言问数：自动生成 SQL → 只读沙箱执行 → 返回列、行与 AI 解读。适合统计、对比、趋势类问题（如「上月各渠道接通率」）",
            "parameters": {
                "type": "object",
                "properties": {
                    "dataset": {"type": "string", "description": "数据集名称或 ID（先 list_datasets 确认）"},
                    "question": {"type": "string", "description": "用自然语言描述要查的问题"},
                },
                "required": ["dataset", "question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "preview_dataset",
            "description": "预览数据集前 5 行真实数据，用于了解表结构与内容样例",
            "parameters": {
                "type": "object",
                "properties": {
                    "dataset": {"type": "string", "description": "数据集名称或 ID"},
                },
                "required": ["dataset"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_data_global",
            "description": "全局问数（不指定数据集）：自动从全部可见数据表中选出相关表（可跨表 JOIN），生成 SQL 只读执行并返回结果与解读。适合不确定数据在哪张表、或需要跨表关联的问题；已知数据集时优先用 query_dataset",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "用自然语言描述要查的问题"},
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_dataset",
            "description": "把对话中用户提供或生成的表格数据保存为个人数据集（与上传入库同一管线，存好即可 query_dataset 问数）。csv 参数传完整 CSV 文本：首行为表头、逗号分隔、每行一条；Markdown 表格必须先转成 CSV 再传",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "数据集名（可选，默认「对话数据集」）"},
                    "csv": {"type": "string", "description": "完整 CSV 文本（首行为表头）"},
                    "folder": {"type": "string", "description": "分组路径（可选）"},
                },
                "required": ["csv"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_task",
            "description": "创建批量任务：用指定工具的系统提示词逐条处理一批文本（后台异步执行，不阻塞对话）。适合分类、打标、意图分析、批量提取等同质条目场景",
            "parameters": {
                "type": "object",
                "properties": {
                    "tool": {"type": "string", "description": "工具名称或 ID（先 list_tools 确认可用工具）"},
                    "items": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "待处理条目列表，每条一段文本（1-500 条）",
                    },
                    "name": {"type": "string", "description": "任务名（可选，默认自动生成）"},
                },
                "required": ["tool", "items"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": "列出我最近的批量任务及进度（状态、完成数、失败数）",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_task",
            "description": "查看一个批量任务的进度与结果摘要（含前几条处理样例）。用户问「任务跑完没」「结果如何」时调用",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "任务 ID"},
                    "name": {"type": "string", "description": "任务名（与 task_id 二选一，支持片段匹配）"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_collect_task",
            "description": (
                "创建后台采集任务：对白名单注册站点（金融监管总局/药监局/医保局/保险业协会/平安产险/人保财险）"
                "抓取公开网页列表与详情全文，限速执行，产出明细台账并自动注册为数据集（可问数），可选入知识库。"
                "适合行业/监管/同业/药监/医保公开信息采集；不做登录、不绕验证码、自动脱敏"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "site": {
                        "type": "string",
                        "description": "站点 key：nfra(金融监管总局)/nmpa(药监局)/nhsa(医保局)/iachina(保险业协会)/pingan(平安产险)/epicc(人保财险)",
                    },
                    "columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "栏目名列表（可选，如 [\"公告通知\"]；省略=该站全部栏目）",
                    },
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "过滤关键词（可选，标题/链接命中才保留；省略=不过滤）",
                    },
                    "max_items": {"type": "integer", "description": "条数上限 1-200，默认 20"},
                    "to_kb": {"type": "boolean", "description": "是否把结果存入个人知识库（默认否）"},
                    "name": {"type": "string", "description": "任务名（可选）"},
                },
                "required": ["site"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_collect_tasks",
            "description": "列出我最近的采集任务及进度（状态、已采/总数、受阻数、产出数据集）",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_collect_task",
            "description": "查看一个采集任务的进度与结果摘要（含前几条样例：标题/链接/正文字数）。用户问「采集跑完没」「采到什么」时调用",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "任务 ID"},
                    "name": {"type": "string", "description": "任务名（与 task_id 二选一，支持片段匹配）"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_todo",
            "description": "为用户创建一条待办。用户说「记一下」「别忘了」「帮我留着这个事」时使用",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "待办内容（一句话）"},
                    "due_date": {"type": "string", "description": "截止日期 YYYY-MM-DD（可选）"},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_todos",
            "description": "列出我的待办（含完成状态与截止日期）。用户问「我有哪些待办」「还有什么没做」时调用",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_todo",
            "description": "把一条待办标记为已完成。用户说「XX 办完了」「这件事处理好了」时调用",
            "parameters": {
                "type": "object",
                "properties": {
                    "todo_id": {"type": "integer", "description": "待办 ID"},
                    "title": {"type": "string", "description": "标题片段（与 todo_id 二选一，支持模糊匹配）"},
                },
            },
        },
    },
]




ACTION_GROUPS = {
    "tool_lib": ("list_tools", "create_tool", "update_tool", "delete_tool"),
    "kb_read": ("search_kb", "read_kb_doc", "list_kb"),
    "kb_manage": (
        "kb_create_folder", "kb_rename_folder", "kb_delete_folder", "kb_move_doc",
        "kb_rename_doc", "kb_delete_doc", "kb_create_doc", "kb_organize_inbox", "kb_apply_team",
    ),
    "data": ("list_datasets", "query_dataset", "query_data_global", "preview_dataset", "create_dataset"),
    "task": ("create_task", "list_tasks", "get_task"),
    "collect": ("create_collect_task", "list_collect_tasks", "get_collect_task"),
    "todo": ("create_todo", "list_todos", "complete_todo"),
}
ALL_ACTION_NAMES = {s["function"]["name"] for s in TOOL_SPECS}
_GROUP_ZH = {
    "tool_lib": "工具库管理", "kb_read": "知识库读取", "kb_manage": "知识库管理",
    "data": "数据问数", "task": "批量任务", "collect": "后台采集", "todo": "待办",
}


def clean_allowed_actions(raw) -> str:
    """清洗白名单输入 → JSON 字符串；只保留合法的组名/动作名，空 = 全部动作。"""
    if not isinstance(raw, list):
        return "[]"
    seen, out = set(), []
    for x in raw:
        v = str(x).strip()[:40]
        if v and v != "all" and v not in seen and (v in ACTION_GROUPS or v in ALL_ACTION_NAMES):
            out.append(v)
            seen.add(v)
    return json.dumps(out, ensure_ascii=False)


def expand_actions(entries: list[str] | None) -> list[str] | None:
    """展开组名 + 动作名 → 动作名列表；空或全部非法 → None（表示不限制）。"""
    names: set[str] = set()
    for e in entries or []:
        v = str(e).strip()
        if v in ACTION_GROUPS:
            names.update(ACTION_GROUPS[v])
        elif v in ALL_ACTION_NAMES:
            names.add(v)
    return sorted(names) or None


def filter_tool_specs(names: list[str] | None) -> list[dict]:
    if not names:
        return TOOL_SPECS
    keep = set(names)
    return [s for s in TOOL_SPECS if s["function"]["name"] in keep]


def scoped_hint(tool_name: str, actions: list[str]) -> str:
    """白名单工具场景的系统说明：只介绍可用动作 + 通用规则（能力外的请求如实说明）。"""
    covered = {a for acts in ACTION_GROUPS.values() for a in acts}
    lines = []
    for g, acts in ACTION_GROUPS.items():
        hit = [a for a in acts if a in actions]
        if hit:
            lines.append(f"· {_GROUP_ZH[g]}：{' / '.join(hit)}")
    others = [a for a in actions if a not in covered]
    if others:
        lines.append("· 其他：" + " / ".join(others))
    return (
        f"你当前以工具「{tool_name}」的身份运行，只具备以下函数能力（按需自动判断使用）：\n"
        + "\n".join(lines) + "\n"
        "清单之外的工作台能力（数据、任务、工具管理等）对你不可用；相关请求请如实说明能力范围，不要假装执行。\n"
        + BASE_RULES
    )


