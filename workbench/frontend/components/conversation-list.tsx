"use client";

import { usePathname, useRouter } from "next/navigation";
import { useAppData } from "@/lib/app-data";
import { api } from "@/lib/api";
import { askConfirm, askPrompt } from "@/components/dialog";

export function ConversationList() {
  const { conversations, refreshConversations } = useAppData();
  const pathname = usePathname();
  const router = useRouter();

  async function rename(id: number, current: string) {
    const title = await askPrompt({ title: "重命名会话", defaultValue: current, allowEmpty: true });
    if (title === null) return;
    try {
      await api(`/api/conversations/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ title: (title.trim() || current).slice(0, 128) }),
      });
      refreshConversations();
    } catch (e) {
      window.alert(e instanceof Error ? e.message : "重命名失败");
    }
  }

  async function remove(id: number, title: string) {
    if (!(await askConfirm({ title: "删除会话", message: `删除会话「${title}」？该操作不可恢复。`, danger: true, confirmText: "删除" }))) return;
    try {
      await api(`/api/conversations/${id}`, { method: "DELETE" });
    } catch (e) {
      window.alert(e instanceof Error ? e.message : "删除失败");
      return;
    }
    if (pathname === `/chat/${id}`) router.push("/");
    refreshConversations();
  }

  if (conversations.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-border-subtle px-3 py-3 text-center text-xs leading-relaxed text-muted-foreground/70">
        开始对话后
        <br />
        会话将出现在这里
      </div>
    );
  }

  return (
    <div className="space-y-0.5">
      {conversations.map((c) => {
        const active = pathname === `/chat/${c.id}`;
        return (
          <div
            key={c.id}
            className={`group flex items-center rounded-lg transition-colors ${
              active
                ? "bg-background shadow-[0_1px_2px_rgba(0,0,0,0.04)]"
                : "hover:bg-background/70"
            }`}
          >
            <button
              onClick={() => router.push(`/chat/${c.id}`)}
              className="min-w-0 flex-1 truncate px-3 py-[7px] text-left text-[13px] leading-snug"
              title={c.title}
            >
              {c.title}
            </button>
            <div className="mr-1 hidden shrink-0 gap-0.5 group-hover:flex">
              <button
                onClick={() => rename(c.id, c.title)}
                title="重命名"
                className="rounded p-1 text-muted-foreground/60 hover:text-foreground"
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
                </svg>
              </button>
              <button
                onClick={() => remove(c.id, c.title)}
                title="删除"
                className="rounded p-1 text-muted-foreground/60 hover:text-red-500"
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
                </svg>
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}
