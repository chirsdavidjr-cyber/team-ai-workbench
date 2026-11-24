"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import { useAppData } from "@/lib/app-data";
import { ModelPicker } from "@/components/model-picker";
import { ToolPicker } from "@/components/tool-picker";
import { AttachButton, AttachChips, composeWithAttachments, type Attachment } from "@/components/attach";
import { IconArrowUp } from "@/components/icons";

const GUIDE_STEPS: [string, string, string][] = [
  ["💬", "对话", "在下方输入框提问；上传附件（文档/表格/图片）AI 直接阅读；说「把这份资料存入知识库」即可入库整理"],
  ["📚", "知识库", "四区工作流：收件箱收新资料 → ✨整理收件箱 AI 自动归类 → 编译知识页；对话里也可以直接指挥 AI 管理文件夹"],
  ["📊", "数据库", "上传 Excel/CSV 或粘贴 CSV → AI 速读 → 自然语言问数（SQL 与图表全透明）；数据集可分组、可申请入团队库"],
  ["🧰", "工具与任务", "把好用的对话一键「沉淀为工具」（agent/skill）；任务中心可批量处理成百上千条文本"],
];

export default function HomePage() {
  const { user } = useAuth();
  const router = useRouter();
  const { providers, selection, providersLoading, refreshConversations, tools, activeTool, setActiveTool } =
    useAppData();
  const [content, setContent] = useState("");
  const [atts, setAtts] = useState<Attachment[]>([]);
  const [busy, setBusy] = useState(false);
  const [guideOpen, setGuideOpen] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // 新用户首次进入引导（一次性）
  useEffect(() => {
    if (!localStorage.getItem("home-guide-seen")) setGuideOpen(true);
  }, []);

  const hour = new Date().getHours();
  const greet =
    hour < 6 ? "夜深了" : hour < 12 ? "早上好" : hour < 18 ? "下午好" : "晚上好";

  const noModel = !providersLoading && providers.length === 0;

  async function send() {
    const text = content.trim();
    if (!text && atts.length === 0) return;
    if (busy) return;
    if (!selection) {
      router.push("/settings");
      return;
    }
    setBusy(true);
    try {
      const conv = await api<{ id: number }>("/api/conversations", {
        method: "POST",
        body: JSON.stringify({}),
      });
      sessionStorage.setItem(
        `wb-pending-${conv.id}`,
        JSON.stringify({
          content: composeWithAttachments(text, atts),
          providerId: selection.providerId,
          modelId: selection.modelId,
          modelLabel: selection.modelLabel,
        })
      );
      setAtts([]);
      refreshConversations();
      router.push(`/chat/${conv.id}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto flex min-h-full max-w-3xl flex-col justify-center px-6 py-14">
      {guideOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-6">
          <div className="w-[460px] max-w-full rounded-2xl border border-border-subtle bg-surface p-6 shadow-2xl">
            <div className="text-[16px] font-semibold">欢迎使用湖北 AI 工作台</div>
            <div className="mt-1 text-[13px] text-muted-foreground">四步了解你的工作台</div>
            <div className="mt-4 space-y-3">
              {GUIDE_STEPS.map(([icon, title, desc]) => (
                <div key={title} className="flex gap-3 rounded-xl border border-border-subtle bg-muted/40 px-3.5 py-3">
                  <span className="text-[20px] leading-none">{icon}</span>
                  <div className="min-w-0">
                    <div className="text-[13.5px] font-medium">{title}</div>
                    <div className="mt-0.5 text-[12px] leading-relaxed text-muted-foreground">{desc}</div>
                  </div>
                </div>
              ))}
            </div>
            <button
              onClick={() => {
                localStorage.setItem("home-guide-seen", "1");
                setGuideOpen(false);
              }}
              className="mt-5 w-full rounded-lg bg-accent py-2.5 text-[13.5px] font-medium text-accent-foreground hover:opacity-90"
            >
              开始使用
            </button>
          </div>
        </div>
      )}

      <div className="text-center">
        <h1 className="text-[28px] font-semibold tracking-tight">
          {greet}，{user?.name}
        </h1>
        <p className="mt-2 text-[15px] text-muted-foreground">
          今天想从哪里开始？
        </p>
      </div>

      <div className="mt-8">
        <div className="rounded-2xl border border-border-subtle bg-surface p-4 shadow-[0_1px_3px_rgba(0,0,0,0.04),0_8px_24px_rgba(0,0,0,0.04)] transition-shadow focus-within:shadow-[0_1px_3px_rgba(0,0,0,0.04),0_12px_32px_rgba(0,0,0,0.08)]">
          {activeTool && (
            <div className="mb-2.5 flex items-center gap-2 rounded-lg bg-accent-soft px-3 py-1.5 text-[12.5px]">
              <span>{activeTool.icon}</span>
              <span className="font-medium text-accent">{activeTool.name}</span>
              <span className="min-w-0 flex-1 truncate text-muted-foreground">
                {activeTool.description}
              </span>
              <button
                onClick={() => setActiveTool(null)}
                className="shrink-0 text-muted-foreground hover:text-foreground"
                title="退出该工具"
              >
                ✕
              </button>
            </div>
          )}
          <AttachChips files={atts} onRemove={(n) => setAtts((prev) => prev.filter((a) => a.name !== n))} />
          <textarea
            ref={inputRef}
            rows={2}
            value={content}
            onChange={(e) => setContent(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                send();
              }
            }}
            placeholder={
              noModel
                ? "请先在「模型与设置」中配置 API Key…"
                : activeTool
                  ? `正在使用「${activeTool.name}」，输入内容开始…`
                  : "向 AI 提问，或选择左侧工具…"
            }
            className="min-h-[64px] max-h-[45vh] w-full resize-y overflow-y-auto overflow-x-hidden bg-transparent py-1 text-[15px] leading-relaxed outline-none placeholder:text-muted-foreground/60"
          />
          <div className="mt-2 flex items-center gap-2">
            <AttachButton
              onLoaded={(files, skipped) => {
                setAtts((prev) => [...prev, ...files].slice(0, 5));
                inputRef.current?.focus();
              }}
            />
            <ToolPicker />
            <ModelPicker />
            <div className="flex-1" />
            <button
              onClick={send}
              disabled={(!content.trim() && atts.length === 0) || busy}
              className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent text-accent-foreground transition-all hover:opacity-90 disabled:bg-muted disabled:text-muted-foreground/40"
              title="发送"
            >
              <IconArrowUp />
            </button>
          </div>
        </div>

        {activeTool && activeTool.openings.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-2">
            {activeTool.openings.map((o) => (
              <button
                key={o}
                onClick={() => {
                  setContent(o);
                  inputRef.current?.focus();
                }}
                className="rounded-full border border-border-subtle bg-surface px-3 py-1 text-[12.5px] text-muted-foreground transition-colors hover:border-accent/50 hover:text-foreground"
              >
                {o}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="mt-9">
        <div className="mb-3 flex items-baseline justify-between">
          <span className="text-[13px] font-medium text-muted-foreground">工具</span>
          <a href="/tools" className="text-[12.5px] text-muted-foreground hover:text-accent">
            管理工具 →
          </a>
        </div>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          {tools.slice(0, 6).map((t) => (
            <button
              key={t.id}
              onClick={() => {
                setActiveTool(t);
                inputRef.current?.focus();
              }}
              className={`rounded-xl border p-4 text-left transition-colors ${
                activeTool?.id === t.id
                  ? "border-accent bg-accent-soft"
                  : "border-border-subtle bg-surface hover:border-accent/50"
              }`}
            >
              <div className="flex items-center gap-2">
                <span className="text-[17px]">{t.icon}</span>
                <span className="text-[14px] font-medium">{t.name}</span>
              </div>
              <div className="mt-1.5 line-clamp-2 text-xs leading-relaxed text-muted-foreground">
                {t.description}
              </div>
              <div className="mt-2 flex items-center gap-2 text-[10.5px] text-muted-foreground/60">
                <span className="rounded bg-muted px-1.5 py-px uppercase">{t.kind}</span>
                <span>已用 {t.use_count} 次</span>
              </div>
            </button>
          ))}
          {tools.length === 0 && (
            <div className="col-span-full rounded-xl border border-dashed border-border-subtle px-4 py-8 text-center text-[13px] text-muted-foreground/70">
              还没有工具，去「工具库」创建你的第一个 Agent
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
