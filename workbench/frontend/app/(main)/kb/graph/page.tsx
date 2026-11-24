"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import type { KbGraph } from "@/lib/types";

interface Node {
  id: number;
  title: string;
  scope: string;
  chunk_count: number;
  x: number; y: number; vx: number; vy: number;
  fixed?: boolean;
}
interface Edge { s: Node; t: Node }

/** 全屏知识图谱：深空背景、光晕节点、力导向持续模拟、拖拽/缩放/悬停高亮。 */
export default function KbGraphPage() {
  const router = useRouter();
  const [scope, setScope] = useState<"personal" | "team">("personal");

  // 挂载后从 URL 读取 scope（客户端导航时 window.location 在 mount 时才就绪，避免竞态）
  useEffect(() => {
    const s = new URLSearchParams(window.location.search).get("scope");
    if (s === "team" || s === "personal") setScope(s);
  }, []);
  const [stats, setStats] = useState({ nodes: 0, edges: 0 });
  const [loading, setLoading] = useState(true);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const nodesRef = useRef<Node[]>([]);
  const edgesRef = useRef<Edge[]>([]);
  const viewRef = useRef({ scale: 1, ox: 0, oy: 0 });
  const hoverRef = useRef<Node | null>(null);
  const dragRef = useRef<{ node: Node; moved: boolean; dx: number; dy: number } | null>(null);
  const pointerRef = useRef({ x: -1, y: -1 });

  useEffect(() => {
    // URL scope 优先（直载/客户端导航双场景兜底，彻底避免首载竞态）
    const urlScope = new URLSearchParams(window.location.search).get("scope");
    const effScope: "personal" | "team" =
      urlScope === "team" || urlScope === "personal" ? urlScope : scope;
    if (effScope !== scope) {
      setScope(effScope);
      return;
    }
    setLoading(true);
    api<KbGraph>(`/api/kb/graph?scope=${effScope}`)
      .then((g) => {
        const W = window.innerWidth, H = window.innerHeight;
        const nodes: Node[] = g.nodes.map((n, i) => {
          const a = (2 * Math.PI * i) / Math.max(g.nodes.length, 1);
          const r = Math.min(W, H) * 0.3;
          return { ...n, x: W / 2 + r * Math.cos(a), y: H / 2 + r * Math.sin(a), vx: 0, vy: 0 };
        });
        const map = new Map(nodes.map((n) => [n.id, n]));
        const edges: Edge[] = [];
        for (const e of g.edges) {
          const s = map.get(e.source), t = e.target ? map.get(e.target) : undefined;
          if (s && t) edges.push({ s, t });
        }
        nodesRef.current = nodes;
        edgesRef.current = edges;
        viewRef.current = { scale: 1, ox: 0, oy: 0 };
        setStats({ nodes: nodes.length, edges: edges.length });
      })
      .finally(() => setLoading(false));
  }, [scope]);

  const openDoc = useCallback((id: number) => {
    router.push(`/kb?open=${id}`);
  }, [router]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    let raf = 0;
    let alpha = 1; // 全局冷却（阻尼）
    let iter = 0; // 帧计数：前 300 帧全力模拟，之后降频微调
    let dirty = true; // 静止时跳过重绘，交互（悬停/拖拽/缩放/resize）置脏

    function resize() {
      if (!canvas) return;
      canvas.width = canvas.clientWidth * dpr;
      canvas.height = canvas.clientHeight * dpr;
      dirty = true;
    }
    resize();
    window.addEventListener("resize", resize);

    function radius(n: Node) {
      return (6 + Math.min(n.chunk_count || 1, 8)) * 1.1;
    }

    function step() {
      const W = canvas!.clientWidth, H = canvas!.clientHeight;
      const nodes = nodesRef.current, edges = edgesRef.current;
      const view = viewRef.current;

      // ---- 力导向模拟（限频：前 300 帧全力收敛，此后每 6 帧低强度微调；拖拽期间唤醒全速）----
      iter++;
      const hot = iter < 300 || dragRef.current !== null;
      const simAlpha = hot ? alpha : iter % 6 === 0 ? 0.12 : 0;
      let moved = false;
      if (simAlpha > 0.005) {
        moved = true;
        for (let i = 0; i < nodes.length; i++) {
          const a = nodes[i];
          for (let j = i + 1; j < nodes.length; j++) {
            const b = nodes[j];
            let dx = a.x - b.x, dy = a.y - b.y;
            let d2 = dx * dx + dy * dy;
            if (d2 < 1) { d2 = 1; dx = 1; }
            const d = Math.sqrt(d2);
            const f = (2600 / d2) * simAlpha;
            const ux = dx / d, uy = dy / d;
            a.vx += ux * f; a.vy += uy * f;
            b.vx -= ux * f; b.vy -= uy * f;
          }
        }
        for (const e of edges) {
          const dx = e.t.x - e.s.x, dy = e.t.y - e.s.y;
          const d = Math.sqrt(dx * dx + dy * dy) || 1;
          const f = (d - 170) * 0.012 * simAlpha;
          e.s.vx += (dx / d) * f * 12; e.s.vy += (dy / d) * f * 12;
          e.t.vx -= (dx / d) * f * 12; e.t.vy -= (dy / d) * f * 12;
        }
        for (const n of nodes) {
          n.vx += (W / 2 - n.x) * 0.0016 * simAlpha;
          n.vy += (H / 2 - n.y) * 0.0016 * simAlpha;
          n.vx *= 0.86; n.vy *= 0.86;
          if (!n.fixed && !dragRef.current?.node) { n.x += n.vx; n.y += n.vy; }
        }
        if (!dragRef.current && iter < 300) alpha *= 0.997;
      }

      // ---- 渲染（静止且无交互时跳过，降低空载 CPU）----
      if (!moved && !dirty) {
        raf = requestAnimationFrame(step);
        return;
      }
      dirty = false;
      ctx!.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx!.clearRect(0, 0, W, H);

      const hover = hoverRef.current;
      const neighbors = new Set<number>();
      if (hover) {
        for (const e of edges) {
          if (e.s === hover) neighbors.add(e.t.id);
          if (e.t === hover) neighbors.add(e.s.id);
        }
      }

      ctx!.save();
      ctx!.translate(view.ox, view.oy);
      ctx!.scale(view.scale, view.scale);

      // 边
      for (const e of edges) {
        const hot = hover && (e.s === hover || e.t === hover);
        const dim = hover && !hot;
        ctx!.strokeStyle = hot ? "rgba(52, 211, 153, 0.85)" : dim ? "rgba(148,163,184,0.05)" : "rgba(148,163,184,0.22)";
        ctx!.lineWidth = hot ? 2 / view.scale : 1 / view.scale;
        ctx!.shadowBlur = hot ? 10 : 0;
        ctx!.shadowColor = "rgba(52, 211, 153, 0.9)";
        ctx!.beginPath();
        // 轻微弧线，更有引力感
        const mx = (e.s.x + e.t.x) / 2, my = (e.s.y + e.t.y) / 2;
        const nx = -(e.t.y - e.s.y), ny = e.t.x - e.s.x;
        const len = Math.hypot(nx, ny) || 1;
        const bend = Math.min(Math.hypot(e.t.x - e.s.x, e.t.y - e.s.y) * 0.08, 22);
        ctx!.quadraticCurveTo(mx + (nx / len) * bend, my + (ny / len) * bend, e.t.x, e.t.y);
        ctx!.moveTo(e.s.x, e.s.y);
        ctx!.stroke();
        ctx!.shadowBlur = 0;
      }

      // 节点
      for (const n of nodes) {
        const r = radius(n) * (n === hover ? 1.35 : 1);
        const isNeighbor = hover && neighbors.has(n.id);
        const dim = hover && n !== hover && !isNeighbor;
        const base = n.scope === "team" ? [16, 185, 129] : [56, 189, 248];
        const glow = n === hover ? 26 : isNeighbor ? 16 : 10;

        ctx!.globalAlpha = dim ? 0.18 : 1;
        // 光晕
        const g = ctx!.createRadialGradient(n.x, n.y, r * 0.2, n.x, n.y, r + glow);
        g.addColorStop(0, `rgba(${base[0]},${base[1]},${base[2]},0.55)`);
        g.addColorStop(1, `rgba(${base[0]},${base[1]},${base[2]},0)`);
        ctx!.fillStyle = g;
        ctx!.beginPath();
        ctx!.arc(n.x, n.y, r + glow, 0, 2 * Math.PI);
        ctx!.fill();
        // 主体
        const g2 = ctx!.createRadialGradient(n.x - r * 0.35, n.y - r * 0.35, r * 0.1, n.x, n.y, r);
        g2.addColorStop(0, "#ffffff");
        g2.addColorStop(0.25, `rgb(${base[0]},${base[1]},${base[2]})`);
        g2.addColorStop(1, `rgb(${Math.round(base[0] * 0.35)},${Math.round(base[1] * 0.35)},${Math.round(base[2] * 0.45)})`);
        ctx!.fillStyle = g2;
        ctx!.beginPath();
        ctx!.arc(n.x, n.y, r, 0, 2 * Math.PI);
        ctx!.fill();
        if (n === hover) {
          ctx!.strokeStyle = "rgba(255,255,255,0.9)";
          ctx!.lineWidth = 1.5 / view.scale;
          ctx!.stroke();
        }
        // 标签
        if (nodes.length <= 60 || n === hover || isNeighbor) {
          ctx!.globalAlpha = dim ? 0.1 : n === hover ? 1 : 0.75;
          ctx!.fillStyle = n === hover ? "#ffffff" : "rgba(226,232,240,0.9)";
          const fs = (n === hover ? 14 : 11.5) / Math.max(view.scale, 0.7);
          ctx!.font = `${n === hover ? "600 " : ""}${fs}px "PingFang SC", sans-serif`;
          ctx!.textAlign = "center";
          ctx!.fillText(n.title.slice(0, 14), n.x, n.y - r - 7 / view.scale);
        }
        ctx!.globalAlpha = 1;
      }
      ctx!.restore();
      raf = requestAnimationFrame(step);
    }
    step();

    // ---- 交互 ----
    function toWorld(cx: number, cy: number) {
      const view = viewRef.current;
      return { x: (cx - view.ox) / view.scale, y: (cy - view.oy) / view.scale };
    }
    function hit(cx: number, cy: number): Node | null {
      const p = toWorld(cx, cy);
      let best: Node | null = null, bd = Infinity;
      for (const n of nodesRef.current) {
        const d = Math.hypot(n.x - p.x, n.y - p.y);
        if (d < radius(n) + 6 && d < bd) { bd = d; best = n; }
      }
      return best;
    }
    function onMove(e: MouseEvent) {
      dirty = true;
      const rect = canvas!.getBoundingClientRect();
      const cx = e.clientX - rect.left, cy = e.clientY - rect.top;
      pointerRef.current = { x: cx, y: cy };
      const drag = dragRef.current;
      if (drag) {
        const p = toWorld(cx, cy);
        drag.node.x = p.x - drag.dx;
        drag.node.y = p.y - drag.dy;
        drag.moved = true;
      } else {
        hoverRef.current = hit(cx, cy);
        canvas!.style.cursor = hoverRef.current ? "pointer" : "grab";
      }
    }
    function onDown(e: MouseEvent) {
      dirty = true;
      const rect = canvas!.getBoundingClientRect();
      const cx = e.clientX - rect.left, cy = e.clientY - rect.top;
      const n = hit(cx, cy);
      if (n) {
        const p = toWorld(cx, cy);
        dragRef.current = { node: n, moved: false, dx: p.x - n.x, dy: p.y - n.y };
        alpha = Math.max(alpha, 0.5); // 拖拽时唤醒模拟
      }
    }
    function onUp() {
      dirty = true;
      const drag = dragRef.current;
      if (drag && !drag.moved) openDoc(drag.node.id);
      dragRef.current = null;
    }
    function onWheel(e: WheelEvent) {
      e.preventDefault();
      dirty = true;
      const view = viewRef.current;
      const rect = canvas!.getBoundingClientRect();
      const cx = e.clientX - rect.left, cy = e.clientY - rect.top;
      const k = e.deltaY < 0 ? 1.12 : 0.89;
      const ns = Math.min(2.5, Math.max(0.35, view.scale * k));
      view.ox = cx - ((cx - view.ox) / view.scale) * ns;
      view.oy = cy - ((cy - view.oy) / view.scale) * ns;
      view.scale = ns;
    }
    canvas.addEventListener("mousemove", onMove);
    canvas.addEventListener("mousedown", onDown);
    window.addEventListener("mouseup", onUp);
    canvas.addEventListener("wheel", onWheel, { passive: false });

    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", resize);
      canvas.removeEventListener("mousemove", onMove);
      canvas.removeEventListener("mousedown", onDown);
      window.removeEventListener("mouseup", onUp);
      canvas.removeEventListener("wheel", onWheel);
    };
  }, [openDoc, loading]); // loading 变化时重建渲染循环

  return (
    <div className="fixed inset-0 z-40 overflow-hidden bg-[#070b14]">
      {/* 星空底色 */}
      <div
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            "radial-gradient(1200px 700px at 70% 20%, rgba(16,185,129,0.10), transparent 60%), radial-gradient(900px 600px at 20% 80%, rgba(56,189,248,0.08), transparent 60%), #070b14",
        }}
      />
      <canvas ref={canvasRef} className="absolute inset-0 h-full w-full" />

      {/* 顶部栏 */}
      <div className="absolute left-0 right-0 top-0 z-20 flex items-center gap-3 bg-gradient-to-b from-black/60 to-transparent px-5 py-3.5">
        <button
          onClick={() => router.push("/kb")}
          className="rounded-lg border border-white/15 bg-white/5 px-3 py-1.5 text-[13px] text-slate-200 backdrop-blur transition-colors hover:bg-white/10"
        >
          ← 返回知识库
        </button>
        <div className="flex gap-1 rounded-lg border border-white/10 bg-white/5 p-1 backdrop-blur">
          {(["personal", "team"] as const).map((s) => (
            <button
              key={s}
              onClick={() => setScope(s)}
              className={`rounded-md px-3.5 py-1 text-[12.5px] transition-colors ${
                scope === s ? "bg-emerald-500/90 font-medium text-white" : "text-slate-300 hover:text-white"
              }`}
            >
              {s === "personal" ? "个人图谱" : "团队图谱"}
            </button>
          ))}
        </div>
        <div className="text-[12.5px] text-slate-400">
          {stats.nodes} 篇文档 · {stats.edges} 条双链
        </div>
        <div className="flex-1" />
        <div className="hidden text-[11.5px] text-slate-500 sm:block">
          拖拽节点 · 滚轮缩放 · 点击打开文档 · 悬停高亮关联
        </div>
      </div>

      {/* 图例 */}
      <div className="absolute bottom-4 left-5 flex items-center gap-4 rounded-lg border border-white/10 bg-black/40 px-4 py-2 text-[11.5px] text-slate-300 backdrop-blur">
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-full" style={{ background: "linear-gradient(135deg,#fff,#10b981)" }} />团队库
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-full" style={{ background: "linear-gradient(135deg,#fff,#38bdf8)" }} />个人库
        </span>
        <span className="text-slate-500">节点越大知识块越多</span>
      </div>

      {loading && (
        <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center text-sm text-slate-400">
          图谱写入中…
        </div>
      )}
      {!loading && stats.nodes === 0 && (
        <div className="pointer-events-none absolute inset-0 z-10 flex flex-col items-center justify-center text-slate-400">
          <div className="text-[15px]">{scope === "personal" ? "个人库" : "团队库"}还没有文档</div>
          <div className="mt-1 text-[13px] text-slate-500">上传知识后，双链关系将在此呈现</div>
        </div>
      )}
    </div>
  );
}
