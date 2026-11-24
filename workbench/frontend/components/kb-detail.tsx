"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { KbDocDetail } from "@/lib/types";
import { askPrompt } from "@/components/dialog";
import { Markdown } from "./markdown";

export function KbDetail({
  docId,
  onBack,
  onOpenDoc,
  onChanged,
}: {
  docId: number;
  onBack: () => void;
  onOpenDoc: (docId: number) => void;
  onChanged: () => void;
}) {
  const [doc, setDoc] = useState<KbDocDetail | null>(null);
  const [mode, setMode] = useState<"preview" | "edit">("preview");
  const [draft, setDraft] = useState("");
  const [draftTitle, setDraftTitle] = useState("");
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState("");

  const load = useCallback(async () => {
    const d = await api<KbDocDetail>(`/api/kb/docs/${docId}`);
    setDoc(d);
    setDraft(d.content);
    setDraftTitle(d.title);
  }, [docId]);

  useEffect(() => {
    load().catch(() => onBack());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docId]);

  if (!doc) {
    return <div className="py-20 text-center text-sm text-muted-foreground">加载中…</div>;
  }

  async function save() {
    setSaving(true);
    setToast("");
    try {
      await api(`/api/kb/docs/${docId}`, {
        method: "PUT",
        body: JSON.stringify({ title: draftTitle, content: draft }),
      });
      await load();
      setMode("preview");
      setToast("已保存，索引与双链已重建");
      onChanged();
    } catch (e) {
      setToast(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  const outgoingMap = new Map(
    (doc.links?.outgoing ?? []).map((l) => [l.title, l.doc_id])
  );

  async function action(path: string, body?: object, okMsg?: string) {
    setToast("");
    try {
      await api(`/api/kb/docs/${docId}/${path}`, {
        method: "POST",
        body: JSON.stringify(body ?? {}),
      });
      await load();
      onChanged();
      setToast(okMsg ?? "操作成功");
    } catch (e) {
      setToast(e instanceof Error ? e.message : "操作失败");
    }
  }

  async function moveDoc() {
    if (!doc) return;
    const parts = doc.path.split("/");
    const current = parts.length > 1 ? parts.slice(0, -1).join("/") : "";
    const folder = await askPrompt({ title: "移动文档", message: "移动到哪个文件夹？（留空 = 根目录）", defaultValue: current, allowEmpty: true });
    if (folder === null) return;
    try {
      await api(`/api/kb/docs/${docId}/move`, {
        method: "PUT",
        body: JSON.stringify({ folder: folder.trim() }),
      });
      await load();
      onChanged();
      setToast("已移动");
    } catch (e) {
      setToast(e instanceof Error ? e.message : "移动失败");
    }
  }

  const teamBadge =
    doc.scope === "team" || doc.team_status === "approved"
      ? { text: "已入团队库", cls: "bg-accent-soft text-accent" }
      : doc.team_status === "pending"
        ? { text: "团队库·审核中", cls: "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-400" }
        : doc.team_status === "rejected"
          ? { text: "团队库·已驳回", cls: "bg-red-100 text-red-600 dark:bg-red-900/40 dark:text-red-400" }
          : null;

  return (
    <div className="mx-auto max-w-3xl px-6 py-8">
      {/* 顶栏 */}
      <div className="flex items-center gap-3">
        <button onClick={onBack} className="text-muted-foreground hover:text-foreground">←</button>
        <div className="min-w-0 flex-1 truncate text-[16px] font-semibold">{doc.title}</div>
        {mode === "preview" ? (
          <button
            onClick={() => setMode("edit")}
            className="rounded-lg border border-border-subtle px-3 py-1.5 text-[12.5px] text-muted-foreground hover:text-foreground"
          >
            编辑
          </button>
        ) : (
          <>
            <button
              onClick={() => { setMode("preview"); setDraft(doc.content); setDraftTitle(doc.title); }}
              className="rounded-lg border border-border-subtle px-3 py-1.5 text-[12.5px] text-muted-foreground hover:text-foreground"
            >
              取消
            </button>
            <button
              onClick={save}
              disabled={saving}
              className="rounded-lg bg-accent px-3.5 py-1.5 text-[12.5px] font-medium text-accent-foreground hover:opacity-90 disabled:opacity-60"
            >
              {saving ? "保存中…" : "保存并重建索引"}
            </button>
          </>
        )}
      </div>

      {/* 元信息 */}
      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <span className="font-mono">{doc.path}</span>
        <span>{fmtSize(doc.size)}</span>
        <span>更新于 {fmtTime(doc.updated_at ?? doc.created_at)}</span>
        <span>{doc.chunk_count} 个知识块</span>
        <span className="flex items-center gap-1.5">
          <span className={`rounded-full px-2 py-0.5 ${doc.status === "ready" ? "bg-accent-soft text-accent" : "bg-muted"}`}>
            个人库{doc.status === "ready" ? "✓ 已解析" : "·草稿"}
          </span>
          {teamBadge && <span className={`rounded-full px-2 py-0.5 ${teamBadge.cls}`}>{teamBadge.text}</span>}
        </span>
        {doc.tags.map((t) => (
          <span key={t} className="rounded-full bg-muted px-2 py-0.5 text-muted-foreground">#{t}</span>
        ))}
      </div>

      {/* 操作区 */}
      <div className="mt-3 flex flex-wrap gap-2">
        {!teamBadge && (
          <button
            onClick={() => action("request-team", {}, "AI 预审完成，已提交管理员审核")}
            className="rounded-lg bg-accent px-3 py-1.5 text-[12.5px] font-medium text-accent-foreground hover:opacity-90"
            title="AI 预审（查重/质量/敏感）后提交管理员终审"
          >
            申请入团队库
          </button>
        )}
        <button
          onClick={() => action("personal", { enabled: doc.status !== "ready" }, doc.status === "ready" ? "已移出个人库（转草稿）" : "已入个人库")}
          className="rounded-lg border border-border-subtle px-3 py-1.5 text-[12.5px] text-muted-foreground hover:text-foreground"
        >
          {doc.status === "ready" ? "移出个人库" : "入个人库"}
        </button>
        <button
          onClick={() => action("reindex", {}, "已重新解析")}
          className="rounded-lg border border-border-subtle px-3 py-1.5 text-[12.5px] text-muted-foreground hover:text-foreground"
        >
          重新解析
        </button>
        <button
          onClick={moveDoc}
          className="rounded-lg border border-border-subtle px-3 py-1.5 text-[12.5px] text-muted-foreground hover:text-foreground"
        >
          移动到…
        </button>
        {doc.has_source_file && (
          <button
            onClick={async () => {
              try {
                const { API_BASE, getToken } = await import("@/lib/api");
                const res = await fetch(`${API_BASE}/api/kb/docs/${docId}/file`, {
                  headers: { Authorization: `Bearer ${getToken() ?? ""}` },
                });
                if (!res.ok) throw new Error("获取原文件失败");
                const blob = await res.blob();
                const url = URL.createObjectURL(blob);
                // 浏览器可原生预览的类型 → 新标签页查看；其余（office 等）→ 直接下载
                const ext = (doc.path.split(".").pop() || "").toLowerCase();
                const previewable = ["pdf", "jpg", "jpeg", "png", "webp", "gif", "bmp", "txt", "md"].includes(ext)
                  || blob.type.startsWith("image/") || blob.type === "application/pdf";
                if (previewable) {
                  window.open(url, "_blank");
                } else {
                  const a = document.createElement("a");
                  a.href = url;
                  a.download = doc.path.split("/").pop() || `${doc.title}.md`;
                  a.click();
                }
                setTimeout(() => URL.revokeObjectURL(url), 60000);
              } catch (e2) {
                setToast(e2 instanceof Error ? e2.message : "获取原文件失败");
              }
            }}
            className="rounded-lg border border-border-subtle px-3 py-1.5 text-[12.5px] text-muted-foreground hover:text-foreground"
            title="查看/下载上传时的原文件：PDF 与图片在线预览，其他格式下载"
          >
            查看原文件
          </button>
        )}
      </div>
      {toast && <div className="mt-2 rounded-lg bg-muted px-3 py-1.5 text-[12.5px] text-muted-foreground">{toast}</div>}

      {/* AI 摘要（后台异步生成，上传后稍等片刻自动出现） */}
      {doc.summary && (
        <div className="mt-3 rounded-xl border border-border-subtle bg-muted/50 px-4 py-3 text-[13px] leading-relaxed">
          <span className="mr-1.5 font-medium text-accent">AI 摘要</span>
          {doc.summary}
        </div>
      )}

      {/* 正文 */}
      <div className="mt-5">
        {mode === "preview" ? (
          <Markdown
            content={doc.content}
            onWikiLink={(title) => {
              const target = outgoingMap.get(title);
              if (target) onOpenDoc(target);
              else setToast(`双链目标「${title}」尚不存在，可在编辑器中创建同名笔记后自动关联`);
            }}
          />
        ) : (
          <div>
            <input
              value={draftTitle}
              onChange={(e) => setDraftTitle(e.target.value)}
              className="mb-3 w-full rounded-lg border border-border-subtle bg-background px-3 py-2 text-[14px] font-medium outline-none focus:border-accent"
              placeholder="笔记标题"
            />
            <textarea
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              rows={20}
              className="w-full resize-y rounded-xl border border-border-subtle bg-background px-4 py-3 font-mono text-[13px] leading-relaxed outline-none focus:border-accent"
              placeholder="Markdown 内容… 支持 [[双链]] 与 #标签"
            />
          </div>
        )}
      </div>

      {/* 双链关系 */}
      <div className="mt-8 grid grid-cols-1 gap-4 sm:grid-cols-2">
        <LinkPanel
          title={`链接到（${doc.links?.outgoing.length ?? 0}）`}
          items={(doc.links?.outgoing ?? []).map((l) => ({
            label: l.title,
            docId: l.doc_id,
            missing: l.doc_id == null,
          }))}
          onOpen={onOpenDoc}
        />
        <LinkPanel
          title={`反向链接（${doc.links?.incoming.length ?? 0}）`}
          items={(doc.links?.incoming ?? []).map((l) => ({ label: l.title, docId: l.doc_id }))}
          onOpen={onOpenDoc}
        />
      </div>
    </div>
  );
}

function LinkPanel({
  title,
  items,
  onOpen,
}: {
  title: string;
  items: { label: string; docId: number | null; missing?: boolean }[];
  onOpen: (id: number) => void;
}) {
  return (
    <div className="rounded-xl border border-border-subtle bg-surface p-4">
      <div className="text-[13px] font-semibold">{title}</div>
      {items.length === 0 ? (
        <div className="mt-2 text-xs text-muted-foreground/70">暂无</div>
      ) : (
        <div className="mt-2 space-y-1">
          {items.map((it, i) =>
            it.docId && !it.missing ? (
              <button
                key={i}
                onClick={() => onOpen(it.docId!)}
                className="block w-full truncate rounded-md px-2 py-1 text-left text-[13px] text-accent hover:bg-muted"
              >
                {it.label}
              </button>
            ) : (
              <div key={i} className="truncate px-2 py-1 text-[13px] text-muted-foreground/60" title="目标笔记不存在">
                {it.label}（未创建）
              </div>
            )
          )}
        </div>
      )}
    </div>
  );
}

export function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function fmtTime(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso.includes("T") ? iso : iso.replace(" ", "T"));
  if (Number.isNaN(d.getTime())) return iso.slice(0, 16);
  const now = new Date();
  const diff = (now.getTime() - d.getTime()) / 1000;
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400 && d.getDate() === now.getDate()) return `${Math.floor(diff / 3600)} 小时前`;
  const y = d.getFullYear() === now.getFullYear() ? "" : `${d.getFullYear()}-`;
  return `${y}${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
