"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { DatasetInfo, KbDocInfo } from "@/lib/types";
import { fmtSize, fmtTime } from "@/components/kb-detail";

interface AiReview {
  duplicate: { most_similar: string | null; score: number; flag: boolean };
  sensitive: { count: number; flag: boolean; hits: { kind: string; match: string; context: string }[] };
  quality: { score: string; comment: string; suggestion: string };
}

interface ReviewItem {
  id: number;
  doc: KbDocInfo;
  requester: string;
  ai_review: AiReview;
  status: string;
  reason: string;
  created_at: string | null;
}

export default function AdminPage() {
  const [zone, setZone] = useState<"kb" | "data">("kb");
  const [tab, setTab] = useState<"pending" | "all">("pending");
  const [items, setItems] = useState<ReviewItem[]>([]);
  const [dsRequests, setDsRequests] = useState<DatasetInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [rejecting, setRejecting] = useState<number | null>(null);
  const [reason, setReason] = useState("");
  const [toast, setToast] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [r, d] = await Promise.all([
        api<ReviewItem[]>(`/api/kb/reviews?status=${tab}`).catch(() => [] as ReviewItem[]),
        api<DatasetInfo[]>("/api/data/team-requests").catch(() => [] as DatasetInfo[]),
      ]);
      setItems(r);
      setDsRequests(d);
      setToast("");
    } catch (e) {
      setItems([]);
      setDsRequests([]);
      setToast(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [tab]);

  useEffect(() => {
    load();
  }, [load]);

  async function decide(id: number, action: "approve" | "reject") {
    setToast("");
    try {
      await api(`/api/kb/reviews/${id}/decide`, {
        method: "POST",
        body: JSON.stringify({ action, reason }),
      });
      setRejecting(null);
      setReason("");
      setToast(action === "approve" ? "已通过，文档进入团队库" : "已驳回，理由已通知提交人");
      load();
    } catch (e) {
      setToast(e instanceof Error ? e.message : "操作失败");
    }
  }

  async function decideDs(id: number, approve: boolean) {
    setToast("");
    try {
      await api(`/api/data/datasets/${id}/review-team`, {
        method: "POST",
        body: JSON.stringify({ approve, reason }),
      });
      setToast(approve ? "已通过，数据集进入团队库" : "已驳回");
      setRejecting(null);
      setReason("");
      load();
    } catch (e) {
      setToast(e instanceof Error ? e.message : "操作失败");
    }
  }

  return (
    <div className="mx-auto max-w-3xl px-6 py-10">
      <h1 className="text-[20px] font-semibold tracking-tight">审核中心</h1>
      <p className="mt-1.5 text-sm text-muted-foreground">
        团队资产入库的统一终审：知识文档（AI 预审查重/质量/敏感）与数据集申请，一处处理。
      </p>

      {/* 一级：资产类型 */}
      <div className="mt-5 flex gap-1 rounded-lg bg-muted p-1 w-fit">
        {([["kb", "知识库入库"], ["data", `数据集入库（${dsRequests.length}）`]] as const).map(([z, label]) => (
          <button
            key={z}
            onClick={() => setZone(z)}
            className={`rounded-md px-4 py-1.5 text-[13px] transition-colors ${
              zone === z ? "bg-background font-medium shadow-[0_1px_2px_rgba(0,0,0,0.05)]" : "text-muted-foreground"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {zone === "data" ? (
        <>
          {toast && <div className="mt-3 rounded-lg bg-muted px-3.5 py-2 text-[13px] text-muted-foreground">{toast}</div>}
          <div className="mt-5 space-y-3">
            {loading ? (
              <div className="py-10 text-center text-sm text-muted-foreground">加载中…</div>
            ) : dsRequests.length === 0 ? (
              <div className="rounded-xl border border-dashed border-border-subtle px-4 py-12 text-center text-[13px] text-muted-foreground/70">
                没有待审核的数据集申请
              </div>
            ) : (
              dsRequests.map((d) => {
                const semCount = Object.keys(d.semantic?.columns ?? {}).length;
                return (
                  <div key={d.id} className="rounded-xl border border-border-subtle bg-surface p-5">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="text-[15px] font-semibold">{d.name}</div>
                        <div className="mt-0.5 text-xs text-muted-foreground">
                          {d.row_count} 行 × {d.profile.length} 列 · 语义层 {semCount}/{d.profile.length} · 申请于 {fmtTime(d.created_at)} · 来源 {d.filename}
                        </div>
                      </div>
                      <span className="shrink-0 rounded-full bg-amber-100 px-2.5 py-1 text-[11.5px] font-medium text-amber-700 dark:bg-amber-900/40 dark:text-amber-400">
                        待审核
                      </span>
                    </div>
                    <div className="mt-3 flex flex-wrap gap-1.5">
                      {d.profile.slice(0, 10).map((c) => (
                        <span key={c.name} className="rounded bg-muted px-2 py-0.5 font-mono text-[11px] text-muted-foreground">
                          {c.name}
                        </span>
                      ))}
                      {d.profile.length > 10 && <span className="text-[11px] text-muted-foreground/60">…共 {d.profile.length} 字段</span>}
                    </div>
                    <div className="mt-4">
                      {rejecting === d.id ? (
                        <div className="rounded-xl border border-border-subtle p-3.5">
                          <div className="text-[13px] font-medium">驳回理由（将通知提交人）</div>
                          <textarea
                            value={reason}
                            onChange={(e) => setReason(e.target.value)}
                            rows={2}
                            className="mt-2 w-full rounded-lg border border-border-subtle bg-background px-3 py-2 text-[13px] outline-none focus:border-accent"
                            placeholder="如：数据口径不清 / 与团队数据集重复"
                          />
                          <div className="mt-2.5 flex gap-2">
                            <button
                              onClick={() => decideDs(d.id, false)}
                              className="rounded-lg bg-red-500 px-4 py-1.5 text-[13px] font-medium text-white hover:opacity-90"
                            >
                              确认驳回
                            </button>
                            <button onClick={() => { setRejecting(null); setReason(""); }} className="rounded-lg border border-border-subtle px-4 py-1.5 text-[13px] text-muted-foreground">
                              取消
                            </button>
                          </div>
                        </div>
                      ) : (
                        <div className="flex gap-2.5">
                          <button
                            onClick={() => decideDs(d.id, true)}
                            className="rounded-lg bg-accent px-5 py-2 text-[13px] font-medium text-accent-foreground hover:opacity-90"
                          >
                            通过，入团队数据
                          </button>
                          <button
                            onClick={() => { setRejecting(d.id); setReason(""); }}
                            className="rounded-lg border border-border-subtle px-5 py-2 text-[13px] text-muted-foreground hover:border-red-400 hover:text-red-500"
                          >
                            驳回
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </>
      ) : (
        <>
          {/* 二级：知识库 pending/all */}
          <div className="mt-4 flex gap-1 rounded-lg bg-muted p-1 w-fit">
            {([["pending", "待审核"], ["all", "全部记录"]] as const).map(([t, label]) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={`rounded-md px-4 py-1.5 text-[13px] transition-colors ${
                  tab === t ? "bg-background font-medium shadow-[0_1px_2px_rgba(0,0,0,0.05)]" : "text-muted-foreground"
                }`}
              >
                {label}
                {t === "pending" && items.length > 0 && tab === "pending" ? `（${items.length}）` : ""}
              </button>
            ))}
          </div>
          {toast && <div className="mt-3 rounded-lg bg-muted px-3.5 py-2 text-[13px] text-muted-foreground">{toast}</div>}

          <div className="mt-5 space-y-3">
            {loading ? (
              <div className="py-10 text-center text-sm text-muted-foreground">加载中…</div>
            ) : items.length === 0 ? (
              <div className="rounded-xl border border-dashed border-border-subtle px-4 py-12 text-center text-[13px] text-muted-foreground/70">
                {tab === "pending" ? "没有待审核的入库申请" : "暂无审核记录"}
              </div>
            ) : (
              items.map((r) => (
                <div key={r.id} className="rounded-xl border border-border-subtle bg-surface p-5">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="text-[15px] font-semibold">{r.doc.title}</div>
                      <div className="mt-0.5 text-xs text-muted-foreground">
                        {r.requester} 申请 · {fmtTime(r.created_at)} · {r.doc.chunk_count} 知识块 · {fmtSize(r.doc.size)}
                      </div>
                    </div>
                    <span
                      className={`shrink-0 rounded-full px-2.5 py-1 text-[11.5px] font-medium ${
                        r.status === "pending"
                          ? "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-400"
                          : r.status === "approved"
                            ? "bg-accent-soft text-accent"
                            : "bg-red-100 text-red-600 dark:bg-red-900/40 dark:text-red-400"
                      }`}
                    >
                      {r.status === "pending" ? "待审核" : r.status === "approved" ? "已通过" : "已驳回"}
                    </span>
                  </div>

                  {/* AI 预审结果 */}
                  <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-3">
                    <AiCard
                      title="查重"
                      ok={!r.ai_review?.duplicate?.flag}
                      body={
                        r.ai_review?.duplicate?.most_similar
                          ? `最相似：《${r.ai_review.duplicate.most_similar}》 ${(r.ai_review.duplicate.score * 100).toFixed(0)}%`
                          : "未发现相似文档"
                      }
                    />
                    <AiCard
                      title="质量"
                      ok={r.ai_review?.quality?.score !== "C"}
                      body={`评级 ${r.ai_review?.quality?.score || "?"}：${r.ai_review?.quality?.comment || ""}`}
                    />
                    <AiCard
                      title="敏感信息"
                      ok={!r.ai_review?.sensitive?.flag}
                      body={
                        r.ai_review?.sensitive?.flag
                          ? `命中 ${r.ai_review.sensitive.count} 处（${r.ai_review.sensitive.hits?.[0]?.kind}：…${r.ai_review.sensitive.hits?.[0]?.context}…）`
                          : "未检出手机号/身份证/银行卡"
                      }
                    />
                  </div>

                  {r.status === "rejected" && r.reason && (
                    <div className="mt-3 rounded-lg bg-red-50 px-3.5 py-2 text-[13px] text-red-600 dark:bg-red-950/40 dark:text-red-400">
                      驳回理由：{r.reason}
                    </div>
                  )}

                  {r.status === "pending" && (
                    <div className="mt-4">
                      {rejecting === r.id ? (
                        <div className="rounded-xl border border-border-subtle p-3.5">
                          <div className="text-[13px] font-medium">驳回理由（必填，将通知提交人）</div>
                          <textarea
                            value={reason}
                            onChange={(e) => setReason(e.target.value)}
                            rows={2}
                            className="mt-2 w-full rounded-lg border border-border-subtle bg-background px-3 py-2 text-[13px] outline-none focus:border-accent"
                            placeholder="如：与团队库已有文档重复 / 内容需补充 / 含敏感信息需脱敏"
                          />
                          <div className="mt-2.5 flex gap-2">
                            <button
                              onClick={() => decide(r.id, "reject")}
                              disabled={!reason.trim()}
                              className="rounded-lg bg-red-500 px-4 py-1.5 text-[13px] font-medium text-white hover:opacity-90 disabled:opacity-50"
                            >
                              确认驳回
                            </button>
                            <button
                              onClick={() => { setRejecting(null); setReason(""); }}
                              className="rounded-lg border border-border-subtle px-4 py-1.5 text-[13px] text-muted-foreground"
                            >
                              取消
                            </button>
                          </div>
                        </div>
                      ) : (
                        <div className="flex gap-2.5">
                          <button
                            onClick={() => decide(r.id, "approve")}
                            className="rounded-lg bg-accent px-5 py-2 text-[13px] font-medium text-accent-foreground hover:opacity-90"
                          >
                            通过，入团队库
                          </button>
                          <button
                            onClick={() => { setRejecting(r.id); setReason(""); }}
                            className="rounded-lg border border-border-subtle px-5 py-2 text-[13px] text-muted-foreground hover:border-red-400 hover:text-red-500"
                          >
                            驳回
                          </button>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ))
            )}
          </div>
        </>
      )}
    </div>
  );
}

function AiCard({ title, ok, body }: { title: string; ok: boolean; body: string }) {
  return (
    <div className={`rounded-lg border px-3 py-2.5 ${ok ? "border-border-subtle bg-muted/40" : "border-amber-300/60 bg-amber-50 dark:border-amber-900/50 dark:bg-amber-950/30"}`}>
      <div className="flex items-center gap-1.5 text-[12px] font-medium">
        {ok ? "✓" : "⚠"} {title}
      </div>
      <div className="mt-1 text-[12px] leading-relaxed text-muted-foreground">{body}</div>
    </div>
  );
}
