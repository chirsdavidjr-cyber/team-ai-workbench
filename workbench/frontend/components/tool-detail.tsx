"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import type { ToolDraft, ToolInfo } from "@/lib/types";
import { fmtTime } from "./kb-detail";
import { askConfirm, askPrompt } from "@/components/dialog";

/** 能力面分组选项（与后端 agent.ACTION_GROUPS 对齐；空 = 全部能力） */
export const ACTION_OPTIONS: { id: string; label: string }[] = [
  { id: "tool_lib", label: "工具库管理" },
  { id: "kb_read", label: "知识库读取" },
  { id: "kb_manage", label: "知识库管理" },
  { id: "data", label: "数据问数" },
  { id: "task", label: "批量任务" },
  { id: "collect", label: "后台采集" },
  { id: "todo", label: "待办" },
];

const KIND_ZH: Record<string, string> = { agent: "Agent 智能体", skill: "Skill 技能", template: "Template 模板" };

function draftFrom(t: ToolInfo): ToolDraft {
  return {
    name: t.name,
    icon: t.icon,
    category: t.category,
    kind: t.kind,
    description: t.description,
    system_prompt: t.system_prompt,
    openings: t.openings,
    allowed_actions: t.allowed_actions,
    folder: t.folder,
    scope: t.scope,
  };
}

/** 工具详情：预览 / 行内编辑原地切换（与知识库 KbDetail 同构）。 */
export function ToolDetail({
  toolId,
  isAdmin,
  folders,
  onBack,
  onChanged,
  onUse,
}: {
  toolId: number;
  isAdmin: boolean;
  folders: string[];
  onBack: () => void;
  onChanged: () => void;
  onUse: (t: ToolInfo) => void;
}) {
  const [tool, setTool] = useState<ToolInfo | null>(null);
  const [mode, setMode] = useState<"preview" | "edit">("preview");
  const [draft, setDraft] = useState<ToolDraft | null>(null);
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState("");
  const { user } = useAuth();

  const load = useCallback(async () => {
    const t = await api<ToolInfo>(`/api/tools/${toolId}`);
    setTool(t);
    setDraft(draftFrom(t));
  }, [toolId]);

  useEffect(() => {
    load().catch(() => onBack());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [toolId]);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(""), 1500);
    return () => clearTimeout(t);
  }, [toast]);

  if (!tool || !draft) {
    return <div className="py-20 text-center text-sm text-muted-foreground">加载中…</div>;
  }

  const canEdit = isAdmin || tool.owner_id === user?.id; // 权限与后端一致：所有者或管理员

  async function save() {
    if (!draft) return;
    if (!draft.name.trim() || !draft.system_prompt.trim()) {
      setToast("名称与系统提示词必填");
      return;
    }
    setSaving(true);
    setToast("");
    try {
      const body: Record<string, unknown> = {
        name: draft.name.trim(),
        icon: draft.icon || "🧰",
        category: draft.category.trim() || "通用",
        kind: draft.kind,
        description: draft.description.trim(),
        system_prompt: draft.system_prompt,
        openings: draft.openings.map((s) => s.trim()).filter(Boolean),
        allowed_actions: draft.allowed_actions,
        folder: draft.folder,
      };
      if (isAdmin) body.scope = draft.scope; // 范围调整仅管理员（成员转团队走申请审核）
      await api(`/api/tools/${toolId}`, { method: "PUT", body: JSON.stringify(body) });
      await load();
      setMode("preview");
      setToast("已保存");
      onChanged();
    } catch (e) {
      setToast(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  async function moveTool() {
    if (!tool) return;
    const target = await askPrompt({ title: "移动分组", message: "留空 = 未分组，可用 / 分层级，如：客服/质检", defaultValue: tool.folder, allowEmpty: true });
    if (target === null) return;
    try {
      await api(`/api/tools/${toolId}/move`, {
        method: "PATCH",
        body: JSON.stringify({ folder: target.trim() }),
      });
      await load();
      setToast("已移动");
      onChanged();
    } catch (e) {
      setToast(e instanceof Error ? e.message : "移动失败");
    }
  }

  async function remove() {
    if (!tool) return;
    if (!(await askConfirm({ title: "删除工具", message: `删除工具「${tool.name}」？不可恢复。`, danger: true, confirmText: "删除" }))) return;
    try {
      await api(`/api/tools/${toolId}`, { method: "DELETE" });
      onChanged();
      onBack();
    } catch (e) {
      setToast(e instanceof Error ? e.message : "删除失败");
    }
  }

  async function requestTeam() {
    try {
      await api(`/api/tools/${toolId}/request-team`, { method: "POST", body: JSON.stringify({}) });
      await load();
      setToast("已提交管理员审核");
      onChanged();
    } catch (e) {
      setToast(e instanceof Error ? e.message : "申请失败");
    }
  }

  const teamBadge =
    tool.scope === "team" || tool.team_status === "approved"
      ? { text: "团队工具", cls: "bg-accent-soft text-accent" }
      : tool.team_status === "pending"
        ? { text: "团队库·审核中", cls: "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-400" }
        : tool.team_status === "rejected"
          ? { text: "团队库·已驳回", cls: "bg-red-100 text-red-600 dark:bg-red-900/40 dark:text-red-400" }
          : null;

  const input = "w-full rounded-lg border border-border-subtle bg-background px-3 py-2 text-[13.5px] outline-none focus:border-accent";

  return (
    <div className="mx-auto max-w-3xl px-6 py-8">
      {/* 顶栏 */}
      <div className="flex items-center gap-3">
        <button onClick={onBack} className="text-muted-foreground hover:text-foreground">←</button>
        <span className="text-[22px]">{tool.icon}</span>
        <div className="min-w-0 flex-1 truncate text-[16px] font-semibold">{tool.name}</div>
        {mode === "preview" ? (
          <>
            <button
              onClick={() => onUse(tool)}
              className="rounded-lg bg-accent px-3.5 py-1.5 text-[12.5px] font-medium text-accent-foreground hover:opacity-90"
              title="设为当前对话工具，回到首页输入框直接使用"
            >
              使用工具
            </button>
            {canEdit && (
              <button
                onClick={() => setMode("edit")}
                className="rounded-lg border border-border-subtle px-3 py-1.5 text-[12.5px] text-muted-foreground hover:text-foreground"
              >
                编辑
              </button>
            )}
          </>
        ) : (
          <>
            <button
              onClick={() => { setMode("preview"); setDraft(draftFrom(tool)); }}
              className="rounded-lg border border-border-subtle px-3 py-1.5 text-[12.5px] text-muted-foreground hover:text-foreground"
            >
              取消
            </button>
            <button
              onClick={save}
              disabled={saving}
              className="rounded-lg bg-accent px-3.5 py-1.5 text-[12.5px] font-medium text-accent-foreground hover:opacity-90 disabled:opacity-60"
            >
              {saving ? "保存中…" : "保存"}
            </button>
          </>
        )}
      </div>

      {/* 元信息 */}
      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <span>{KIND_ZH[tool.kind] ?? tool.kind}</span>
        <span className="rounded-full bg-muted px-2 py-0.5">{tool.category}</span>
        <span title={tool.folder || "未分组"}>分组：{tool.folder || "未分组"}</span>
        <span>已使用 {tool.use_count} 次</span>
        <span>更新于 {fmtTime(tool.updated_at ?? tool.created_at)}</span>
        <span className={`rounded-full px-2 py-0.5 ${tool.scope === "team" ? "bg-accent-soft text-accent" : "bg-muted"}`}>
          {tool.scope === "team" ? "团队" : "个人"}
        </span>
        {teamBadge && <span className={`rounded-full px-2 py-0.5 ${teamBadge.cls}`}>{teamBadge.text}</span>}
      </div>

      {/* 操作区 */}
      {mode === "preview" && (
        <div className="mt-3 flex flex-wrap gap-2">
          {tool.scope === "personal" && tool.owner_id === user?.id && !teamBadge && (
            <button
              onClick={requestTeam}
              className="rounded-lg bg-accent px-3 py-1.5 text-[12.5px] font-medium text-accent-foreground hover:opacity-90"
              title="提交管理员审核，通过后全员可用"
            >
              申请入团队库
            </button>
          )}
          {canEdit && (
            <>
              <button
                onClick={moveTool}
                className="rounded-lg border border-border-subtle px-3 py-1.5 text-[12.5px] text-muted-foreground hover:text-foreground"
              >
                移动分组…
              </button>
              <button
                onClick={remove}
                className="rounded-lg border border-border-subtle px-3 py-1.5 text-[12.5px] text-red-500 hover:border-red-400"
              >
                删除
              </button>
            </>
          )}
        </div>
      )}
      {toast && <div className="mt-2 rounded-lg bg-muted px-3 py-1.5 text-[12.5px] text-muted-foreground">{toast}</div>}

      {mode === "preview" ? (
        <>
          {tool.description && (
            <div className="mt-5 rounded-xl border border-border-subtle bg-muted/50 px-4 py-3 text-[13px] leading-relaxed">
              {tool.description}
            </div>
          )}

          {/* 系统提示词 */}
          <div className="mt-5">
            <div className="mb-1.5 text-[13px] font-semibold">系统提示词</div>
            <pre className="overflow-x-auto whitespace-pre-wrap rounded-xl border border-border-subtle bg-surface px-4 py-3 font-mono text-[12.5px] leading-relaxed">{tool.system_prompt}</pre>
          </div>

          {/* 推荐问题 */}
          {tool.openings.length > 0 && (
            <div className="mt-5">
              <div className="mb-1.5 text-[13px] font-semibold">推荐问题</div>
              <div className="flex flex-wrap gap-2">
                {tool.openings.map((o) => (
                  <button
                    key={o}
                    onClick={() => onUse(tool)}
                    title="使用工具后回到首页，点击推荐问题开始"
                    className="rounded-full border border-border-subtle px-3 py-1.5 text-[12.5px] text-muted-foreground transition-colors hover:border-accent/60 hover:text-accent"
                  >
                    {o}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* 能力面 */}
          <div className="mt-5">
            <div className="mb-1.5 text-[13px] font-semibold">能力面</div>
            {tool.allowed_actions.length === 0 ? (
              <div className="text-[12.5px] text-muted-foreground/70">全部工作台能力（未设白名单）</div>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {tool.allowed_actions.map((a) => (
                  <span key={a} className="rounded-full bg-muted px-2.5 py-1 text-[11.5px] text-muted-foreground">
                    {ACTION_OPTIONS.find((o) => o.id === a)?.label ?? a}
                  </span>
                ))}
              </div>
            )}
          </div>
        </>
      ) : (
        /* 行内编辑（与知识库详情编辑同构） */
        <div className="mt-5 space-y-3">
          <div className="grid grid-cols-[72px_1fr_1fr] gap-3">
            <div>
              <label className="mb-1 block text-[12.5px] font-medium">图标</label>
              <input className={input} value={draft.icon} onChange={(e) => setDraft({ ...draft, icon: e.target.value })} maxLength={4} />
            </div>
            <div>
              <label className="mb-1 block text-[12.5px] font-medium">名称</label>
              <input className={input} value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
            </div>
            <div>
              <label className="mb-1 block text-[12.5px] font-medium">分类</label>
              <input className={input} value={draft.category} onChange={(e) => setDraft({ ...draft, category: e.target.value })} />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-[12.5px] font-medium">类型</label>
              <select
                className={input}
                value={draft.kind}
                onChange={(e) => setDraft({ ...draft, kind: e.target.value as ToolDraft["kind"] })}
              >
                <option value="agent">Agent 智能体</option>
                <option value="skill">Skill 技能</option>
                <option value="template">Template 模板</option>
              </select>
            </div>
            <div>
              <label className="mb-1 block text-[12.5px] font-medium">分组</label>
              <input
                list="tool-folder-list"
                className={input}
                value={draft.folder}
                onChange={(e) => setDraft({ ...draft, folder: e.target.value })}
                placeholder="未分组（可输入如：客服/质检）"
              />
              <datalist id="tool-folder-list">
                {folders.map((f) => <option key={f} value={f} />)}
              </datalist>
            </div>
          </div>
          <div>
            <label className="mb-1 block text-[12.5px] font-medium">一句话描述</label>
            <input className={input} value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} />
          </div>
          <div>
            <label className="mb-1 block text-[12.5px] font-medium">系统提示词（工具的核心）</label>
            <textarea
              value={draft.system_prompt}
              onChange={(e) => setDraft({ ...draft, system_prompt: e.target.value })}
              rows={10}
              className={`${input} font-mono text-[12.5px] leading-relaxed`}
            />
          </div>
          <div>
            <label className="mb-1 block text-[12.5px] font-medium">推荐问题（每行一个，最多 3 条）</label>
            <textarea
              value={draft.openings.join("\n")}
              onChange={(e) => setDraft({ ...draft, openings: e.target.value.split("\n") })}
              rows={3}
              className={input}
            />
          </div>
          <div>
            <label className="mb-1 block text-[12.5px] font-medium">能力面（不选 = 全部工作台能力）</label>
            <div className="flex flex-wrap gap-2">
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
                      on ? "border-accent bg-accent-soft font-medium text-accent" : "border-border-subtle text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    {o.label}
                  </button>
                );
              })}
            </div>
          </div>
          {isAdmin && (
            <div className="flex items-center gap-5 text-[13px]">
              <span className="text-muted-foreground">共享范围：</span>
              <label className="flex cursor-pointer items-center gap-1.5">
                <input type="radio" checked={draft.scope === "team"} onChange={() => setDraft({ ...draft, scope: "team" })} />团队（全员可用）
              </label>
              <label className="flex cursor-pointer items-center gap-1.5">
                <input type="radio" checked={draft.scope === "personal"} onChange={() => setDraft({ ...draft, scope: "personal" })} />仅所有者
              </label>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
