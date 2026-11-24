"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "./api";
import { useAppData } from "./app-data";
import type { DatasetInfo, KbDocInfo, TodoInfo, WorkbenchSummary } from "./types";
import { fmtTime } from "@/components/kb-detail";

/**
 * 系统卡片清单（Widget Registry）。
 * 对齐 PRD FR-WORK-02 Assumption：卡片来源 = 系统预置清单，不开放自由开发。
 * 新增卡片：在此注册 def + body 分支，并在后端默认布局中按需追加。
 */

export type WidgetKey =
  | "recent_chats"
  | "batch_tasks"
  | "kb_activity"
  | "my_tools"
  | "todos"
  | "review_queue"
  | "team_usage"
  | "research_activity"
  | "data_progress";

export interface WidgetDef {
  key: WidgetKey;
  title: string;
  desc: string;
  /** 跳转入口 */
  more: { href: string; label: string };
  roles?: Array<"admin" | "member">; // 缺省 = 全部角色可见
}

export const WIDGET_LIST: WidgetDef[] = [
  {
    key: "recent_chats",
    title: "最近会话",
    desc: "最近展开的对话",
    more: { href: "/", label: "去对话" },
  },
  {
    key: "batch_tasks",
    title: "批量任务",
    desc: "运行中与近期的批量任务进度",
    more: { href: "/tasks", label: "全部任务" },
  },
  {
    key: "kb_activity",
    title: "知识库动态",
    desc: "最近更新的知识文档",
    more: { href: "/kb", label: "进入知识库" },
  },
  {
    key: "my_tools",
    title: "常用工具",
    desc: "高频使用的工具与数据集入口",
    more: { href: "/tools", label: "工具库" },
  },
  {
    key: "todos",
    title: "我的待办",
    desc: "手动创建的待办，可设截止日期",
    more: { href: "/tasks", label: "任务中心" },
  },
  {
    key: "review_queue",
    title: "审核队列",
    desc: "知识库与数据集入团申请待审数",
    more: { href: "/admin", label: "去审核" },
    roles: ["admin"],
  },
  {
    key: "team_usage",
    title: "团队用量",
    desc: "近 7 天活跃与团队资产概览",
    more: { href: "/admin", label: "管理后台" },
    roles: ["admin"],
  },
  {
    key: "research_activity",
    title: "行研动态",
    desc: "行研类工具发起的近期会话",
    more: { href: "/", label: "发起行研" },
  },
  {
    key: "data_progress",
    title: "数据进展",
    desc: "近 7 天问数次数与新增数据集",
    more: { href: "/data", label: "去问数" },
  },
];

export function widgetDef(key: string): WidgetDef | undefined {
  return WIDGET_LIST.find((w) => w.key === key);
}

/** 当前角色是否可用该卡片 */
export function widgetAllowed(def: WidgetDef, role: string | undefined): boolean {
  return !def.roles || !role || def.roles.includes(role as "admin" | "member");
}

export function WidgetBody({ widgetKey }: { widgetKey: WidgetKey }) {
  switch (widgetKey) {
    case "recent_chats":
      return <RecentChatsBody />;
    case "batch_tasks":
      return <BatchTasksBody />;
    case "kb_activity":
      return <KbActivityBody />;
    case "my_tools":
      return <MyToolsBody />;
    case "todos":
      return <TodosBody />;
    case "review_queue":
      return <ReviewQueueBody />;
    case "team_usage":
      return <TeamUsageBody />;
    case "research_activity":
      return <ResearchActivityBody />;
    case "data_progress":
      return <DataProgressBody />;
  }
}

/* ---------------- 统计聚合（多卡共享，30s 内复用同一请求） ---------------- */

let summaryCache: { at: number; promise: Promise<WorkbenchSummary | null> } | null = null;

function fetchSummary(): Promise<WorkbenchSummary | null> {
  const now = Date.now();
  if (!summaryCache || now - summaryCache.at > 30_000) {
    summaryCache = {
      at: now,
      promise: api<WorkbenchSummary>("/api/workbenches/summary").catch(() => null),
    };
  }
  return summaryCache.promise;
}

function useSummary() {
  const [data, setData] = useState<WorkbenchSummary | null>(null);
  useEffect(() => {
    fetchSummary().then(setData);
  }, []);
  return data;
}

/* ---------------- 卡片内容（各自懒加载自己的数据） ---------------- */

interface TaskInfo {
  id: number;
  name: string;
  status: string;
  total: number;
  done: number;
  failed: number;
}

function RecentChatsBody() {
  const { conversations } = useAppData();
  return (
    <>
      {conversations.slice(0, 5).map((c) => (
        <Link
          key={c.id}
          href={`/chat/${c.id}`}
          className="flex items-center gap-3 rounded-lg px-3 py-2 transition-colors hover:bg-muted/60"
        >
          <span className="min-w-0 flex-1 truncate text-[13.5px]">{c.title}</span>
          <span className="shrink-0 text-[11px] text-muted-foreground/70">
            {c.message_count} 条 · {fmtTime(c.updated_at)}
          </span>
        </Link>
      ))}
      {conversations.length === 0 && <Empty text="还没有对话，去首页开始第一句" />}
    </>
  );
}

function BatchTasksBody() {
  const [tasks, setTasks] = useState<TaskInfo[] | null>(null);
  useEffect(() => {
    api<TaskInfo[]>("/api/tasks")
      .then(setTasks)
      .catch(() => setTasks([]));
  }, []);
  if (tasks === null) return <Loading />;
  const running = tasks.filter((t) => t.status === "running" || t.status === "queued");
  return (
    <>
      {(running.length > 0 ? running : tasks.slice(0, 4)).map((t) => (
        <Link
          key={t.id}
          href="/tasks"
          className="block rounded-lg px-3 py-2 transition-colors hover:bg-muted/60"
        >
          <div className="flex items-center gap-2">
            <span className="min-w-0 flex-1 truncate text-[13.5px]">{t.name}</span>
            <StatusBadge status={t.status} />
          </div>
          {t.total > 0 && (
            <div className="mt-1.5 h-1 overflow-hidden rounded bg-muted">
              <div
                className="h-full rounded bg-accent transition-all"
                style={{ width: `${Math.round((t.done / t.total) * 100)}%` }}
              />
            </div>
          )}
        </Link>
      ))}
      {tasks.length === 0 && <Empty text="暂无批量任务，可在任务中心创建" />}
    </>
  );
}

function KbActivityBody() {
  const [docs, setDocs] = useState<KbDocInfo[] | null>(null);
  useEffect(() => {
    api<KbDocInfo[]>("/api/kb/docs?scope=all")
      .then((d) => {
        setDocs(
          [...d]
            .sort((a, b) => (b.updated_at ?? "").localeCompare(a.updated_at ?? ""))
            .slice(0, 6)
        );
      })
      .catch(() => setDocs([]));
  }, []);
  if (docs === null) return <Loading />;
  return (
    <>
      {docs.map((d) => (
        <Link
          key={d.id}
          href={`/kb?open=${d.id}`}
          className="flex items-center gap-3 rounded-lg px-3 py-2 transition-colors hover:bg-muted/60"
        >
          <span className="shrink-0 text-[13px]">{d.scope === "team" ? "👥" : "👤"}</span>
          <span className="min-w-0 flex-1 truncate text-[13.5px]">{d.title}</span>
          <span
            className="shrink-0 max-w-[180px] truncate text-[11px] text-muted-foreground/70"
            title={d.path}
          >
            {d.path}
          </span>
        </Link>
      ))}
      {docs.length === 0 && <Empty text="知识库为空，上传第一份资料" />}
    </>
  );
}

function MyToolsBody() {
  const { tools } = useAppData();
  const [datasets, setDatasets] = useState<DatasetInfo[]>([]);
  useEffect(() => {
    api<DatasetInfo[]>("/api/data/datasets?scope=all")
      .then(setDatasets)
      .catch(() => setDatasets([]));
  }, []);
  return (
    <>
      <div className="grid grid-cols-2 gap-2 px-1 py-1">
        {tools.slice(0, 4).map((t) => (
          <Link
            key={t.id}
            href="/"
            className="rounded-lg border border-border-subtle bg-surface px-3 py-2.5 transition-colors hover:border-accent/50"
          >
            <div className="flex items-center gap-1.5">
              <span className="text-[15px]">{t.icon}</span>
              <span className="truncate text-[13px] font-medium">{t.name}</span>
            </div>
            <div className="mt-1 truncate text-[11px] text-muted-foreground">
              已用 {t.use_count} 次
            </div>
          </Link>
        ))}
      </div>
      {tools.length === 0 && <Empty text="还没有工具，去工具库创建" />}
      {datasets.length > 0 && (
        <div className="mt-3 border-t border-border-subtle pt-2.5">
          <Link
            href="/data"
            className="flex items-center gap-2 px-3 py-1.5 text-[12.5px] text-muted-foreground hover:text-accent"
          >
            📊 数据集 {datasets.length} 个 · 去问数 →
          </Link>
        </div>
      )}
    </>
  );
}

/* ---------------- 新增卡（W-2） ---------------- */

function TodosBody() {
  const [todos, setTodos] = useState<TodoInfo[] | null>(null);
  const [title, setTitle] = useState("");
  const [due, setDue] = useState("");

  useEffect(() => {
    api<TodoInfo[]>("/api/todos").then(setTodos).catch(() => setTodos([]));
  }, []);

  const refresh = () => api<TodoInfo[]>("/api/todos").then(setTodos).catch(() => {});

  const add = async () => {
    const t = title.trim();
    if (!t) return;
    setTitle("");
    const keptDue = due;
    setDue("");
    await api("/api/todos", {
      method: "POST",
      body: JSON.stringify({ title: t, due_date: keptDue || null }),
    }).catch(() => {});
    void refresh();
  };

  const toggle = async (t: TodoInfo) => {
    setTodos((prev) => prev?.map((x) => (x.id === t.id ? { ...x, done: !x.done } : x)) ?? null);
    await api(`/api/todos/${t.id}`, {
      method: "PATCH",
      body: JSON.stringify({ done: !t.done }),
    }).catch(() => void refresh());
  };

  const remove = async (id: number) => {
    setTodos((prev) => prev?.filter((x) => x.id !== id) ?? null);
    await api(`/api/todos/${id}`, { method: "DELETE" }).catch(() => void refresh());
  };

  const today = new Date().toISOString().slice(0, 10);
  if (todos === null) return <Loading />;
  return (
    <>
      {todos.map((t) => {
        const overdue = !t.done && t.due_date && t.due_date < today;
        return (
          <div key={t.id} className="group flex items-center gap-2.5 rounded-lg px-3 py-2 hover:bg-muted/60">
            <button
              onClick={() => void toggle(t)}
              className={`flex h-4 w-4 shrink-0 items-center justify-center rounded border text-[10px] transition-colors ${
                t.done
                  ? "border-accent bg-accent text-white"
                  : "border-border hover:border-accent"
              }`}
              title={t.done ? "标记未完成" : "完成"}
            >
              {t.done ? "✓" : ""}
            </button>
            <span
              className={`min-w-0 flex-1 truncate text-[13.5px] ${
                t.done ? "text-muted-foreground/50 line-through" : ""
              }`}
            >
              {t.title}
            </span>
            {t.due_date && (
              <span
                className={`shrink-0 rounded-full px-2 py-0.5 text-[10.5px] ${
                  overdue
                    ? "bg-red-100 text-red-600 dark:bg-red-900/40 dark:text-red-400"
                    : "bg-muted text-muted-foreground"
                }`}
              >
                {overdue ? "逾期 " : ""}
                {t.due_date.slice(5)}
              </span>
            )}
            <button
              onClick={() => void remove(t.id)}
              title="删除"
              className="shrink-0 text-[11px] text-muted-foreground/0 transition-colors group-hover:text-muted-foreground hover:!text-red-500"
            >
              ✕
            </button>
          </div>
        );
      })}
      {todos.length === 0 && <Empty text="暂无待办，写下第一件事" />}
      <div className="mt-2 flex items-center gap-2 px-1">
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void add()}
          placeholder="添加待办…"
          className="min-w-0 flex-1 rounded-lg border border-border-subtle bg-surface px-2.5 py-1.5 text-[13px] outline-none focus:border-accent/60"
        />
        <input
          type="date"
          value={due}
          onChange={(e) => setDue(e.target.value)}
          title="截止日期（可选）"
          className="w-[7.5rem] shrink-0 rounded-lg border border-border-subtle bg-surface px-2 py-1.5 text-[11.5px] text-muted-foreground outline-none focus:border-accent/60"
        />
        <button
          onClick={() => void add()}
          className="shrink-0 rounded-lg border border-border-subtle px-2.5 py-1.5 text-[12.5px] hover:border-accent/50 hover:text-accent"
        >
          添加
        </button>
      </div>
    </>
  );
}

function ReviewQueueBody() {
  const s = useSummary();
  if (!s) return <Loading />;
  const r = s.reviews;
  if (!r) return <Empty text="仅管理员可见" />;
  const total = r.kb + r.data;
  return (
    <>
      <Link href="/admin" className="flex items-center gap-3 rounded-lg px-3 py-2 hover:bg-muted/60">
        <span className="min-w-0 flex-1 truncate text-[13.5px]">知识库入团申请</span>
        <CountBadge n={r.kb} />
      </Link>
      <Link href="/admin" className="flex items-center gap-3 rounded-lg px-3 py-2 hover:bg-muted/60">
        <span className="min-w-0 flex-1 truncate text-[13.5px]">数据集入团申请</span>
        <CountBadge n={r.data} />
      </Link>
      {total === 0 && <Empty text="待审已全部处理完毕" />}
    </>
  );
}

function CountBadge({ n }: { n: number }) {
  return (
    <span
      className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium ${
        n > 0 ? "bg-accent-soft text-accent" : "bg-muted text-muted-foreground/70"
      }`}
    >
      {n} 条
    </span>
  );
}

function TeamUsageBody() {
  const s = useSummary();
  if (!s) return <Loading />;
  const u = s.usage;
  if (!u) return <Empty text="仅管理员可见" />;
  const items: [string, number][] = [
    ["近 7 天消息", u.msgs_7d],
    ["活跃成员", u.active_users_7d],
    ["近 7 天任务", u.tasks_7d],
    ["团队工具", u.team_tools],
    ["团队文档", u.team_docs],
    ["团队数据集", u.team_datasets],
  ];
  return (
    <div className="grid grid-cols-3 gap-2 px-1 py-1">
      {items.map(([label, v]) => (
        <div key={label} className="rounded-lg border border-border-subtle bg-surface px-2.5 py-2">
          <div className="text-[17px] font-semibold leading-6">{v}</div>
          <div className="mt-0.5 truncate text-[11px] text-muted-foreground">{label}</div>
        </div>
      ))}
    </div>
  );
}

function ResearchActivityBody() {
  const s = useSummary();
  if (!s) return <Loading />;
  const list = s.research ?? [];
  return (
    <>
      {list.map((r) => (
        <Link
          key={r.id}
          href={`/chat/${r.id}`}
          className="flex items-center gap-3 rounded-lg px-3 py-2 transition-colors hover:bg-muted/60"
        >
          <span className="min-w-0 flex-1 truncate text-[13.5px]">{r.title}</span>
          <span className="shrink-0 text-[11px] text-muted-foreground/70">
            {r.owner} · {fmtTime(r.updated_at)}
          </span>
        </Link>
      ))}
      {list.length === 0 && <Empty text="暂无行研究会话，用「行研咨询」工具开启" />}
    </>
  );
}

function DataProgressBody() {
  const s = useSummary();
  if (!s) return <Loading />;
  const d = s.data;
  if (!d) return <Empty text="暂无数据" />;
  return (
    <>
      <div className="grid grid-cols-2 gap-2 px-1 py-1">
        <div className="rounded-lg border border-border-subtle bg-surface px-3 py-2.5">
          <div className="text-[17px] font-semibold leading-6">{d.asks_7d}</div>
          <div className="mt-0.5 text-[11px] text-muted-foreground">近 7 天问数次数</div>
        </div>
        <div className="rounded-lg border border-border-subtle bg-surface px-3 py-2.5">
          <div className="text-[17px] font-semibold leading-6">{d.new_datasets_7d}</div>
          <div className="mt-0.5 text-[11px] text-muted-foreground">近 7 天新增数据集</div>
        </div>
      </div>
      <div className="mt-3 border-t border-border-subtle pt-2.5">
        <Link
          href="/data"
          className="flex items-center gap-2 px-3 py-1.5 text-[12.5px] text-muted-foreground hover:text-accent"
        >
          📊 上传新表格，直接开始问数 →
        </Link>
      </div>
    </>
  );
}

/* ---------------- 卡片内小组件 ---------------- */

export function Loading() {
  return <div className="px-3 py-6 text-center text-[12.5px] text-muted-foreground/60">加载中…</div>;
}

export function Empty({ text }: { text: string }) {
  return <div className="px-3 py-6 text-center text-[12.5px] text-muted-foreground/60">{text}</div>;
}

export function StatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    running: "bg-accent-soft text-accent",
    queued: "bg-muted text-muted-foreground",
    success: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-400",
    failed: "bg-red-100 text-red-600 dark:bg-red-900/40 dark:text-red-400",
    cancelled: "bg-muted text-muted-foreground/70",
  };
  const label: Record<string, string> = {
    running: "运行中", queued: "排队中", success: "已完成", failed: "失败", cancelled: "已取消",
  };
  return (
    <span className={`shrink-0 rounded-full px-2 py-0.5 text-[10.5px] font-medium ${map[status] ?? map.queued}`}>
      {label[status] ?? status}
    </span>
  );
}
