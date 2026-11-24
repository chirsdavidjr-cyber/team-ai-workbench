"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAppData } from "@/lib/app-data";
import type { ToolInfo } from "@/lib/types";
import { Markdown } from "@/components/markdown";
import { askConfirm } from "@/components/dialog";

interface TaskItem {
  input: string;
  output: string;
  status: "pending" | "success" | "failed";
  error?: string;
}

interface TaskInfo {
  id: number;
  name: string;
  tool_id: number;
  status: "queued" | "running" | "success" | "failed" | "cancelled";
  total: number;
  done: number;
  failed: number;
  created_at: string | null;
  items?: TaskItem[];
}

export default function TasksPage() {
  const { tools } = useAppData();
  const [tasks, setTasks] = useState<TaskInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [detail, setDetail] = useState<TaskInfo | null>(null);

  const load = useCallback(async () => {
    try {
      setTasks(await api<TaskInfo[]>("/api/tasks"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // 运行中任务轮询进度
  useEffect(() => {
    const active = tasks.some((t) => t.status === "queued" || t.status === "running");
    if (!active) return;
    const t = setInterval(load, 2500);
    return () => clearInterval(t);
  }, [tasks, load]);

  // 详情轮询
  useEffect(() => {
    if (!detail || (detail.status !== "running" && detail.status !== "queued")) return;
    const t = setInterval(async () => {
      const d = await api<TaskInfo>(`/api/tasks/${detail.id}`);
      setDetail(d);
    }, 2500);
    return () => clearInterval(t);
  }, [detail]);

  async function cancel(t: TaskInfo) {
    await api(`/api/tasks/${t.id}/cancel`, { method: "POST" });
    load();
  }

  async function remove(t: TaskInfo) {
    if (!(await askConfirm({ title: "删除任务", message: `删除任务「${t.name}」？`, danger: true, confirmText: "删除" }))) return;
    await api(`/api/tasks/${t.id}`, { method: "DELETE" });
    if (detail?.id === t.id) setDetail(null);
    load();
  }

  const toolName = (id: number) => tools.find((t) => t.id === id)?.name ?? `工具#${id}`;

  return (
    <div className="mx-auto max-w-4xl px-6 py-10">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-[20px] font-semibold tracking-tight">任务中心</h1>
          <p className="mt-1.5 text-sm text-muted-foreground">
            批量文本任务：选一个工具（如意图分析），粘贴批量内容，后台逐条执行，完成后可导出。
          </p>
        </div>
        <button
          onClick={() => setCreating(true)}
          className="shrink-0 rounded-lg bg-accent px-4 py-2 text-[13.5px] font-medium text-accent-foreground hover:opacity-90"
        >
          + 新建批量任务
        </button>
      </div>

      <div className="mt-6 space-y-2">
        {loading ? (
          <div className="py-10 text-center text-sm text-muted-foreground">加载中…</div>
        ) : tasks.length === 0 ? (
          <div className="rounded-xl border border-dashed border-border-subtle px-4 py-12 text-center text-[13px] text-muted-foreground/70">
            还没有任务。试试用「意图分析」批量处理一批咨询文本
          </div>
        ) : (
          tasks.map((t) => (
            <div
              key={t.id}
              className="group flex cursor-pointer items-center gap-3 rounded-xl border border-border-subtle bg-surface px-4 py-3 hover:border-accent/50"
              onClick={() => api<TaskInfo>(`/api/tasks/${t.id}`).then(setDetail)}
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate text-[14px] font-medium">{t.name}</span>
                  <StatusBadge status={t.status} />
                </div>
                <div className="mt-1 flex items-center gap-3 text-xs text-muted-foreground">
                  <span>{toolName(t.tool_id)}</span>
                  <span>{t.done}/{t.total} 条{t.failed > 0 ? ` · 失败 ${t.failed}` : ""}</span>
                  {(t.status === "running" || t.status === "queued") && (
                    <div className="h-1.5 w-32 overflow-hidden rounded-full bg-muted">
                      <div
                        className="h-full rounded-full bg-accent transition-all"
                        style={{ width: `${t.total ? (t.done / t.total) * 100 : 0}%` }}
                      />
                    </div>
                  )}
                </div>
              </div>
              {(t.status === "running" || t.status === "queued") && (
                <button
                  onClick={(e) => { e.stopPropagation(); cancel(t); }}
                  className="rounded-lg border border-border-subtle px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground"
                >
                  取消
                </button>
              )}
              <button
                onClick={(e) => { e.stopPropagation(); remove(t); }}
                className="hidden rounded-lg border border-border-subtle px-3 py-1.5 text-xs text-muted-foreground hover:border-red-400 hover:text-red-500 group-hover:block"
              >
                删除
              </button>
            </div>
          ))
        )}
      </div>

      {creating && (
        <CreateDialog
          tools={tools}
          onClose={() => setCreating(false)}
          onDone={async () => {
            setCreating(false);
            await load();
          }}
        />
      )}
      {detail && <DetailDialog task={detail} toolName={toolName(detail.tool_id)} onClose={() => setDetail(null)} />}
    </div>
  );
}

function StatusBadge({ status }: { status: TaskInfo["status"] }) {
  const map: Record<string, [string, string]> = {
    queued: ["排队中", "bg-muted text-muted-foreground"],
    running: ["运行中", "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-400"],
    success: ["已完成", "bg-accent-soft text-accent"],
    failed: ["失败", "bg-red-100 text-red-600 dark:bg-red-900/40 dark:text-red-400"],
    cancelled: ["已取消", "bg-muted text-muted-foreground"],
  };
  const [text, cls] = map[status] ?? map.queued;
  return <span className={`rounded-full px-2 py-0.5 text-[10.5px] font-medium ${cls}`}>{text}</span>;
}

function CreateDialog({
  tools,
  onClose,
  onDone,
}: {
  tools: ToolInfo[];
  onClose: () => void;
  onDone: () => void;
}) {
  const skillTools = tools.filter((t) => t.kind !== "template");
  const [toolId, setToolId] = useState<number>(skillTools[0]?.id ?? 0);
  const [name, setName] = useState("");
  const [content, setContent] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [fileName, setFileName] = useState("");

  const lines = content.split("\n").map((s) => s.trim()).filter(Boolean);

  async function create() {
    if (!toolId || lines.length === 0) {
      setError("请选择工具并输入至少一条内容");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await api("/api/tasks", {
        method: "POST",
        body: JSON.stringify({ tool_id: toolId, name: name.trim(), items: lines }),
      });
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : "创建失败");
    } finally {
      setBusy(false);
    }
  }

  const input = "w-full rounded-lg border border-border-subtle bg-background px-3 py-2 text-[13.5px] outline-none focus:border-accent";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto bg-black/40 p-6" onClick={onClose}>
      <div className="my-8 w-full max-w-xl rounded-2xl border border-border-subtle bg-surface p-6 shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-[15px] font-semibold">新建批量任务</h3>
        <div className="mt-4 space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-[12.5px] font-medium">使用工具</label>
              <select value={toolId} onChange={(e) => setToolId(Number(e.target.value))} className={input}>
                {skillTools.map((t) => (
                  <option key={t.id} value={t.id}>{t.icon} {t.name}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="mb-1 block text-[12.5px] font-medium">任务名（可选）</label>
              <input className={input} value={name} onChange={(e) => setName(e.target.value)} placeholder="如：11月咨询意图分析" />
            </div>
          </div>
          <div>
            <label className="mb-1 block text-[12.5px] font-medium">
              批量内容（每行一条，共 {lines.length} 条）
            </label>
            <textarea
              value={content}
              onChange={(e) => setContent(e.target.value)}
              rows={8}
              className={`${input} font-mono text-[12.5px] leading-relaxed`}
              placeholder={"你们的会员有什么权益\n我的订单怎么还没发货\n我要投诉刚才的客服"}
            />
            <label className="mt-2 inline-block cursor-pointer text-[12.5px] text-accent hover:underline">
              或导入 txt/csv 文件{fileName ? `（已加载 ${fileName}）` : ""}
              <input
                type="file"
                accept=".txt,.csv"
                className="hidden"
                onChange={async (e) => {
                  const f = e.target.files?.[0];
                  if (!f) return;
                  const text = await f.text();
                  setContent(text);
                  setFileName(f.name);
                  if (!name.trim()) setName(f.name.replace(/\.(txt|csv)$/i, ""));
                }}
              />
            </label>
          </div>
          {error && <div className="rounded-lg border border-red-300/60 bg-red-50 px-3 py-2 text-[13px] text-red-600 dark:border-red-900/60 dark:bg-red-950/40 dark:text-red-400">{error}</div>}
          <div className="flex gap-2.5 pt-1">
            <button onClick={create} disabled={busy} className="flex-1 rounded-lg bg-accent py-2 text-[13.5px] font-medium text-accent-foreground hover:opacity-90 disabled:opacity-60">
              {busy ? "创建中…" : `开始执行（${lines.length} 条）`}
            </button>
            <button onClick={onClose} className="flex-1 rounded-lg border border-border-subtle py-2 text-[13.5px] text-muted-foreground hover:text-foreground">取消</button>
          </div>
        </div>
      </div>
    </div>
  );
}

function DetailDialog({ task, toolName, onClose }: { task: TaskInfo; toolName: string; onClose: () => void }) {
  const items = task.items ?? [];
  const done = items.filter((i) => i.status === "success");

  function exportCsv() {
    const esc = (s: string | number) => `"${String(s).replace(/"/g, '""')}"`;
    const rows = [
      ["序号", "输入", "结果", "状态"].map(esc).join(","),
      ...items.map((i, n) => [n + 1, i.input, i.output || i.error || "", i.status].map(esc).join(",")),
    ];
    const blob = new Blob(["\uFEFF" + rows.join("\n")], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${task.name}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-6" onClick={onClose}>
      <div className="flex max-h-[85vh] w-full max-w-3xl flex-col rounded-2xl border border-border-subtle bg-surface shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex shrink-0 items-center gap-3 border-b border-border-subtle px-5 py-3.5">
          <b className="text-[15px]">{task.name}</b>
          <StatusBadge status={task.status} />
          <span className="text-xs text-muted-foreground">{toolName} · {task.done}/{task.total}{task.failed ? ` · 失败${task.failed}` : ""}</span>
          <div className="flex-1" />
          {done.length > 0 && (
            <button onClick={exportCsv} className="rounded-lg border border-border-subtle px-3 py-1.5 text-[12.5px] text-muted-foreground hover:text-foreground">
              导出 CSV
            </button>
          )}
          <button onClick={onClose} className="rounded-md p-1.5 text-muted-foreground hover:bg-muted">✕</button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          {items.length === 0 ? (
            <div className="py-10 text-center text-sm text-muted-foreground">加载中…</div>
          ) : (
            <div className="space-y-3">
              {items.map((i, n) => (
                <div key={n} className="rounded-xl border border-border-subtle p-3.5">
                  <div className="flex items-start gap-2 text-[13px]">
                    <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-[10.5px] text-muted-foreground">#{n + 1}</span>
                    <span className="min-w-0 flex-1 whitespace-pre-wrap break-words">{i.input}</span>
                  </div>
                  {i.status === "pending" ? (
                    <div className="mt-2 pl-8 text-[12.5px] text-muted-foreground/60">等待处理…</div>
                  ) : i.status === "failed" ? (
                    <div className="mt-2 pl-8 text-[12.5px] text-red-500">失败：{i.error}</div>
                  ) : (
                    <div className="mt-2 border-l-2 border-border-subtle pl-3">
                      <Markdown content={i.output} />
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
