"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  DndContext,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  arrayMove,
  rectSortingStrategy,
  useSortable,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { WidgetLayoutItem, WorkbenchInfo } from "@/lib/types";
import { WIDGET_LIST, WidgetBody, widgetAllowed, widgetDef } from "@/lib/workbench-widgets";

const ACTIVE_KEY = "wb-active-workbench";

/** 布局消毒：过滤未知卡片 key、当前角色不可用卡、去重（保证拖拽 id 唯一）、规整 span */
function sanitize(items: WidgetLayoutItem[], role: string | undefined): WidgetLayoutItem[] {
  const seen = new Set<string>();
  const out: WidgetLayoutItem[] = [];
  for (const it of items) {
    const def = widgetDef(it.widget);
    if (!def || seen.has(it.widget) || !widgetAllowed(def, role)) continue;
    seen.add(it.widget);
    out.push({ widget: it.widget, span: it.span === 2 ? 2 : 1 });
  }
  return out;
}

export default function WorkbenchPage() {
  const { user } = useAuth();
  const [workbenches, setWorkbenches] = useState<WorkbenchInfo[] | null>(null);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [editing, setEditing] = useState(false);
  const [adding, setAdding] = useState(false);
  const [renamingId, setRenamingId] = useState<number | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null);
  const [saveState, setSaveState] = useState<"idle" | "saving" | "error">("idle");

  useEffect(() => {
    api<WorkbenchInfo[]>("/api/workbenches")
      .then((list) => {
        setWorkbenches(list);
        const saved = Number(localStorage.getItem(ACTIVE_KEY));
        setActiveId(list.some((w) => w.id === saved) ? saved : list[0]?.id ?? null);
      })
      .catch(() => setWorkbenches([]));
  }, []);

  const active = useMemo(
    () => workbenches?.find((w) => w.id === activeId) ?? workbenches?.[0] ?? null,
    [workbenches, activeId]
  );

  const switchTo = (id: number) => {
    setActiveId(id);
    localStorage.setItem(ACTIVE_KEY, String(id));
    setRenamingId(null);
    setConfirmDeleteId(null);
  };

  /** 布局变更：本地乐观更新 + 立即保存，失败还原 */
  const persistLayout = useCallback(
    async (wbId: number, next: WidgetLayoutItem[]) => {
      const prev = workbenches;
      if (!prev) return;
      setWorkbenches(prev.map((w) => (w.id === wbId ? { ...w, layout: next } : w)));
      setSaveState("saving");
      try {
        await api(`/api/workbenches/${wbId}`, {
          method: "PATCH",
          body: JSON.stringify({ layout: next }),
        });
        setSaveState("idle");
      } catch {
        setWorkbenches(prev);
        setSaveState("error");
      }
    },
    [workbenches]
  );

  const layout = active ? sanitize(active.layout, user?.role) : [];

  const addWidget = (key: string) => {
    if (!active) return;
    setAdding(false);
    void persistLayout(active.id, [...layout, { widget: key, span: 1 }]);
  };

  const onDragEnd = (e: DragEndEvent) => {
    const { active: a, over } = e;
    if (!over || a.id === over.id || !active) return;
    const oldIdx = layout.findIndex((it) => it.widget === a.id);
    const newIdx = layout.findIndex((it) => it.widget === over.id);
    if (oldIdx < 0 || newIdx < 0) return;
    void persistLayout(active.id, arrayMove(layout, oldIdx, newIdx));
  };

  const resetDefault = async () => {
    if (!active) return;
    setSaveState("saving");
    try {
      const w = await api<WorkbenchInfo>(`/api/workbenches/${active.id}/reset`, { method: "POST" });
      setWorkbenches((prev) => prev?.map((x) => (x.id === w.id ? w : x)) ?? null);
      setSaveState("idle");
    } catch {
      setSaveState("error");
    }
  };

  const createWorkbench = async () => {
    try {
      const count = workbenches?.length ?? 0;
      const w = await api<WorkbenchInfo>("/api/workbenches", {
        method: "POST",
        body: JSON.stringify({ name: `新工作台 ${count + 1}` }),
      });
      setWorkbenches((prev) => [...(prev ?? []), w]);
      switchTo(w.id);
      setEditing(true);
    } catch {
      setSaveState("error");
    }
  };

  const commitRename = async () => {
    const id = renamingId;
    if (!id) return;
    const name = renameDraft.trim();
    setRenamingId(null);
    if (!name) return;
    const prev = workbenches;
    setWorkbenches((p) => p?.map((w) => (w.id === id ? { ...w, name } : w)) ?? null);
    try {
      await api(`/api/workbenches/${id}`, { method: "PATCH", body: JSON.stringify({ name }) });
    } catch {
      setWorkbenches(prev);
      setSaveState("error");
    }
  };

  const deleteWorkbench = async (id: number) => {
    const prev = workbenches;
    const rest = (prev ?? []).filter((w) => w.id !== id);
    if (rest.length === 0) return; // 后端也会拦：至少保留一个
    setWorkbenches(rest);
    setConfirmDeleteId(null);
    if (activeId === id) switchTo(rest[0].id);
    try {
      await api(`/api/workbenches/${id}`, { method: "DELETE" });
    } catch {
      setWorkbenches(prev);
      setSaveState("error");
    }
  };

  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }));

  const hour = new Date().getHours();
  const greet = hour < 6 ? "夜深了" : hour < 12 ? "早上好" : hour < 18 ? "下午好" : "晚上好";
  const available = WIDGET_LIST.filter(
    (w) => widgetAllowed(w, user?.role) && !layout.some((it) => it.widget === w.key)
  );

  return (
    <div className="mx-auto max-w-5xl px-6 py-8">
      {/* 头部 */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-[20px] font-semibold tracking-tight">
            {greet}，{user?.name}
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">你的工作动态一览</p>
        </div>
        <div className="flex items-center gap-2">
          {saveState !== "idle" && (
            <span
              className={`text-[12px] ${saveState === "error" ? "text-red-500" : "text-muted-foreground"}`}
            >
              {saveState === "saving" ? "保存中…" : "保存失败，已还原"}
            </span>
          )}
          <button
            onClick={() => setEditing((v) => !v)}
            className={`rounded-lg border px-3 py-1.5 text-[12.5px] transition-colors ${
              editing
                ? "border-accent bg-accent text-white"
                : "border-border-subtle bg-surface hover:border-accent/50"
            }`}
          >
            {editing ? "完成" : "自定义"}
          </button>
        </div>
      </div>

      {/* 工作台 Tab 条 */}
      {workbenches === null ? (
        <div className="py-10 text-center text-sm text-muted-foreground">加载中…</div>
      ) : (
        <div className="mt-5 flex flex-wrap items-center gap-1.5">
          {workbenches.map((w) => {
            const isActive = active?.id === w.id;
            return (
              <div
                key={w.id}
                className={`group flex items-center gap-1 rounded-full border px-3 py-1.5 transition-colors ${
                  isActive
                    ? "border-accent bg-accent-soft text-accent"
                    : "border-border-subtle bg-surface text-muted-foreground hover:border-accent/40"
                }`}
              >
                {renamingId === w.id ? (
                  <input
                    autoFocus
                    value={renameDraft}
                    onChange={(e) => setRenameDraft(e.target.value)}
                    onBlur={commitRename}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") void commitRename();
                      if (e.key === "Escape") setRenamingId(null);
                    }}
                    className="w-28 bg-transparent text-[13px] outline-none"
                  />
                ) : (
                  <button
                    onClick={() => switchTo(w.id)}
                    className="max-w-[10rem] truncate text-[13px] font-medium"
                  >
                    {w.name}
                  </button>
                )}
                {editing && renamingId !== w.id && (
                  <>
                    <button
                      title="重命名"
                      onClick={() => {
                        setRenamingId(w.id);
                        setRenameDraft(w.name);
                      }}
                      className="text-[11px] opacity-60 hover:opacity-100"
                    >
                      ✏️
                    </button>
                    {confirmDeleteId === w.id ? (
                      <span className="flex items-center gap-1 text-[11px]">
                        <button
                          onClick={() => void deleteWorkbench(w.id)}
                          className="font-medium text-red-500"
                        >
                          删除
                        </button>
                        <button onClick={() => setConfirmDeleteId(null)} className="opacity-60">
                          取消
                        </button>
                      </span>
                    ) : (
                      <button
                        title="删除工作台"
                        onClick={() => setConfirmDeleteId(w.id)}
                        className="text-[11px] opacity-60 hover:opacity-100"
                      >
                        ✕
                      </button>
                    )}
                  </>
                )}
              </div>
            );
          })}
          <button
            onClick={() => void createWorkbench()}
            className="rounded-full border border-dashed border-border-subtle px-3 py-1.5 text-[12.5px] text-muted-foreground transition-colors hover:border-accent/50 hover:text-accent"
          >
            ＋ 新建
          </button>
        </div>
      )}

      {/* 编辑工具条 */}
      {editing && active && (
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <button
            onClick={() => setAdding((v) => !v)}
            className="rounded-lg border border-border-subtle bg-surface px-3 py-1.5 text-[12.5px] hover:border-accent/50"
          >
            ＋ 添加卡片
          </button>
          <button
            onClick={() => void resetDefault()}
            className="rounded-lg border border-border-subtle bg-surface px-3 py-1.5 text-[12.5px] text-muted-foreground hover:border-accent/50"
          >
            恢复默认
          </button>
          <span className="text-[11.5px] text-muted-foreground/60">
            拖动 ⠿ 排序 · ⇔ 切换宽窄 · ✕ 移除卡片，改动自动保存
          </span>
        </div>
      )}

      {/* 添加卡片面板 */}
      {editing && adding && (
        <div className="mt-3 rounded-xl border border-border-subtle bg-surface p-3">
          {available.length === 0 ? (
            <div className="px-2 py-1 text-[12.5px] text-muted-foreground">
              系统卡片已全部添加，可在工具库沉淀更多能力
            </div>
          ) : (
            <div className="flex flex-wrap gap-2">
              {available.map((w) => (
                <button
                  key={w.key}
                  onClick={() => addWidget(w.key)}
                  className="rounded-lg border border-border-subtle bg-surface px-3 py-2 text-left transition-colors hover:border-accent/50"
                >
                  <div className="text-[13px] font-medium">{w.title}</div>
                  <div className="text-[11px] text-muted-foreground">{w.desc}</div>
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {/* 卡片网格 */}
      {active && (
        <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
          <SortableContext items={layout.map((it) => it.widget)} strategy={rectSortingStrategy}>
            <div className="mt-5 grid grid-cols-1 gap-4 lg:grid-cols-2">
              {layout.map((it) => (
                <SortableCard
                  key={it.widget}
                  item={it}
                  editing={editing}
                  onRemove={() => void persistLayout(active.id, layout.filter((x) => x.widget !== it.widget))}
                  onToggleSpan={() =>
                    void persistLayout(
                      active.id,
                      layout.map((x) =>
                        x.widget === it.widget ? { ...x, span: x.span === 2 ? 1 : 2 } : x
                      )
                    )
                  }
                />
              ))}
              {layout.length === 0 && (
                <div className="col-span-full rounded-xl border border-dashed border-border-subtle py-14 text-center">
                  <div className="text-[13px] text-muted-foreground">这个工作台还是空的</div>
                  <button
                    onClick={() => {
                      setEditing(true);
                      setAdding(true);
                    }}
                    className="mt-3 rounded-lg border border-border-subtle bg-surface px-4 py-2 text-[13px] hover:border-accent/50"
                  >
                    添加第一张卡片
                  </button>
                </div>
              )}
            </div>
          </SortableContext>
        </DndContext>
      )}
    </div>
  );
}

function SortableCard({
  item,
  editing,
  onRemove,
  onToggleSpan,
}: {
  item: WidgetLayoutItem;
  editing: boolean;
  onRemove: () => void;
  onToggleSpan: () => void;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: item.widget,
    disabled: !editing,
  });
  const def = widgetDef(item.widget);
  if (!def) return null;
  return (
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={`${item.span === 2 ? "lg:col-span-2" : ""} ${isDragging ? "z-10 opacity-80" : ""}`}
    >
      <div
        className={`h-full rounded-xl border bg-surface p-4 ${
          editing ? "border-dashed border-accent/50" : "border-border-subtle"
        }`}
      >
        <div className="flex items-center justify-between gap-2 px-1 pb-2">
          <div className="flex min-w-0 items-center gap-1.5">
            {editing && (
              <button
                {...attributes}
                {...listeners}
                title="拖动排序"
                className="cursor-grab touch-none text-[13px] text-muted-foreground/50 hover:text-accent active:cursor-grabbing"
              >
                ⠿
              </button>
            )}
            <span className="truncate text-[13.5px] font-semibold">{def.title}</span>
          </div>
          {editing ? (
            <div className="flex shrink-0 items-center gap-1.5 text-[11px]">
              <button
                onClick={onToggleSpan}
                title={item.span === 2 ? "改为半宽" : "改为全宽"}
                className="rounded px-1.5 py-0.5 text-muted-foreground hover:bg-muted"
              >
                ⇔
              </button>
              <button
                onClick={onRemove}
                title="移除卡片"
                className="rounded px-1.5 py-0.5 text-muted-foreground hover:bg-red-50 hover:text-red-500 dark:hover:bg-red-900/30"
              >
                ✕
              </button>
            </div>
          ) : (
            <Link
              href={def.more.href}
              className="shrink-0 text-[12px] text-muted-foreground hover:text-accent"
            >
              {def.more.label} →
            </Link>
          )}
        </div>
        <div className="space-y-0.5">
          <WidgetBody widgetKey={def.key} />
        </div>
      </div>
    </div>
  );
}
