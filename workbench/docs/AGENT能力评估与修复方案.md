# 团队 AI 工作台：Agent 能力评估与修复方案

> 面向对象：FastAPI 后端 + Next.js 前端的网页 harness
> 评估范围：对话链路、工具/skill 机制、知识库存储与调用、团队库权限
> 评估方式：静态代码审阅 + SQLite 只读查询，未修改任何代码与数据
> 代码位置：`/Users/luyawen/总部工作台-我的理解/workbench/`
> 评估日期：2026-11-21

---

## 一、一句话结论

这个 harness 已经具备"强底座"：模型层、会话层、知识库检索层、知识库管理操作层全部就位。**唯一缺的是把它们串成 agent 的两根筋——工具调用协议（function calling）和工具执行循环（agent loop）。** 补上这两根筋，你的 skill 和知识库就能被模型自主调用；底座代码几乎不用重写，是"接管子"，不是"推倒重来"。

---

## 二、现状评估：你已经做对了什么

### 2.1 整体架构

| 层 | 文件 | 现状 | 评价 |
|---|---|---|---|
| 模型调用 | `app/llm.py` | OpenAI 兼容协议，统一封装 GLM，支持流式/非流式/拉模型列表 | 完整，协议标准，换模型只改配置 |
| 会话管理 | `app/models.py` 的 Conversation/Message | SQLite 存会话与消息，带引用 JSON | 完整 |
| 对话主链路 | `app/routers/chat.py` | 组上下文、选模型、流式输出、落库 | 完整，但只是"单次补全" |
| 知识库检索 | `app/kb_service.py` | 切块 + 混合检索（向量可降级关键词）+ 带编号注入 | 完整 |
| 知识库管理 | `app/routers/kb.py` 的 `apply_kb_op` | 建/删/改/移文件夹与文档，审核流 | 完整，且是一套现成的"可执行动作集" |
| 工具管理 | `app/routers/tools.py` | 工具增删改查、从对话提炼工具草案 | 只有"提示词壳"，无执行能力 |
| 数据集/问数 | `app/data_service.py`、独立 `datasets.db` | Text2SQL + 沙库 | 完整 |

数据库实际数据量（只读统计）：用户 2、供应商 1（智谱 GLM）、会话 21、消息 68、工具 8、知识文档 21、知识块 16、数据集 3。

### 2.2 关键判断：底座齐全，缺的是串联

你问"为什么对话框不能直接调用我做好的 skills、修改工作台"。答案不是底座不够，而是**串联机制缺失**。下面两节说清缺在哪。

---

## 三、根因诊断：为什么对话框调不动 skill

### 3.1 直接原因：工具只是一段"说明书"，没有"手"

看 `tools.py` 的 `ToolIn` 模型：一个工具只有 `system_prompt` 字段。再看 `chat.py` 第 260 行：

```python
tool_system = tool.system_prompt
```

工具被选中后，只是把它的 `system_prompt` 拼进 system 提示词。也就是说，你做的 skill/agent，本质是**一段注入的文字**——模型读了它，会照着这个人设说话，但**没有任何执行动作的通道**。它能"讲怎么做"，不能"真的做"。

### 3.2 更深一层：模型根本不知道有工具可调

看 `llm.py` 第 26 行：

```python
stream = await client.chat.completions.create(
    model=model_id, messages=messages, stream=True
)
```

只传了 `model` 和 `messages`，**没有传 `tools` 参数**。在 OpenAI 兼容协议里，工具是通过 `tools` 字段以 JSON Schema 描述给模型的。没传这个字段，模型就不会返回"我要调用某工具"的申请（tool_calls），整个 agent 循环的第一环就断了。

### 3.3 但你已经做出了 agent loop 的雏形

`chat.py` 里的"知识库管家"（`_butler_stream`）其实已经是一套完整的"模型出指令 → 后端执行 → 汇总回复"的循环：

```
BUTLER_PROMPT 告诉模型有哪些 ops（动作）
  → 模型输出 JSON ops
  → 代码解析、逐个调 apply_kb_op 真正执行
  → 拼成结果流式返回
```

这正是标准 agent loop 的雏形，只是有三个限制：

| 限制 | 说明 |
|---|---|
| 用文本模拟工具调用 | 靠"模型输出文本里嵌 JSON"代替标准 function calling，脆弱、易解析失败 |
| 单场景硬编码 | 只有知识库管家一条路，其他 skill 没有执行能力 |
| 单轮不回灌 | 一次性执行完 ops 就结束，不会把结果喂回模型让它继续推理下一轮 |

### 3.4 根因归纳

| 缺失项 | 对应层 | 后果 |
|---|---|---|
| 模型调用没传 `tools` | 模型层/协议层 | 模型不会发起工具申请 |
| 没有通用工具执行器 | 工具层 | 除了知识库管家，没有能真正动手的函数注册处 |
| `chat.py` 是单次补全 | 循环层 | 没有"执行→回灌→再调模型"的多轮机制 |
| Tool 模型只有 system_prompt | 数据层 | 工具无法声明自己有哪些可执行动作 |

---

## 四、知识库调用评估

### 4.1 数据怎么存

物理存储分三处：

| 内容 | 位置 |
|---|---|
| 文档正文、标题、标签、摘要、审核状态 | `workbench.db` 的 `kb_docs` 表，`content` 字段直接存全文 |
| 检索切块 | `kb_chunks` 表，每篇约 500 字一块 |
| 上传的原文件 | `backend/app/uploads/`，库里只存文件名 |

关键事实：**"文件夹"不是真实目录**，是 `path` 字符串字段 + `kb_folders` 表维护的虚拟路径树，文档实体全在 `kb_docs` 一张表里。

### 4.2 个人库与团队库怎么分

**不是两张表，是同一张 `kb_docs` 靠两个字段区分**：

- `scope=personal` + `team_status=none`：纯个人库，仅本人可见
- `scope=team`：团队库，管理员直接上传，全员可见
- `scope=personal` + `team_status=approved`：个人文档过审后仍属个人，但对全员可见可检索

权限在**应用层代码**判断（`visible_docs`、`_folder_scope_filter`），数据库层无隔离。成员想贡献团队知识走：`request-team` → AI 预审（查重/敏感扫描/质量打分）→ 管理员终审。

当前库里真正全员可见的只有 3 篇（2 篇 `scope=team` + 1 篇 `approved`）。

### 4.3 harness 现在能不能调知识库

分三种"调"，结论不同：

| 调用方式 | 现状 | 依据 |
|---|---|---|
| 检索进上下文（RAG） | **已通，一直在跑** | `chat.py` 勾 `use_kb` → `search_kb` → `build_kb_prompt` 注入带编号片段。这是后端内部函数直调，不依赖工具协议 |
| AI 增删移知识库 | **半通** | 知识库管家靠关键词命中（`_looks_like_kb_op`）触发，模型吐 JSON ops，`apply_kb_op` 执行。能用，但是规则触发 + 一次性指令，不是模型自主调用 |
| 模型标准 function calling 自由调用 | **未做** | 就是第三节诊断的缺失项 |

### 4.4 检索质量现状

`kb_service.py` 是混合检索：有向量走余弦，没向量走关键词（中文 bigram 余弦）。**当前 16 个切块全部没有向量**（`embedding_json` 为空），因为没配 `KB_EMBEDDING_API_KEY`，向量分支降级了。所以现在实际是**纯关键词检索**——字面匹配才命中，"意思相近但用词不同"搜不到。

---

## 五、修复方案

总体思路：不重写底座，只补两根筋。分四个动作，按依赖顺序排列。

### 动作一：模型层支持工具调用协议

**文件**：`app/llm.py`

新增一个能传 `tools`、能接 `tool_calls` 的调用方法。现有 `stream_completion`/`complete` 保持不变（知识库管家等还在用）。

```python
async def complete_with_tools(
    provider: Provider,
    model_id: str,
    messages: list[dict],
    tools: list[dict],
) -> dict:
    """非流式调用，返回 {content, tool_calls}。
    tools 为 JSON Schema 列表：{"type":"function","function":{name,description,parameters}}"""
    client = client_for(provider)
    res = await client.chat.completions.create(
        model=model_id, messages=messages, tools=tools, stream=False,
    )
    msg = res.choices[0].message
    return {
        "content": msg.content or "",
        "tool_calls": [
            {"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments}
            for tc in (msg.tool_calls or [])
        ],
    }
```

验收：写个单测，传一个 `read_file` 工具定义 + 提问"读 a.md"，确认返回里带 `tool_calls`。GLM 支持 function calling，这一步能直接跑通。

### 动作二：建立通用工具执行器

**文件**：新建 `app/tool_runtime.py`

把散落的可执行函数集中注册。工具执行函数统一签名 `(db, user, arguments) -> str`，天然带 user 上下文（权限过滤的关键）。

```python
# 工具注册表：name -> 执行函数
TOOL_REGISTRY: dict[str, callable] = {}

def register(name: str):
    def deco(fn):
        TOOL_REGISTRY[name] = fn
        return fn
    return deco

@register("search_kb")
async def t_search_kb(db, user, args):
    results = await search_kb(db, user.id, args["query"], scope="all", top_k=5)
    return build_kb_prompt(results) if results else "知识库未找到相关内容。"

@register("kb_op")
async def t_kb_op(db, user, args):
    return await apply_kb_op(db, user, args["op"], args.get("scope", "personal"))

# 工具定义（喂给模型的 JSON Schema），供动作一使用
TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "search_kb",
        "description": "检索团队知识库和个人知识库，回答知识类问题时调用",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "检索问题"}
        }, "required": ["query"]},
    }},
    # kb_op 按同样格式补充
]

async def run_tool(name: str, db, user, arguments: dict) -> str:
    fn = TOOL_REGISTRY.get(name)
    if fn is None:
        return f"未知工具：{name}"
    return await fn(db, user, arguments)
```

这一步的价值：**知识库管家那套 `apply_kb_op` 现成的执行逻辑，直接注册进来就能被模型调用**，不用重写。

### 动作三：把单次补全改成 agent loop

**文件**：`app/routers/chat.py`

新增一条 agent 分支（不要动现有普通聊天和知识库管家分支，稳定主干保留）。循环逻辑：

```python
messages = llm_messages[:]          # 已有的上下文
for _ in range(MAX_TOOL_ROUNDS):    # 设上限，如 6 轮，防死循环
    out = await complete_with_tools(provider, model_id, messages, TOOL_SCHEMAS)
    if not out["tool_calls"]:
        final_text = out["content"]
        break
    # 把模型的 tool_calls 消息追加进上下文
    messages.append({
        "role": "assistant", "content": out["content"] or None,
        "tool_calls": [ ... 标准格式 ... ],
    })
    # 逐个执行工具，结果以 role=tool 消息回灌
    for tc in out["tool_calls"]:
        args = json.loads(tc["arguments"])
        result = await run_tool(tc["name"], db, user, args)
        messages.append({
            "role": "tool", "tool_call_id": tc["id"],
            "content": result,
        })
else:
    final_text = "（达到工具调用轮次上限，已停止）" + out["content"]
```

关键点：

- 工具结果以 `role=tool` 消息回灌（`tool_call_id` 对应），这是 OpenAI 兼容协议的硬性要求，GLM 遵循。
- 每轮工具执行都带 `user` 上下文，`search_kb`、`apply_kb_op` 内部据此做权限过滤，不会越权看到别人的个人库。
- 设轮次上限，避免模型反复调用停不下来。
- 流式输出：工具执行阶段可以先推一条"正在检索/正在执行"的进度事件，最终文本再按现有 `delta` 分段下发，前端不用大改。

### 动作四：让 skill 能声明自己要调哪些工具

**文件**：`app/models.py`、`app/routers/tools.py`

给 `Tool` 加一个 `allowed_tools_json` 字段（存该 skill 允许使用的工具名列表）。选中 skill 时，把它的 `system_prompt` 作为 system 消息、把 `allowed_tools` 对应的 schema 作为本次可用的工具集。这样：

- 通用聊天：给全部工具
- 某个专用 skill：只给它声明的子集，权限更收敛

数据库加列走 SQLite 的 `ALTER TABLE`（新项目可直接改模型后重建），存量 8 个工具默认空列表 = 不额外限制。

### 修复后能达到的效果

| 场景 | 修复前 | 修复后 |
|---|---|---|
| 用户问"查一下坐席培训指南里 X 规定" | 需手动勾 `use_kb` | 模型自己判断要检索，调 `search_kb`，带引用回答 |
| 用户说"把这份周报存进团队库" | 靠关键词碰运气触发管家 | 模型自主调 `kb_op` 执行，并回灌结果继续对话 |
| 用户用了某 skill | 只换 system 提示词，仍不能动手 | skill 声明的工具被激活，能检索、能操作知识库 |

---

## 六、风险与边界

| 风险 | 说明 | 处理 |
|---|---|---|
| 权限越界 | 权限在应用层判断，工具裸跑会绕过 | 所有工具函数强制接收 `user`，内部过滤；`scope=team` 写操作校验 admin |
| 工具调用死循环 | 模型可能反复调同一工具 | agent loop 设 `MAX_TOOL_ROUNDS` 上限 |
| 工具参数幻觉 | 模型可能生成不存在的路径/文档名 | `apply_kb_op` 已有"操作真实路径、不虚构"的提示约束；执行层再校验一次，出错返回文案不中断 |
| 单次调用成本 | 多轮 = 多次模型请求 | 控制轮次上限；简单问题走原聊天分支，不进 agent loop |
| SQLite 并发 | 单文件 + WAL，10 人轻量可用，并发写入有上限 | 保持 WAL；Step 9 迁 Postgres 的方向正确，agent 化后更应尽快迁 |
| 检索质量 | 当前纯关键词，语义搜不到 | 配 `KB_EMBEDDING_API_KEY` 启用向量，16 个切块重 embeddings 即可 |

---

## 七、落地顺序

1. **动作一**（半天）：`llm.py` 加 `complete_with_tools`，单测验证 GLM 能返回 tool_calls。
2. **动作二**（半天）：建 `tool_runtime.py`，先注册 `search_kb` 一个工具，跑通"模型调 search_kb"。
3. **动作三**（一天）：`chat.py` 加 agent 分支，跑通完整循环。此时最小闭环成立。
4. **动作四**（半天）：Tool 加 `allowed_tools_json`，skill 按声明激活工具子集。
5. **增强**（按需）：注册更多工具、启用向量检索、迁 Postgres。

最小可验证目标：**网页说"查一下坐席培训指南里关于首解率的规定" → 模型自主调 `search_kb` → 结果回灌 → 模型带引用回答**。这一条通了，整套机制就通了，后面全是往注册表里加函数。

---

## 附：本次评估未做的事

- 未修改任何 `.py`、`.tsx`、数据库文件或配置。
- 数据库仅以只读模式（`mode=ro`）连接统计。
- 未验证 GLM 的 function calling 实际返回（需配好 key 后跑动作一的单测确认）。
- 未评估前端 `workbench/frontend/` 的交互改造（agent 进度事件的前端渲染，属于动作三之后的配套）。
