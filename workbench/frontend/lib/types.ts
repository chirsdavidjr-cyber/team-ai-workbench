export interface ModelItem {
  id: string;
  label: string;
}

export interface ProviderInfo {
  id: number;
  name: string;
  base_url: string;
  api_key_masked: string;
  models: ModelItem[];
  enabled: boolean;
  scope: "team" | "personal";
  owner_id: number | null;
}

export interface ConversationInfo {
  id: number;
  title: string;
  updated_at: string | null;
  message_count: number;
}

export interface MessageInfo {
  id: number;
  role: "user" | "assistant";
  content: string;
  model: string | null;
  citations: Citation[] | null;
  created_at: string | null;
}

export interface Citation {
  n: number;
  doc_id: number;
  title: string;
  snippet: string;
  mode?: string;
}

export interface KbDocInfo {
  id: number;
  title: string;
  path: string;
  scope: "personal" | "team";
  team_status: "none" | "pending" | "approved" | "rejected";
  owner_id: number;
  size: number;
  tags: string[];
  summary: string;
  has_source_file: boolean;
  chunk_count: number;
  status: string;
  created_at: string | null;
  updated_at: string | null;
  link_count?: number | null;
}

export interface KbDocDetail extends KbDocInfo {
  content: string;
  links?: {
    outgoing: { title: string; doc_id: number | null }[];
    incoming: { title: string; doc_id: number }[];
  };
}

export interface KbGraph {
  nodes: { id: number; title: string; scope: string; chunk_count: number }[];
  edges: { source: number; target: number | null; title: string }[];
}

export interface ColumnProfile {
  name: string;
  dtype: string;
  non_null: number;
  null_rate: number;
  distinct: number;
  samples: string[];
}

export interface SemanticLayer {
  columns: Record<string, { desc: string; enum?: Record<string, string> }>;
  metrics?: { name: string; expr: string; desc?: string }[];
}

export interface DatasetInfo {
  id: number;
  name: string;
  filename: string;
  scope: "personal" | "team";
  folder: string;
  row_count: number;
  profile: ColumnProfile[];
  semantic: SemanticLayer;
  status: string;
  summary?: string;
  team_status?: string;
  created_at: string | null;
}

export interface ToolInfo {
  id: number;
  name: string;
  description: string;
  icon: string;
  category: string;
  kind: "agent" | "skill" | "template";
  system_prompt: string;
  openings: string[];
  allowed_actions: string[]; // 能力面白名单（空 = 全部动作）
  scope: "personal" | "team";
  team_status: string; // 入团队库审核状态：""/pending/approved/rejected（与数据模块同构）
  folder: string; // 分组路径（与知识库/数据模块同构）
  owner_id: number;
  use_count: number;
  created_at: string | null;
  updated_at: string | null;
}

/** 新建/编辑工具的表单载荷（与后端 ToolIn/ToolUpdateIn 对应） */
export interface ToolDraft {
  name: string;
  icon: string;
  category: string;
  kind: "agent" | "skill" | "template";
  description: string;
  system_prompt: string;
  openings: string[];
  allowed_actions: string[];
  folder: string;
  scope: "personal" | "team";
}

export interface ChartPayload {
  sql: string | null;
  chart_type: string | null;
  title: string | null;
  value_column: string | null; // 回答问题的指标列（后端 text2sql 指定，图表数值列据此选，不靠猜）
  columns: string[];
  rows: unknown[][];
  truncated: boolean;
  error: string | null;
  /** 全局问数：自动选中的数据表（旧消息无此字段） */
  dataset_ids?: number[];
  tables?: { id: number; name: string }[];
  /** 过程提示（受控执行/检索/二次修正保留等，旧消息无此字段） */
  notes?: string[] | null;
}

export interface DataMessageInfo {
  id: number;
  role: "user" | "assistant";
  content: string;
  payload: ChartPayload | null;
  created_at: string | null;
}

export interface ChatSSEEvent {
  type: "delta" | "reasoning" | "done" | "error" | "tool";
  text?: string;
  detail?: string;
  message_id?: number;
  conversation_title?: string;
  citations?: Citation[];
  /** type === "done"：工具调用轮次耗尽，回复「继续」可接续执行 */
  continuable?: boolean;
  /** type === "tool"：工作台动作调用进度（function calling） */
  name?: string;
  args?: Record<string, unknown>;
  status?: "calling" | "ok" | "error";
  result?: { ok?: boolean; message?: string; error?: string; [k: string]: unknown };
}

/** 当前选中的模型：providerId + modelId，序列化存 localStorage */
export interface ModelSelection {
  providerId: number;
  providerName: string;
  modelId: string;
  modelLabel: string;
}

export function selectionKey(s: ModelSelection) {
  return `${s.providerId}:${s.modelId}`;
}

/** 工作台卡片布局项（与后端 workbenches.layout_json 对应） */
export interface WidgetLayoutItem {
  widget: string;
  span: 1 | 2;
}

export interface WorkbenchInfo {
  id: number;
  name: string;
  layout: WidgetLayoutItem[];
  is_default: boolean;
  updated_at: string | null;
}

/** 待办（FR-WORK-03 来源① 手动创建） */
export interface TodoInfo {
  id: number;
  title: string;
  due_date: string | null;
  done: boolean;
  created_at: string | null;
}

/** 工作台统计卡聚合（/api/workbenches/summary，管理员多返回 reviews/usage） */
export interface WorkbenchSummary {
  data: { asks_7d: number; new_datasets_7d: number } | null;
  research: { id: number; title: string; owner: string; updated_at: string | null }[] | null;
  reviews?: { kb: number; data: number } | null;
  usage?: {
    msgs_7d: number;
    active_users_7d: number;
    tasks_7d: number;
    team_tools: number;
    team_docs: number;
    team_datasets: number;
  } | null;
}
