"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import { useAppData } from "@/lib/app-data";
import type { ToolDraft, ToolInfo } from "@/lib/types";
import { ToolTreePanel } from "@/components/tool-tree";
import { ToolDetail, ACTION_OPTIONS } from "@/components/tool-detail";
import { fmtTime } from "@/components/kb-detail";
import { askConfirm, askPrompt } from "@/components/dialog";

const KIND_ZH: Record<string, string> = { agent: "Agent", skill: "Skill", template: "模板" };

export default function ToolsPage() {
  const router = useRouter();
  const { user } = useAuth();
  const { refreshTools, setActiveTool } = useAppData();
  const isAdmin = user?.role === "admin";

  const [scope, setScope] = useState<"personal" | "team">("personal");
  const [q, setQ] = useState("");
  const [sortKey, setSortKey] = useState<"use" | "updated" | "name">("use");
  const [kind, setKind] = useState<"all" | "agent" | "skill" | "template">("all");
  const [folder, setFolder] = useState<string | null>(null);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [folderRows, setFolderRows] = useState<{ path: string; sort: number }[]>([]);
  const [pending, setPending] = useState<ToolInfo[]>([]); // 待审核（管理员）
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<number | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [guideOpen, setGuideOpen] = useState(false);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [menuTool, setMenuTool] = useState<number | null>(null);
  const [toast, setToast] = useState("");
  const qTrim = q.trim();

  // toast 自动消失（与知识库一致）
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(""), 1500);
    return () => clearTimeout(t);
  }, [toast]);

  // 首次进入自动弹出使用指引（与知识库一致）
  useEffect(() => {
    if (!localStorage.getItem("tools-guide-seen")) setGuideOpen(true);
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [ts, fs] = await Promise.all([
        api<ToolInfo[]>(`/api/tools?scope=${scope}&q=${encodeURIComponent(qTrim)}&sort=${sortKey}`),
        api<{ path: string; sort: number }[]>(`/api/tools/folders?scope=${scope}`),
      ]);
      setTools(ts);
      setFolderRows(fs);
      if (isAdmin) {
        try {
          setPending(await api<ToolInfo[]>("/api/tools/team-requests"));
        } catch { setPending([]); }
      }
    } finally {
      setLoading(false);
    }
  }, [scope, qTrim, sortKey, isAdmin]);

  // 搜索防抖（与知识库一致：300ms）；其余条件变化立即加载
  useEffect(() => {
    const t = q ? setTimeout(load, 300) : undefined;
    if (!t) load();
    return () => t && clearTimeout(t);
  }, [load, q]);

  // Esc 关闭行操作菜单
  useEffect(() => {
    if (!menuTool) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setMenuTool(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menuTool]);

  // 深链：/tools?scope=team&folder=客服&open=3
  useEffect(() => {
    const p = new URLSearchParams(window.location.search);
    const s = p.get("scope");
    if (s === "team" || s === "personal") setScope(s);
    const f = p.get("folder");
    if (f !== null) setFolder(f || null);
    const o = p.get("open");
    if (o) setSelected(Number(o));
  }, []);

  // 分组树：工具 folder 前缀聚合 ∪ 占位（与知识库/数据库同构）
  const folders = useMemo(() => {
    const set = new Set<string>(folderRows.map((f) => f.path));
    for (const t of tools) {
      if (!t.folder) continue;
      const parts = t.folder.split("/");
      for (let i = 1; i <= parts.length; i++) set.add(parts.slice(0, i).join("/"));
    }
    return [...set];
  }, [tools, folderRows]);

  const visible = useMemo(() => {
    const list = tools.filter((t) => {
      if (kind !== "all" && t.kind !== kind) return false;
      if (folder === null) return true;
      return t.folder === folder || t.folder.startsWith(folder + "/");
    });
    return list; // 排序由后端完成（use/updated/name）
  }, [tools, folder, kind]);

  /** 变更后联动刷新：本页列表 + 全局工具上下文（首页/对话页的工具选择器） */
  async function changed() {
    await Promise.all([load(), refreshTools()]);
  }

  function use(t: ToolInfo) {
    setActiveTool(t);
    router.push("/");
  }

  async function moveTool(t: ToolInfo) {
    const target = await askPrompt({ title: "移动分组", message: "留空 = 未分组，可用 / 分层级，如：客服/质检", defaultValue: t.folder, allowEmpty: true });
    if (target === null) return;
    try {
      await api(`/api/tools/${t.id}/move`, {
        method: "PATCH",
        body: JSON.stringify({ folder: target.trim() }),
      });
      setToast("已移动");
      changed();
    } catch (e) {
      setToast(e instanceof Error ? e.message : "移动失败");
    }
  }

  async function remove(t: ToolInfo) {
    if (!(await askConfirm({ title: "删除工具", message: `删除工具「${t.name}」？不可恢复。`, danger: true, confirmText: "删除" }))) return;
    try {
      await api(`/api/tools/${t.id}`, { method: "DELETE" });
      if (selected === t.id) setSelected(null);
      setToast("已删除");
      changed();
    } catch (e) {
      setToast(e instanceof Error ? e.message : "删除失败");
    }
  }

  async function requestTeam(t: ToolInfo) {
    try {
      await api(`/api/tools/${t.id}/request-team`, { method: "POST", body: JSON.stringify({}) });
      setToast("已提交管理员审核");
      changed();
    } catch (e) {
      setToast(e instanceof Error ? e.message : "申请失败");
    }
  }

  return (
    <div className="flex h-full min-h-0">
      {/* 左：分组树（可整体收起） */}
      <ToolTreePanel
        scope={scope}
        folders={folders}
        active={folder}
        toolsCount={tools.length}
        onSelect={setFolder}
        onChanged={changed}
      />

      {/* 右：列表 ⇄ 详情（与知识库/数据库同构：单页原地切换） */}
      <div className="min-w-0 flex-1 overflow-y-auto">
        {selected !== null ? (
          <ToolDetail
            toolId={selected}
            isAdmin={!!isAdmin}
            folders={folders}
            onBack={() => setSelected(null)}
            onChanged={changed}
            onUse={use}
          />
        ) : (
          <>
            {/* 工具栏 */}
            <div className="sticky top-0 z-10 flex flex-wrap items-center gap-2 border-b border-border-subtle bg-background/95 px-6 py-3 backdrop-blur">
              <div className="flex gap-1 rounded-lg bg-muted p-1">
                {(["personal", "team"] as const).map((s) => (
                  <button
                    key={s}
                    onClick={() => { setScope(s); setFolder(null); }}
                    className={`rounded-md px-3.5 py-1 text-[12.5px] transition-colors ${
                      scope === s ? "bg-background font-medium shadow-[0_1px_2px_rgba(0,0,0,0.05)]" : "text-muted-foreground"
                    }`}
                  >
                    {s === "personal" ? "我的工具" : "团队工具"}
                  </button>
                ))}
              </div>
              <input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="搜索名称/描述/提示词…"
                className="min-w-[150px] flex-1 rounded-lg border border-border-subtle bg-surface px-3 py-1.5 text-[13px] outline-none focus:border-accent"
              />
              <select
                value={kind}
                onChange={(e) => setKind(e.target.value as typeof kind)}
                title="类型筛选"
                className="rounded-lg border border-border-subtle bg-surface px-2 py-1.5 text-[12.5px] text-muted-foreground outline-none"
              >
                <option value="all">全部类型</option>
                <option value="agent">Agent</option>
                <option value="skill">Skill</option>
                <option value="template">Template</option>
              </select>
              <select
                value={sortKey}
                onChange={(e) => setSortKey(e.target.value as typeof sortKey)}
                title="排序"
                className="rounded-lg border border-border-subtle bg-surface px-2 py-1.5 text-[12.5px] text-muted-foreground outline-none"
              >
                <option value="use">使用最多</option>
                <option value="updated">最近更新</option>
                <option value="name">名称</option>
              </select>
              <button
                onClick={() => setGuideOpen(true)}
                className="rounded-lg border border-border-subtle px-3 py-1.5 text-[12.5px] text-muted-foreground hover:text-foreground"
              >
                操作指引
              </button>
              {isAdmin && (
                <button
                  onClick={() => setReviewOpen(true)}
                  className="relative rounded-lg border border-border-subtle px-3 py-1.5 text-[12.5px] text-muted-foreground hover:text-foreground"
                >
                  团队审核
                  {pending.length > 0 && (
                    <span className="ml-1.5 rounded-full bg-accent px-1.5 py-px text-[10px] font-semibold text-accent-foreground">
                      {pending.length}
                    </span>
                  )}
                </button>
              )}
              <button
                onClick={() => setCreateOpen(true)}
                className="rounded-lg bg-accent px-3.5 py-1.5 text-[13px] font-medium text-accent-foreground transition-opacity hover:opacity-90"
              >
                + 新建工具
              </button>
            </div>

            {toast && (
              <div className="mx-6 mt-3 rounded-lg border border-border-subtle bg-surface px-3.5 py-2 text-[13px] text-muted-foreground">
                {toast}
              </div>
            )}

            {/* 工具列表 */}
            <div className="px-6 py-4">
              {folder !== null && (
                <div className="mb-3 flex items-center gap-2 text-[12.5px] text-muted-foreground">
                  <button onClick={() => setFolder(null)} className="hover:text-accent">全部工具</button>
                  {folder.split("/").map((seg, i, arr) => (
                    <span key={i} className="flex items-center gap-2">
                      / <span className={i === arr.length - 1 ? "font-medium text-foreground" : ""}>{seg}</span>
                    </span>
                  ))}
                </div>
              )}

              {loading ? (
                <div className="py-12 text-center text-sm text-muted-foreground">加载中…</div>
              ) : visible.length === 0 ? (
                <div className="rounded-xl border border-dashed border-border-subtle px-4 py-14 text-center text-[13px] text-muted-foreground/70">
                  {tools.length === 0
                    ? scope === "personal"
                      ? "还没有自己的工具：点右上「新建工具」，或在对话结束后「沉淀为工具」"
                      : "团队工具库是空的：管理员可直接新建，成员可从个人工具申请入库"
                    : "当前筛选条件下没有工具"}
                </div>
              ) : (
                <table className="w-full border-collapse text-[13px]">
                  <thead>
                    <tr className="border-b border-border-subtle text-left text-[12px] text-muted-foreground">
                      <th className="py-2 pr-3 font-medium">工具</th>
                      <th className="w-20 py-2 pr-3 font-medium">类型</th>
                      <th className="w-24 py-2 pr-3 font-medium">分类</th>
                      <th className="w-32 py-2 pr-3 font-medium">分组</th>
                      <th className="w-16 py-2 pr-3 font-medium">使用</th>
                      <th className="w-24 py-2 pr-3 font-medium">更新时间</th>
                      <th className="w-28 py-2 pr-3 font-medium">状态</th>
                      <th className="w-14 py-2 font-medium" />
                    </tr>
                  </thead>
                  <tbody>
                    {visible.map((t) => {
                      const canEdit = isAdmin || t.owner_id === user?.id;
                      return (
                        <tr
                          key={t.id}
                          className="group cursor-pointer border-b border-border-subtle/60 hover:bg-muted/40"
                          onClick={() => setSelected(t.id)}
                        >
                          <td className="py-2.5 pr-3">
                            <div className="flex min-w-0 items-center gap-2">
                              <span className="text-[18px]">{t.icon}</span>
                              <div className="min-w-0">
                                <div className="truncate font-medium group-hover:text-accent">{t.name}</div>
                                <div className="truncate text-[11.5px] text-muted-foreground/70">{t.description || "—"}</div>
                              </div>
                            </div>
                          </td>
                          <td className="py-2.5 pr-3 text-muted-foreground">{KIND_ZH[t.kind] ?? t.kind}</td>
                          <td className="py-2.5 pr-3">
                            <span className="rounded-full bg-muted px-2 py-0.5 text-[10.5px] text-muted-foreground">{t.category}</span>
                          </td>
                          <td className="max-w-[8rem] truncate py-2.5 pr-3 text-muted-foreground" title={t.folder || "未分组"}>
                            {t.folder || "未分组"}
                          </td>
                          <td className="py-2.5 pr-3 text-muted-foreground">{t.use_count}</td>
                          <td className="py-2.5 pr-3 text-muted-foreground">{fmtTime(t.updated_at ?? t.created_at)}</td>
                          <td className="py-2.5 pr-3">
                            <div className="flex flex-wrap gap-1">
                              <span className={`rounded px-1.5 py-0.5 text-[10.5px] ${t.scope === "team" ? "bg-accent-soft text-accent" : "bg-muted text-muted-foreground"}`}>
                                {t.scope === "team" ? "团队" : "个人"}
                              </span>
                              {t.team_status === "pending" && (
                                <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[10.5px] text-amber-700 dark:bg-amber-900/40 dark:text-amber-400">审核中</span>
                              )}
                              {t.team_status === "rejected" && (
                                <span className="rounded bg-red-100 px-1.5 py-0.5 text-[10.5px] text-red-600 dark:bg-red-900/40 dark:text-red-400">已驳回</span>
                              )}
                            </div>
                          </td>
                          <td className="py-2.5 text-right">
                            <div className="relative hidden justify-end group-hover:flex">
                              <button
                                onClick={(e) => { e.stopPropagation(); setMenuTool(menuTool === t.id ? null : t.id); }}
                                title="工具操作"
                                className="rounded-md border border-border-subtle px-2 py-1 text-[11.5px] text-muted-foreground hover:text-accent"
                              >
                                ⋯
                              </button>
                              {menuTool === t.id && (
                                <>
                                  <div className="fixed inset-0 z-30" onClick={(e) => { e.stopPropagation(); setMenuTool(null); }} />
                                  <div className="absolute right-0 top-8 z-40 w-40 overflow-hidden rounded-xl border border-border-subtle bg-surface py-1 shadow-[0_8px_30px_rgba(0,0,0,0.15)]">
                                    <button
                                      onClick={(e) => { e.stopPropagation(); setMenuTool(null); setSelected(t.id); }}
                                      className="flex w-full items-center gap-2 px-3 py-[7px] text-left text-[13px] hover:bg-muted"
                                    >
                                      📄 打开详情
                                    </button>
                                    <button
                                      onClick={(e) => { e.stopPropagation(); setMenuTool(null); use(t); }}
                                      className="flex w-full items-center gap-2 px-3 py-[7px] text-left text-[13px] hover:bg-muted"
                                    >
                                      💬 使用工具
                                    </button>
                                    <button
                                      onClick={(e) => { e.stopPropagation(); setMenuTool(null); moveTool(t); }}
                                      className="flex w-full items-center gap-2 px-3 py-[7px] text-left text-[13px] hover:bg-muted"
                                    >
                                      📁 移动分组…
                                    </button>
                                    {t.scope === "personal" && t.owner_id === user?.id && !t.team_status && (
                                      <button
                                        onClick={(e) => { e.stopPropagation(); setMenuTool(null); requestTeam(t); }}
                                        className="flex w-full items-center gap-2 px-3 py-[7px] text-left text-[13px] hover:bg-muted"
                                      >
                                        📤 申请入团队库
                                      </button>
                                    )}
                                    {canEdit && (
                                      <button
                                        onClick={(e) => { e.stopPropagation(); setMenuTool(null); remove(t); }}
                                        className="flex w-full items-center gap-2 px-3 py-[7px] text-left text-[13px] text-red-500 hover:bg-red-50 dark:hover:bg-red-950/30"
                                      >
                                        🗑️ 删除
                                      </button>
                                    )}
                                  </div>
                                </>
                              )}
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
            </div>
          </>
        )}
      </div>

      {/* 新建工具弹窗 */}
      {createOpen && (
        <CreateDialog
          isAdmin={!!isAdmin}
          defaultScope={scope}
          folders={folders}
          defaultFolder={folder}
          onClose={() => setCreateOpen(false)}
          onDone={(id) => {
            setCreateOpen(false);
            setToast("已创建");
            changed();
            setSelected(id);
          }}
        />
      )}

      {/* 团队审核弹窗（管理员） */}
      {reviewOpen && (
        <ReviewDialog
          pending={pending}
          onClose={() => setReviewOpen(false)}
          onDecided={(msg) => {
            setToast(msg);
            changed();
          }}
        />
      )}

      {/* 操作指引：首次进入自动弹出 */}
      {guideOpen && <GuideDialog onClose={() => setGuideOpen(false)} />}
    </div>
  );
}

function CreateDialog({
  isAdmin,
  defaultScope,
  folders,
  defaultFolder,
  onClose,
  onDone,
}: {
  isAdmin: boolean;
  defaultScope: "personal" | "team";
  folders: string[];
  defaultFolder: string | null;
  onClose: () => void;
  onDone: (id: number) => void;
}) {
  const [d, setD] = useState<ToolDraft>({
    name: "",
    icon: "🧰",
    category: "通用",
    kind: "agent",
    description: "",
    system_prompt: "",
    openings: [],
    allowed_actions: [],
    folder: defaultFolder ?? "",
    scope: defaultScope === "team" && !isAdmin ? "personal" : defaultScope,
  });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const input = "w-full rounded-lg border border-border-subtle bg-background px-3 py-2 text-[13.5px] outline-none focus:border-accent";

  async function save() {
    if (!d.name.trim() || !d.system_prompt.trim()) {
      setError("名称与系统提示词必填");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const res = await api<ToolInfo>("/api/tools", {
        method: "POST",
        body: JSON.stringify({
          name: d.name.trim(),
          icon: d.icon || "🧰",
          category: d.category.trim() || "通用",
          kind: d.kind,
          description: d.description.trim(),
          system_prompt: d.system_prompt,
          openings: d.openings.map((s) => s.trim()).filter(Boolean),
          allowed_actions: d.allowed_actions,
          folder: d.folder.trim(),
          scope: d.scope,
        }),
      });
      onDone(res.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto bg-black/40 p-6" onClick={onClose}>
      <div className="my-8 w-full max-w-lg rounded-2xl border border-border-subtle bg-surface p-6 shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-[15px] font-semibold">新建工具</h3>
        <div className="mt-4 space-y-3">
          <div className="grid grid-cols-[72px_1fr_1fr] gap-3">
            <div>
              <label className="mb-1 block text-[12.5px] font-medium">图标</label>
              <input className={input} value={d.icon} onChange={(e) => setD({ ...d, icon: e.target.value })} maxLength={4} />
            </div>
            <div>
              <label className="mb-1 block text-[12.5px] font-medium">名称</label>
              <input className={input} value={d.name} onChange={(e) => setD({ ...d, name: e.target.value })} placeholder="如：意图分析" autoFocus />
            </div>
            <div>
              <label className="mb-1 block text-[12.5px] font-medium">分类</label>
              <input className={input} value={d.category} onChange={(e) => setD({ ...d, category: e.target.value })} placeholder="如：质检" />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-[12.5px] font-medium">类型</label>
              <select className={input} value={d.kind} onChange={(e) => setD({ ...d, kind: e.target.value as ToolDraft["kind"] })}>
                <option value="agent">Agent 智能体（持续性角色）</option>
                <option value="skill">Skill 技能（固定流程）</option>
                <option value="template">Template 模板（固定产出）</option>
              </select>
            </div>
            <div>
              <label className="mb-1 block text-[12.5px] font-medium">分组</label>
              <input
                list="tool-create-folder-list"
                className={input}
                value={d.folder}
                onChange={(e) => setD({ ...d, folder: e.target.value })}
                placeholder="未分组（可输入如：客服/质检）"
              />
              <datalist id="tool-create-folder-list">
                {folders.map((f) => <option key={f} value={f} />)}
              </datalist>
            </div>
          </div>
          <div>
            <label className="mb-1 block text-[12.5px] font-medium">一句话描述</label>
            <input className={input} value={d.description} onChange={(e) => setD({ ...d, description: e.target.value })} placeholder="展示在工具列表上" />
          </div>
          <div>
            <label className="mb-1 block text-[12.5px] font-medium">系统提示词（工具的核心）</label>
            <textarea
              value={d.system_prompt}
              onChange={(e) => setD({ ...d, system_prompt: e.target.value })}
              rows={6}
              className={`${input} font-mono text-[12.5px] leading-relaxed`}
              placeholder="定义这个工具的角色、能力边界与输出格式…"
            />
          </div>
          <div>
            <label className="mb-1 block text-[12.5px] font-medium">推荐问题（每行一个，最多 3 条）</label>
            <textarea
              value={d.openings.join("\n")}
              onChange={(e) => setD({ ...d, openings: e.target.value.split("\n") })}
              rows={2}
              className={input}
              placeholder={"分析这条咨询的意图：…\n帮我写一条公告"}
            />
          </div>
          <div>
            <label className="mb-1 block text-[12.5px] font-medium">能力面（不选 = 全部工作台能力）</label>
            <div className="flex flex-wrap gap-2">
              <button
                onClick={() => setD({ ...d, allowed_actions: [] })}
                className={`rounded-full border px-2.5 py-1 text-[12px] transition-colors ${
                  d.allowed_actions.length === 0 ? "border-accent bg-accent-soft font-medium text-accent" : "border-border-subtle text-muted-foreground hover:text-foreground"
                }`}
              >
                全部能力
              </button>
              {ACTION_OPTIONS.map((o) => {
                const on = d.allowed_actions.includes(o.id);
                return (
                  <button
                    key={o.id}
                    onClick={() => setD({ ...d, allowed_actions: on ? d.allowed_actions.filter((a) => a !== o.id) : [...d.allowed_actions, o.id] })}
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
                <input type="radio" checked={d.scope === "team"} onChange={() => setD({ ...d, scope: "team" })} />团队（全员可用）
              </label>
              <label className="flex cursor-pointer items-center gap-1.5">
                <input type="radio" checked={d.scope === "personal"} onChange={() => setD({ ...d, scope: "personal" })} />仅自己
              </label>
            </div>
          )}
          {error && <div className="rounded-lg border border-red-300/60 bg-red-50 px-3 py-2 text-[13px] text-red-600 dark:border-red-900/60 dark:bg-red-950/40 dark:text-red-400">{error}</div>}
          <div className="flex gap-2.5 pt-1">
            <button onClick={save} disabled={busy} className="flex-1 rounded-lg bg-accent py-2 text-[13.5px] font-medium text-accent-foreground hover:opacity-90 disabled:opacity-60">
              {busy ? "保存中…" : "保存"}
            </button>
            <button onClick={onClose} className="flex-1 rounded-lg border border-border-subtle py-2 text-[13.5px] text-muted-foreground hover:text-foreground">取消</button>
          </div>
        </div>
      </div>
    </div>
  );
}

/** 团队审核（管理员）：批准 → 全员可用的团队工具；驳回 → 退回个人 */
function ReviewDialog({
  pending,
  onClose,
  onDecided,
}: {
  pending: ToolInfo[];
  onClose: () => void;
  onDecided: (msg: string) => void;
}) {
  const [busyId, setBusyId] = useState<number | null>(null);

  async function decide(t: ToolInfo, approve: boolean) {
    if (!approve && !(await askConfirm({ title: "驳回申请", message: `驳回「${t.name}」的入团队库申请？`, confirmText: "驳回" }))) return;
    setBusyId(t.id);
    try {
      await api(`/api/tools/${t.id}/review-team`, {
        method: "POST",
        body: JSON.stringify({ approve }),
      });
      onDecided(approve ? `已批准「${t.name}」入团队库` : `已驳回「${t.name}」`);
    } catch (e) {
      onDecided(e instanceof Error ? e.message : "操作失败");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-6" onClick={onClose}>
      <div className="w-full max-w-md rounded-2xl border border-border-subtle bg-surface p-6 shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-[15px] font-semibold">入团队库申请（{pending.length}）</h3>
        {pending.length === 0 ? (
          <div className="mt-4 rounded-xl border border-dashed border-border-subtle px-4 py-8 text-center text-[13px] text-muted-foreground/70">
            暂无待审核申请
          </div>
        ) : (
          <div className="mt-4 space-y-2">
            {pending.map((t) => (
              <div key={t.id} className="flex items-center gap-3 rounded-xl border border-border-subtle px-4 py-3">
                <span className="text-[18px]">{t.icon}</span>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[13.5px] font-medium">{t.name}</div>
                  <div className="truncate text-[11.5px] text-muted-foreground/70">{t.description || t.category}</div>
                </div>
                <button
                  onClick={() => decide(t, true)}
                  disabled={busyId === t.id}
                  className="rounded-lg bg-accent px-3 py-1.5 text-[12px] font-medium text-accent-foreground hover:opacity-90 disabled:opacity-60"
                >
                  批准
                </button>
                <button
                  onClick={() => decide(t, false)}
                  disabled={busyId === t.id}
                  className="rounded-lg border border-border-subtle px-3 py-1.5 text-[12px] text-muted-foreground hover:border-red-400 hover:text-red-500 disabled:opacity-60"
                >
                  驳回
                </button>
              </div>
            ))}
          </div>
        )}
        <button onClick={onClose} className="mt-4 w-full rounded-lg border border-border-subtle py-2 text-[13px] text-muted-foreground hover:text-foreground">关闭</button>
      </div>
    </div>
  );
}

/** 工具库使用指引（首次进入自动弹出，与知识库指引同构） */
function GuideDialog({ onClose }: { onClose: () => void }) {
  const steps: [string, string, string][] = [
    ["🧰", "工具 = 提示词 + 能力面", "Agent（持续性角色）/ Skill（固定流程）/ Template（固定产出）三类。在对话输入框的工具选择器、首页工具卡可直接调用，也可用于批量任务。"],
    ["🗂️", "分组管理，对齐知识库", "左侧分组树支持多级分组（如：客服/质检），可新建、重命名、移动、删除；工具可随时移动分组，成员的「沉淀为工具」默认进未分组。"],
    ["📤", "个人 → 团队：申请审核", "成员工具默认仅自己可用；点「申请入团队库」提交管理员审核，通过后全员可用。团队工具与团队分组由管理员直接维护。"],
    ["🔒", "能力面白名单", "创建时可限定工具可用的工作台能力（如仅知识库读取），防止提示词工具越权操作；不选即全部能力。"],
  ];
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto bg-black/50 p-6" onClick={onClose}>
      <div className="my-8 w-full max-w-lg rounded-2xl border border-border-subtle bg-surface p-6 shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-[16px] font-semibold">工具库使用指引</h3>
        <p className="mt-1 text-[12.5px] text-muted-foreground">
          把验证有效的做法沉淀成可复用工具：创建 → 分组管理 → 团队共享
        </p>
        <div className="mt-4 space-y-3">
          {steps.map(([icon, title, desc]) => (
            <div key={title} className="flex gap-3 rounded-xl border border-border-subtle px-4 py-3">
              <span className="mt-0.5 text-[18px]">{icon}</span>
              <div className="min-w-0">
                <div className="text-[13.5px] font-medium">{title}</div>
                <div className="mt-1 text-[12.5px] leading-relaxed text-muted-foreground">{desc}</div>
              </div>
            </div>
          ))}
        </div>
        <button
          onClick={() => { localStorage.setItem("tools-guide-seen", "1"); onClose(); }}
          className="mt-4 w-full rounded-lg bg-accent py-2 text-[13.5px] font-medium text-accent-foreground hover:opacity-90"
        >
          开始使用
        </button>
      </div>
    </div>
  );
}
