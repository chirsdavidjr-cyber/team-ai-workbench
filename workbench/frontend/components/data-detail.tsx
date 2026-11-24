"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { DataMessageInfo, DatasetInfo, SemanticLayer } from "@/lib/types";
import { MiniChart } from "@/components/mini-chart";
import { IconArrowUp } from "@/components/icons";
import { askConfirm, askPrompt } from "@/components/dialog";

type Tab = "preview" | "ask" | "profile" | "semantic";

interface PreviewData {
  columns: string[];
  rows: (string | number)[][];
  row_count: number;
  truncated: boolean;
}

function fmtTime(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** 数据集详情（版式对齐知识库 KbDetail：顶栏 / 元信息 / 操作区 / AI 速读卡 / 正文区块）。 */
export function DataDetail({
  dsId,
  onBack,
  onChanged,
}: {
  dsId: number;
  onBack: () => void;
  onChanged: () => void;
}) {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [ds, setDs] = useState<DatasetInfo | null>(null);
  const [messages, setMessages] = useState<DataMessageInfo[]>([]);
  const [tab, setTab] = useState<Tab>("preview");
  const [toast, setToast] = useState("");
  const [sumBusy, setSumBusy] = useState(false);

  // 问数
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState<string | null>(null);
  const [lastQ, setLastQ] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  // 预览
  const [preview, setPreview] = useState<PreviewData | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  // 语义层
  const [semDraft, setSemDraft] = useState<SemanticLayer>({ columns: {} });
  const [semMsg, setSemMsg] = useState("");
  const [semQ, setSemQ] = useState("");
  const [regenBusy, setRegenBusy] = useState(false);
  const [semImportBusy, setSemImportBusy] = useState(false);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(""), 1500);
    return () => clearTimeout(t);
  }, [toast]);

  const load = useCallback(async () => {
    const data = await api<DatasetInfo & { messages: DataMessageInfo[] }>(
      `/api/data/datasets/${dsId}`
    );
    setDs(data);
    setMessages(data.messages);
    setSemDraft(data.semantic ?? { columns: {} });
    setPreview(null); // 切换数据集时重新拉取预览
  }, [dsId]);

  useEffect(() => {
    load().catch(() => setToast("加载失败"));
  }, [load]);

  // 预览懒加载
  useEffect(() => {
    if (tab !== "preview" || preview || previewLoading) return;
    setPreviewLoading(true);
    api<PreviewData>(`/api/data/datasets/${dsId}/preview?limit=50`)
      .then(setPreview)
      .catch(() => setPreview(null))
      .finally(() => setPreviewLoading(false));
  }, [tab, preview, previewLoading, dsId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy]);

  async function ask(preset?: string) {
    const q = (preset ?? question).trim();
    if (!q || busy) return;
    setBusy(true);
    setLastQ(q);
    setStage("正在连接…");
    setQuestion("");
    const tempId = -Date.now();
    setMessages((prev) => [...prev, { id: tempId, role: "user", content: q, payload: null, created_at: null }]);
    try {
      const { API_BASE, getToken } = await import("@/lib/api");
      const res = await fetch(`${API_BASE}/api/data/datasets/${dsId}/ask-stream`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(getToken() ? { Authorization: `Bearer ${getToken()}` } : {}),
        },
        body: JSON.stringify({ question: q }),
      });
      if (!res.ok || !res.body) throw new Error(`请求失败（${res.status}）`);
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let result: { id: number; content: string; payload: DataMessageInfo["payload"] } | null = null;
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split("\n\n");
        buffer = events.pop() ?? "";
        for (const ev of events) {
          const line = ev.trim();
          if (!line.startsWith("data: ")) continue;
          const e = JSON.parse(line.slice(6));
          if (e.type === "stage") setStage(e.detail ?? e.stage);
          else if (e.type === "result") result = e;
        }
      }
      if (result) {
        const r = result;
        setMessages((prev) => [
          ...prev.map((m) => (m.id === tempId ? { ...m, id: r.id * -1 } : m)),
          { id: r.id, role: "assistant", content: r.content, payload: r.payload, created_at: null },
        ]);
      } else {
        throw new Error("连接中断，请重试");
      }
    } catch (e) {
      setMessages((prev) => [
        ...prev,
        {
          id: -Date.now(),
          role: "assistant",
          content: "",
          payload: { sql: null, chart_type: null, title: null, value_column: null, columns: [], rows: [], truncated: false, error: e instanceof Error ? e.message : "问数失败" },
          created_at: null,
        },
      ]);
    } finally {
      setBusy(false);
      setStage(null);
    }
  }

  async function genSummary() {
    setSumBusy(true);
    setToast("");
    try {
      const res = await api<DatasetInfo>(`/api/data/datasets/${dsId}/summarize`, { method: "POST" });
      setDs(res);
      setToast("AI 速读已生成");
    } catch (e) {
      setToast(e instanceof Error ? e.message : "生成失败");
    } finally {
      setSumBusy(false);
    }
  }

  async function moveDs() {
    if (!ds) return;
    const target = await askPrompt({ title: "移动分组", message: "留空 = 未分组，可用 / 分层级", defaultValue: ds.folder, allowEmpty: true });
    if (target === null) return;
    try {
      const res = await api<DatasetInfo>(`/api/data/datasets/${dsId}/move`, {
        method: "PATCH",
        body: JSON.stringify({ folder: target.trim() }),
      });
      setDs(res);
      setToast("已移动分组");
      onChanged();
    } catch (e) {
      setToast(e instanceof Error ? e.message : "移动失败");
    }
  }

  async function saveSemantic() {
    setSemMsg("");
    try {
      const res = await api<DatasetInfo>(`/api/data/datasets/${dsId}/semantic`, {
        method: "PUT",
        body: JSON.stringify({ semantic: semDraft }),
      });
      setDs(res);
      setSemMsg("语义层已保存，问数将使用新口径");
    } catch (e) {
      setSemMsg(e instanceof Error ? e.message : "保存失败");
    }
  }

  async function regenSemantic() {
    setRegenBusy(true);
    setSemMsg("");
    try {
      const res = await api<DatasetInfo>(`/api/data/datasets/${dsId}/semantic/regenerate`, { method: "POST" });
      setDs(res);
      setSemDraft(res.semantic ?? { columns: {} });
      setSemMsg("AI 已生成语义层草稿");
    } catch (e) {
      setSemMsg(e instanceof Error ? e.message : "生成失败");
    } finally {
      setRegenBusy(false);
    }
  }

  async function importSemantic(files: FileList | null) {
    if (!files || files.length === 0) return;
    setSemImportBusy(true);
    setSemMsg("");
    try {
      // 自适应编码：UTF-8 出现替换符（乱码）时按 GBK 重读
      const buf = await files[0].arrayBuffer();
      const utf8 = new TextDecoder("utf-8").decode(buf);
      const content = utf8.includes("\uFFFD")
        ? (() => { try { return new TextDecoder("gbk").decode(buf); } catch { return utf8; } })()
        : utf8;
      const res = await api<DatasetInfo & { imported_fields?: number }>(
        `/api/data/datasets/${dsId}/semantic/import`,
        { method: "POST", body: JSON.stringify({ content }) }
      );
      setDs(res);
      setSemDraft(res.semantic ?? { columns: {} });
      setSemMsg(`口径文档解析成功，新增/补充 ${res.imported_fields ?? 0} 个字段定义`);
    } catch (e) {
      setSemMsg(e instanceof Error ? e.message : "导入失败");
    } finally {
      setSemImportBusy(false);
    }
  }

  if (!ds) {
    return <div className="py-20 text-center text-sm text-muted-foreground">加载中…</div>;
  }

  const semCount = Object.keys(ds.semantic?.columns ?? {}).length;
  const semTotal = ds.profile.length;
  // 权限：团队数据集成员只读（问数/预览/画像可看，语义层与删除仅管理员）
  const canWrite = isAdmin || ds.scope === "personal";

  const teamBadge =
    ds.scope === "team" || ds.team_status === "approved"
      ? { text: "已入团队库", cls: "bg-accent-soft text-accent" }
      : ds.team_status === "pending"
        ? { text: "团队库·审核中", cls: "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-400" }
        : ds.team_status === "rejected"
          ? { text: "团队库·已驳回", cls: "bg-red-100 text-red-600 dark:bg-red-900/40 dark:text-red-400" }
          : null;

  return (
    <div className="mx-auto max-w-4xl px-6 py-8">
      {/* 顶栏 */}
      <div className="flex items-center gap-3">
        <button onClick={onBack} className="text-muted-foreground hover:text-foreground">←</button>
        <div className="min-w-0 flex-1 truncate text-[16px] font-semibold">{ds.name}</div>
        {canWrite && (
          <button
            onClick={async () => {
              if (!(await askConfirm({ title: "删除数据集", message: `删除数据集「${ds.name}」？问数记录将一并删除，不可恢复。`, danger: true, confirmText: "删除" }))) return;
              try {
                await api(`/api/data/datasets/${dsId}`, { method: "DELETE" });
                onChanged();
                onBack();
              } catch (e) {
                setToast(e instanceof Error ? e.message : "删除失败");
              }
            }}
            className="rounded-lg border border-border-subtle px-3 py-1.5 text-[12.5px] text-muted-foreground hover:text-red-500"
          >
            删除
          </button>
        )}
      </div>

      {/* 元信息 */}
      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <span className="font-mono">{ds.folder ? `📁 ${ds.folder}` : "未分组"}</span>
        <span>{ds.row_count} 行 × {semTotal} 列</span>
        <span title={ds.filename}>来源 {ds.filename}</span>
        <span>上传于 {fmtTime(ds.created_at)}</span>
        <span className={`rounded-full px-2 py-0.5 ${ds.scope === "team" ? "bg-accent-soft text-accent" : "bg-muted"}`}>
          {ds.scope === "team" ? "团队数据" : "个人数据"}
        </span>
        <span className={`rounded-full px-2 py-0.5 ${semCount >= semTotal ? "bg-accent-soft text-accent" : "bg-muted"}`}>
          语义层 {semCount}/{semTotal}
        </span>
        {teamBadge && <span className={`rounded-full px-2 py-0.5 ${teamBadge.cls}`}>{teamBadge.text}</span>}
      </div>

      {/* 操作区 */}
      <div className="mt-3 flex flex-wrap gap-2">
        {canWrite && (
          <button
            onClick={genSummary}
            disabled={sumBusy}
            className="rounded-lg bg-accent px-3.5 py-1.5 text-[12.5px] font-medium text-accent-foreground hover:opacity-90 disabled:opacity-60"
          >
            {sumBusy ? "AI 速读中…" : ds.summary ? "重新 AI 速读" : "AI 速读"}
          </button>
        )}
        <button
          onClick={() => setTab("ask")}
          className="rounded-lg border border-border-subtle px-3 py-1.5 text-[12.5px] text-muted-foreground hover:text-foreground"
        >
          问数
        </button>
        {canWrite && (
          <button
            onClick={moveDs}
            className="rounded-lg border border-border-subtle px-3 py-1.5 text-[12.5px] text-muted-foreground hover:text-foreground"
          >
            移动分组…
          </button>
        )}
        {/* 个人数据集：申请入团队库（默认不入，管理员审核） */}
        {ds.scope === "personal" && (ds.team_status === "" || ds.team_status === "rejected") && (
          <button
            onClick={async () => {
              try {
                const res = await api<DatasetInfo>(`/api/data/datasets/${dsId}/request-team`, { method: "POST" });
                setDs(res);
                setToast("已提交申请，等待管理员审核");
                onChanged();
              } catch (e) {
                setToast(e instanceof Error ? e.message : "申请失败");
              }
            }}
            className="rounded-lg border border-border-subtle px-3 py-1.5 text-[12.5px] text-muted-foreground hover:text-foreground"
            title="个人数据默认不入团队库；申请后由管理员审核"
          >
            申请入团队库
          </button>
        )}
        {/* 管理员审核（pending） */}
        {isAdmin && ds.team_status === "pending" && (
          <>
            <button
              onClick={async () => {
                try {
                  const res = await api<DatasetInfo>(`/api/data/datasets/${dsId}/review-team`, {
                    method: "POST",
                    body: JSON.stringify({ approve: true }),
                  });
                  setDs(res);
                  setToast("已通过，数据集进入团队库");
                  onChanged();
                } catch (e) {
                  setToast(e instanceof Error ? e.message : "操作失败");
                }
              }}
              className="rounded-lg bg-accent px-3 py-1.5 text-[12.5px] font-medium text-accent-foreground hover:opacity-90"
            >
              通过入团队库
            </button>
            <button
              onClick={async () => {
                try {
                  const res = await api<DatasetInfo>(`/api/data/datasets/${dsId}/review-team`, {
                    method: "POST",
                    body: JSON.stringify({ approve: false }),
                  });
                  setDs(res);
                  setToast("已驳回");
                  onChanged();
                } catch (e) {
                  setToast(e instanceof Error ? e.message : "操作失败");
                }
              }}
              className="rounded-lg border border-border-subtle px-3 py-1.5 text-[12.5px] text-red-500 hover:bg-red-50 dark:hover:bg-red-950/30"
            >
              驳回
            </button>
          </>
        )}
      </div>
      {toast && <div className="mt-2 rounded-lg bg-muted px-3 py-1.5 text-[12.5px] text-muted-foreground">{toast}</div>}

      {/* AI 速读卡（对标知识库 AI 摘要） */}
      {ds.summary && (
        <div className="mt-3 rounded-xl border border-border-subtle bg-muted/50 px-4 py-3 text-[13px] leading-relaxed">
          <span className="mr-1.5 font-medium text-accent">AI 速读</span>
          {ds.summary}
        </div>
      )}

      {/* 正文：轻量分段（默认数据预览，像文档正文） */}
      <div className="mt-5">
        <div className="flex gap-1 rounded-lg bg-muted p-1 w-fit">
          {([["preview", "数据预览"], ["ask", "问数"], ["profile", "数据画像"], ["semantic", "语义层"]] as [Tab, string][]).map(([t, label]) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`rounded-md px-3.5 py-1 text-[12.5px] transition-colors ${
                tab === t ? "bg-background font-medium shadow-[0_1px_2px_rgba(0,0,0,0.05)]" : "text-muted-foreground"
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        {/* 数据预览 */}
        {tab === "preview" && (
          <div className="mt-4">
            {previewLoading ? (
              <div className="py-12 text-center text-sm text-muted-foreground">加载数据中…</div>
            ) : !preview ? (
              <div className="rounded-xl border border-dashed border-border-subtle px-4 py-10 text-center text-[13px] text-muted-foreground/70">
                数据读取失败，<button onClick={() => setPreview(null)} className="text-accent hover:underline">重试</button>
              </div>
            ) : (
              <>
                <div className="max-h-[60vh] overflow-auto rounded-xl border border-border-subtle">
                  <table className="w-full border-collapse text-[12.5px]">
                    <thead className="sticky top-0 z-10">
                      <tr className="bg-muted">
                        {preview.columns.map((c) => (
                          <th key={c} className="whitespace-nowrap px-3 py-2 text-left font-medium" title={c}>{c}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {preview.rows.map((row, i) => (
                        <tr key={i} className="border-t border-border-subtle transition-colors hover:bg-muted/40">
                          {row.map((v, j) => (
                            <td
                              key={j}
                              title={String(v)}
                              className={`max-w-56 truncate px-3 py-1.5 ${
                                typeof v === "number" ? "text-right font-mono tabular-nums" : "text-muted-foreground"
                              }`}
                            >
                              {v === "" ? <span className="text-muted-foreground/30">—</span> : v}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="mt-2 text-xs text-muted-foreground/70">
                  {preview.truncated
                    ? `仅显示前 ${preview.rows.length} 行（共 ${preview.row_count} 行），完整数据请用「问数」查询`
                    : `已显示全部 ${preview.row_count} 行`}
                </div>
              </>
            )}
          </div>
        )}

        {/* 问数 */}
        {tab === "ask" && (
          <div className="mt-4">
            <div className="max-h-[52vh] space-y-5 overflow-y-auto pr-1">
              {messages.length === 0 && !busy && (
                <div className="py-10 text-center">
                  <div className="text-[14px] text-muted-foreground">用自然语言查询「{ds.name}」</div>
                  <div className="mx-auto mt-4 max-w-md space-y-2">
                    {["各渠道接通率是多少", "哪个月呼入量最高", "满意度趋势如何"].map((s) => (
                      <button
                        key={s}
                        onClick={() => ask(s)}
                        className="block w-full rounded-xl border border-border-subtle bg-surface px-4 py-2.5 text-left text-[13.5px] text-muted-foreground transition-colors hover:border-accent/50 hover:text-foreground"
                      >
                        {s}
                      </button>
                    ))}
                  </div>
                </div>
              )}
              {messages.map((m) =>
                m.role === "user" ? (
                  <div key={m.id} className="flex justify-end">
                    <div
                      onClick={() => !busy && ask(m.content)}
                      title="点击重新提问"
                      className="max-w-[85%] cursor-pointer whitespace-pre-wrap rounded-2xl rounded-br-md bg-muted px-4 py-2.5 text-[14px] leading-relaxed transition-colors hover:bg-muted/70"
                    >
                      {m.content}
                    </div>
                  </div>
                ) : (
                  <div key={m.id} className="flex gap-3">
                    <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-accent text-[11px] font-bold text-accent-foreground">Q</div>
                    <div className="min-w-0 flex-1 space-y-3">
                      {m.payload?.error ? (
                        <div>
                          <div className="rounded-lg border border-red-300/60 bg-red-50 px-3.5 py-2.5 text-[13px] text-red-600 dark:border-red-900/60 dark:bg-red-950/40 dark:text-red-400">
                            {m.payload.error}
                          </div>
                          {!busy && m.content === "" && (
                            <button
                              onClick={() => ask(lastQ)}
                              disabled={!lastQ}
                              className="mt-2 rounded-lg border border-border-subtle px-3 py-1.5 text-[12.5px] text-muted-foreground hover:text-foreground disabled:opacity-50"
                            >
                              重试
                            </button>
                          )}
                        </div>
                      ) : m.payload ? (
                        <>
                          {m.payload.title && <div className="text-[14.5px] font-semibold">{m.payload.title}</div>}
                          {!!m.payload.notes?.length && (
                            <div className="text-[11.5px] text-amber-600 dark:text-amber-400">
                              ⚠ {m.payload.notes.join("；")}
                            </div>
                          )}
                          <MiniChart payload={m.payload} />
                          {m.payload.sql && <SqlBox sql={m.payload.sql} />}
                          {m.content && (
                            <div className="rounded-xl border border-border-subtle bg-muted/50 px-4 py-3 text-[13px] leading-relaxed">
                              <span className="mr-1.5 font-medium text-accent">AI 解读</span>
                              {m.content}
                            </div>
                          )}
                        </>
                      ) : null}
                    </div>
                  </div>
                )
              )}
              {busy && (
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-border-subtle border-t-accent" />
                  {stage ?? "正在生成 SQL 并执行…"}
                </div>
              )}
              <div ref={bottomRef} />
            </div>
            <div className="mt-3 flex items-center gap-2 rounded-2xl border border-border-subtle bg-surface p-2 pl-4">
              <input
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && ask()}
                placeholder="用自然语言提问，如：11月各渠道接通率排名"
                className="flex-1 bg-transparent text-[14px] outline-none placeholder:text-muted-foreground/60"
              />
              <button
                onClick={() => ask()}
                disabled={!question.trim() || busy}
                className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent text-accent-foreground transition-all hover:opacity-90 disabled:bg-muted disabled:text-muted-foreground/40"
              >
                <IconArrowUp />
              </button>
            </div>
          </div>
        )}

        {/* 数据画像 */}
        {tab === "profile" && (
          <div className="mt-4 overflow-x-auto rounded-xl border border-border-subtle">
            <table className="w-full border-collapse text-[12.5px]">
              <thead>
                <tr className="bg-muted">
                  {["字段", "类型", "非空率", "唯一值", "样本值"].map((h) => (
                    <th key={h} className="px-3 py-2 text-left font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {ds.profile.map((c) => (
                  <tr key={c.name} className="border-t border-border-subtle">
                    <td className="px-3 py-2 font-medium">{c.name}</td>
                    <td className="px-3 py-2 font-mono text-[11.5px] text-muted-foreground">{c.dtype}</td>
                    <td className="px-3 py-2">{((1 - c.null_rate) * 100).toFixed(1)}%</td>
                    <td className="px-3 py-2">{c.distinct}</td>
                    <td className="max-w-64 truncate px-3 py-2 text-muted-foreground" title={c.samples.join("、")}>
                      {c.samples.join("、")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* 语义层 */}
        {tab === "semantic" && (
          <div className="mt-4">
            {canWrite ? (
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-xs text-muted-foreground">
                  三种来源：①上传口径文档 ②问数时 AI 现场推断并沉淀 ③下方手工编辑。手工定义不会被 AI 覆盖。
                </p>
                <div className="flex shrink-0 gap-2">
                  <label className="cursor-pointer rounded-lg border border-border-subtle px-3.5 py-1.5 text-[12.5px] text-muted-foreground hover:text-foreground">
                    {semImportBusy ? "解析中…" : "上传口径文档"}
                    <input type="file" accept=".md,.txt,.json,.csv" className="hidden" onChange={(e) => importSemantic(e.target.files)} />
                  </label>
                  <button
                    onClick={regenSemantic}
                    disabled={regenBusy}
                    className="rounded-lg border border-border-subtle px-3.5 py-1.5 text-[12.5px] text-muted-foreground hover:text-foreground disabled:opacity-60"
                  >
                    {regenBusy ? "生成中…" : "AI 生成草稿"}
                  </button>
                  <button
                    onClick={saveSemantic}
                    className="rounded-lg bg-accent px-3.5 py-1.5 text-[12.5px] font-medium text-accent-foreground hover:opacity-90"
                  >
                    保存
                  </button>
                </div>
              </div>
            ) : (
              <div className="rounded-lg border border-border-subtle bg-muted/50 px-3.5 py-2.5 text-[12.5px] text-muted-foreground">
                🔒 团队数据集的语义层由管理员维护，成员只读（问数时仍会自动使用）
              </div>
            )}
            {semMsg && <div className="mt-3 rounded-lg bg-muted px-3.5 py-2 text-[13px] text-muted-foreground">{semMsg}</div>}

            {ds.profile.length > 8 && (
              <input
                value={semQ}
                onChange={(e) => setSemQ(e.target.value)}
                placeholder="按字段名 / 含义过滤…"
                className="mt-3 w-full rounded-lg border border-border-subtle bg-surface px-3 py-1.5 text-[13px] outline-none focus:border-accent"
              />
            )}

            <div className="mt-4 space-y-2.5">
              {ds.profile
                .filter((c) => {
                  if (!semQ.trim()) return true;
                  const desc = semDraft.columns[c.name]?.desc ?? "";
                  return c.name.toLowerCase().includes(semQ.toLowerCase()) || desc.includes(semQ.trim());
                })
                .map((c) => {
                  const col = semDraft.columns[c.name] ?? { desc: "" };
                  const enumStr = Object.entries(col.enum ?? {}).map(([k, v]) => `${k}=${v}`).join(", ");
                  return (
                    <div key={c.name} className="rounded-xl border border-border-subtle bg-surface p-3.5">
                      <div className="flex items-center gap-2">
                        <span className="text-[13.5px] font-medium">{c.name}</span>
                        <span className="font-mono text-[11px] text-muted-foreground">{c.dtype}</span>
                        <span className="truncate text-[11px] text-muted-foreground/70">样本：{c.samples.slice(0, 3).join("、")}</span>
                      </div>
                      <div className="mt-2.5 grid grid-cols-1 gap-2 sm:grid-cols-2">
                        <input
                          value={col.desc}
                          disabled={!canWrite}
                          onChange={(e) =>
                            setSemDraft((prev) => ({
                              ...prev,
                              columns: { ...prev.columns, [c.name]: { ...col, desc: e.target.value } },
                            }))
                          }
                          placeholder="业务含义，如：接通量占呼入量的比例"
                          className="rounded-lg border border-border-subtle bg-background px-3 py-1.5 text-[13px] outline-none focus:border-accent disabled:cursor-not-allowed disabled:opacity-70"
                        />
                        <input
                          defaultValue={enumStr}
                          disabled={!canWrite}
                          onBlur={(e) => {
                            const enumObj: Record<string, string> = {};
                            e.target.value.split(/[,，]/).forEach((pair) => {
                              const [k, v] = pair.split("=");
                              if (k?.trim() && v?.trim()) enumObj[k.trim()] = v.trim();
                            });
                            setSemDraft((prev) => ({
                              ...prev,
                              columns: { ...prev.columns, [c.name]: { ...col, enum: enumObj } },
                            }));
                          }}
                          placeholder="枚举翻译，如：1=电话, 2=在线（失焦保存）"
                          className="rounded-lg border border-border-subtle bg-background px-3 py-1.5 text-[13px] outline-none focus:border-accent"
                        />
                      </div>
                    </div>
                  );
                })}
            </div>

            {/* 指标注册表：固定口径受控执行 */}
            <div className="mt-6">
              <div className="flex items-center justify-between">
                <div className="text-[13px] font-medium">指标注册表</div>
                {canWrite && (
                  <button
                    onClick={() =>
                      setSemDraft((prev) => ({
                        ...prev,
                        metrics: [...(prev.metrics ?? []), { name: "", expr: "", desc: "" }],
                      }))
                    }
                    className="rounded-lg border border-border-subtle px-3 py-1.5 text-[12.5px] text-muted-foreground hover:text-foreground"
                  >
                    添加指标
                  </button>
                )}
              </div>
              <p className="mt-1 text-xs text-muted-foreground">
                固定口径指标（如加微率、绑定率）：问数问题命中指标名时按此口径模板受控执行，不经模型生成，杜绝口径漂移。expr 为 SQL 聚合表达式，如 ROUND(AVG(CASE WHEN "加微标记"='是' THEN 1.0 ELSE 0.0 END)*100,1)。
              </p>
              <div className="mt-2.5 space-y-2.5">
                {(semDraft.metrics ?? []).map((m, i) => (
                  <div key={i} className="rounded-xl border border-border-subtle bg-surface p-3.5">
                    <div className="flex items-center gap-2">
                      <input
                        value={m.name}
                        disabled={!canWrite}
                        onChange={(e) =>
                          setSemDraft((prev) => ({
                            ...prev,
                            metrics: (prev.metrics ?? []).map((x, j) => (j === i ? { ...x, name: e.target.value } : x)),
                          }))
                        }
                        placeholder="指标名，如：加微率"
                        className="w-44 rounded-lg border border-border-subtle bg-background px-3 py-1.5 text-[13px] font-medium outline-none focus:border-accent disabled:cursor-not-allowed disabled:opacity-70"
                      />
                      <input
                        value={m.desc}
                        disabled={!canWrite}
                        onChange={(e) =>
                          setSemDraft((prev) => ({
                            ...prev,
                            metrics: (prev.metrics ?? []).map((x, j) => (j === i ? { ...x, desc: e.target.value } : x)),
                          }))
                        }
                        placeholder="口径说明，如：加微信客户占比(%)"
                        className="min-w-0 flex-1 rounded-lg border border-border-subtle bg-background px-3 py-1.5 text-[13px] outline-none focus:border-accent disabled:cursor-not-allowed disabled:opacity-70"
                      />
                      {canWrite && (
                        <button
                          onClick={() =>
                            setSemDraft((prev) => ({
                              ...prev,
                              metrics: (prev.metrics ?? []).filter((_, j) => j !== i),
                            }))
                          }
                          className="shrink-0 rounded-lg border border-border-subtle px-2.5 py-1.5 text-[12px] text-muted-foreground hover:text-red-500"
                        >
                          删除
                        </button>
                      )}
                    </div>
                    <input
                      value={m.expr}
                      disabled={!canWrite}
                      onChange={(e) =>
                        setSemDraft((prev) => ({
                          ...prev,
                          metrics: (prev.metrics ?? []).map((x, j) => (j === i ? { ...x, expr: e.target.value } : x)),
                        }))
                      }
                      placeholder='SQL 聚合表达式，字段用双引号，如 ROUND(SUM("加微数")*100.0/COUNT(*),1)'
                      className="mt-2 w-full rounded-lg border border-border-subtle bg-background px-3 py-1.5 font-mono text-[12px] outline-none focus:border-accent disabled:cursor-not-allowed disabled:opacity-70"
                    />
                  </div>
                ))}
                {!(semDraft.metrics ?? []).length && (
                  <div className="rounded-lg border border-dashed border-border-subtle px-3.5 py-3 text-[12.5px] text-muted-foreground">
                    暂无注册指标。可「上传口径文档」自动提取、AI 生成草稿后补充，或点「添加指标」手工登记。
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function SqlBox({ sql }: { sql: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button
        onClick={() => setOpen(!open)}
        className="text-[12px] text-muted-foreground transition-colors hover:text-foreground"
      >
        {open ? "▾ 收起 SQL" : "▸ 查看 SQL"}
      </button>
      {open && (
        <pre className="mt-2 overflow-x-auto rounded-lg border border-border-subtle bg-muted/70 px-3.5 py-3 font-mono text-[12px] leading-relaxed">
          {sql}
        </pre>
      )}
    </div>
  );
}
