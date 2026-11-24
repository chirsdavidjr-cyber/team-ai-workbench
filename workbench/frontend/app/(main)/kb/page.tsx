"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import type { KbDocInfo } from "@/lib/types";
import { KbDetail, fmtSize, fmtTime } from "@/components/kb-detail";
import { askConfirm, askPrompt } from "@/components/dialog";

const IGNORE = new Set([".obsidian", ".trash", ".git", "__pycache__", "node_modules"]);
const SUPPORTED = /\.(md|markdown|txt|pdf|docx|pptx|doc|xls|zip)$/i;

export default function KbPage() {
  const router = useRouter();
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [scope, setScope] = useState<"personal" | "team">("personal");
  const [q, setQ] = useState("");
  const [docs, setDocs] = useState<KbDocInfo[]>([]);
  const [folderRows, setFolderRows] = useState<{ path: string; sort: number }[]>([]);
  const [sortKey, setSortKey] = useState<"updated" | "title" | "size" | "chunks">("updated");
  const [loading, setLoading] = useState(true);
  const [folder, setFolder] = useState<string | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [guideOpen, setGuideOpen] = useState(false);
  const [toast, setToast] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [menuFolder, setMenuFolder] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  // toast 自动消失（1.5s）
  useEffect(() => {
    // 首次进入知识库自动弹出使用指引
    if (!localStorage.getItem("kb-guide-seen")) setGuideOpen(true);
  }, []);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(""), 1500);
    return () => clearTimeout(t);
  }, [toast]);

  // Esc 关闭文件夹操作菜单
  useEffect(() => {
    if (!menuFolder) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMenuFolder(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menuFolder]);

  // 从图谱页跳转打开指定文档
  useEffect(() => {
    const open = new URLSearchParams(window.location.search).get("open");
    if (open) setSelected(Number(open));
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [d, f] = await Promise.all([
        api<KbDocInfo[]>(`/api/kb/docs?scope=${scope}&q=${encodeURIComponent(q)}`),
        api<{ path: string; sort: number }[]>(`/api/kb/folders?scope=${scope}`),
      ]);
      setDocs(d);
      setFolderRows(f);
    } finally {
      setLoading(false);
    }
  }, [scope, q]);

  useEffect(() => {
    const t = q ? setTimeout(load, 300) : undefined;
    if (!t) load();
    return () => t && clearTimeout(t);
  }, [load, q]);

  // 目录树：当前库文档路径聚合 ∪ 文件夹占位（保留占位顺序：有 sort 的在前按 sort，路径聚合的按字典序）
  const folders = useMemo(() => {
    const placeholder = new Map(folderRows.map((f) => [f.path, f.sort]));
    const set = new Set<string>(placeholder.keys());
    for (const d of docs) {
      const parts = d.path.split("/");
      for (let i = 1; i < parts.length; i++) set.add(parts.slice(0, i).join("/"));
    }
    return [...set].sort((a, b) => {
      const sa = placeholder.has(a) ? placeholder.get(a)! : 9999;
      const sb = placeholder.has(b) ? placeholder.get(b)! : 9999;
      return sa !== sb ? sa - sb : a.localeCompare(b);
    });
  }, [docs, folderRows]);

  // Karpathy LLM Wiki 骨架：四区分组，骨架路径锁定不可删改
  const SKELETON_ZONES: [string, string][] = [
    ["收件箱", "📥"],
    ["原始资料", "🗄️"],
    ["知识库", "💡"],
    ["来源台账", "📋"],
  ];
  const SKELETON_SET = useMemo(() => {
    const s = new Set<string>(["收件箱", "原始资料", "知识库", "来源台账"]);
    for (const f of folders) {
      const top = f.split("/")[0];
      if (s.has(top)) s.add(f);
    }
    return s;
  }, [folders]);
  const hasWorkflow = folders.includes("收件箱") || folders.includes("原始资料");
  const inboxCount = useMemo(
    () => docs.filter((d) => d.path.startsWith("收件箱/") && d.path !== "收件箱.md").length,
    [docs]
  );
  // 骨架之外的路径 → 未归类分组
  const looseFolders = useMemo(
    () => folders.filter((f) => !SKELETON_SET.has(f)),
    [folders, SKELETON_SET]
  );

  // 树形结构：parent path（""=根）→ 直接子文件夹列表
  const childMap = useMemo(() => {
    const m = new Map<string, string[]>();
    for (const f of folders) {
      const parts = f.split("/");
      const parent = parts.length === 1 ? "" : parts.slice(0, -1).join("/");
      if (!m.has(parent)) m.set(parent, []);
      m.get(parent)!.push(f);
    }
    return m;
  }, [folders]);

  // 展开/收起状态（按库记忆）
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  useEffect(() => {
    try {
      setCollapsed(new Set(JSON.parse(localStorage.getItem(`kb-collapsed-${scope}`) ?? "[]") as string[]));
    } catch {
      setCollapsed(new Set());
    }
  }, [scope]);
  function toggleCollapse(p: string) {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(p)) next.delete(p);
      else next.add(p);
      localStorage.setItem(`kb-collapsed-${scope}`, JSON.stringify([...next]));
      return next;
    });
  }

  // 左侧目录树面板整体收起/展开（记忆）
  const [treeHidden, setTreeHidden] = useState(false);
  useEffect(() => {
    setTreeHidden(localStorage.getItem("kb-tree-hidden") === "1");
  }, []);
  function toggleTree() {
    setTreeHidden((v) => {
      localStorage.setItem("kb-tree-hidden", v ? "0" : "1");
      return !v;
    });
  }

  const visible = useMemo(() => {
    const list = docs.filter((d) => !folder || d.path.startsWith(folder + "/"));
    const sorted = [...list];
    if (sortKey === "title") sorted.sort((a, b) => a.title.localeCompare(b.title, "zh"));
    else if (sortKey === "size") sorted.sort((a, b) => b.size - a.size);
    else if (sortKey === "chunks") sorted.sort((a, b) => b.chunk_count - a.chunk_count);
    return sorted; // 默认按 updated_at（后端已排序）
  }, [docs, folder, sortKey]);

  async function upload(files: FileList | File[], targetScope: string, target = "收件箱") {
    const list = Array.from(files).filter((f) => SUPPORTED.test(f.name));
    if (list.length === 0) return;
    try {
      const fd = new FormData();
      list.forEach((f) => fd.append("files", f));
      fd.append("scope", targetScope);
      fd.append("target", target);
      const res = await api<{ docs: KbDocInfo[]; skipped: { name: string; reason: string }[] }>(
        "/api/kb/upload",
        { method: "POST", body: fd }
      );
      setToast(`导入 ${res.docs.length} 篇${res.skipped.length ? `，跳过 ${res.skipped.length} 个（${res.skipped[0].reason}）` : ""}`);
      setUploadOpen(false);
      await load();
    } catch (e) {
      setToast(e instanceof Error ? e.message : "上传失败");
    } finally {
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  /** 连接本地 Obsidian 文件夹：浏览器直读目录，增量上传新笔记 */
  async function connectLocal() {
    const picker = (window as unknown as { showDirectoryPicker?: () => Promise<FileSystemDirectoryHandle> })
      .showDirectoryPicker;
    if (!picker) {
      setToast("当前浏览器不支持本地文件夹访问，请使用 Chrome/Edge，或用 zip 上传");
      return;
    }
    setConnecting(true);
    try {
      const root = await picker();
      const existing = new Set(docs.map((d) => d.path));
      const files: File[] = [];

      async function walk(dir: FileSystemDirectoryHandle, prefix: string) {
        for await (const entry of (dir as unknown as { values(): AsyncIterable<FileSystemHandle> }).values()) {
          if (IGNORE.has(entry.name)) continue;
          const rel = prefix ? `${prefix}/${entry.name}` : entry.name;
          if (entry.kind === "directory") {
            await walk(entry as FileSystemDirectoryHandle, rel);
          } else if (/\.(md|markdown|txt|pdf|docx)$/i.test(entry.name)) {
            if (existing.has(rel)) continue; // 增量：已存在的跳过
            const file = await (entry as FileSystemFileHandle).getFile();
            const f = new File([file], rel, { type: "text/markdown" });
            files.push(f);
          }
        }
      }
      await walk(root, "");
      if (files.length === 0) {
        setToast("本地库与线上已同步，没有新笔记");
      } else {
        // 本地同步保留原有目录结构，直接按原路径入库（不走收件箱）
        await upload(files, scope, "");
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError") {
        setToast(e instanceof Error ? e.message : "连接失败");
      }
    } finally {
      setConnecting(false);
    }
  }

  async function createDoc(title: string, targetFolder: string, targetScope: string) {
    const res = await api<KbDocInfo>("/api/kb/docs", {
      method: "POST",
      body: JSON.stringify({ title, folder: targetFolder, content: `# ${title}\n`, scope: targetScope }),
    });
    setCreateOpen(false);
    setToast(`已创建「${res.title}」`);
    await load();
    setSelected(res.id);
  }

  async function createFolder() {
    const name = await askPrompt({ title: "新建文件夹" });
    if (!name?.trim()) return;
    const path = folder ? `${folder}/${name.trim()}` : name.trim();
    await api("/api/kb/folders", {
      method: "POST",
      body: JSON.stringify({ path, scope }),
    });
    setToast(`已创建文件夹「${path}」`);
    load();
  }

  async function renameFolder(oldPath: string) {
    const parts = oldPath.split("/");
    const name = await askPrompt({ title: "重命名文件夹", defaultValue: parts.pop() });
    if (!name?.trim() || name.trim() === oldPath.split("/").pop()) return;
    const newPath = [...parts, name.trim()].join("/");
    const res = await api<{ moved: number }>("/api/kb/folders", {
      method: "PATCH",
      body: JSON.stringify({ old_path: oldPath, new_path: newPath, scope }),
    });
    setToast("已重命名");
    setFolder((f) => (f === oldPath ? newPath : f));
    load();
  }

  async function moveFolder(oldPath: string) {
    const parent = oldPath.includes("/") ? oldPath.slice(0, oldPath.lastIndexOf("/")) : "";
    const target = await askPrompt({ title: "移动文件夹", message: "移动到哪个文件夹下？（留空 = 根目录）", defaultValue: parent, allowEmpty: true });
    if (target === null) return;
    const name = oldPath.split("/").pop()!;
    const newPath = target.trim()
      ? `${target.trim().replace(/\/+$/, "")}/${name}`
      : name;
    if (newPath === oldPath) return;
    if (!(await askConfirm({ title: "移动文件夹", message: `将「${oldPath}」移动到「${newPath}」？\n其下所有文档与子文件夹会同步移动。`, confirmText: "移动" }))) return;
    const res = await api<{ moved: number }>("/api/kb/folders", {
      method: "PATCH",
      body: JSON.stringify({ old_path: oldPath, new_path: newPath, scope }),
    });
    setToast(`已移动（${res.moved} 篇文档同步）`);
    setFolder(newPath);
    load();
  }

  async function deleteFolder(path: string) {
    if (!(await askConfirm({ title: "删除文件夹", message: `删除空文件夹「${path}」？`, danger: true, confirmText: "删除" }))) return;
    try {
      await api("/api/kb/folders", {
        method: "DELETE",
        body: JSON.stringify({ path, scope }),
      });
      if (folder === path) setFolder(null);
      setToast("文件夹已删除");
      load();
    } catch (e) {
      setToast(e instanceof Error ? e.message : "删除失败");
    }
  }

  async function moveFolderSort(path: string, direction: "up" | "down") {
    await api("/api/kb/folders/sort", {
      method: "POST",
      body: JSON.stringify({ path, direction, scope }),
    });
    load();
  }

  async function initWorkflow() {
    const res = await api<{ created_folders: number; created_docs: number }>(
      "/api/kb/init-workflow",
      { method: "POST", body: JSON.stringify({ scope }) }
    );
    setToast(`工作流已就绪：新建 ${res.created_folders} 个文件夹、${res.created_docs} 个台账页`);
    load();
  }

  async function ingestInbox() {
    if (!(await askConfirm({ title: "AI 整理收件箱", message: "AI 将整理收件箱：自动分类移入「原始资料」、登记来源台账、高价值内容编译为知识页。继续？", confirmText: "开始整理" }))) return;
    setConnecting(true);
    setToast("AI 整理中，请稍候…");
    try {
      const res = await api<{ processed: number; report: { title: string; category: string; compiled_page: string | null }[] }>(
        "/api/kb/ingest",
        { method: "POST", body: JSON.stringify({ scope }) }
      );
      if (res.processed === 0) {
        setToast("收件箱是空的：先把新资料上传或移动到「收件箱」文件夹");
      } else {
        const compiled = res.report.filter((r) => r.compiled_page).length;
        setToast(`已整理 ${res.processed} 篇（编译 ${compiled} 篇进知识库）`);
      }
      await load();
    } catch (e) {
      setToast(e instanceof Error ? e.message : "整理失败");
    } finally {
      setConnecting(false);
    }
  }

  async function renameDoc(d: KbDocInfo) {
    const name = await askPrompt({ title: "重命名文档", defaultValue: d.title });
    if (!name?.trim() || name.trim() === d.title) return;
    await api(`/api/kb/docs/${d.id}`, {
      method: "PUT",
      body: JSON.stringify({ title: name.trim() }),
    });
    load();
  }

  async function remove(d: KbDocInfo) {
    if (!(await askConfirm({ title: "删除文档", message: `删除「${d.title}」？`, danger: true, confirmText: "删除" }))) return;
    await api(`/api/kb/docs/${d.id}`, { method: "DELETE" });
    if (selected === d.id) setSelected(null);
    load();
  }

  return (
    <div className="flex h-full min-h-0">
      {/* 左：目录树（可整体收起） */}
      {!treeHidden && (
      <div className="w-60 shrink-0 overflow-y-auto border-r border-border-subtle px-3 py-4">
        {/* 图谱入口：个人/团队分开，跳转全屏页 */}
        <div className="mb-2 grid grid-cols-2 gap-1.5">
          {(["personal", "team"] as const).map((s) => (
            <button
              key={s}
              onClick={() => router.push(`/kb/graph?scope=${s}`)}
              className="rounded-lg border border-border-subtle px-2 py-1.5 text-[11.5px] text-muted-foreground transition-colors hover:border-accent/50 hover:text-foreground"
              title={`${s === "personal" ? "个人" : "团队"}知识图谱（全屏）`}
            >
              🕸 {s === "personal" ? "个人图谱" : "团队图谱"}
            </button>
          ))}
        </div>

        <div className="mt-3 flex items-center justify-between px-2 pb-1">
          <span className="text-[11.5px] font-medium uppercase tracking-wider text-muted-foreground/70">
            文件夹
          </span>
          <div className="flex items-center gap-1">
            <button
              onClick={createFolder}
              title="新建文件夹"
              className="rounded p-0.5 text-muted-foreground/70 hover:text-foreground"
            >
              ＋
            </button>
            <button
              onClick={toggleTree}
              title="收起目录树"
              className="rounded p-0.5 text-muted-foreground/70 hover:text-foreground"
            >
              «
            </button>
          </div>
        </div>
        <button
          onClick={() => setFolder(null)}
          className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-[6px] text-left text-[13px] ${
            folder === null ? "bg-background font-medium shadow-[0_1px_2px_rgba(0,0,0,0.04)]" : "text-muted-foreground hover:bg-background/60"
          }`}
        >
          📁 全部（{docs.length}）
        </button>

        {hasWorkflow ? (
          <>
            {/* 骨架四区：收件箱 / 原始资料 / 知识库 / 来源台账（锁定不可删改，可展开收起） */}
            {SKELETON_ZONES.map(([zone, icon]) => {
              const children = childMap.get(zone) ?? [];
              if (children.length === 0 && !folders.includes(zone)) return null;
              const isCollapsed = collapsed.has(zone);
              return (
                <div key={zone} className="mt-1.5">
                  <div
                    className={`group flex items-center rounded-lg ${
                      folder === zone ? "bg-background shadow-[0_1px_2px_rgba(0,0,0,0.04)]" : "hover:bg-background/60"
                    }`}
                  >
                    {children.length > 0 ? (
                      <button
                        onClick={() => toggleCollapse(zone)}
                        title={isCollapsed ? "展开" : "收起"}
                        className="ml-1.5 shrink-0 rounded p-0.5 text-[10px] text-muted-foreground/70 transition-colors hover:bg-muted hover:text-foreground"
                      >
                        {isCollapsed ? "▸" : "▾"}
                      </button>
                    ) : (
                      <span className="w-[19px] shrink-0" />
                    )}
                    <button
                      onClick={() => setFolder(folder === zone ? null : zone)}
                      className={`flex min-w-0 flex-1 items-center gap-1.5 truncate px-1.5 py-[6px] text-left text-[13px] font-medium ${
                        folder === zone ? "" : "text-foreground/85"
                      }`}
                    >
                      <span>{icon}</span>
                      <span className="min-w-0 flex-1 truncate">{zone}</span>
                      {zone === "收件箱" && inboxCount > 0 && (
                        <span
                          title={`${inboxCount} 篇待整理`}
                          className="rounded-full bg-accent px-1.5 py-px text-[10px] font-semibold text-accent-foreground"
                        >
                          {inboxCount}
                        </span>
                      )}
                      {zone === "收件箱" && inboxCount === 0 && (
                        <span className="text-[10px] text-muted-foreground/50">空</span>
                      )}
                    </button>
                  </div>
                  {!isCollapsed &&
                    children.map((f) => (
                      <FolderNode
                        key={f}
                        f={f}
                        childMap={childMap}
                        collapsed={collapsed}
                        onToggle={toggleCollapse}
                        folder={folder}
                        onSelect={setFolder}
                        onMenu={setMenuFolder}
                        menuFolder={menuFolder}
                        actions={{
                          rename: renameFolder,
                          move: moveFolder,
                          sort: moveFolderSort,
                          del: deleteFolder,
                        }}
                      />
                    ))}
                </div>
              );
            })}

            {/* 未归类：旧文件夹与散目录，可自由整理 */}
            {looseFolders.length > 0 && (
              <div className="mt-3 border-t border-border-subtle pt-2">
                <div className="px-2 pb-1 text-[10.5px] font-medium uppercase tracking-wider text-muted-foreground/60">
                  未归类
                </div>
                {looseFolders
                  .filter((f) => {
                    const parent = f.includes("/") ? f.slice(0, f.lastIndexOf("/")) : "";
                    return !looseFolders.includes(parent);
                  })
                  .map((f) => (
                    <FolderNode
                      key={f}
                      f={f}
                      childMap={childMap}
                      collapsed={collapsed}
                      onToggle={toggleCollapse}
                      folder={folder}
                      onSelect={setFolder}
                      onMenu={setMenuFolder}
                      menuFolder={menuFolder}
                      actions={{
                        rename: renameFolder,
                        move: moveFolder,
                        sort: moveFolderSort,
                        del: deleteFolder,
                      }}
                    />
                  ))}
              </div>
            )}
          </>
        ) : (
          /* 未初始化工作流：保持自由树（根级递归） */
          (childMap.get("") ?? []).map((f) => (
            <FolderNode
              key={f}
              f={f}
              childMap={childMap}
              collapsed={collapsed}
              onToggle={toggleCollapse}
              folder={folder}
              onSelect={setFolder}
              onMenu={setMenuFolder}
              menuFolder={menuFolder}
              actions={{
                rename: renameFolder,
                move: moveFolder,
                sort: moveFolderSort,
                del: deleteFolder,
              }}
            />
          ))
        )}
        {folders.length === 0 && (
          <div className="px-2.5 py-2 text-xs text-muted-foreground/60">点击 ＋ 新建文件夹</div>
        )}
      </div>
      )}

      {/* 收起态：窄条展开入口 */}
      {treeHidden && (
        <div className="flex w-10 shrink-0 flex-col items-center gap-2.5 border-r border-border-subtle bg-muted/40 py-3">
          <button
            onClick={toggleTree}
            title="展开目录树"
            className="rounded-lg p-1.5 text-muted-foreground transition-colors hover:bg-background hover:text-foreground"
          >
            »
          </button>
          <button
            onClick={() => router.push(`/kb/graph?scope=personal`)}
            title="个人图谱"
            className="rounded-lg p-1.5 text-[14px] transition-colors hover:bg-background"
          >
            🕸
          </button>
        </div>
      )}

      {/* 右：列表 / 详情 */}
      <div className="min-w-0 flex-1 overflow-y-auto">
        {selected !== null ? (
          <KbDetail
            docId={selected}
            onBack={() => setSelected(null)}
            onOpenDoc={(id) => setSelected(id)}
            onChanged={load}
          />
        ) : (
          <div>
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
                    {s === "personal" ? "个人库" : "团队库"}
                  </button>
                ))}
              </div>
              <input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="搜索标题与全文…"
                className="w-52 rounded-lg border border-border-subtle bg-background px-3 py-1.5 text-[13px] outline-none focus:border-accent"
              />
              <select
                value={sortKey}
                onChange={(e) => setSortKey(e.target.value as typeof sortKey)}
                title="文件排序"
                className="rounded-lg border border-border-subtle bg-background px-2 py-1.5 text-[12.5px] text-muted-foreground outline-none"
              >
                <option value="updated">最近更新</option>
                <option value="title">标题</option>
                <option value="size">大小</option>
                <option value="chunks">知识块</option>
              </select>
              <div className="flex-1" />
              <button
                onClick={() => setGuideOpen(true)}
                title="操作指引"
                className="rounded-lg border border-border-subtle px-3 py-1.5 text-[12.5px] text-muted-foreground hover:text-foreground"
              >
                操作指引
              </button>
              <button
                onClick={initWorkflow}
                title="创建标准知识工作流目录（收件箱/原始资料/来源台账/知识库，中文命名，幂等）"
                className="rounded-lg border border-border-subtle px-3 py-1.5 text-[12.5px] text-muted-foreground hover:text-foreground"
              >
                初始化工作流
              </button>
              <button
                onClick={ingestInbox}
                disabled={connecting}
                title="AI 一键整理收件箱：分类移动 + 登记台账 + 编译知识页"
                className="rounded-lg border border-accent/50 bg-accent-soft px-3 py-1.5 text-[12.5px] font-medium text-accent hover:opacity-80 disabled:opacity-60"
              >
                {connecting ? "整理中…" : "✨ 整理收件箱"}
              </button>
              <button
                onClick={() => setCreateOpen(true)}
                className="rounded-lg border border-border-subtle px-3 py-1.5 text-[12.5px] text-muted-foreground hover:text-foreground"
              >
                新建笔记
              </button>
              <button
                onClick={() => setUploadOpen(true)}
                className="rounded-lg border border-border-subtle px-3 py-1.5 text-[12.5px] text-muted-foreground hover:text-foreground"
              >
                上传
              </button>
              <button
                onClick={connectLocal}
                disabled={connecting}
                className="rounded-lg border border-border-subtle px-3 py-1.5 text-[12.5px] text-muted-foreground hover:text-foreground disabled:opacity-60"
                title="浏览器直读本地文件夹，增量同步新笔记"
              >
                {connecting ? "连接中…" : "连接本地"}
              </button>
            </div>

            {toast && (
              <div className="mx-6 mt-3 rounded-lg border border-border-subtle bg-surface px-3.5 py-2 text-[13px] text-muted-foreground">
                {toast}
              </div>
            )}

            {/* 文件列表 */}
            <div className="px-6 py-4">
              {loading ? (
                <div className="py-12 text-center text-sm text-muted-foreground">加载中…</div>
              ) : visible.length === 0 ? (
                <div className="rounded-xl border border-dashed border-border-subtle px-4 py-14 text-center text-[13px] text-muted-foreground/70">
                  {docs.length === 0 ? "库是空的：点击「上传」导入 Obsidian 库，或「新建笔记」开始" : "没有匹配的文档"}
                </div>
              ) : (
                <table className="w-full border-collapse text-[13px]">
                  <thead>
                    <tr className="border-b border-border-subtle text-left text-[12px] text-muted-foreground">
                      <th className="py-2 pr-3 font-medium">标题</th>
                      <th className="w-36 py-2 pr-3 font-medium">入库状态</th>
                      <th className="w-20 py-2 pr-3 font-medium">大小</th>
                      <th className="w-24 py-2 pr-3 font-medium">更新时间</th>
                      <th className="w-16 py-2 pr-3 font-medium">知识块</th>
                      <th className="w-24 py-2 pr-3 font-medium">标签</th>
                      <th className="w-16 py-2 font-medium" />
                    </tr>
                  </thead>
                  <tbody>
                    {visible.map((d) => (
                      <tr
                        key={d.id}
                        className="group cursor-pointer border-b border-border-subtle/60 hover:bg-muted/40"
                        onClick={() => setSelected(d.id)}
                      >
                        <td className="py-2.5 pr-3">
                          <div className="truncate font-medium">{d.title}</div>
                          <div className="truncate text-[11.5px] text-muted-foreground/70">{d.path}</div>
                        </td>
                        <td className="py-2.5 pr-3">
                          <div className="flex flex-wrap gap-1">
                            <span className={`rounded px-1.5 py-0.5 text-[10.5px] ${d.status === "ready" ? "bg-accent-soft text-accent" : "bg-muted text-muted-foreground"}`}>
                              个人{d.status === "ready" ? "✓" : "·草稿"}
                            </span>
                            {d.scope === "team" || d.team_status === "approved" ? (
                              <span className="rounded bg-accent-soft px-1.5 py-0.5 text-[10.5px] text-accent">团队✓</span>
                            ) : d.team_status === "pending" ? (
                              <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[10.5px] text-amber-700 dark:bg-amber-900/40 dark:text-amber-400">团队·审核中</span>
                            ) : d.team_status === "rejected" ? (
                              <span className="rounded bg-red-100 px-1.5 py-0.5 text-[10.5px] text-red-600 dark:bg-red-900/40 dark:text-red-400">团队·已驳回</span>
                            ) : (
                              <span className="rounded bg-muted px-1.5 py-0.5 text-[10.5px] text-muted-foreground">团队·未申请</span>
                            )}
                          </div>
                        </td>
                        <td className="py-2.5 pr-3 text-muted-foreground">{fmtSize(d.size)}</td>
                        <td className="py-2.5 pr-3 text-muted-foreground">{fmtTime(d.updated_at ?? d.created_at)}</td>
                        <td className="py-2.5 pr-3 text-muted-foreground">{d.chunk_count}</td>
                        <td className="py-2.5 pr-3">
                          <div className="flex gap-1 overflow-hidden">
                            {d.tags.slice(0, 2).map((t) => (
                              <span key={t} className="shrink-0 rounded-full bg-muted px-1.5 py-0.5 text-[10.5px] text-muted-foreground">#{t}</span>
                            ))}
                          </div>
                        </td>
                        <td className="py-2.5 text-right">
                          <div className="hidden items-center justify-end gap-0.5 group-hover:flex">
                            <button
                              onClick={(e) => { e.stopPropagation(); renameDoc(d); }}
                              className="rounded p-1 text-[11px] text-muted-foreground/50 hover:text-foreground"
                              title="重命名"
                            >
                              ✎
                            </button>
                            <button
                              onClick={(e) => { e.stopPropagation(); remove(d); }}
                              className="rounded p-1 text-[11px] text-muted-foreground/50 hover:text-red-500"
                              title="删除"
                            >
                              🗑
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>
        )}
      </div>

      {/* 上传弹窗 */}
      {uploadOpen && (
        <UploadDialog
          isAdmin={!!isAdmin}
          defaultScope={scope}
          folders={folders}
          onClose={() => setUploadOpen(false)}
          onUpload={upload}
        />
      )}

      {/* 使用指引：首次进入自动弹出，之后可从工具栏 ? 再次查看 */}
      {guideOpen && <GuideDialog onClose={() => setGuideOpen(false)} />}

      {/* 新建笔记弹窗 */}
      {createOpen && (
        <CreateDialog
          defaultFolder={folder ?? ""}
          defaultScope={scope}
          isAdmin={!!isAdmin}
          onClose={() => setCreateOpen(false)}
          onCreate={createDoc}
        />
      )}
    </div>
  );
}

function UploadDialog({
  isAdmin,
  defaultScope,
  folders,
  onClose,
  onUpload,
}: {
  isAdmin: boolean;
  defaultScope: string;
  folders: string[];
  onClose: () => void;
  onUpload: (files: FileList | File[], scope: string, target: string) => void;
}) {
  const [scope, setScope] = useState(defaultScope === "team" && !isAdmin ? "personal" : defaultScope);
  const [target, setTarget] = useState("收件箱");
  const ref = useRef<HTMLInputElement>(null);
  const input = "w-full rounded-lg border border-border-subtle bg-background px-3 py-2 text-[13.5px] outline-none focus:border-accent";
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-8" onClick={onClose}>
      <div className="w-full max-w-md rounded-2xl border border-border-subtle bg-surface p-6 shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-[15px] font-semibold">上传到知识库</h3>
        {isAdmin && (
          <div className="mt-3 flex gap-4 text-[13px]">
            <label className="flex cursor-pointer items-center gap-1.5">
              <input type="radio" checked={scope === "team"} onChange={() => setScope("team")} />团队库
            </label>
            <label className="flex cursor-pointer items-center gap-1.5">
              <input type="radio" checked={scope === "personal"} onChange={() => setScope("personal")} />个人库
            </label>
          </div>
        )}
        <div className="mt-3">
          <label className="mb-1 block text-[12.5px] font-medium">上传到（新资料建议进「收件箱」，由 AI 整理归类）</label>
          <input
            list="kb-folder-list"
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            placeholder="收件箱"
            className={input}
          />
          <datalist id="kb-folder-list">
            {folders.map((f) => <option key={f} value={f} />)}
          </datalist>
        </div>
        <div
          className="mt-4 rounded-xl border border-dashed border-border-subtle px-6 py-10 text-center transition-colors hover:border-accent/60"
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => { e.preventDefault(); onUpload(e.dataTransfer.files, scope, target); }}
        >
          <div className="text-[13.5px]">拖拽文件到此处，或</div>
          <button
            onClick={() => ref.current?.click()}
            className="mt-2 rounded-lg bg-accent px-4 py-1.5 text-[13px] font-medium text-accent-foreground hover:opacity-90"
          >
            选择本地文件
          </button>
          <div className="mt-2 text-xs text-muted-foreground/70">支持 .md / .txt / .pdf / .docx / .pptx / .doc / .xls / .zip（Obsidian 整库，自动忽略 .obsidian；扫描 PDF 自动视觉识别）</div>
          <input ref={ref} type="file" multiple accept=".md,.markdown,.txt,.pdf,.docx,.pptx,.doc,.xls,.zip" className="hidden" onChange={(e) => e.target.files && onUpload(e.target.files, scope, target)} />
        </div>
        <button onClick={onClose} className="mt-4 w-full rounded-lg border border-border-subtle py-2 text-[13px] text-muted-foreground hover:text-foreground">取消</button>
      </div>
    </div>
  );
}

function CreateDialog({
  defaultFolder,
  defaultScope,
  isAdmin,
  onClose,
  onCreate,
}: {
  defaultFolder: string;
  defaultScope: string;
  isAdmin: boolean;
  onClose: () => void;
  onCreate: (title: string, folder: string, scope: string) => void;
}) {
  const [title, setTitle] = useState("");
  const [folder, setFolder] = useState(defaultFolder);
  const [scope, setScope] = useState(defaultScope === "team" && !isAdmin ? "personal" : defaultScope);
  const input = "w-full rounded-lg border border-border-subtle bg-background px-3 py-2 text-[13.5px] outline-none focus:border-accent";
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-8" onClick={onClose}>
      <div className="w-full max-w-sm rounded-2xl border border-border-subtle bg-surface p-6 shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-[15px] font-semibold">新建笔记</h3>
        <div className="mt-4 space-y-3">
          <div>
            <label className="mb-1 block text-[12.5px] font-medium">标题</label>
            <input className={input} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="笔记标题" autoFocus />
          </div>
          <div>
            <label className="mb-1 block text-[12.5px] font-medium">文件夹（可选）</label>
            <input className={input} value={folder} onChange={(e) => setFolder(e.target.value)} placeholder="如：客服规范" />
          </div>
          {isAdmin && (
            <div className="flex gap-4 text-[13px]">
              <span className="text-muted-foreground">保存到：</span>
              <label className="flex cursor-pointer items-center gap-1.5">
                <input type="radio" checked={scope === "team"} onChange={() => setScope("team")} />团队库
              </label>
              <label className="flex cursor-pointer items-center gap-1.5">
                <input type="radio" checked={scope === "personal"} onChange={() => setScope("personal")} />个人库
              </label>
            </div>
          )}
        </div>
        <div className="mt-5 flex gap-2.5">
          <button
            onClick={() => title.trim() && onCreate(title.trim(), folder.trim(), scope)}
            disabled={!title.trim()}
            className="flex-1 rounded-lg bg-accent py-2 text-[13.5px] font-medium text-accent-foreground hover:opacity-90 disabled:opacity-50"
          >
            创建
          </button>
          <button onClick={onClose} className="flex-1 rounded-lg border border-border-subtle py-2 text-[13.5px] text-muted-foreground hover:text-foreground">取消</button>
        </div>
      </div>
    </div>
  );
}

/** 知识库使用指引（首次进入自动弹出） */
function GuideDialog({ onClose }: { onClose: () => void }) {
  const steps: [string, string, string][] = [
    ["📥", "收集：一切先进收件箱", "文章、PDF、会议记录、网页剪藏…上传时默认进「收件箱」，不必先想好分类。zip 整包（如 Obsidian 库）保留原目录结构。"],
    ["🤖", "整理：点「✨ 整理收件箱」", "AI 按你当前的文件夹结构动态归类（结构变了归类跟着变），自动移动到「原始资料」下、登记来源台账；高价值内容自动编译成知识页（可反复复用）。"],
    ["🗂️", "沉淀：原始资料 → 知识库", "「原始资料」存证据（按地区/来源等任意层级细分，可自由增删改移）；「知识库」放整理后的可复用知识：概念、常见问题、综合分析、项目、主题。"],
    ["🔍", "使用：对话挂知识库 + 双链图谱", "首页对话开启「知识库」开关即可带引用问答；[[双链]] 和 🕸 图谱展示知识关联；个人库可申请入团队库（AI 预审 + 管理员终审）。"],
  ];
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto bg-black/50 p-6" onClick={onClose}>
      <div className="my-8 w-full max-w-lg rounded-2xl border border-border-subtle bg-surface p-6 shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-[16px] font-semibold">知识库使用指引</h3>
        <p className="mt-1 text-[12.5px] text-muted-foreground">
          资料不是收藏夹，而是可持续维护的知识系统：收集 → 整理 → 沉淀 → 使用
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
        <div className="mt-3 rounded-xl bg-muted/60 px-4 py-3 text-[12.5px] leading-relaxed text-muted-foreground">
          四个一级分区（收件箱 / 原始资料 / 知识库 / 来源台账）为固定结构；其下子层级完全自由——
          你可以按「地区 + 来源」等自己的工作维度任意建立多级文件夹，AI 整理时会参考你的目录结构。
        </div>
        <button
          onClick={() => { localStorage.setItem("kb-guide-seen", "1"); onClose(); }}
          className="mt-4 w-full rounded-lg bg-accent py-2 text-[13.5px] font-medium text-accent-foreground hover:opacity-90"
        >
          开始使用
        </button>
      </div>
    </div>
  );
}

/** 自由文件夹节点（骨架区之外的文件夹，带完整操作菜单） */
function FolderNode({
  f,
  childMap,
  collapsed,
  onToggle,
  folder,
  onSelect,
  onMenu,
  menuFolder,
  actions,
}: {
  f: string;
  childMap: Map<string, string[]>;
  collapsed: Set<string>;
  onToggle: (p: string) => void;
  folder: string | null;
  onSelect: (f: string | null) => void;
  onMenu: (f: string | null) => void;
  menuFolder: string | null;
  actions: {
    rename: (f: string) => void;
    move: (f: string) => void;
    sort: (f: string, dir: "up" | "down") => void;
    del: (f: string) => void;
  };
}) {
  const children = childMap.get(f) ?? [];
  const isCollapsed = collapsed.has(f);
  const depth = f.split("/").length - 1;
  return (
    <div>
      <div
        className={`group flex items-center rounded-lg ${
          folder === f ? "bg-background shadow-[0_1px_2px_rgba(0,0,0,0.04)]" : "hover:bg-background/60"
        }`}
        style={{ paddingLeft: `${depth * 14}px` }}
      >
        {children.length > 0 ? (
          <button
            onClick={() => onToggle(f)}
            title={isCollapsed ? "展开" : "收起"}
            className="shrink-0 rounded p-0.5 text-[10px] text-muted-foreground/70 transition-colors hover:bg-muted hover:text-foreground"
          >
            {isCollapsed ? "▸" : "▾"}
          </button>
        ) : (
          <span className="w-[15px] shrink-0" />
        )}
        <button
          onClick={() => onSelect(folder === f ? null : f)}
          className={`min-w-0 flex-1 truncate px-1.5 py-[6px] text-left text-[13px] ${
            folder === f ? "font-medium" : "text-muted-foreground"
          }`}
          title={f}
        >
          📁 {f.split("/").pop()}
          {children.length > 0 && (
            <span className="ml-1.5 text-[10px] text-muted-foreground/50">{children.length}</span>
          )}
        </button>
        <div className="relative mr-1 hidden group-hover:block">
          <button
            onClick={() => onMenu(menuFolder === f ? null : f)}
            title="文件夹操作"
            className="rounded px-1.5 py-0.5 text-[13px] leading-none text-muted-foreground/70 hover:bg-muted hover:text-foreground"
          >
            ⋯
          </button>
          {menuFolder === f && (
            <>
              <div className="fixed inset-0 z-30" onClick={() => onMenu(null)} />
              <div className="absolute right-0 top-7 z-40 w-36 overflow-hidden rounded-xl border border-border-subtle bg-surface py-1 shadow-[0_8px_30px_rgba(0,0,0,0.15)]">
                {([
                  ["✏️ 重命名", () => actions.rename(f)],
                  ["📁 移动到…", () => actions.move(f)],
                  ["⬆️ 上移", () => actions.sort(f, "up")],
                  ["⬇️ 下移", () => actions.sort(f, "down")],
                  ["🗑️ 删除", () => actions.del(f)],
                ] as [string, () => void][]).map(([label, fn]) => (
                  <button
                    key={label}
                    onClick={() => { onMenu(null); fn(); }}
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
      {!isCollapsed &&
        children.map((c) => (
          <FolderNode
            key={c}
            f={c}
            childMap={childMap}
            collapsed={collapsed}
            onToggle={onToggle}
            folder={folder}
            onSelect={onSelect}
            onMenu={onMenu}
            menuFolder={menuFolder}
            actions={actions}
          />
        ))}
    </div>
  );
}
