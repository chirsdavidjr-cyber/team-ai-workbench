"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import type { DatasetInfo } from "@/lib/types";
import { DataTreePanel } from "@/components/data-folder-tree";
import { DataDetail } from "@/components/data-detail";
import { GlobalAskPanel } from "@/components/global-ask-panel";
import { IconDatabase, IconPlus, IconWand } from "@/components/icons";
import { askConfirm, askPrompt } from "@/components/dialog";

function fmtTime(s: string | null): string {
  if (!s) return "";
  const d = new Date(s);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export default function DataPage() {
  const { user } = useAuth();
  const [scope, setScope] = useState<"team" | "personal">("personal");
  const [q, setQ] = useState("");
  const [sortKey, setSortKey] = useState<"created" | "name" | "rows">("created");
  const [folder, setFolder] = useState<string | null>(null);
  const [datasets, setDatasets] = useState<DatasetInfo[]>([]);
  const [folderRows, setFolderRows] = useState<{ path: string; sort: number }[]>([]);
  const [loading, setLoading] = useState(true);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [globalAskOpen, setGlobalAskOpen] = useState(false);
  const [droppedFile, setDroppedFile] = useState<File | null>(null);
  const [menuDs, setMenuDs] = useState<number | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const isAdmin = user?.role === "admin";

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [d, f] = await Promise.all([
        api<DatasetInfo[]>(`/api/data/datasets?scope=${scope}`),
        api<{ path: string; sort: number }[]>(`/api/data/folders?scope=${scope}`),
      ]);
      setDatasets(d);
      setFolderRows(f);
    } finally {
      setLoading(false);
    }
  }, [scope]);

  useEffect(() => {
    load();
    setFolder(null);
  }, [load]);

  // 从详情页左侧树跳转：/data?scope=team&folder=湖北/月报；深链打开数据集：/data?open=3
  useEffect(() => {
    const p = new URLSearchParams(window.location.search);
    const s = p.get("scope");
    if (s === "team" || s === "personal") setScope(s);
    const f = p.get("folder");
    if (f !== null) setFolder(f || null);
    const o = p.get("open");
    if (o) setSelected(Number(o));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!menuDs) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setMenuDs(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menuDs]);

  // 分组树：数据集 folder 前缀聚合 ∪ 占位（与知识库同构）
  const folders = useMemo(() => {
    const set = new Set<string>(folderRows.map((f) => f.path));
    for (const d of datasets) {
      if (!d.folder) continue;
      const parts = d.folder.split("/");
      for (let i = 1; i <= parts.length; i++) set.add(parts.slice(0, i).join("/"));
    }
    return [...set];
  }, [datasets, folderRows]);

  const visible = useMemo(() => {
    const list = datasets.filter((d) => {
      if (folder === null) return true;
      return d.folder === folder || d.folder.startsWith(folder + "/");
    });
    const kw = q.trim().toLowerCase();
    const filtered = kw
      ? list.filter(
          (d) => d.name.toLowerCase().includes(kw) || d.filename.toLowerCase().includes(kw)
        )
      : list;
    const sorted = [...filtered];
    if (sortKey === "name") sorted.sort((a, b) => a.name.localeCompare(b.name, "zh"));
    else if (sortKey === "rows") sorted.sort((a, b) => b.row_count - a.row_count);
    return sorted;
  }, [datasets, folder, q, sortKey]);

  async function remove(ds: DatasetInfo) {
    if (!(await askConfirm({ title: "删除数据集", message: `删除数据集「${ds.name}」？其问数记录将一并删除，不可恢复。`, danger: true, confirmText: "删除" }))) return;
    try {
      await api(`/api/data/datasets/${ds.id}`, { method: "DELETE" });
      await load();
    } catch (e) {
      window.alert(e instanceof Error ? e.message : "删除失败");
    }
  }

  async function moveDs(ds: DatasetInfo) {
    const target = await askPrompt({ title: "移动分组", message: "留空 = 未分组，可用 / 分层级", defaultValue: ds.folder, allowEmpty: true });
    if (target === null) return;
    try {
      await api(`/api/data/datasets/${ds.id}/move`, {
        method: "PATCH",
        body: JSON.stringify({ folder: target.trim() }),
      });
      await load();
    } catch (e) {
      window.alert(e instanceof Error ? e.message : "移动失败");
    }
  }

  return (
    <div
      className="flex h-full min-h-0"
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        e.preventDefault();
        const f = e.dataTransfer.files?.[0];
        if (f) {
          setDroppedFile(f);
          setUploadOpen(true);
        }
      }}
    >
      {/* 左：分组树 */}
      <DataTreePanel
        scope={scope}
        folders={folders}
        active={folder}
        datasetsCount={datasets.length}
        onSelect={setFolder}
        onChanged={load}
      />

      {/* 右：卡片列表 ⇄ 数据集详情（与知识库同构：单页原地切换） */}
      <div className="min-w-0 flex-1 overflow-y-auto">
        {selected !== null ? (
          <DataDetail
            dsId={selected}
            onBack={() => setSelected(null)}
            onChanged={load}
          />
        ) : (
        <>
        <div className="sticky top-0 z-10 flex flex-wrap items-center gap-2 border-b border-border-subtle bg-background/95 px-6 py-3 backdrop-blur">
          <div className="flex gap-1 rounded-lg bg-muted p-1">
            {(["personal", "team"] as const).map((s) => (
              <button
                key={s}
                onClick={() => setScope(s)}
                className={`rounded-md px-3.5 py-1 text-[12.5px] transition-colors ${
                  scope === s ? "bg-background font-medium shadow-[0_1px_2px_rgba(0,0,0,0.05)]" : "text-muted-foreground"
                }`}
              >
                {s === "personal" ? "我的数据" : "团队数据"}
              </button>
            ))}
          </div>
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="搜索数据集…"
            className="min-w-[160px] flex-1 rounded-lg border border-border-subtle bg-surface px-3 py-1.5 text-[13px] outline-none focus:border-accent"
          />
          <select
            value={sortKey}
            onChange={(e) => setSortKey(e.target.value as "created" | "name" | "rows")}
            className="rounded-lg border border-border-subtle bg-surface px-2.5 py-1.5 text-[12.5px] text-muted-foreground outline-none"
          >
            <option value="created">最近上传</option>
            <option value="name">名称</option>
            <option value="rows">数据量</option>
          </select>
          <button
            onClick={() => setGlobalAskOpen(true)}
            title="不选表直接提问，AI 自动选表作答（可跨表）"
            className="flex shrink-0 items-center gap-1.5 rounded-lg border border-border-subtle bg-surface px-3.5 py-1.5 text-[13px] font-medium text-muted-foreground transition-colors hover:border-accent/50 hover:text-accent"
          >
            <IconWand width={14} height={14} />
            全局问数
          </button>
          <button
            onClick={() => setUploadOpen(true)}
            title="上传表格 / 粘贴 CSV / 导入语义层文件"
            className="flex shrink-0 items-center gap-1.5 rounded-lg bg-accent px-3.5 py-1.5 text-[13px] font-medium text-accent-foreground transition-opacity hover:opacity-90"
          >
            <IconPlus width={14} height={14} />
            接入数据
          </button>
        </div>

        <div className="px-6 py-5">
          {folder !== null && (
            <div className="mb-3 flex items-center gap-2 text-[12.5px] text-muted-foreground">
              <button onClick={() => setFolder(null)} className="hover:text-accent">全部数据集</button>
              {folder.split("/").map((seg, i, arr) => (
                <span key={i} className="flex items-center gap-2">
                  / <span className={i === arr.length - 1 ? "font-medium text-foreground" : ""}>{seg}</span>
                </span>
              ))}
            </div>
          )}

          {loading ? (
            <div className="py-14 text-center text-sm text-muted-foreground">加载中…</div>
          ) : visible.length === 0 ? (
            <div className="rounded-xl border border-dashed border-border-subtle px-4 py-14 text-center">
              <IconDatabase className="mx-auto text-muted-foreground/40" width={22} height={22} />
              <div className="mt-2 text-[13px] text-muted-foreground/80">
                {datasets.length === 0
                  ? "还没有数据集，点右上「上传数据」导入 Excel/CSV 开始问数"
                  : "当前分组没有数据集"}
              </div>
            </div>
          ) : (
            <div className="overflow-hidden rounded-xl border border-border-subtle">
              <div className="hidden grid-cols-[minmax(0,1fr)_84px_140px_120px_100px_120px_84px] gap-3 border-b border-border-subtle bg-muted/60 px-4 py-2 text-[11.5px] font-medium text-muted-foreground md:grid">
                <span>数据集</span>
                <span>范围</span>
                <span>分组</span>
                <span>规模</span>
                <span>语义层</span>
                <span>上传日期</span>
                <span className="text-right">操作</span>
              </div>
              {visible.map((d) => {
                const semCount = Object.keys(d.semantic?.columns ?? {}).length;
                const semTotal = d.profile.length;
                return (
                  <div
                    key={d.id}
                    className="group grid cursor-pointer grid-cols-[minmax(0,1fr)_auto] items-center gap-3 border-b border-border-subtle px-4 py-3 transition-colors last:border-0 hover:bg-muted/40 md:grid-cols-[minmax(0,1fr)_84px_140px_120px_100px_120px_84px]"
                    onClick={() => setSelected(d.id)}
                  >
                    <div className="min-w-0">
                      <div className="truncate text-[13.5px] font-medium group-hover:text-accent">{d.name}</div>
                      <div className="mt-0.5 truncate text-[11px] text-muted-foreground/60" title={d.filename}>
                        {d.filename}
                      </div>
                    </div>
                    <span
                      className={`justify-self-end rounded-full px-2 py-0.5 text-[10.5px] md:justify-self-start ${
                        d.scope === "team" ? "bg-accent-soft text-accent" : "bg-muted text-muted-foreground"
                      }`}
                    >
                      {d.scope === "team" ? "团队" : "个人"}
                    </span>
                    <span className="hidden truncate text-[12px] text-muted-foreground md:block" title={d.folder || "未分组"}>
                      {d.folder || "未分组"}
                    </span>
                    <span className="hidden text-[12px] text-muted-foreground md:block">
                      {d.row_count} 行 × {semTotal} 列
                    </span>
                    <span className={`hidden text-[12px] md:block ${semCount >= semTotal ? "text-emerald-600 dark:text-emerald-400" : semCount === 0 ? "text-muted-foreground/60" : "text-amber-600 dark:text-amber-400"}`}>
                      {semCount}/{semTotal}
                    </span>
                    <span className="hidden text-[12px] text-muted-foreground md:block">{fmtTime(d.created_at)}</span>
                    <div className="relative hidden justify-self-end md:block">
                      <button
                        onClick={(e) => { e.stopPropagation(); setMenuDs(menuDs === d.id ? null : d.id); }}
                        title="数据集操作"
                        className="rounded-md border border-border-subtle px-2 py-1 text-[11.5px] text-muted-foreground opacity-0 transition-opacity hover:text-accent group-hover:opacity-100"
                      >
                        ⋯
                      </button>
                      {menuDs === d.id && (
                        <>
                          <div className="fixed inset-0 z-30" onClick={(e) => { e.stopPropagation(); setMenuDs(null); }} />
                          <div className="absolute right-0 top-8 z-40 w-40 overflow-hidden rounded-xl border border-border-subtle bg-surface py-1 shadow-[0_8px_30px_rgba(0,0,0,0.15)]">
                            <button
                              onClick={(e) => { e.stopPropagation(); setMenuDs(null); setSelected(d.id); }}
                              className="flex w-full items-center gap-2 px-3 py-[7px] text-left text-[13px] hover:bg-muted"
                            >
                              💬 打开数据集
                            </button>
                            <button
                              onClick={(e) => { e.stopPropagation(); setMenuDs(null); moveDs(d); }}
                              className="flex w-full items-center gap-2 px-3 py-[7px] text-left text-[13px] hover:bg-muted"
                            >
                              📁 移动分组…
                            </button>
                            <button
                              onClick={(e) => { e.stopPropagation(); setMenuDs(null); remove(d); }}
                              className="flex w-full items-center gap-2 px-3 py-[7px] text-left text-[13px] text-red-500 hover:bg-red-50 dark:hover:bg-red-950/30"
                            >
                              🗑️ 删除
                            </button>
                          </div>
                        </>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
        </>
        )}
      </div>

      {globalAskOpen && (
        <GlobalAskPanel
          onClose={() => setGlobalAskOpen(false)}
          onOpenDataset={(id) => {
            setGlobalAskOpen(false);
            setSelected(id);
          }}
        />
      )}

      {uploadOpen && (
        <UploadDialog
          scope={scope}
          folders={folders}
          datasets={datasets}
          defaultFolder={folder}
          initialFile={droppedFile}
          isAdmin={isAdmin}
          onClose={() => { setUploadOpen(false); setDroppedFile(null); }}
          onDone={(ds) => {
            setUploadOpen(false);
            setDroppedFile(null);
            load();
            setSelected(ds.id); // 原地打开详情（默认数据预览）
          }}
        />
      )}
    </div>
  );
}

type UploadMode = "file" | "paste" | "semantic";

function UploadDialog({
  scope,
  folders,
  datasets,
  defaultFolder,
  initialFile,
  isAdmin,
  onClose,
  onDone,
}: {
  scope: "team" | "personal";
  folders: string[];
  datasets: DatasetInfo[];
  defaultFolder: string | null;
  initialFile: File | null;
  isAdmin: boolean;
  onClose: () => void;
  onDone: (ds: DatasetInfo) => void;
}) {
  const [mode, setMode] = useState<UploadMode>(initialFile ? "file" : "file");
  const [target, setTarget] = useState(defaultFolder ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const semFileRef = useRef<HTMLInputElement>(null);
  // 粘贴 CSV
  const [pasteName, setPasteName] = useState("");
  const [pasteCsv, setPasteCsv] = useState("");
  // 语义层文件
  const [semDs, setSemDs] = useState<number | "">("");
  const [semMsg, setSemMsg] = useState("");
  // 可写数据集（团队数据集语义层仅管理员可改）
  const writable = datasets.filter((d) => isAdmin || d.scope === "personal");

  async function upload(files: FileList | File | null) {
    const file = files instanceof File ? files : files?.[0];
    if (!file || busy) return;
    setBusy(true);
    setError("");
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("scope", scope);
      fd.append("folder", target);
      onDone(await api<DatasetInfo>("/api/data/upload", { method: "POST", body: fd }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "上传失败");
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function createFromPaste() {
    if (!pasteCsv.trim() || busy) return;
    setBusy(true);
    setError("");
    try {
      onDone(
        await api<DatasetInfo>("/api/data/create-csv", {
          method: "POST",
          body: JSON.stringify({ name: pasteName.trim() || "粘贴的数据", csv: pasteCsv, folder: target, scope }),
        })
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "创建失败");
    } finally {
      setBusy(false);
    }
  }

  /** 读文本自适应编码：UTF-8 出现替换符（乱码）时按 GBK 重读（修语义层文档乱码）。 */
  async function readFileAdaptive(file: File): Promise<string> {
    const buf = await file.arrayBuffer();
    const utf8 = new TextDecoder("utf-8").decode(buf);
    if (!utf8.includes("\uFFFD")) return utf8;
    try {
      return new TextDecoder("gbk").decode(buf);
    } catch {
      return utf8;
    }
  }

  async function uploadSemantic(files: FileList | null) {
    if (!files || files.length === 0 || !semDs) return;
    setBusy(true);
    setError("");
    setSemMsg("");
    try {
      const content = await readFileAdaptive(files[0]);
      const res = await api<DatasetInfo & { imported_fields?: number }>(
        `/api/data/datasets/${semDs}/semantic/import`,
        { method: "POST", body: JSON.stringify({ content }) }
      );
      setSemMsg(`口径文档解析成功，新增/补充 ${res.imported_fields ?? 0} 个字段定义`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "导入失败");
    } finally {
      setBusy(false);
      if (semFileRef.current) semFileRef.current.value = "";
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-6" onClick={onClose}>
      <div
        className="max-h-[88vh] w-[480px] max-w-full overflow-y-auto rounded-2xl border border-border-subtle bg-surface p-5 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <span className="text-[14.5px] font-semibold">接入数据</span>
          <button onClick={onClose} className="rounded-lg p-1 text-muted-foreground hover:text-foreground">✕</button>
        </div>

        <div className="mt-3 flex gap-1 rounded-lg bg-muted p-1">
          {([["file", "表格文件"], ["paste", "粘贴 CSV"], ["semantic", "语义层文件"]] as [UploadMode, string][]).map(([m, label]) => (
            <button
              key={m}
              onClick={() => { setMode(m); setError(""); setSemMsg(""); }}
              className={`flex-1 rounded-md px-3 py-1.5 text-[12.5px] transition-colors ${
                mode === m ? "bg-background font-medium shadow-[0_1px_2px_rgba(0,0,0,0.05)]" : "text-muted-foreground"
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        {mode !== "semantic" && (
          <>
            <label className="mt-4 block text-[12.5px] font-medium">目标分组</label>
            <input
              list="data-folder-list"
              value={target}
              onChange={(e) => setTarget(e.target.value)}
              placeholder="未分组（可输入如：湖北/月报）"
              className="mt-1 w-full rounded-lg border border-border-subtle bg-background px-3 py-2 text-[13px] outline-none focus:border-accent"
            />
            <datalist id="data-folder-list">
              {folders.map((f) => (
                <option key={f} value={f} />
              ))}
            </datalist>
          </>
        )}

        {mode === "file" && (
          <div
            onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(e) => { e.preventDefault(); setDragOver(false); upload(e.dataTransfer.files); }}
            className={`mt-4 rounded-xl border border-dashed px-4 py-8 text-center transition-colors ${
              dragOver ? "border-accent bg-accent-soft/40" : "border-border-subtle hover:border-accent/60"
            }`}
          >
            <IconDatabase className="mx-auto text-muted-foreground/50" width={20} height={20} />
            {initialFile ? (
              <>
                <div className="mt-2 truncate text-[13px] font-medium" title={initialFile.name}>📄 {initialFile.name}</div>
                <button
                  onClick={() => upload(initialFile)}
                  disabled={busy}
                  className="mt-2 rounded-lg bg-accent px-4 py-1.5 text-[13px] font-medium text-accent-foreground hover:opacity-90 disabled:opacity-60"
                >
                  {busy ? "正在导入并生成画像…" : "导入到上述分组"}
                </button>
              </>
            ) : (
              <>
                <div className="mt-2 text-[13px]">
                  {busy ? "正在导入并生成画像…" : "拖拽 .xlsx / .csv 到此处，或"}
                </div>
                {!busy && (
                  <button onClick={() => fileRef.current?.click()} className="mt-1 text-[13px] font-medium text-accent hover:underline">
                    选择文件
                  </button>
                )}
              </>
            )}
            <div className="mt-1 text-[11.5px] text-muted-foreground/70">
              导入后自动打开数据预览{scope === "team" && !isAdmin && " · 团队数据需要管理员权限"}
            </div>
            <input ref={fileRef} type="file" accept=".xlsx,.xls,.csv" className="hidden" onChange={(e) => upload(e.target.files)} />
          </div>
        )}

        {mode === "paste" && (
          <div className="mt-4">
            <input
              value={pasteName}
              onChange={(e) => setPasteName(e.target.value)}
              placeholder="数据集名称（如：11月渠道数据）"
              className="w-full rounded-lg border border-border-subtle bg-background px-3 py-2 text-[13px] outline-none focus:border-accent"
            />
            <textarea
              value={pasteCsv}
              onChange={(e) => setPasteCsv(e.target.value)}
              placeholder={"第一行为表头，逗号分隔，例如：\n月份,渠道,呼入量,接通率\n2026-11,电话,1028,0.92"}
              className="mt-2 min-h-[160px] w-full resize-y rounded-lg border border-border-subtle bg-background px-3 py-2 font-mono text-[12px] leading-relaxed outline-none focus:border-accent"
            />
            <button
              onClick={createFromPaste}
              disabled={!pasteCsv.trim() || busy}
              className="mt-2 w-full rounded-lg bg-accent py-2 text-[13px] font-medium text-accent-foreground hover:opacity-90 disabled:opacity-60"
            >
              {busy ? "创建中…" : "创建数据集"}
            </button>
          </div>
        )}

        {mode === "semantic" && (
          <div className="mt-4">
            <label className="block text-[12.5px] font-medium">应用到数据集</label>
            <select
              value={semDs}
              onChange={(e) => setSemDs(e.target.value ? Number(e.target.value) : "")}
              className="mt-1 w-full rounded-lg border border-border-subtle bg-background px-3 py-2 text-[13px] outline-none focus:border-accent"
            >
              <option value="">选择数据集…</option>
              {writable.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}（{d.scope === "team" ? "团队" : "个人"}）
                </option>
              ))}
            </select>
            <button
              onClick={() => semFileRef.current?.click()}
              disabled={!semDs || busy}
              className="mt-3 w-full rounded-lg border border-dashed border-border-subtle py-6 text-[13px] text-muted-foreground transition-colors hover:border-accent/60 hover:text-foreground disabled:opacity-50"
            >
              📄 选择口径文档（.md / .txt / .json / .csv）
              <div className="mt-1 text-[11px] text-muted-foreground/60">AI 自动解析字段定义与指标口径（自动识别 GBK/UTF-8）</div>
            </button>
            <input ref={semFileRef} type="file" accept=".md,.txt,.json,.csv" className="hidden" onChange={(e) => uploadSemantic(e.target.files)} />
            {semMsg && <div className="mt-2 rounded-lg bg-muted px-3 py-2 text-[12.5px] text-muted-foreground">{semMsg}</div>}
          </div>
        )}

        {error && (
          <div className="mt-3 rounded-lg border border-red-300/60 bg-red-50 px-3 py-2 text-[13px] text-red-600 dark:border-red-900/60 dark:bg-red-950/40 dark:text-red-400">
            {error}
          </div>
        )}
      </div>
    </div>
  );
}
