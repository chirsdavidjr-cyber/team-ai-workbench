"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { askConfirm, askPrompt } from "@/components/dialog";

/**
 * 工具库左侧分组树（与知识库/数据库树同构：递归层级、▸/▾ 展开收起、⋯ 操作菜单、面板整体收起）。
 * folders：由页面聚合（工具 folder 前缀 ∪ tool_folders 占位）。
 */
export function ToolTreePanel({
  scope,
  folders,
  active,
  toolsCount,
  onSelect,
  onChanged,
}: {
  scope: "personal" | "team";
  folders: string[];
  active: string | null;
  toolsCount: number;
  onSelect: (f: string | null) => void;
  onChanged: () => void;
}) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [treeHidden, setTreeHidden] = useState(false);
  const [menuFolder, setMenuFolder] = useState<string | null>(null);
  const [toast, setToast] = useState("");

  useEffect(() => {
    try {
      setCollapsed(new Set(JSON.parse(localStorage.getItem(`tools-collapsed-${scope}`) ?? "[]") as string[]));
    } catch {
      setCollapsed(new Set());
    }
    setTreeHidden(localStorage.getItem("tools-tree-hidden") === "1");
  }, [scope]);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(""), 1500);
    return () => clearTimeout(t);
  }, [toast]);

  useEffect(() => {
    if (!menuFolder) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setMenuFolder(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menuFolder]);

  function toggleCollapse(p: string) {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(p)) next.delete(p);
      else next.add(p);
      localStorage.setItem(`tools-collapsed-${scope}`, JSON.stringify([...next]));
      return next;
    });
  }

  function toggleTree() {
    setTreeHidden((v) => {
      localStorage.setItem("tools-tree-hidden", v ? "0" : "1");
      return !v;
    });
  }

  async function createFolder() {
    const prefix = active ? `${active}` : "";
    const name = await askPrompt({ title: "新建工具分组", message: "可用 / 分层级，如：客服/质检", defaultValue: prefix ? `${prefix}/` : "" });
    if (!name?.trim()) return;
    try {
      await api("/api/tools/folders", { method: "POST", body: JSON.stringify({ path: name.trim(), scope }) });
      setToast(`已创建分组「${name.trim()}」`);
      onChanged();
    } catch (e) {
      setToast(e instanceof Error ? e.message : "创建失败");
    }
  }

  async function renameFolder(oldPath: string) {
    const parts = oldPath.split("/");
    const name = await askPrompt({ title: "重命名分组", defaultValue: parts.pop() });
    if (!name?.trim() || name.trim() === oldPath.split("/").pop()) return;
    const newPath = [...parts, name.trim()].join("/");
    try {
      const res = await api<{ moved: number }>("/api/tools/folders", {
        method: "PATCH",
        body: JSON.stringify({ old_path: oldPath, new_path: newPath, scope }),
      });
      setToast(`已重命名（${res.moved} 个工具同步移动）`);
      if (active === oldPath) onSelect(newPath);
      onChanged();
    } catch (e) {
      setToast(e instanceof Error ? e.message : "重命名失败");
    }
  }

  async function moveFolder(oldPath: string) {
    const parent = oldPath.includes("/") ? oldPath.slice(0, oldPath.lastIndexOf("/")) : "";
    const target = await askPrompt({ title: "移动分组", message: "移动到哪个分组下？（留空 = 根目录）", defaultValue: parent, allowEmpty: true });
    if (target === null) return;
    const name = oldPath.split("/").pop()!;
    const newPath = target.trim() ? `${target.trim().replace(/\/+$/, "")}/${name}` : name;
    if (newPath === oldPath) return;
    if (!(await askConfirm({ title: "移动分组", message: `将「${oldPath}」移动到「${newPath}」？\n组内工具与子分组会同步移动。`, confirmText: "移动" }))) return;
    try {
      const res = await api<{ moved: number }>("/api/tools/folders", {
        method: "PATCH",
        body: JSON.stringify({ old_path: oldPath, new_path: newPath, scope }),
      });
      setToast(`已移动（${res.moved} 个工具同步）`);
      if (active === oldPath) onSelect(newPath);
      onChanged();
    } catch (e) {
      setToast(e instanceof Error ? e.message : "移动失败");
    }
  }

  async function deleteFolder(path: string) {
    if (!(await askConfirm({ title: "删除分组", message: `删除空分组「${path}」？`, danger: true, confirmText: "删除" }))) return;
    try {
      await api("/api/tools/folders", { method: "DELETE", body: JSON.stringify({ path, scope }) });
      if (active === path) onSelect(null);
      setToast("分组已删除");
      onChanged();
    } catch (e) {
      setToast(e instanceof Error ? e.message : "删除失败");
    }
  }

  // parent → children
  const childMap = new Map<string, string[]>();
  for (const f of folders) {
    const parts = f.split("/");
    const parent = parts.length === 1 ? "" : parts.slice(0, -1).join("/");
    if (!childMap.has(parent)) childMap.set(parent, []);
    childMap.get(parent)!.push(f);
  }

  const treeNode = (f: string): React.ReactNode => {
    const children = childMap.get(f) ?? [];
    const isCollapsed = collapsed.has(f);
    const depth = f.split("/").length - 1;
    return (
      <div key={f}>
        <div
          className={`group flex items-center rounded-lg ${
            active === f ? "bg-background shadow-[0_1px_2px_rgba(0,0,0,0.04)]" : "hover:bg-background/60"
          }`}
          style={{ paddingLeft: `${depth * 14}px` }}
        >
          {children.length > 0 ? (
            <button
              onClick={() => toggleCollapse(f)}
              title={isCollapsed ? "展开" : "收起"}
              className="shrink-0 rounded p-0.5 text-[10px] text-muted-foreground/70 transition-colors hover:bg-muted hover:text-foreground"
            >
              {isCollapsed ? "▸" : "▾"}
            </button>
          ) : (
            <span className="w-[15px] shrink-0" />
          )}
          <button
            onClick={() => onSelect(active === f ? null : f)}
            className={`min-w-0 flex-1 truncate px-1.5 py-[6px] text-left text-[13px] ${
              active === f ? "font-medium" : "text-muted-foreground"
            }`}
            title={f}
          >
            🧰 {f.split("/").pop()}
            {children.length > 0 && (
              <span className="ml-1.5 text-[10px] text-muted-foreground/50">{children.length}</span>
            )}
          </button>
          <div className="relative mr-1 hidden group-hover:block">
            <button
              onClick={() => setMenuFolder(menuFolder === f ? null : f)}
              title="分组操作"
              className="rounded px-1.5 py-0.5 text-[13px] leading-none text-muted-foreground/70 hover:bg-muted hover:text-foreground"
            >
              ⋯
            </button>
            {menuFolder === f && (
              <>
                <div className="fixed inset-0 z-30" onClick={() => setMenuFolder(null)} />
                <div className="absolute right-0 top-7 z-40 w-36 overflow-hidden rounded-xl border border-border-subtle bg-surface py-1 shadow-[0_8px_30px_rgba(0,0,0,0.15)]">
                  {([
                    ["✏️ 重命名", () => renameFolder(f)],
                    ["📁 移动到…", () => moveFolder(f)],
                    ["🗑️ 删除", () => deleteFolder(f)],
                  ] as [string, () => void][]).map(([label, fn]) => (
                    <button
                      key={label}
                      onClick={() => { setMenuFolder(null); fn(); }}
                      className={`flex w-full items-center gap-2 px-3 py-[7px] text-left text-[13px] transition-colors hover:bg-muted ${
                        label.startsWith("🗑") ? "text-red-500 hover:bg-red-50 dark:hover:bg-red-950/30" : "text-foreground"
                      }`}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>
        </div>
        {!isCollapsed && children.map(treeNode)}
      </div>
    );
  };

  if (treeHidden) {
    return (
      <div className="relative flex w-10 shrink-0 flex-col items-center gap-2.5 border-r border-border-subtle bg-muted/40 py-3">
        {toast && (
          <div className="pointer-events-none absolute left-full top-3 z-50 ml-2 whitespace-nowrap rounded-lg bg-foreground px-3 py-1.5 text-[12px] text-background shadow-lg">
            {toast}
          </div>
        )}
        <button
          onClick={toggleTree}
          title="展开分组树"
          className="rounded-lg p-1.5 text-muted-foreground transition-colors hover:bg-background hover:text-foreground"
        >
          »
        </button>
      </div>
    );
  }

  return (
    <div className="w-60 shrink-0 overflow-y-auto border-r border-border-subtle px-3 py-4">
      {toast && (
        <div className="pointer-events-none fixed bottom-6 left-1/2 z-[60] -translate-x-1/2 rounded-lg bg-foreground px-3.5 py-2 text-[12.5px] text-background shadow-lg">
          {toast}
        </div>
      )}
      <div className="mt-1 flex items-center justify-between px-2 pb-1">
        <span className="text-[11.5px] font-medium uppercase tracking-wider text-muted-foreground/70">
          工具分组
        </span>
        <div className="flex items-center gap-1">
          <button onClick={createFolder} title="新建分组" className="rounded p-0.5 text-muted-foreground/70 hover:text-foreground">
            ＋
          </button>
          <button onClick={toggleTree} title="收起分组树" className="rounded p-0.5 text-muted-foreground/70 hover:text-foreground">
            «
          </button>
        </div>
      </div>
      <button
        onClick={() => onSelect(null)}
        className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-[6px] text-left text-[13px] ${
          active === null ? "bg-background font-medium shadow-[0_1px_2px_rgba(0,0,0,0.04)]" : "text-muted-foreground hover:bg-background/60"
        }`}
      >
        🧰 全部工具（{toolsCount}）
      </button>
      <div className="mt-1.5">
        {(childMap.get("") ?? []).map(treeNode)}
        {folders.length === 0 && (
          <div className="px-2.5 py-2 text-xs text-muted-foreground/60">
            还没有分组，点 ＋ 新建（如：客服/质检）
          </div>
        )}
      </div>
    </div>
  );
}
