"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { ChartPayload, DataMessageInfo } from "@/lib/types";
import { MiniChart } from "@/components/mini-chart";
import { IconArrowUp } from "@/components/icons";

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

/** 全局问数面板：不选数据集直接提问，AI 自动选表（可跨表 JOIN）。Trae 式：目录 → 选表 → 生成执行 → 解读。 */
export function GlobalAskPanel({
  onClose,
  onOpenDataset,
}: {
  onClose: () => void;
  onOpenDataset: (dsId: number) => void;
}) {
  const [messages, setMessages] = useState<DataMessageInfo[]>([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState<string | null>(null);
  const [pickedTables, setPickedTables] = useState<{ id: number; name: string }[] | null>(null);
  const [lastQ, setLastQ] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api<DataMessageInfo[]>("/api/data/ask/messages").then(setMessages).catch(() => {});
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function ask(preset?: string) {
    const q = (preset ?? question).trim();
    if (!q || busy) return;
    setBusy(true);
    setLastQ(q);
    setStage("正在选择数据表…");
    setPickedTables(null);
    setQuestion("");
    const tempId = -Date.now();
    setMessages((prev) => [...prev, { id: tempId, role: "user", content: q, payload: null, created_at: null }]);
    try {
      const { API_BASE, getToken } = await import("@/lib/api");
      const res = await fetch(`${API_BASE}/api/data/ask-stream`, {
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
      let result: { id: number; content: string; payload: ChartPayload | null } | null = null;
      let errDetail = "";
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
          if (e.type === "stage") {
            if (e.stage === "selected") setPickedTables(e.tables ?? null);
            setStage(e.detail ?? e.stage);
          } else if (e.type === "result") {
            result = e;
          } else if (e.type === "error") {
            errDetail = e.detail || "问数失败";
            if (e.payload) result = { id: e.id ?? -Date.now(), content: "", payload: e.payload };
          }
        }
      }
      if (result) {
        const r = result;
        setMessages((prev) => [
          ...prev.map((m) => (m.id === tempId ? { ...m, id: r.id * -1 } : m)),
          { id: r.id, role: "assistant", content: r.content, payload: r.payload, created_at: null },
        ]);
      } else {
        throw new Error(errDetail || "连接中断，请重试");
      }
    } catch (e) {
      setMessages((prev) => [
        ...prev,
        {
          id: -Date.now(),
          role: "assistant",
          content: "",
          payload: {
            sql: null, chart_type: null, title: null, value_column: null,
            columns: [], rows: [], truncated: false,
            error: e instanceof Error ? e.message : "问数失败",
          },
          created_at: null,
        },
      ]);
    } finally {
      setBusy(false);
      setStage(null);
      setPickedTables(null);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-6" onClick={onClose}>
      <div
        className="flex max-h-[86vh] w-[760px] max-w-full flex-col rounded-2xl border border-border-subtle bg-surface shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between border-b border-border-subtle px-5 py-3.5">
          <div>
            <div className="text-[14.5px] font-semibold">全局问数</div>
            <div className="mt-0.5 text-[12px] text-muted-foreground">
              不用选表，AI 自动从「我的 + 团队」数据表中选表作答，支持跨表关联
            </div>
          </div>
          <button onClick={onClose} className="rounded-lg p-1 text-muted-foreground hover:text-foreground">✕</button>
        </div>

        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-5 py-4">
          {messages.length === 0 && !busy && (
            <div className="py-10 text-center">
              <div className="text-[14px] text-muted-foreground">直接问一个数据问题</div>
              <div className="mx-auto mt-4 max-w-md space-y-2">
                {["各数据表里哪张行数最多", "各渠道的接通率怎么样", "对比一下不同月份的呼入量"].map((s) => (
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
                      {m.payload.tables && m.payload.tables.length > 0 && (
                        <div className="flex flex-wrap items-center gap-1.5">
                          <span className="text-[11.5px] text-muted-foreground/70">用到数据表：</span>
                          {m.payload.tables.map((t) => (
                            <button
                              key={t.id}
                              onClick={() => onOpenDataset(t.id)}
                              title="打开数据集详情"
                              className="rounded-full border border-border-subtle bg-muted/60 px-2.5 py-0.5 text-[11.5px] text-muted-foreground transition-colors hover:border-accent/50 hover:text-accent"
                            >
                              📊 {t.name}
                            </button>
                          ))}
                        </div>
                      )}
                      {!!m.payload.notes?.length && (
                        <div className="text-[11.5px] text-amber-600 dark:text-amber-400">⚠ {m.payload.notes.join("；")}</div>
                      )}
                      {m.payload.title && <div className="text-[14.5px] font-semibold">{m.payload.title}</div>}
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
            <div className="space-y-2">
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-border-subtle border-t-accent" />
                {stage ?? "正在选表并生成查询…"}
              </div>
              {pickedTables && pickedTables.length > 0 && (
                <div className="flex flex-wrap items-center gap-1.5 pl-5">
                  {pickedTables.map((t) => (
                    <span key={t.id} className="rounded-full bg-accent-soft px-2.5 py-0.5 text-[11.5px] text-accent">
                      📊 {t.name}
                    </span>
                  ))}
                </div>
              )}
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        <div className="border-t border-border-subtle p-3">
          <div className="flex items-center gap-2 rounded-2xl border border-border-subtle bg-background p-2 pl-4">
            <input
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && ask()}
              placeholder="直接提问，如：各区域负责人及订单总额（AI 自动选表，可跨表）"
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
      </div>
    </div>
  );
}
