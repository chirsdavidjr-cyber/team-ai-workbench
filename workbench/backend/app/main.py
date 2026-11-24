import json
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select

from .config import ADMIN_EMAIL, ADMIN_PASSWORD
from .db import Base, SessionLocal, engine
from .models import Provider, Tool, User
from .routers import auth, chat, collect, conversations, data, kb, providers, tasks, todos, tools, workbenches
from .security import hash_password

app = FastAPI(title="湖北 AI 工作台", version="0.3.0")

app.add_middleware(
    CORSMiddleware,
    # 局域网部署时通过 CORS_ORIGINS 附加来源（逗号分隔），如 http://192.168.1.10:3000
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        *[o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()],
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(providers.router)
app.include_router(conversations.router)
app.include_router(chat.router)
app.include_router(kb.router)
app.include_router(data.router)
app.include_router(tools.router)
app.include_router(tasks.router)
app.include_router(collect.router)
app.include_router(workbenches.router)
app.include_router(todos.router)


@app.get("/api/health")
def health():
    return {"status": "ok", "app": "workbench-backend"}


def _migrate() -> None:
    """SQLite 轻量迁移：create_all 不会给已有表加新列，手工补齐。"""
    from sqlalchemy import text

    with engine.begin() as conn:
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(tools)"))}
        if "allowed_actions_json" not in cols:
            conn.execute(
                text("ALTER TABLE tools ADD COLUMN allowed_actions_json TEXT NOT NULL DEFAULT '[]'")
            )
        # 工具库对齐知识库/数据模块的分组与团队审核规则（增量列，旧数据不受影响）
        if "folder" not in cols:
            conn.execute(text("ALTER TABLE tools ADD COLUMN folder TEXT NOT NULL DEFAULT ''"))
            # 存量回填：分组初始 = 原 category，保证旧工具直接进入分组树
            conn.execute(
                text("UPDATE tools SET folder = category WHERE folder = '' AND IFNULL(category, '') != ''")
            )
        if "team_status" not in cols:
            conn.execute(text("ALTER TABLE tools ADD COLUMN team_status TEXT NOT NULL DEFAULT ''"))

        # 会话记录发起工具（行研动态卡等聚合依据；旧会话为 NULL = 自由对话）
        conv_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(conversations)"))}
        if "tool_id" not in conv_cols:
            conn.execute(text("ALTER TABLE conversations ADD COLUMN tool_id INTEGER NULL"))

        # 全局问数：data_messages 支持 dataset_id 为空（归属改用 owner_id）
        dm_cols = {r[1]: r for r in conn.execute(text("PRAGMA table_info(data_messages)"))}
        if "owner_id" not in dm_cols:
            conn.execute(text("ALTER TABLE data_messages ADD COLUMN owner_id INTEGER NULL"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_data_messages_owner_id ON data_messages (owner_id)"))
        if dm_cols and dm_cols["dataset_id"][3] == 1:  # notnull 仍为 1 → 重建表放开约束
            conn.execute(text(
                "CREATE TABLE data_messages_new ("
                "id INTEGER NOT NULL PRIMARY KEY, "
                "dataset_id INTEGER NULL REFERENCES datasets (id), "
                "owner_id INTEGER NULL, "
                "role VARCHAR(16) NOT NULL, "
                "content TEXT NOT NULL, "
                "payload_json TEXT, "
                "created_at DATETIME DEFAULT (CURRENT_TIMESTAMP))"
            ))
            conn.execute(text(
                "INSERT INTO data_messages_new (id, dataset_id, owner_id, role, content, payload_json, created_at) "
                "SELECT id, dataset_id, NULL, role, content, payload_json, created_at FROM data_messages"
            ))
            conn.execute(text("DROP TABLE data_messages"))
            conn.execute(text("ALTER TABLE data_messages_new RENAME TO data_messages"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_data_messages_dataset_id ON data_messages (dataset_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_data_messages_owner_id ON data_messages (owner_id)"))


def init_db() -> None:
    """建表；users 为空时按 .env 创建首个管理员；providers 为空时按 .env 初始化开发用供应商。"""
    Base.metadata.create_all(engine)
    _migrate()
    # 采集任务后台协程随进程存活：重启时把未完成任务置为 failed，用户重建即可
    with engine.begin() as conn:
        from sqlalchemy import text as _text

        conn.execute(_text(
            "UPDATE collect_tasks SET status='failed', error='服务重启中断' "
            "WHERE status IN ('queued','running')"
        ))

    if ADMIN_EMAIL and ADMIN_PASSWORD:
        with SessionLocal() as db:
            if db.scalar(select(func.count()).select_from(User)) == 0:
                db.add(
                    User(
                        email=ADMIN_EMAIL.strip().lower(),
                        name="管理员",
                        password_hash=hash_password(ADMIN_PASSWORD),
                        role="admin",
                    )
                )
                db.commit()

    seed_provider(
        name=os.getenv("SEED_PROVIDER_NAME", "智谱 GLM"),
        base_url=os.getenv("SEED_PROVIDER_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"),
        api_key=os.getenv("SEED_PROVIDER_API_KEY", ""),
        models=json.loads(os.getenv("SEED_PROVIDER_MODELS", '[{"id":"glm5.3","label":"GLM-5.3"}]')),
    )
    seed_tools()


def seed_tools() -> None:
    """首次启动预置团队工具（tools 表为空时，挂到首个管理员名下）。"""
    import json as _json

    presets = [
        {
            "name": "意图分析", "icon": "🎯", "category": "意图分析", "kind": "skill",
            "description": "批量客服文本的意图识别、分类与统计",
            "system_prompt": (
                "你是客服意图分析专家。用户会提供客户咨询文本（一条或多条）。"
                "请逐条判断客户核心意图，输出：意图标签（简短词组）、置信度（高/中/低）、关键依据（原文摘录）。"
                "多条输入时用 Markdown 表格呈现，最后给出意图分布统计。"
                "意图标签保持口径一致：同一类表达归入同一标签，不要造近义重复标签。"
            ),
            "openings": ["分析这条咨询的意图：你们的会员有什么权益", "批量分析这些文本的意图分布"],
        },
        {
            "name": "文案助手", "icon": "✍️", "category": "文案", "kind": "agent",
            "description": "外呼话术、公告、推广文案的生成与润色",
            "system_prompt": (
                "你是资深客服运营文案专家。根据需求生成话术、通知公告或推广文案。"
                "要求：口语自然、信息准确、有明确的行动指引；给出 2 个版本（稳妥版/活泼版）；"
                "涉及承诺、时效、金额的表述要提示用户核实业务口径。"
            ),
            "openings": ["写一条双十一大促的群发短信", "帮我润色这条挽留话术"],
        },
        {
            "name": "坐席能力评估", "icon": "📊", "category": "质检", "kind": "skill",
            "description": "基于会话记录的多维度坐席能力打分与诊断",
            "system_prompt": (
                "你是客服坐席能力评估专家。输入坐席会话记录，请从：服务规范、问题解决、"
                "沟通表达、共情能力、效率五个维度各打 1-5 分并给出一句话依据；"
                "输出总分（满分 25）、优势 2 条、改进建议 3 条（可直接执行）。"
                "评分严格基于记录内容，不要臆测未出现的行为。"
            ),
            "openings": ["评估这段会话的坐席表现：", "帮我设计坐席月度能力评估表"],
        },
        {
            "name": "培训助手", "icon": "🎓", "category": "培训", "kind": "agent",
            "description": "新人培训问答、知识考核出题",
            "system_prompt": (
                "你是客服新人培训导师。负责解答业务知识疑问、生成培训材料大纲、"
                "出考核题（单选/多选/判断，附答案与解析）。"
                "讲解时用通俗例子，先结论后展开；出题时覆盖易错点。"
            ),
            "openings": ["给我出一套 10 题的质检规范考核题", "新员工第一周应该学什么"],
        },
        {
            "name": "行研咨询", "icon": "📈", "category": "行研", "kind": "agent",
            "description": "行业情报速览、竞品分析与解读",
            "system_prompt": (
                "你是行业研究分析师。基于你的知识进行行业与竞品分析，输出结构化速览："
                "关键趋势、市场规模量级（标注截至你的知识时间）、竞争格局、对客服行业的启示。"
                "所有数据注明时效性，不确定的要明说，不要编造精确数字。"
            ),
            "openings": ["2025 年智能客服行业有什么趋势", "帮我对比几个主流客服系统"],
        },
        {
            "name": "网页/PPT 制作", "icon": "🎨", "category": "创作", "kind": "agent",
            "description": "生成完整可预览的 HTML 页面与演示文稿",
            "system_prompt": (
                "你是资深前端与演示设计专家。根据需求生成完整、自包含的 HTML 代码"
                "（单文件，内联 CSS/JS，不引用外部资源，中文内容）。"
                "做网页：现代简洁风格，响应式。做 PPT：生成基于 <section> 分页的横向演示文稿"
                "（每页一个 section，含翻页按钮与键盘导航，16:9 比例）。"
                "生成后用一两句话说明页面结构，代码放在 ```html 代码块中以便预览。"
            ),
            "openings": ["做一个 5 页的产品介绍 PPT", "帮我做一个数据看板网页"],
        },
    ]
    with SessionLocal() as db:
        if db.scalar(select(func.count()).select_from(Tool)) == 0:
            admin = db.scalar(select(User).where(User.role == "admin"))
            if admin is None:
                return
            for p in presets:
                db.add(Tool(
                    owner_id=admin.id, scope="team",
                    name=p["name"], icon=p["icon"], category=p["category"],
                    kind=p["kind"], description=p["description"],
                    system_prompt=p["system_prompt"],
                    openings_json=_json.dumps(p["openings"], ensure_ascii=False),
                ))
            db.commit()

    # 知识整理官（幂等追加：不存在才加）
    with SessionLocal() as db:
        if db.scalar(select(func.count()).select_from(Tool)) > 0 and \
                not db.scalar(select(Tool).where(Tool.name == "知识整理官")):
            admin = db.scalar(select(User).where(User.role == "admin"))
            if admin:
                db.add(Tool(
                    owner_id=admin.id, scope="team", name="知识整理官",
                    icon="🗃️", category="知识管理", kind="agent",
                    description="知识库管理员的口诀与工作法：存证留 raw、复用编 wiki、来源记台账",
                    system_prompt=(
                        "你是「湖北 AI 工作台」的知识整理官，指导用户使用 Obsidian 式知识管理工作流。"
                        "工作流结构：收件箱/（新资料先进这里）→ 原始资料/（文章·论文报告·书籍摘录·视频转录·会议记录·灵感摘录·数据表格）→"
                        "来源台账/来源登记（记录来源/状态/是否已编译）→ 知识库/（概念·常见问题·综合分析·项目·主题，只放整理后的可复用知识）。\n"
                        "你的判断口诀：只需要存证的留在原始资料；未来会复用的编译进知识库；来源不清楚的提醒补充登记；"
                        "重要结论建议用户更新索引；每次整理要看维护日志。\n"
                        "用户可以随时说「整理收件箱」，你在知识库页点击「整理收件箱」按钮即可自动完成：AI 分类移动→登记台账→高价值内容自动编译为知识页（带双链回指原文）。\n"
                        "回答保持简短可执行；用户问你某资料放哪时，先问来源形态（文章/会议/数据…）再给目录建议，不要按主题分类。"
                    ),
                    openings_json=_json.dumps(
                        ["一份公众号文章应该放进哪个目录？", "帮我判断这条会议记录值不值得编译进知识库", "知识库用久了很乱，怎么治理？"],
                        ensure_ascii=False,
                    ),
                ))
                db.commit()

    # 知识库管家（幂等追加）：对话中直接指挥 AI 执行知识库文件操作
    with SessionLocal() as db:
        if not db.scalar(select(Tool).where(Tool.name == "知识库管家")):
            admin = db.scalar(select(User).where(User.role == "admin"))
            if admin:
                db.add(Tool(
                    owner_id=admin.id, scope="team", name="知识库管家",
                    icon="🗂️", category="知识管理", kind="agent",
                    description="在对话中直接指挥 AI 管理知识库：新建/改名/移动/删除文件夹与文档、把资料存入收件箱",
                    system_prompt=(
                        "你是知识库管家。用户会用自然语言下达知识库操作指令（新建文件夹、把某文档移到某处、"
                        "把资料/对话内容存入知识库、整理收件箱等）。\n"
                        "执行方法：先调用 list_kb 查看真实库结构，再调用 kb_ 开头的函数执行"
                        "（kb_create_folder / kb_rename_folder / kb_delete_folder / kb_move_doc / "
                        "kb_rename_doc / kb_delete_doc / kb_create_doc / kb_organize_inbox）。\n"
                        "规则：默认操作个人库；用户明确要求团队库时传 scope=team（需管理员权限）；"
                        "新资料入库用 kb_create_doc 存入「收件箱」；不要虚构路径，逐条执行并简要汇报结果。"
                    ),
                    openings_json=_json.dumps(
                        ["新建文件夹 原始资料/湖北/周报", "把附件内容存入知识库", "把《xx》移到知识库/综合分析"],
                        ensure_ascii=False,
                    ),
                ))
                db.commit()


def seed_provider(name: str, base_url: str, api_key: str, models: list[dict]) -> None:
    if not api_key or not models:
        return
    with SessionLocal() as db:
        if db.scalar(select(func.count()).select_from(Provider)) == 0:
            db.add(
                Provider(
                    owner_id=None,
                    name=name,
                    base_url=base_url,
                    api_key=api_key,
                    models_json=json.dumps(models, ensure_ascii=False),
                )
            )
            db.commit()


init_db()
