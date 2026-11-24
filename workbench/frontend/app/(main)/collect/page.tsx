"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { askConfirm } from "@/components/dialog";

interface CollectItem {
  url: string;
  title: string;
  published_at: string;
  column: string;
  direction: string;
  keywords: string[];
  content: string;
  content_len: number;
  desensitized: string[];
  status: "pending" | "success" | "failed" | "hindered";
  error: string;
  collected_at: string;
}

interface CollectInfo {
  id: number;
  name: string;
  site_key: string;
  site_name: string;
  status: "queued" | "running" | "success" | "failed" | "cancelled";
  total: number;
  done: number;
  failed: number;
  hindered: number;
  keywords: string[];
  max_items: number;
  to_kb: boolean;
  dataset_id: number | null;
  kb_doc_title: string;
  error: string;
  created_at: string | null;
  items?: CollectItem[];
}

interface SiteColumn {
  url: string;
  name: string;
  direction: string;
}

interface SiteInfo {
  key: string;
  name: string;
  note: string;
  static_fetch: boolean;
  columns: SiteColumn[];
}

export default function CollectPage() {
  const [tasks, setTasks] = useState<CollectInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [detail, setDetail] = useState<CollectInfo | null>(null);

  const load = useCallback(async () => {
    try {
      setTasks(await api<CollectInfo[]>("/api/collect"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    const active = tasks.some((t) => t.status === "queued" || t.status === "running");
    if (!active) return;
    const t = setInterval(load, 2500);
    return () => clearInterval(t);
  }, [tasks, load]);

  useEffect(() => {
    if (!detail || (detail.status !== "running" && detail.status !== "queued")) return;
    const t = setInterval(async () => {
      const d = await api<CollectInfo>(`/api/collect/${detail.id}`);
      setDetail(d);
    }, 2500);
    return () => clearInterval(t);
  }, [detail]);

  async function cancel(t: CollectInfo) {
    await api(`/api/collect/${t.id}/cancel`, { method: "POST" });
    load();
  }

  async function remove(t: CollectInfo) {
    if (!(await askConfirm({ title: "删除采集任务", message: `删除「${t.name}」？（已生成的数据集与知识库文档不受影响）`, danger: true, confirmText: "删除" }))) return;
    await api(`/api/collect/${t.id}`, { method: "DELETE" });
    if (detail?.id === t.id) setDetail(null);
    load();
  }

  return (
    <div className="mx-auto max-w-4xl px-6 py-10">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-[20px] font-semibold tracking-tight">采集任务</h1>
          <p className="mt-1.5 text-sm text-muted-foreground">
            对白名单公开站点（监管/协会/同业/药监/医保）后台采集：抓列表与详情全文，自动脱敏去重，产出台账数据集，可选入知识库。
          </p>
        </div>
        <button
          onClick={() => setCreating(true)}
          className="shrink-0 rounded-lg bg-accent px-4 py-2 text-[13.5px] font-medium text-accent-foreground hover:opacity-90"
        >
          + 新建采集任务
        </button>
      </div>

      <div className="mt-6 space-y-2">
        {loading ? (
          <div className="py-10 text-center text-sm text-muted-foreground">加载中…</div>
        ) : tasks.length === 0 ? (
          <div className="rounded-xl border border-dashed border-border-subtle px-4 py-12 text-center text-[13px] text-muted-foreground/70">
            还没有采集任务。试试采集金融监管总局最近的公告通知
          </div>
        ) : (
          tasks.map((t) => (
            <div
              key={t.id}
              className="group flex cursor-pointer items-center gap-3 rounded-xl border border-border-subtle bg-surface px-4 py-3 hover:border-accent/50"
              onClick={() => api<CollectInfo>(`/api/collect/${t.id}`).then(setDetail)}
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate text-[14px] font-medium">{t.name}</span>
                  <StatusBadge status={t.status} />
                </div>
                <div className="mt-1 flex items-center gap-3 text-xs text-muted-foreground">
                  <span>{t.site_name}</span>
                  <span>
                    {t.done}/{t.total} 条
                    {t.hindered > 0 ? ` · 受阻 ${t.hindered}` : ""}
                    {t.failed > 0 ? ` · 失败 ${t.failed}` : ""}
                  </span>
                  {t.dataset_id ? <span className="text-accent">数据集 #{t.dataset_id}</span> : null}
                  {(t.status === "running" || t.status === "queued") && (
                    <div className="h-1.5 w-32 overflow-hidden rounded-full bg-muted">
                      <div
                        className="h-full rounded-full bg-accent transition-all"
                        style={{ width: `${t.total ? (t.done / t.total) * 100 : 0}%` }}
                      />
                    </div>
                  )}
                </div>
              </div>
              {(t.status === "running" || t.status === "queued") && (
                <button
                  onClick={(e) => { e.stopPropagation(); cancel(t); }}
                  className="rounded-lg border border-border-subtle px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground"
                >
                  取消
                </button>
              )}
              <button
                onClick={(e) => { e.stopPropagation(); remove(t); }}
                className="hidden rounded-lg border border-border-subtle px-3 py-1.5 text-xs text-muted-foreground hover:border-red-400 hover:text-red-500 group-hover:block"
              >
                删除
              </button>
            </div>
          ))
        )}
      </div>

      {creating && <CreateDialog onClose={() => setCreating(false)} onDone={async () => { setCreating(false); await load(); }} />}
      {detail && <DetailDialog task={detail} onClose={() => setDetail(null)} />}
    </div>
  );
}

function StatusBadge({ status }: { status: CollectInfo["status"] }) {
  const map: Record<string, [string, string]> = {
    queued: ["排队中", "bg-muted text-muted-foreground"],
    running: ["采集中", "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-400"],
    success: ["已完成", "bg-accent-soft text-accent"],
    failed: ["失败", "bg-red-100 text-red-600 dark:bg-red-900/40 dark:text-red-400"],
    cancelled: ["已取消", "bg-muted text-muted-foreground"],
  };
  const [text, cls] = map[status] ?? map.queued;
  return <span className={`rounded-full px-2 py-0.5 text-[10.5px] font-medium ${cls}`}>{text}</span>;
}

function CreateDialog({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [sites, setSites] = useState<SiteInfo[]>([]);
  const [siteKey, setSiteKey] = useState("");
  const [columnUrls, setColumnUrls] = useState<string[]>([]);
  const [keywords, setKeywords] = useState("");
  const [maxItems, setMaxItems] = useState(20);
  const [toKb, setToKb] = useState(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api<SiteInfo[]>("/api/collect/sites").then((list) => {
      setSites(list);
      const first = list.find((s) => s.static_fetch) ?? list[0];
      if (first) {
        setSiteKey(first.key);
        setColumnUrls(first.columns.map((c) => c.url));
      }
    }).catch(() => setError("站点台账加载失败"));
  }, []);

  const site = sites.find((s) => s.key === siteKey);

  function pickSite(key: string) {
    setSiteKey(key);
    const s = sites.find((x) => x.key === key);
    setColumnUrls(s ? s.columns.map((c) => c.url) : []);
  }

  function toggleColumn(url: string) {
    setColumnUrls((prev) => (prev.includes(url) ? prev.filter((u) => u !== url) : [...prev, url]));
  }

  async function create() {
    if (!siteKey) {
      setError("请选择站点");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await api("/api/collect", {
        method: "POST",
        body: JSON.stringify({
          site_key: siteKey,
          columns: columnUrls,
          keywords: keywords.split(/[,，;；\s]+/).map((s) => s.trim()).filter(Boolean),
          max_items: maxItems,
          name: name.trim(),
          to_kb: toKb,
        }),
      });
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : "创建失败");
    } finally {
      setBusy(false);
    }
  }

  const input = "w-full rounded-lg border border-border-subtle bg-background px-3 py-2 text-[13.5px] outline-none focus:border-accent";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto bg-black/40 p-6" onClick={onClose}>
      <div className="my-8 w-full max-w-xl rounded-2xl border border-border-subtle bg-surface p-6 shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-[15px] font-semibold">新建采集任务</h3>
        <div className="mt-4 space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-[12.5px] font-medium">采集站点（白名单）</label>
              <select value={siteKey} onChange={(e) => pickSite(e.target.value)} className={input}>
                {sites.map((s) => (
                  <option key={s.key} value={s.key} disabled={!s.static_fetch}>
                    {s.name}
                    {s.static_fetch ? "" : "（需浏览器渲染，暂不支持）"}
                  </option>
                ))}
              </select>
              {site?.note && <p className="mt-1 text-[11.5px] text-muted-foreground">{site.note}</p>}
            </div>
            <div>
              <label className="mb-1 block text-[12.5px] font-medium">任务名（可选）</label>
              <input className={input} value={name} onChange={(e) => setName(e.target.value)} placeholder={`如：${site?.name ?? "站点"}·本周采集`} />
            </div>
          </div>
          {site && (
            <div>
              <label className="mb-1 block text-[12.5px] font-medium">栏目（不勾=全部）</label>
              <div className="flex flex-wrap gap-2">
                {site.columns.map((c) => (
                  <label
                    key={c.url}
                    className={`flex cursor-pointer items-center gap-1.5 rounded-lg border px-3 py-1.5 text-[12.5px] transition-colors ${
                      columnUrls.includes(c.url)
                        ? "border-accent/60 bg-accent-soft text-accent"
                        : "border-border-subtle text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    <input
                      type="checkbox"
                      className="h-3.5 w-3.5 accent-[color:var(--color-accent)]"
                      checked={columnUrls.includes(c.url)}
                      onChange={() => toggleColumn(c.url)}
                    />
                    {c.name}
                    <span className="text-[10.5px] opacity-70">{c.direction}</span>
                  </label>
                ))}
              </div>
            </div>
          )}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-[12.5px] font-medium">过滤关键词（可选，逗号分隔）</label>
              <input className={input} value={keywords} onChange={(e) => setKeywords(e.target.value)} placeholder="如：车险、处罚（空=不过滤）" />
            </div>
            <div>
              <label className="mb-1 block text-[12.5px] font-medium">条数上限（1-200）</label>
              <input
                type="number"
                min={1}
                max={200}
                className={input}
                value={maxItems}
                onChange={(e) => setMaxItems(Math.max(1, Math.min(200, Number(e.target.value) || 20)))}
              />
            </div>
          </div>
          <label className="flex cursor-pointer items-center gap-2 text-[12.5px] text-muted-foreground">
            <input
              type="checkbox"
              className="h-3.5 w-3.5 accent-[color:var(--color-accent)]"
              checked={toKb}
              onChange={(e) => setToKb(e.target.checked)}
            />
            采集完成后自动存入个人知识库（元信息+摘要+原文链接）
          </label>
          <p className="text-[11.5px] leading-relaxed text-muted-foreground/70">
            合规约束：仅公开页面、每条限速 ≥2.5 秒、自动脱敏（手机号/身份证/订单保单/住址）、遇登录墙或验证码自动停止。
          </p>
          {error && <div className="rounded-lg border border-red-300/60 bg-red-50 px-3 py-2 text-[13px] text-red-600 dark:border-red-900/60 dark:bg-red-950/40 dark:text-red-400">{error}</div>}
          <div className="flex gap-2.5 pt-1">
            <button onClick={create} disabled={busy} className="flex-1 rounded-lg bg-accent py-2 text-[13.5px] font-medium text-accent-foreground hover:opacity-90 disabled:opacity-60">
              {busy ? "创建中…" : "开始采集"}
            </button>
            <button onClick={onClose} className="flex-1 rounded-lg border border-border-subtle py-2 text-[13.5px] text-muted-foreground hover:text-foreground">取消</button>
          </div>
        </div>
      </div>
    </div>
  );
}

function DetailDialog({ task, onClose }: { task: CollectInfo; onClose: () => void }) {
  const items = task.items ?? [];
  const ok = items.filter((i) => i.status === "success");

  function exportCsv() {
    const esc = (s: string | number) => `"${String(s).replace(/"/g, '""')}"`;
    const header = ["序号", "方向", "站点", "栏目", "标题", "发布时间", "命中关键词", "链接", "正文摘要", "正文字数", "脱敏", "采集时间", "状态"];
    const rows = [
      header.map(esc).join(","),
      ...items.map((i, n) => [
        n + 1, i.direction, task.site_name, i.column, i.title, i.published_at,
        (i.keywords || []).join(";"), i.url, i.content.slice(0, 200), i.content_len,
        i.desensitized?.length ? `已脱敏（${i.desensitized.join("、")}）` : "无命中",
        i.collected_at, i.status,
      ].map(esc).join(",")),
    ];
    const blob = new Blob(["\uFEFF" + rows.join("\n")], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `采集台账·${task.name}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-6" onClick={onClose}>
      <div className="flex max-h-[85vh] w-full max-w-3xl flex-col rounded-2xl border border-border-subtle bg-surface shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex shrink-0 items-center gap-3 border-b border-border-subtle px-5 py-3.5">
          <b className="text-[15px]">{task.name}</b>
          <StatusBadge status={task.status} />
          <span className="text-xs text-muted-foreground">
            {task.site_name} · {task.done}/{task.total}
            {task.hindered ? ` · 受阻${task.hindered}` : ""}
            {task.failed ? ` · 失败${task.failed}` : ""}
          </span>
          <div className="flex-1" />
          {ok.length > 0 && (
            <button onClick={exportCsv} className="rounded-lg border border-border-subtle px-3 py-1.5 text-[12.5px] text-muted-foreground hover:text-foreground">
              导出台账 CSV
            </button>
          )}
          <button onClick={onClose} className="rounded-md p-1.5 text-muted-foreground hover:bg-muted">✕</button>
        </div>
        {(task.dataset_id || task.kb_doc_title || task.error) && (
          <div className="shrink-0 space-y-1 border-b border-border-subtle bg-muted/30 px-5 py-2.5 text-[12.5px]">
            {task.dataset_id ? (
              <div>
                产出数据集 <span className="text-accent">#{task.dataset_id} 采集台账·{task.name}</span>，可到数据库模块直接问数
              </div>
            ) : null}
            {task.kb_doc_title ? <div>已入知识库：《{task.kb_doc_title}》</div> : null}
            {task.error ? <div className={task.status === "failed" ? "text-red-500" : "text-amber-600 dark:text-amber-400"}>{task.status === "failed" ? "失败" : "备注"}：{task.error}</div> : null}
          </div>
        )}
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          {items.length === 0 ? (
            <div className="py-10 text-center text-sm text-muted-foreground">
              {task.status === "queued" || task.status === "running" ? "正在扫描列表页，确定候选条目…" : "加载中…"}
            </div>
          ) : (
            <div className="space-y-3">
              {items.map((i, n) => (
                <div key={n} className="rounded-xl border border-border-subtle p-3.5">
                  <div className="flex items-start gap-2">
                    <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-[10.5px] text-muted-foreground">#{n + 1}</span>
                    <div className="min-w-0 flex-1">
                      <a href={i.url} target="_blank" rel="noreferrer" className="block break-words text-[13px] font-medium hover:text-accent hover:underline">
                        {i.title}
                      </a>
                      <div className="mt-1 flex flex-wrap items-center gap-2 text-[11.5px] text-muted-foreground">
                        <span>{i.column} · {i.direction}</span>
                        {i.published_at ? <span>{i.published_at}</span> : null}
                        {i.status === "success" ? <span>{i.content_len} 字</span> : null}
                        {i.desensitized?.length ? (
                          <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] text-amber-700 dark:bg-amber-900/40 dark:text-amber-400">
                            已脱敏：{i.desensitized.join("、")}
                          </span>
                        ) : null}
                        {i.keywords?.length ? <span className="text-accent">命中：{i.keywords.join("、")}</span> : null}
                      </div>
                    </div>
                  </div>
                  {i.status === "pending" ? (
                    <div className="mt-2 pl-8 text-[12.5px] text-muted-foreground/60">等待采集…</div>
                  ) : i.status === "hindered" ? (
                    <div className="mt-2 pl-8 text-[12.5px] text-amber-600 dark:text-amber-400">受阻：{i.error}</div>
                  ) : i.status === "failed" ? (
                    <div className="mt-2 pl-8 text-[12.5px] text-red-500">失败：{i.error}</div>
                  ) : (
                    <details className="mt-2 pl-8">
                      <summary className="cursor-pointer text-[12.5px] text-muted-foreground hover:text-foreground">查看正文</summary>
                      <div className="mt-2 max-h-72 overflow-y-auto whitespace-pre-wrap break-words border-l-2 border-border-subtle pl-3 text-[12.5px] leading-relaxed">
                        {i.content}
                      </div>
                    </details>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
