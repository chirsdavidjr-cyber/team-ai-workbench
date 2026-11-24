"""最小 function calling 闭环：模型 ⇄ 工作台动作调用循环。

harness 的核心机制：模型通过 OpenAI 兼容 tools 协议发起结构化调用，
后端执行动作（当前为工具库 CRUD）→ 结果以 role=tool 回灌 → 模型继续推理，
循环直到模型给出最终文字回答。后续扩大范围时只需往 TOOL_SPECS /
run_tool_action 里注册新动作。
"""

import json
import re
from typing import AsyncIterator

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .data_service import sandbox
from .kb_service import search_kb, visible_docs
from .llm import client_for
from .models import BatchTask, CollectTask, Conversation, Dataset, Message, Provider, Todo, Tool, User
from .routers.kb import apply_kb_op, kb_context_text

MAX_ROUNDS = 6  # 最大工具调用轮数，防止模型死循环
TOOL_RESULT_MAX = 4000  # 回灌给模型的动作结果截断长度

# 恒定注入的系统说明：模型据此自动判断是否需要调用（普通问答不调用）
_CAPABILITY_HINT = (
    "你运行在团队 AI 工作台中，具备以下函数能力，按需自动判断使用：\n"
    "① 工具库管理：list_tools / create_tool / update_tool / delete_tool；\n"
    "② 知识库读取：search_kb（检索片段）、read_kb_doc（读全文）——需要基于知识库回答或引用素材前先检索；\n"
    "③ 知识库管理：list_kb（查看库结构）、kb_create_folder / kb_rename_folder / kb_delete_folder / "
    "kb_move_doc / kb_rename_doc / kb_delete_doc / kb_create_doc / kb_organize_inbox（整理收件箱）。\n"
    "④ 数据问数：list_datasets（列数据集）、query_dataset（对指定数据集问数，出 SQL+结果+解读）、"
    "query_data_global（不指定表的全局问数，自动选表可跨表）、preview_dataset（看样例数据）、"
    "create_dataset（把用户在对话里提供/生成的表格数据存为新数据集）。\n"
    "⑤ 批量任务：create_task（用指定工具批量处理一批文本条目，后台逐条执行）、list_tasks（查任务进度）、"
    "get_task（取任务结果摘要）。用户给出一批同质条目要逐条处理（分类/打标/提取/生成）时，"
    "优先创建批量任务而不是在对话里逐条回答；创建后告知用户可随时问进度或去批量任务页查看。\n"
    "⑥ 后台采集：create_collect_task（对白名单站点发起公开网页采集，后台限速执行，产出明细台账+数据集，可选入知识库）、"
    "list_collect_tasks（查采集任务进度）、get_collect_task（取采集结果摘要）。"
    "用户要采集公开信息（行业/监管/同业/药监/医保）时用 create_collect_task 创建后台任务，"
    "不要在对话里逐条抓取；创建后告知可问进度或到采集任务页查看。\n"
    "⑦ 待办：create_todo（记待办）、list_todos（列待办）、complete_todo（完成待办）。"
    "用户口头交代「记一下/别忘了/XX 办完了」类事项时落到待办模块，不要只停留在对话文字里。\n"
)

# 通用规则段：全量场景与工具白名单场景共用
BASE_RULES = (
    "权限规则：知识库操作默认个人库；团队库全员可读，但写入/修改/删除仅限管理员——"
    "成员要把内容共享给团队时，先 kb_create_doc 存入个人库，再 kb_apply_team 申请入团队库走审核。\n"
    "删除类操作（delete_tool / kb_delete_doc / kb_delete_folder）采用系统级两步确认："
    "收到删除请求后**直接发起函数调用**——第一次会被系统拦截并登记该对象，属正常流程；"
    "随后向用户复述将删除的对象并请求确认，用户明确回复（如「确认」「确定删除」）后"
    "再次调用删除**同一对象**即可执行；对其他对象的确认无效，不要替用户确认。\n"
    "调用原则：仅当用户请求确实涉及上述工作台操作或知识库素材时才调用函数"
    "（涉及管理类操作时先 list_kb 查看真实结构再操作，不要虚构路径）；"
    "普通聊天与问答直接回答，不要调用任何函数；知识库中不存在的素材如实说明，严禁编造。\n"
    "重要：一切操作必须通过真实发起函数调用完成；严禁在回复文本中伪造调用过程或结果"
    "（例如编造调用标记、谎称已执行）——没有真正调用函数，就不得声称已完成任何操作。"
)

WORKBENCH_HINT = _CAPABILITY_HINT + BASE_RULES

# 轮次预算耗尽时的交接说明：引导模型交代进度并告知续跑方式（下一轮用户消息自动恢复全部预算）
HANDOVER_HINT = (
    "（系统提示：你已用完本轮的工具调用预算，本条回复不会再提供函数工具。）"
    "请基于以上已真实执行的调用，向用户清楚交代：已完成了什么、还剩什么没做。"
    "若还有未完成事项，明确告诉用户回复「继续」，你会在下一轮接着执行剩余操作；"
    "严禁声称执行了未真实调用的操作。"
)

# 动作注册表（OpenAI function calling schema）
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

# 能力分组：工具声明白名单时按组名或具体动作名引用；未声明（空）= 全部动作
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


DELETE_OPS = {"delete_tool", "kb_delete_doc", "kb_delete_folder"}  # 破坏性操作：以用户对话确认为准
CONFIRM_KW = ("确认", "确定", "同意", "删吧", "是的", "执行")


def _user_confirmed(text: str) -> bool:
    """用户是否在本轮消息中明确确认（短消息 + 确认词）。模型的 confirm 参数不算数。"""
    t = text.strip()
    return 0 < len(t) <= 40 and any(k in t for k in CONFIRM_KW)


# ---- 删除确认单：确认必须绑定到具体对象（防偷换对象/重复删） ----

class PendingConfirmError(ValueError):
    """删除未获用户对【同一对象】的确认；agent_stream 捕获后登记该对象的确认单。"""


_PENDING_TTL = 600  # 确认单有效期（秒）


def _delete_target_key(name: str, args: dict, db: Session | None = None, user: User | None = None) -> str:
    """删除对象的指纹：用户确认 A 不能用来删 B，故确认必须能对上同一对象。
    kb_delete_doc 优先解析为文档 ID（模型复述标题时可能带路径/缺 .md 后缀，按 ID 判定同一性最稳）。"""
    if name == "delete_tool":
        return f"tool:{args.get('tool_id')}"
    if name == "kb_delete_doc":
        title = str(args.get("title", "")).strip()
        scope = args.get("scope", "personal")
        if db is not None and user is not None and title:
            docs = visible_docs(db, user.id, scope if scope in ("personal", "team") else None)
            hit = next(
                (d for d in docs if d.title == title or d.path == title or d.path == f"{title}.md"),
                None,
            ) or next(
                (
                    d for d in docs
                    if title.lower() in d.title.lower()
                    or title.lower() in d.path.lower()
                    or d.title.lower() in title.lower()
                ),
                None,
            )
            if hit is not None:
                return f"doc:{hit.scope}:{hit.id}"
        return f"doc:{scope}:{title.lower()}"
    if name == "kb_delete_folder":
        return f"folder:{args.get('scope', 'personal')}:{str(args.get('path', '')).strip().strip('/').lower()}"
    return f"{name}:{json.dumps(args, ensure_ascii=False, sort_keys=True)}"


def _register_pending(db: Session, conv_id: int, name: str, args: dict, user: User | None = None) -> None:
    """登记对象确认单（DB 持久化，重启不丢）；顺带清理过期单。"""
    from sqlalchemy import func as _func

    from .models import PendingDelete

    db.query(PendingDelete).filter(
        PendingDelete.created_at < _func.datetime("now", f"-{_PENDING_TTL} seconds")
    ).delete(synchronize_session=False)
    key = _delete_target_key(name, args, db, user)
    if not db.scalar(
        select(PendingDelete).where(PendingDelete.conv_id == conv_id, PendingDelete.target_key == key)
    ):
        db.add(PendingDelete(conv_id=conv_id, target_key=key))
    db.commit()


def _match_pending(db: Session, conv_id: int, name: str, args: dict, user: User | None = None) -> bool:
    """用户的确认词只对已登记的同一对象放行；一次性消费，防重复删。"""
    from sqlalchemy import func as _func

    from .models import PendingDelete

    key = _delete_target_key(name, args, db, user)
    row = db.query(PendingDelete).filter(
        PendingDelete.conv_id == conv_id,
        PendingDelete.target_key == key,
        PendingDelete.created_at >= _func.datetime("now", f"-{_PENDING_TTL} seconds"),
    ).first()
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


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
        from .routers.kb import request_team
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
        from .routers.data import _ask_flow
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
        from .routers.data import _global_ask_events

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

        from .data_service import build_profile, write_dataframe

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
        from .routers.tasks import MAX_ITEMS, _out as _task_out

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

        from .routers.tasks import _run_task

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
        from .routers.tasks import _out as _task_out

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
        from .routers.tasks import _out as _task_out

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
        from .collect_service import MAX_ITEMS_HARD, run_collect
        from .collect_sites import SITES, get_site

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
        from .routers.collect import _out as _collect_out

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
        from .routers.collect import _out as _collect_out

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
