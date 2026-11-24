"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";

interface GNode {
  id: string;
  kind: "dataset" | "field";
  title: string;
  scope?: string;
  rows?: number;
  defined?: boolean;
  dtype?: string;
}
interface GEdge {
  source: string;
  target: string;
}

/** 数据图谱：数据集 ↔ 字段（同名字段跨数据集共享同一节点，形成天然的跨表关联）。 */
export default function DataGraphPage() {
  const router = useRouter();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [nodes, setNodes] = useState<GNode[]>([]);
  const [edges, setEdges] = useState<GEdge[]>([]);
  const [scope, setScope] = useState<"all" | "personal" | "team">(() => {
    const s = typeof window !== "undefined" ? new URLSearchParams(window.location.search).get("scope") : null;
    return s === "personal" || s === "team" ? s : "all";
  });
  const [hover, setHover] = useState<{ x: number; y: number; node: GNode } | null>(null);
  const simRef = useRef<{
    pos: Map<string, { x: number; y: number; vx: number; vy: number; fixed: boolean }>;
    scale: number;
    ox: number;
    oy: number;
    nodes: GNode[];
    edges: GEdge[];
    drag: string | null;
    hoverId: string | null;
  } | null>(null);

  useEffect(() => {
    api<{ nodes: GNode[]; edges: GEdge[] }>(`/api/data/graph?scope=${scope}`).then((d) => {
      setNodes(d.nodes);
      setEdges(d.edges);
    });
  }, [scope]);

  useEffect(() => {
    if (nodes.length === 0) return;
    const canvas = canvasRef.current!;
    const ctx = canvas.getContext("2d")!;

    const W = () => canvas.clientWidth;
    const H = () => canvas.clientHeight;
    const pos = new Map<string, { x: number; y: number; vx: number; vy: number; fixed: boolean }>();
    nodes.forEach((n, i) => {
      const a = (i / Math.max(nodes.length, 1)) * Math.PI * 2;
      const r = n.kind === "dataset" ? 240 : 380;
      pos.set(n.id, {
        x: Math.cos(a) * r + Math.random() * 60,
        y: Math.sin(a) * r + Math.random() * 60,
        vx: 0,
        vy: 0,
        fixed: false,
      });
    });

    const sim = { pos, scale: 1, ox: 0, oy: 0, nodes, edges, drag: null as string | null, hoverId: null as string | null };
    simRef.current = sim;

    const radius = (n: GNode) => (n.kind === "dataset" ? (n.title.length > 6 ? 26 : 20) : 9);
    const isDark = document.documentElement.classList.contains("dark");

    function step(alpha: number) {
      // 斥力
      const arr = [...pos.entries()];
      for (let i = 0; i < arr.length; i++) {
        for (let j = i + 1; j < arr.length; j++) {
          const [idA, a] = arr[i];
          const [idB, b] = arr[j];
          let dx = b.x - a.x, dy = b.y - a.y;
          let d2 = dx * dx + dy * dy;
          if (d2 < 1) { d2 = 1; dx = Math.random(); dy = Math.random(); }
          const f = 2600 / d2;
          const d = Math.sqrt(d2);
          const fx = (dx / d) * f, fy = (dy / d) * f;
          a.vx -= fx * alpha; a.vy -= fy * alpha;
          b.vx += fx * alpha; b.vy += fy * alpha;
        }
      }
      // 弹簧
      for (const e of edges) {
        const a = pos.get(e.source), b = pos.get(e.target);
        if (!a || !b) continue;
        const dx = b.x - a.x, dy = b.y - a.y;
        const d = Math.sqrt(dx * dx + dy * dy) || 1;
        const target = 90;
        const f = (d - target) * 0.012;
        const fx = (dx / d) * f, fy = (dy / d) * f;
        a.vx += fx; a.vy += fy;
        b.vx -= fx; b.vy -= fy;
      }
      // 中心引力 + 阻尼
      for (const [, p] of pos) {
        p.vx -= p.x * 0.0015;
        p.vy -= p.y * 0.0015;
        p.vx *= 0.85; p.vy *= 0.85;
        if (!p.fixed) { p.x += p.vx; p.y += p.vy; }
      }
    }

    let raf = 0;
    let iter = 0;
    function draw() {
      canvas.width = W() * devicePixelRatio;
      canvas.height = H() * devicePixelRatio;
      ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
      ctx.clearRect(0, 0, W(), H());
      ctx.save();
      ctx.translate(W() / 2 + sim.ox, H() / 2 + sim.oy);
      ctx.scale(sim.scale, sim.scale);

      // 边
      ctx.strokeStyle = isDark ? "rgba(148,163,184,0.22)" : "rgba(100,116,139,0.2)";
      ctx.lineWidth = 1;
      for (const e of edges) {
        const a = pos.get(e.source), b = pos.get(e.target);
        if (!a || !b) continue;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
      }
      // 节点
      for (const n of nodes) {
        const p = pos.get(n.id);
        if (!p) continue;
        const isHover = sim.hoverId === n.id;
        if (n.kind === "dataset") {
          ctx.beginPath();
          ctx.arc(p.x, p.y, radius(n), 0, Math.PI * 2);
          ctx.fillStyle = n.scope === "team" ? (isDark ? "#38bdf8" : "#0284c7") : (isDark ? "#a78bfa" : "#7c3aed");
          ctx.fill();
          if (isHover) { ctx.strokeStyle = "#f59e0b"; ctx.lineWidth = 3; ctx.stroke(); }
          // 标签
          ctx.fillStyle = isDark ? "#e2e8f0" : "#1e293b";
          ctx.font = "12px system-ui";
          ctx.textAlign = "center";
          const label = n.title.length > 10 ? n.title.slice(0, 9) + "…" : n.title;
          ctx.fillText(label, p.x, p.y + radius(n) + 14);
        } else {
          ctx.beginPath();
          ctx.arc(p.x, p.y, radius(n), 0, Math.PI * 2);
          ctx.fillStyle = n.defined
            ? (isDark ? "#fbbf24" : "#d97706")  // 有语义定义：金色
            : (isDark ? "#475569" : "#94a3b8"); // 未定义：灰
          ctx.fill();
          if (isHover) { ctx.strokeStyle = "#f59e0b"; ctx.lineWidth = 2; ctx.stroke(); }
          if (isHover || sim.scale > 1.4) {
            ctx.fillStyle = isDark ? "#94a3b8" : "#64748b";
            ctx.font = "10px system-ui";
            ctx.textAlign = "center";
            ctx.fillText(n.title, p.x, p.y - 12);
          }
        }
      }
      ctx.restore();
    }

    function loop() {
      iter++;
      if (iter < 400) step(1);
      else if (iter % 6 === 0) step(0.12);
      if (sim.drag === null || iter % 2 === 0) draw();
      raf = requestAnimationFrame(loop);
    }
    loop();

    // 交互：命中检测
    function toWorld(cx: number, cy: number) {
      return { x: (cx - W() / 2 - sim.ox) / sim.scale, y: (cy - H() / 2 - sim.oy) / sim.scale };
    }
    function hit(cx: number, cy: number): GNode | null {
      const w = toWorld(cx, cy);
      for (let i = nodes.length - 1; i >= 0; i--) {
        const n = nodes[i];
        const p = pos.get(n.id);
        if (!p) continue;
        const dx = w.x - p.x, dy = w.y - p.y;
        if (dx * dx + dy * dy <= (radius(n) + 4) ** 2) return n;
      }
      return null;
    }
    const onMove = (e: MouseEvent) => {
      const rect = canvas.getBoundingClientRect();
      const cx = e.clientX - rect.left, cy = e.clientY - rect.top;
      if (sim.drag) {
        const w = toWorld(cx, cy);
        const p = pos.get(sim.drag);
        if (p) { p.x = w.x; p.y = w.y; p.vx = 0; p.vy = 0; }
        return;
      }
      const n = hit(cx, cy);
      sim.hoverId = n?.id ?? null;
      setHover(n ? { x: cx, y: cy, node: n } : null);
      canvas.style.cursor = n ? "pointer" : "grab";
    };
    const onDown = (e: MouseEvent) => {
      const rect = canvas.getBoundingClientRect();
      const n = hit(e.clientX - rect.left, e.clientY - rect.top);
      if (n) {
        sim.drag = n.id;
        const p = pos.get(n.id);
        if (p) p.fixed = true;
      }
    };
    const onUp = () => {
      if (sim.drag) {
        const p = pos.get(sim.drag);
        if (p) p.fixed = false;
        sim.drag = null;
      }
    };
    const onClick = (e: MouseEvent) => {
      const rect = canvas.getBoundingClientRect();
      const n = hit(e.clientX - rect.left, e.clientY - rect.top);
      if (n && n.kind === "dataset") {
        router.push(`/data?open=${n.id.slice(3)}`);
      }
    };
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const factor = e.deltaY < 0 ? 1.12 : 0.89;
      const newScale = Math.min(3, Math.max(0.3, sim.scale * factor));
      const rect = canvas.getBoundingClientRect();
      const cx = e.clientX - rect.left - W() / 2;
      const cy = e.clientY - rect.top - H() / 2;
      sim.ox = cx - ((cx - sim.ox) * newScale) / sim.scale;
      sim.oy = cy - ((cy - sim.oy) * newScale) / sim.scale;
      sim.scale = newScale;
    };
    canvas.addEventListener("mousemove", onMove);
    canvas.addEventListener("mousedown", onDown);
    window.addEventListener("mouseup", onUp);
    canvas.addEventListener("click", onClick);
    canvas.addEventListener("wheel", onWheel, { passive: false });
    return () => {
      cancelAnimationFrame(raf);
      canvas.removeEventListener("mousemove", onMove);
      canvas.removeEventListener("mousedown", onDown);
      window.removeEventListener("mouseup", onUp);
      canvas.removeEventListener("click", onClick);
      canvas.removeEventListener("wheel", onWheel);
    };
  }, [nodes, edges, router]);

  return (
    <div className="relative h-full w-full overflow-hidden bg-background">
      <canvas ref={canvasRef} className="h-full w-full" />

      {/* 顶部控制条 */}
      <div className="absolute left-1/2 top-4 z-10 flex -translate-x-1/2 items-center gap-2 rounded-xl border border-border-subtle bg-surface/90 px-3 py-2 shadow-lg backdrop-blur">
        <span className="text-[13px] font-semibold">数据图谱</span>
        <div className="flex gap-1 rounded-lg bg-muted p-0.5">
          {(["all", "personal", "team"] as const).map((s) => (
            <button
              key={s}
              onClick={() => setScope(s)}
              className={`rounded-md px-2.5 py-1 text-[11.5px] transition-colors ${
                scope === s ? "bg-background font-medium shadow-[0_1px_2px_rgba(0,0,0,0.05)]" : "text-muted-foreground"
              }`}
            >
              {s === "all" ? "全部" : s === "personal" ? "个人" : "团队"}
            </button>
          ))}
        </div>
        <button onClick={() => router.push("/data")} className="rounded-lg border border-border-subtle px-2.5 py-1 text-[11.5px] text-muted-foreground hover:text-foreground">
          ← 返回
        </button>
      </div>

      {/* 图例 */}
      <div className="absolute bottom-4 left-4 z-10 rounded-xl border border-border-subtle bg-surface/90 px-3 py-2.5 text-[11.5px] leading-relaxed shadow-lg backdrop-blur">
        <div className="mb-1 font-medium">图例</div>
        <div className="flex items-center gap-2"><span className="h-2.5 w-2.5 rounded-full" style={{ background: "#0284c7" }} />团队数据集（点击打开）</div>
        <div className="flex items-center gap-2"><span className="h-2.5 w-2.5 rounded-full" style={{ background: "#7c3aed" }} />个人数据集</div>
        <div className="flex items-center gap-2"><span className="h-2 w-2 rounded-full" style={{ background: "#d97706" }} />字段（金色=已有语义定义）</div>
        <div className="mt-1 text-muted-foreground/70">同名字段跨表共享节点 = 表间关联 · 拖拽节点 / 滚轮缩放</div>
      </div>

      {nodes.length === 0 && (
        <div className="absolute inset-0 flex items-center justify-center text-sm text-muted-foreground">
          暂无数据集，先去上传几份表格
        </div>
      )}

      {/* 悬浮提示 */}
      {hover && (
        <div
          className="pointer-events-none absolute z-20 max-w-64 rounded-lg border border-border-subtle bg-surface px-3 py-2 text-[11.5px] shadow-xl"
          style={{ left: hover.x + 14, top: hover.y + 14 }}
        >
          <div className="font-medium">
            {hover.node.kind === "dataset" ? "📊" : "🔤"} {hover.node.title}
          </div>
          {hover.node.kind === "dataset" ? (
            <div className="mt-0.5 text-muted-foreground">
              {hover.node.scope === "team" ? "团队" : "个人"} · {hover.node.rows} 行 · 点击打开
            </div>
          ) : (
            <div className="mt-0.5 text-muted-foreground">
              {hover.node.dtype} · {hover.node.defined ? "已有语义定义" : "未定义（问数时 AI 推断）"}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
