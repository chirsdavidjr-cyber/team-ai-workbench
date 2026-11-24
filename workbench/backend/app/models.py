import json
from datetime import datetime

from sqlalchemy import String, Text, DateTime, ForeignKey, Boolean, Integer, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64))
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(16), default="member")  # admin | member
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Provider(Base):
    """模型供应商配置。owner_id 为空 = 团队全局（管理员配置），否则为成员个人 Key（BYOK）。"""

    __tablename__ = "providers"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(64))
    base_url: Mapped[str] = mapped_column(String(255))
    api_key: Mapped[str] = mapped_column(String(512))
    models_json: Mapped[str] = mapped_column(Text, default="[]")  # [{"id": "...", "label": "..."}]
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    @property
    def models(self) -> list[dict]:
        try:
            return json.loads(self.models_json)
        except (json.JSONDecodeError, TypeError):
            return []

    def mask_key(self) -> str:
        if len(self.api_key) <= 8:
            return "****"
        return self.api_key[:4] + "****" + self.api_key[-4:]


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(128), default="新对话")
    tool_id: Mapped[int | None] = mapped_column(
        ForeignKey("tools.id"), nullable=True
    )  # 会话发起时使用的工具（行研动态等卡片据此聚合；None = 自由对话）
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.id",
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String(16))  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)  # assistant 消息记录所用模型
    citations_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # 知识引用 [{n,title,snippet,doc_id}]
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class KbDoc(Base):
    """知识文档。scope: personal（个人库，仅本人）| team（团队库，管理员直接上传）。
    team_status：个人文档申请入团队库的状态 none|pending|approved|rejected，
    approved 时该文档对全员可见可检索（同时保留在个人库）。"""

    __tablename__ = "kb_docs"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    scope: Mapped[str] = mapped_column(String(16), default="personal", index=True)
    team_status: Mapped[str] = mapped_column(String(16), default="none", index=True)
    title: Mapped[str] = mapped_column(String(255))
    path: Mapped[str] = mapped_column(String(512), default="")  # 上传时的相对路径（保留目录结构）
    content: Mapped[str] = mapped_column(Text)
    size: Mapped[int] = mapped_column(default=0)  # 字节
    tags_json: Mapped[str] = mapped_column(Text, default="[]")  # #标签 列表
    summary: Mapped[str] = mapped_column(Text, default="")  # AI 摘要（后台异步生成）
    source_file: Mapped[str] = mapped_column(String(512), default="")  # 原文件保存路径（uploads/）
    chunk_count: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(16), default="ready")  # ready | draft（草稿=不入个人检索）
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    @property
    def tags(self) -> list[str]:
        try:
            return json.loads(self.tags_json)
        except (json.JSONDecodeError, TypeError):
            return []


class KbFolder(Base):
    """空文件夹占位（有文档的文件夹由文档 path 动态生成）。与文档一样区分个人/团队。"""

    __tablename__ = "kb_folders"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    scope: Mapped[str] = mapped_column(String(16), default="personal", index=True)
    path: Mapped[str] = mapped_column(String(512), index=True)
    sort: Mapped[int] = mapped_column(default=0)


class KbLink(Base):
    """文档间双链：source 文档中的 [[target]]。target_doc_id 在目标存在时回填。"""

    __tablename__ = "kb_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("kb_docs.id"), index=True)
    target_title: Mapped[str] = mapped_column(String(255), index=True)
    target_doc_id: Mapped[int | None] = mapped_column(ForeignKey("kb_docs.id"), nullable=True)


class KbReview(Base):
    """知识入库团队库审核单：AI 预审 + 管理员终审。"""

    __tablename__ = "kb_reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    doc_id: Mapped[int] = mapped_column(ForeignKey("kb_docs.id"), index=True)
    requester_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    ai_review_json: Mapped[str] = mapped_column(Text, default="{}")  # {duplicate, quality, sensitive}
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending|approved|rejected
    reviewer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    @property
    def ai_review(self) -> dict:
        try:
            return json.loads(self.ai_review_json)
        except (json.JSONDecodeError, TypeError):
            return {}


class KbChunk(Base):
    __tablename__ = "kb_chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    doc_id: Mapped[int] = mapped_column(ForeignKey("kb_docs.id"), index=True)
    idx: Mapped[int] = mapped_column(default=0)
    text: Mapped[str] = mapped_column(Text)
    embedding_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # 向量缺失时走关键词检索
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Dataset(Base):
    """数据集：一次 Excel/CSV 上传落成的结构化表（存于独立沙库 datasets.db）。"""

    __tablename__ = "datasets"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    scope: Mapped[str] = mapped_column(String(16), default="personal", index=True)
    name: Mapped[str] = mapped_column(String(128))
    filename: Mapped[str] = mapped_column(String(255), default="")
    table_name: Mapped[str] = mapped_column(String(64))  # 沙库中的物理表名 ds_{id}
    folder: Mapped[str] = mapped_column(String(512), default="", index=True)  # 所属分组路径（与知识库文件夹同构）
    summary: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)  # AI 数据速读（生成后缓存）
    team_status: Mapped[str] = mapped_column(String(16), default="", index=True)  # 入团队库状态：""/pending/approved/rejected
    row_count: Mapped[int] = mapped_column(default=0)
    profile_json: Mapped[str] = mapped_column(Text, default="[]")  # 数据画像
    semantic_json: Mapped[str] = mapped_column(Text, default="{}")  # 语义层
    status: Mapped[str] = mapped_column(String(16), default="ready")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    @property
    def profile(self) -> list[dict]:
        try:
            return json.loads(self.profile_json)
        except (json.JSONDecodeError, TypeError):
            return []

    @property
    def semantic(self) -> dict:
        try:
            return json.loads(self.semantic_json)
        except (json.JSONDecodeError, TypeError):
            return {}


class Tool(Base):
    """工具（应用壳）：系统提示词 + 可选开场白 + 分类。scope: personal | team。
    folder：分组路径（与知识库 path / 数据集 folder 同构，支持 / 多级）；
    team_status：个人工具申请入团队库的审核状态（""/pending/approved/rejected，与数据集同构）。"""

    __tablename__ = "tools"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    scope: Mapped[str] = mapped_column(String(16), default="personal", index=True)
    name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(String(255), default="")
    icon: Mapped[str] = mapped_column(String(16), default="🧰")  # emoji
    category: Mapped[str] = mapped_column(String(32), default="通用")  # 意图分析/文案/行研...
    folder: Mapped[str] = mapped_column(String(512), default="", index=True)  # 所属分组路径（与知识库/数据模块同构）
    team_status: Mapped[str] = mapped_column(String(16), default="", index=True)  # 入团队库状态：""/pending/approved/rejected
    kind: Mapped[str] = mapped_column(String(16), default="agent")  # agent | skill | template
    system_prompt: Mapped[str] = mapped_column(Text)
    openings_json: Mapped[str] = mapped_column(Text, default="[]")  # 推荐问题列表
    allowed_actions_json: Mapped[str] = mapped_column(Text, default="[]")  # 能力面白名单（组名/动作名，空=全部）
    use_count: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    @property
    def openings(self) -> list[str]:
        try:
            return json.loads(self.openings_json)
        except (json.JSONDecodeError, TypeError):
            return []

    @property
    def allowed_actions(self) -> list[str]:
        """能力面白名单（组名/动作名）；空列表 = 全部动作可用。"""
        try:
            v = json.loads(self.allowed_actions_json)
            return [str(x).strip() for x in v if str(x).strip()] if isinstance(v, list) else []
        except (json.JSONDecodeError, TypeError):
            return []


class PendingDelete(Base):
    """删除确认单：模型首次删除被拦截时登记对象指纹，用户确认后仅对同一对象放行（一次性）。
    持久化存储——后端重启不丢确认状态。"""

    __tablename__ = "pending_deletes"

    id: Mapped[int] = mapped_column(primary_key=True)
    conv_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    target_key: Mapped[str] = mapped_column(String(255), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class DatasetFolder(Base):
    """数据集分组占位（空分组可存在；唯一约束含 owner_id，多用户互不冲突）。"""

    __tablename__ = "dataset_folders"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    scope: Mapped[str] = mapped_column(String(16), default="personal", index=True)
    path: Mapped[str] = mapped_column(String(512), index=True)
    sort: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ToolFolder(Base):
    """工具分组占位（与 DatasetFolder 同构：空分组可存在，个人/团队分开）。"""

    __tablename__ = "tool_folders"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    scope: Mapped[str] = mapped_column(String(16), default="personal", index=True)
    path: Mapped[str] = mapped_column(String(512), index=True)
    sort: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class BatchTask(Base):
    """异步批量任务：用工具（agent/skill）逐条处理文本列表。"""

    __tablename__ = "batch_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    tool_id: Mapped[int] = mapped_column(ForeignKey("tools.id"))
    name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)  # queued|running|success|failed|cancelled
    total: Mapped[int] = mapped_column(default=0)
    done: Mapped[int] = mapped_column(default=0)
    failed: Mapped[int] = mapped_column(default=0)
    items_json: Mapped[str] = mapped_column(Text, default="[]")  # [{input, output, status, error}]
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    @property
    def items(self) -> list[dict]:
        try:
            return json.loads(self.items_json)
        except (json.JSONDecodeError, TypeError):
            return []


class CollectTask(Base):
    """后台采集任务：对注册站点（域名白名单）抓列表+详情，脱敏去重后产出
    明细台账（items_json）并自动注册为数据集（dataset_id）、可选入库知识库（kb_doc_id）。
    合规约束在 collect_service 强制：限速 ≥2s/条、单批 ≤200、受阻即停。"""

    __tablename__ = "collect_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    site_key: Mapped[str] = mapped_column(String(32), index=True)
    columns_json: Mapped[str] = mapped_column(Text, default="[]")  # 选定栏目 URL 列表
    keywords_json: Mapped[str] = mapped_column(Text, default="[]")  # 空 = 不过滤
    max_items: Mapped[int] = mapped_column(default=20)  # ≤200
    to_kb: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)  # queued|running|success|failed|cancelled
    total: Mapped[int] = mapped_column(default=0)
    done: Mapped[int] = mapped_column(default=0)
    failed: Mapped[int] = mapped_column(default=0)
    hindered: Mapped[int] = mapped_column(default=0)  # 受阻条数（登录墙/验证码/空正文）
    items_json: Mapped[str] = mapped_column(Text, default="[]")
    dataset_id: Mapped[int | None] = mapped_column(ForeignKey("datasets.id"), nullable=True)
    kb_doc_id: Mapped[int | None] = mapped_column(ForeignKey("kb_docs.id"), nullable=True)
    kb_doc_title: Mapped[str] = mapped_column(String(255), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    @property
    def items(self) -> list[dict]:
        try:
            return json.loads(self.items_json)
        except (json.JSONDecodeError, TypeError):
            return []


class DataMessage(Base):
    """问数对话记录。assistant 的 payload_json 存 {sql,chart_type,title,columns,rows,interpretation}。
    dataset_id 为空 = 全局问数（多表选表），归属用 owner_id 表达。"""

    __tablename__ = "data_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int | None] = mapped_column(
        ForeignKey("datasets.id"), index=True, nullable=True
    )
    owner_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    role: Mapped[str] = mapped_column(String(16))  # user | assistant
    content: Mapped[str] = mapped_column(Text, default="")  # user=问题 assistant=解读文本
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Workbench(Base):
    """个人工作台（每人可多个）。layout_json 存卡片布局：
    [{"widget": "recent_chats", "span": 1}, ...]，widget 取值 = 前端系统卡片清单 key。"""

    __tablename__ = "workbenches"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(64), default="我的工作台")
    layout_json: Mapped[str] = mapped_column(Text, default="[]")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)  # 首个默认台，删除时兜底
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    @property
    def layout(self) -> list[dict]:
        try:
            data = json.loads(self.layout_json)
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, TypeError):
            return []


class Todo(Base):
    """工作台待办（FR-WORK-03 来源① 手动创建；来源② AI 行动项建议见 W-3）。"""

    __tablename__ = "todos"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(256))
    due_date: Mapped[str | None] = mapped_column(String(10), nullable=True)  # YYYY-MM-DD，可空
    done: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
