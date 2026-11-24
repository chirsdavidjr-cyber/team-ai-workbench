"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useAppData } from "@/lib/app-data";
import { streamChat } from "@/lib/sse";
import type { MessageInfo, Citation } from "@/lib/types";
import { Markdown } from "@/components/markdown";
import { ModelPicker } from "@/components/model-picker";
import { ToolPicker } from "@/components/tool-picker";
import { AttachButton, AttachChips, composeWithAttachments, UserMessageContent, type Attachment } from "@/components/attach";
import { CitationCards } from "@/components/citation-cards";
import { IconArrowUp, IconBook, IconPlus, IconWand } from "@/components/icons";

/** 沉淀为工具的草案（AI 生成，用户可编辑确认）。 */
interface ToolDraft {
  name: string;
  icon: string;
  category: string;
  kind: string;
  description: string;
  system_prompt: string;
  openings: string[];
  allowed_actions: string[];
}

/** 能力面选项：不选任何项 = 全部动作可用 */
const ACTION_OPTIONS = [
  { id: "kb_read", label: "知识库读取" },
  { id: "kb_manage", label: "知识库管理" },
  { id: "data", label: "数据问数" },
  { id: "task", label: "批量任务" },
  { id: "tool_lib", label: "工具库管理" },
  { id: "collect", label: "后台采集" },
  { id: "todo", label: "待办" },
];

interface LocalMessage extends MessageInfo {
  streaming?: boolean;
  error?: string;
  /** 推理模型的思考过程（实时流式展示，刷新后不保留） */
  reasoning?: string;
  /** 思考增量正在到达（正文/工具轨迹一旦出现即置 false） */
  thinking?: boolean;
}

/** 消息体分段：正文 Markdown / 工具调用卡片 / 系统警示条 */
interface ToolSeg {
  kind: "tool";
  name: string;
  brief?: string;
  status: "calling" | "ok" | "error";
  detail?: string;
}
type Seg = { kind: "text"; text: string } | ToolSeg | { kind: "warn"; text: string };

const CALL_RE = /^> 🔧 调用 `([^`]+)`(?:（(.*)）)?…$/;
const RESULT_RE = /^> (✅|❌) `([^`]+)` → (.*)$/;

/** 把消息内容中的函数调用轨迹行解析为分段（实时与刷新后一致） */
function parseSegments(content: string): Seg[] {
  const segs: Seg[] = [];
  let text: string[] = [];
  const flush = () => {
    if (text.length) {
      segs.push({ kind: "text", text: text.join("\n") });
      text = [];
    }
  };
  for (const line of content.split("\n")) {
    const c = line.match(CALL_RE);
    if (c) {
      flush();
      segs.push({ kind: "tool", name: c[1], brief: c[2], status: "calling" });
      continue;
    }
    const r = line.match(RESULT_RE);
    if (r) {
      const last = segs[segs.length - 1];
      if (last && last.kind === "tool" && last.status === "calling" && last.name === r[2]) {
        last.status = r[1] === "✅" ? "ok" : "error";
        last.detail = r[3];
      } else {
        flush();
        segs.push({
          kind: "tool",
          name: r[2],
          status: r[1] === "✅" ? "ok" : "error",
          detail: r[3],
        });
      }
      continue;
    }
    if (line.startsWith("> ⚠️")) {
      flush();
      segs.push({ kind: "warn", text: line.replace(/^> ⚠️\s*/, "") });
      continue;
    }
    text.push(line);
  }
  flush();
  return segs;
}

function ToolCallCard({ seg }: { seg: ToolSeg }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="my-2 overflow-hidden rounded-lg border border-border-subtle bg-muted/30 text-[12.5px]">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-muted/60"
      >
        {seg.status === "calling" ? (
          <span className="inline-block h-3.5 w-3.5 shrink-0 animate-spin rounded-full border-2 border-border-subtle border-t-accent" />
        ) : seg.status === "ok" ? (
          <span className="shrink-0 text-[13px] leading-none text-green-600 dark:text-green-400">✓</span>
        ) : (
          <span className="shrink-0 text-[13px] leading-none text-red-500">✗</span>
        )}
        <span className="shrink-0 font-mono text-[12px]">{seg.name}</span>
        <span className="min-w-0 flex-1 truncate text-muted-foreground">{seg.brief ?? ""}</span>
        <span
          className={`shrink-0 text-[11.5px] ${
            seg.status === "error" ? "text-red-500" : "text-muted-foreground/70"
          }`}
        >
          {seg.status === "calling" ? "执行中…" : seg.status === "ok" ? "完成" : "失败"}
        </span>
        <span className="shrink-0 text-muted-foreground/50">{open ? "▴" : "▾"}</span>
      </button>
      {open && (
        <div className="border-t border-border-subtle px-3 py-2 text-[12px] leading-relaxed text-muted-foreground">
          {seg.detail || seg.brief || "（无详情）"}
        </div>
      )}
    </div>
  );
}

/** 首字前的等待指示：带计时，避免长时间静默像卡死 */
function ThinkingIndicator() {
  const [sec, setSec] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setSec((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, []);
  return (
    <div className="flex items-center gap-2 text-[13px] text-muted-foreground">
      <span className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-border-subtle border-t-accent" />
      正在思考…
      {sec > 2 && <span className="tabular-nums text-muted-foreground/70">{sec}s</span>}
    </div>
  );
}

/** 推理模型思考过程：流式时实时展开并自动滚动，正文开始输出后自动收起，可手动展开 */
function ReasoningBlock({ text, active }: { text: string; active: boolean }) {
  const [open, setOpen] = useState(true);
  const touched = useRef(false); // 用户手动切换后不再自动收/展开
  const bodyRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!touched.current) setOpen(active);
  }, [active]);
  useEffect(() => {
    if (open && active && bodyRef.current) {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
    }
  }, [text, open, active]);
  return (
    <div className="mb-2 overflow-hidden rounded-lg border border-border-subtle bg-muted/30 text-[12.5px]">
      <button
        type="button"
        onClick={() => {
          touched.current = true;
          setOpen(!open);
        }}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-muted-foreground transition-colors hover:bg-muted/60"
      >
        {active ? (
          <span className="inline-block h-3 w-3 shrink-0 animate-spin rounded-full border-2 border-border-subtle border-t-accent" />
        ) : (
          <span className="shrink-0 text-[12px] leading-none">💭</span>
        )}
        <span className="shrink-0 font-medium">思考过程</span>
        <span className="shrink-0 text-[11.5px] text-muted-foreground/70">
          {active ? "思考中…" : "已完成"}
        </span>
        <span className="min-w-0 flex-1 truncate text-muted-foreground/50">{text.slice(-80)}</span>
        <span className="shrink-0 text-muted-foreground/50">{open ? "▴" : "▾"}</span>
      </button>
      {open && (
        <div
          ref={bodyRef}
          className="max-h-64 overflow-y-auto whitespace-pre-wrap border-t border-border-subtle px-3 py-2 text-[12px] leading-relaxed text-muted-foreground"
        >
          {text}
        </div>
      )}
    </div>
  );
}

function MessageBody({
  content,
  onPreviewHtml,
}: {
  content: string;
  onPreviewHtml: (h: string) => void;
}) {
  const segs = useMemo(() => parseSegments(content), [content]);
  const hasSpecial = segs.some((s) => s.kind !== "text");
  if (!hasSpecial) return <Markdown content={content} onPreviewHtml={onPreviewHtml} />;
  return (
    <div>
      {segs.map((s, i) =>
        s.kind === "text" ? (
          s.text.trim() ? (
            <Markdown key={i} content={s.text} onPreviewHtml={onPreviewHtml} />
          ) : null
        ) : s.kind === "tool" ? (
          <ToolCallCard key={i} seg={s} />
        ) : (
          <div
            key={i}
            className="my-2 rounded-lg border border-amber-300/60 bg-amber-50 px-3 py-1.5 text-[12px] text-amber-700 dark:border-amber-700/50 dark:bg-amber-950/30 dark:text-amber-400"
          >
            {s.text}
          </div>
        )
      )}
    </div>
  );
}

export default function ChatPage() {
  const { id } = useParams<{ id: string }>();
  const convId = Number(id);
  const router = useRouter();
  const { selection, refreshConversations, refreshTools, activeTool } = useAppData();

  const [title, setTitle] = useState("对话");
  const [messages, setMessages] = useState<LocalMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [useKb, setUseKb] = useState(false);
  const [atts, setAtts] = useState<Attachment[]>([]);
  const [previewHtml, setPreviewHtml] = useState<string | null>(null);
  const [draftOpen, setDraftOpen] = useState(false);
  const [draftLoading, setDraftLoading] = useState(false);
  const [draft, setDraft] = useState<ToolDraft | null>(null);
  const [toast, setToast] = useState("");
  const [continuable, setContinuable] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const pendingSent = useRef(false);
  const loadedRef = useRef<number | null>(null);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(""), 1500);
    return () => clearTimeout(t);
  }, [toast]);

  // 滚动到底部
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  const send = useCallback(
    async (
      text: string,
      override?: { providerId: number; modelId: string; modelLabel: string }
    ) => {
      const content = composeWithAttachments(text.trim(), atts);
      const target = override ?? selection;
      if (!content || busy || !target) return;
      setBusy(true);
      setInput("");
      setAtts([]);
      setContinuable(false);
      const assistantId = -Date.now();
      setMessages((prev) => [
        ...prev,
        {
          id: Date.now(),
          role: "user",
          content,
          model: null,
          citations: null,
          created_at: null,
        },
        {
          id: assistantId,
          role: "assistant",
          content: "",
          model: `${target.modelLabel}`,
          citations: null,
          created_at: null,
          streaming: true,
        },
      ]);

      await streamChat(
        {
          conversation_id: convId,
          provider_id: target.providerId,
          model_id: target.modelId,
          content,
          use_kb: useKb,
          tool_id: activeTool?.id ?? null,
        },
        (ev) => {
          if (ev.type === "reasoning" && ev.text) {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? { ...m, reasoning: (m.reasoning ?? "") + ev.text, thinking: true }
                  : m
              )
            );
          } else if (ev.type === "delta" && ev.text) {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? { ...m, content: m.content + ev.text, thinking: false }
                  : m
              )
            );
          } else if (ev.type === "done") {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? { ...m, streaming: false, thinking: false, citations: ev.citations ?? null }
                  : m
              )
            );
            if (ev.conversation_title) setTitle(ev.conversation_title);
            setContinuable(ev.continuable === true);
          } else if (ev.type === "error") {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? { ...m, streaming: false, thinking: false, error: ev.detail ?? "调用失败" }
                  : m
              )
            );
          }
        }
      );
      // 无论成功或失败，流结束后刷新会话列表（标题/时间/排序更新）
      refreshConversations();
      refreshTools(); // agent 可能改了工具库，同步侧边栏与选择器
      setBusy(false);
    },
    [atts, busy, convId, refreshConversations, refreshTools, router, selection, useKb, activeTool]
  );

  // 加载会话 + 自动发送来自首页的首条消息（携带模型参数，不依赖异步加载的模型状态）
  // loadedRef 防止 StrictMode 下 effect 双执行导致两次异步加载互相覆盖
  useEffect(() => {
    if (loadedRef.current === convId) return;
    loadedRef.current = convId;
    setLoading(true);
    api<{ id: number; title: string; messages: MessageInfo[] }>(
      `/api/conversations/${convId}`
    )
      .then((data) => {
        setTitle(data.title);
        setMessages(data.messages);
        const raw = sessionStorage.getItem(`wb-pending-${convId}`);
        if (raw && !pendingSent.current) {
          pendingSent.current = true;
          sessionStorage.removeItem(`wb-pending-${convId}`);
          try {
            const pending = JSON.parse(raw) as {
              content: string;
              providerId: number;
              modelId: string;
              modelLabel: string;
            };
            setTitle(pending.content.slice(0, 24));
            send(pending.content, {
              providerId: pending.providerId,
              modelId: pending.modelId,
              modelLabel: pending.modelLabel,
            });
          } catch {
            /* 忽略损坏的 pending 数据 */
          }
        }
      })
      .catch(() => router.push("/"))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [convId]);

  // 重试最后一条失败的回复
  async function retryLast() {
    const lastUser = [...messages].reverse().find((m) => m.role === "user");
    if (!lastUser) return;
    // 移除末尾的错误 assistant 占位
    setMessages((prev) => {
      const copy = [...prev];
      while (copy.length && copy[copy.length - 1].role === "assistant") copy.pop();
      return copy;
    });
    send(lastUser.content);
  }

  const hasError = messages.some((m) => m.error);

  return (
    <div className="flex h-full overflow-x-hidden">
      <div className="flex min-w-0 flex-1 flex-col">
      {/* 顶部栏 */}
      <div className="flex h-14 shrink-0 items-center gap-3 border-b border-border-subtle px-5">
        <div className="min-w-0 flex-1 truncate text-[14px] font-medium">
          {title}
        </div>
        <button
          onClick={async () => {
            setDraftOpen(true);
            setDraftLoading(true);
            try {
              setDraft({
                ...(await api<Omit<ToolDraft, "allowed_actions">>(
                  "/api/tools/draft-from-conversation",
                  { method: "POST", body: JSON.stringify({ conversation_id: convId }) }
                )),
                allowed_actions: [],
              });
            } catch (e) {
              setToast(e instanceof Error ? e.message : "草案生成失败");
              setDraftOpen(false);
            } finally {
              setDraftLoading(false);
            }
          }}
          disabled={messages.length === 0 || draftLoading}
          title="AI 根据本段对话提炼可复用的 Agent / Skill，确认后存入工具库"
          className="flex items-center gap-1.5 rounded-lg border border-border-subtle px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:text-foreground disabled:opacity-50"
        >
          <IconWand width={13} height={13} />
          沉淀为工具
        </button>
        <button
          onClick={() => setUseKb(!useKb)}
          title="开启后回答将引用知识库（个人库+团队库）"
          className={`flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs transition-colors ${
            useKb
              ? "border-accent bg-accent-soft font-medium text-accent"
              : "border-border-subtle text-muted-foreground hover:text-foreground"
          }`}
        >
          <IconBook width={13} height={13} />
          知识库
        </button>
        <button
          onClick={async () => {
            const res = await api<{ id: number }>("/api/conversations", {
              method: "POST",
              body: JSON.stringify({}),
            });
            refreshConversations();
            router.push(`/chat/${res.id}`);
          }}
          title="新对话"
          className="rounded-lg border border-border-subtle p-1.5 text-muted-foreground transition-colors hover:text-foreground"
        >
          <IconPlus />
        </button>
      </div>

      {/* 消息区 */}
      <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden">
        <div className="mx-auto max-w-3xl px-6 py-8">
          {loading ? (
            <div className="py-20 text-center text-sm text-muted-foreground">
              加载中…
            </div>
          ) : messages.length === 0 ? (
            <div className="py-24 text-center">
              <div className="text-[15px] text-muted-foreground">
                在下方输入消息，开始与 AI 对话
              </div>
            </div>
          ) : (
            <div className="space-y-6">
              {messages.map((m) =>
                m.role === "user" ? (
                  <div key={m.id} className="flex justify-end">
                    <div className="max-w-[85%] rounded-2xl rounded-br-md bg-muted px-4 py-2.5 text-[14.5px] leading-relaxed">
                      <UserMessageContent content={m.content} />
                    </div>
                  </div>
                ) : (
                  <div key={m.id} className="flex gap-3">
                    <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-accent text-[11px] font-bold text-accent-foreground">
                      AI
                    </div>
                    <div className="min-w-0 flex-1">
                      {m.reasoning ? (
                        <ReasoningBlock text={m.reasoning} active={!!m.thinking} />
                      ) : null}
                      {m.streaming && !m.content && !m.thinking && <ThinkingIndicator />}
                      {m.content ? (
                        <MessageBody content={m.content} onPreviewHtml={setPreviewHtml} />
                      ) : null}
                      {m.streaming && m.content && (
                        <span className="ml-0.5 inline-block h-4 w-[2px] animate-pulse bg-accent align-middle" />
                      )}
                      {m.error && (
                        <div className="mt-2">
                          <div className="rounded-lg border border-red-300/60 bg-red-50 px-3.5 py-2.5 text-[13px] text-red-600 dark:border-red-900/60 dark:bg-red-950/40 dark:text-red-400">
                            {m.error}
                          </div>
                          {!busy && (
                            <button
                              onClick={retryLast}
                              className="mt-2 rounded-lg border border-border-subtle px-3 py-1.5 text-[12.5px] text-muted-foreground transition-colors hover:text-foreground"
                            >
                              重试
                            </button>
                          )}
                        </div>
                      )}
                      {!m.streaming && !m.error && m.model && (
                        <div className="mt-2 text-[11px] text-muted-foreground/60">
                          {m.model}
                        </div>
                      )}
                      {!m.streaming && m.citations && m.citations.length > 0 && (
                        <CitationCards citations={m.citations} />
                      )}
                    </div>
                  </div>
                )
              )}
            </div>
          )}
          <div ref={bottomRef} />
        </div>
      </div>

      {/* 输入区 */}
      <div className="shrink-0 border-t border-border-subtle bg-background">
        <div className="mx-auto max-w-3xl px-6 py-4">
          <div className="rounded-2xl border border-border-subtle bg-surface p-3 shadow-[0_1px_3px_rgba(0,0,0,0.04)]">
            {continuable && !busy && (
              <div className="mb-2 flex items-center gap-2 rounded-lg border border-border-subtle bg-muted px-3 py-1.5 text-[12px] text-muted-foreground">
                <span className="flex-1">本轮操作较多，工具调用预算已用完 —— 回复「继续」可接着执行剩余操作</span>
                <button
                  onClick={() => setContinuable(false)}
                  className="shrink-0 text-muted-foreground/60 hover:text-foreground"
                  title="关闭提示"
                >
                  ✕
                </button>
              </div>
            )}
            <AttachChips files={atts} onRemove={(n) => setAtts((prev) => prev.filter((a) => a.name !== n))} />
            <textarea
              ref={taRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  send(input);
                }
              }}
              placeholder={hasError ? "可点击「重试」重新生成，或继续提问…" : "输入消息…（右下角可拖拽调整高度）"}
              className="min-h-[48px] max-h-[45vh] w-full resize-y overflow-y-auto overflow-x-hidden bg-transparent py-1.5 text-[14.5px] leading-relaxed outline-none placeholder:text-muted-foreground/60"
            />
            <div className="mt-1.5 flex items-center gap-2">
              <AttachButton
                onLoaded={(files) => {
                  setAtts((prev) => [...prev, ...files].slice(0, 5));
                  taRef.current?.focus();
                }}
              />
              <ToolPicker />
              <ModelPicker />
              <div className="flex-1" />
              <span className="text-[11.5px] text-muted-foreground/70">
                Enter 发送 · Shift+Enter 换行
              </span>
              <button
                onClick={() => send(input)}
                disabled={(!input.trim() && atts.length === 0) || busy}
                className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent text-accent-foreground transition-all hover:opacity-90 disabled:bg-muted disabled:text-muted-foreground/40"
                title="发送"
              >
                <IconArrowUp />
              </button>
            </div>
          </div>
        </div>
      </div>
      </div>

      {/* 右侧预览面板：HTML 网页 / PPT 实时渲染 */}
      {previewHtml !== null && (
        <div className="flex w-[52%] min-w-[420px] shrink-0 flex-col border-l border-border-subtle bg-muted/40">
          <div className="flex h-14 shrink-0 items-center gap-2 border-b border-border-subtle bg-background px-4">
            <span className="text-[13px] font-medium">网页预览</span>
            <span className="rounded bg-muted px-1.5 py-0.5 text-[10.5px] text-muted-foreground">
              沙箱渲染
            </span>
            <div className="flex-1" />
            <button
              onClick={() => {
                const w = window.open("", "_blank");
                if (w) {
                  w.document.write(previewHtml);
                  w.document.close();
                }
              }}
              className="rounded-lg border border-border-subtle px-2.5 py-1 text-[11.5px] text-muted-foreground hover:text-foreground"
              title="在新标签页打开"
            >
              新窗口
            </button>
            <button
              onClick={() => {
                const blob = new Blob([previewHtml], { type: "text/html;charset=utf-8" });
                const a = document.createElement("a");
                a.href = URL.createObjectURL(blob);
                a.download = "page.html";
                a.click();
                URL.revokeObjectURL(a.href);
              }}
              className="rounded-lg border border-border-subtle px-2.5 py-1 text-[11.5px] text-muted-foreground hover:text-foreground"
              title="下载 HTML 文件"
            >
              下载
            </button>
            <button
              onClick={() => setPreviewHtml(null)}
              className="rounded-lg border border-border-subtle px-2.5 py-1 text-[11.5px] text-muted-foreground hover:text-foreground"
            >
              关闭
            </button>
          </div>
          <iframe
            title="HTML 预览"
            srcDoc={previewHtml}
            sandbox="allow-scripts allow-popups"
            className="min-h-0 flex-1 bg-white"
          />
        </div>
      )}

      {/* 沉淀为工具：AI 草案 → 用户编辑确认 → 存入工具库 */}
      {draftOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-6">
          <div className="flex max-h-[85vh] w-[560px] max-w-full flex-col rounded-2xl border border-border-subtle bg-surface shadow-2xl">
            <div className="flex h-13 shrink-0 items-center gap-2 border-b border-border-subtle px-5 py-3.5">
              <IconWand width={15} height={15} />
              <span className="text-[14px] font-medium">把这段对话沉淀为工具</span>
              <div className="flex-1" />
              <button
                onClick={() => setDraftOpen(false)}
                className="rounded-lg p-1 text-muted-foreground hover:text-foreground"
              >
                ✕
              </button>
            </div>
            {draftLoading || !draft ? (
              <div className="flex flex-1 items-center justify-center gap-2 py-16 text-[13px] text-muted-foreground">
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-border-subtle border-t-accent" />
                AI 正在分析对话并提炼工具草案…
              </div>
            ) : (
              <>
                <div className="flex-1 space-y-3 overflow-y-auto px-5 py-4">
                  <div className="flex gap-3">
                    <label className="flex w-16 shrink-0 items-center text-[12.5px] text-muted-foreground">
                      图标
                    </label>
                    <input
                      value={draft.icon}
                      onChange={(e) => setDraft({ ...draft, icon: e.target.value })}
                      className="w-16 rounded-lg border border-border-subtle bg-background px-2.5 py-1.5 text-center text-[14px] outline-none focus:border-accent"
                    />
                    <label className="flex w-16 shrink-0 items-center text-[12.5px] text-muted-foreground">
                      名称
                    </label>
                    <input
                      value={draft.name}
                      onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                      className="min-w-0 flex-1 rounded-lg border border-border-subtle bg-background px-2.5 py-1.5 text-[13.5px] outline-none focus:border-accent"
                    />
                  </div>
                  <div className="flex gap-3">
                    <label className="flex w-16 shrink-0 items-center text-[12.5px] text-muted-foreground">
                      类型
                    </label>
                    <select
                      value={draft.kind}
                      onChange={(e) => setDraft({ ...draft, kind: e.target.value })}
                      className="rounded-lg border border-border-subtle bg-background px-2.5 py-1.5 text-[13px] outline-none focus:border-accent"
                    >
                      <option value="agent">agent（持续性角色）</option>
                      <option value="skill">skill（固定流程）</option>
                      <option value="template">template（固定格式）</option>
                    </select>
                    <label className="flex w-16 shrink-0 items-center text-[12.5px] text-muted-foreground">
                      分类
                    </label>
                    <input
                      value={draft.category}
                      onChange={(e) => setDraft({ ...draft, category: e.target.value })}
                      className="min-w-0 flex-1 rounded-lg border border-border-subtle bg-background px-2.5 py-1.5 text-[13px] outline-none focus:border-accent"
                    />
                  </div>
                  <div className="flex gap-3">
                    <label className="flex w-16 shrink-0 pt-2 text-[12.5px] text-muted-foreground">
                      说明
                    </label>
                    <input
                      value={draft.description}
                      onChange={(e) => setDraft({ ...draft, description: e.target.value })}
                      placeholder="一句话说明用途"
                      className="min-w-0 flex-1 rounded-lg border border-border-subtle bg-background px-2.5 py-1.5 text-[13px] outline-none focus:border-accent"
                    />
                  </div>
                  <div className="flex gap-3">
                    <label className="flex w-16 shrink-0 pt-2 text-[12.5px] text-muted-foreground">
                      提示词
                    </label>
                    <textarea
                      value={draft.system_prompt}
                      onChange={(e) => setDraft({ ...draft, system_prompt: e.target.value })}
                      className="min-h-[180px] min-w-0 flex-1 resize-y rounded-lg border border-border-subtle bg-background px-2.5 py-2 font-mono text-[12px] leading-relaxed outline-none focus:border-accent"
                    />
                  </div>
                  <div className="flex gap-3">
                    <label className="flex w-16 shrink-0 pt-2 text-[12.5px] text-muted-foreground">
                      开场
                    </label>
                    <input
                      value={draft.openings.join("；")}
                      onChange={(e) =>
                        setDraft({
                          ...draft,
                          openings: e.target.value.split(/[；;]/).map((s) => s.trim()).filter(Boolean),
                        })
                      }
                      placeholder="用分号分隔多个开场问题"
                      className="min-w-0 flex-1 rounded-lg border border-border-subtle bg-background px-2.5 py-1.5 text-[13px] outline-none focus:border-accent"
                    />
                  </div>
                  <div className="flex gap-3">
                    <label className="flex w-16 shrink-0 pt-2 text-[12.5px] text-muted-foreground">
                      能力
                    </label>
                    <div className="flex min-w-0 flex-1 flex-wrap items-center gap-1.5">
                      <button
                        onClick={() => setDraft({ ...draft, allowed_actions: [] })}
                        className={`rounded-full border px-2.5 py-1 text-[12px] transition-colors ${
                          draft.allowed_actions.length === 0
                            ? "border-accent bg-accent-soft font-medium text-accent"
                            : "border-border-subtle text-muted-foreground hover:text-foreground"
                        }`}
                      >
                        全部能力
                      </button>
                      {ACTION_OPTIONS.map((o) => {
                        const on = draft.allowed_actions.includes(o.id);
                        return (
                          <button
                            key={o.id}
                            onClick={() =>
                              setDraft({
                                ...draft,
                                allowed_actions: on
                                  ? draft.allowed_actions.filter((a) => a !== o.id)
                                  : [...draft.allowed_actions, o.id],
                              })
                            }
                            className={`rounded-full border px-2.5 py-1 text-[12px] transition-colors ${
                              on
                                ? "border-accent bg-accent-soft font-medium text-accent"
                                : "border-border-subtle text-muted-foreground hover:text-foreground"
                            }`}
                          >
                            {o.label}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-2 border-t border-border-subtle px-5 py-3">
                  <span className="text-[11.5px] text-muted-foreground/70">
                    将保存到「我的工具」，可在工具库中管理
                  </span>
                  <div className="flex-1" />
                  <button
                    onClick={() => setDraftOpen(false)}
                    className="rounded-lg border border-border-subtle px-3.5 py-1.5 text-[13px] text-muted-foreground hover:text-foreground"
                  >
                    取消
                  </button>
                  <button
                    onClick={async () => {
                      if (!draft.name.trim() || !draft.system_prompt.trim()) {
                        setToast("名称和提示词不能为空");
                        return;
                      }
                      try {
                        await api("/api/tools", {
                          method: "POST",
                          body: JSON.stringify({
                            name: draft.name,
                            icon: draft.icon || "🧰",
                            category: draft.category || "通用",
                            kind: draft.kind,
                            description: draft.description,
                            system_prompt: draft.system_prompt,
                            openings: draft.openings,
                            allowed_actions: draft.allowed_actions,
                            scope: "personal",
                          }),
                        });
                        setDraftOpen(false);
                        setToast(`已保存工具「${draft.name}」`);
                        await refreshTools();
                      } catch (e) {
                        setToast(e instanceof Error ? e.message : "保存失败");
                      }
                    }}
                    className="rounded-lg bg-accent px-3.5 py-1.5 text-[13px] font-medium text-accent-foreground hover:opacity-90"
                  >
                    保存到工具库
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {/* toast */}
      {toast && (
        <div className="pointer-events-none fixed bottom-6 left-1/2 z-[60] -translate-x-1/2 rounded-lg bg-foreground px-3.5 py-2 text-[12.5px] text-background shadow-lg">
          {toast}
        </div>
      )}
    </div>
  );
}
