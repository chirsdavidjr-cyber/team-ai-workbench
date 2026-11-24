"use client";

import { useState } from "react";
import type { ChartPayload } from "@/lib/types";

/** 自动选数值列：优先信任后端 value_column（text2sql 指定的答案列），否则按「第 2 列=核心指标」约定。 */
function pickValueIndex(payload: ChartPayload): number {
  const { columns, value_column } = payload;
  if (value_column) {
    const i = columns.indexOf(value_column);
    if (i > 0) return i;
  }
  return columns.length > 1 ? 1 : 0;
}

/** 轻量图表：按 chart_type 渲染 bar / line / pie / table（纯 SVG+CSS，无图表库依赖）。 */
export function MiniChart({ payload }: { payload: ChartPayload }) {
  const { chart_type, columns, rows } = payload;
  const [valueIdx, setValueIdx] = useState<number | null>(null);
  if (!rows || rows.length === 0) return null;

  const labelIdx = 0;
  // 手动切换优先（越界回落自动），自动 = value_column → 第 2 列约定
  const autoIdx = pickValueIndex(payload);
  const valueIndex =
    valueIdx != null && valueIdx > 0 && valueIdx < columns.length ? valueIdx : autoIdx;
  const labelCol = columns[labelIdx];
  const valueCol = columns[valueIndex] ?? columns[1] ?? columns[0];
  const isRate = /率|占比|比例/.test(valueCol);

  const data = rows
    .map((r) => ({
      label: String(r[labelIdx] ?? ""),
      value: Number(r[valueIndex] ?? 0),
    }))
    .filter((d) => Number.isFinite(d.value))
    .slice(0, 12);

  const fmt = (v: number) => {
    if (isRate && Math.abs(v) <= 1) return `${(v * 100).toFixed(1)}%`;
    return Math.abs(v) >= 1000 ? v.toLocaleString("zh-CN") : String(Math.round(v * 10000) / 10000);
  };

  // 多列结果给用户一个切换数值列的入口，模型选错时可一键纠正。
  // 图表分支较多，这里用一个包装函数统一在每条分支前插入切换器。
  const withPicker = (node: React.ReactNode) =>
    columns.length > 2 ? (
      <div>
        <div className="mb-2 flex items-center gap-1.5 text-[11.5px] text-muted-foreground">
          <span>图表数值列</span>
          <select
            value={valueIndex}
            onChange={(e) => setValueIdx(Number(e.target.value))}
            className="rounded-md border border-border-subtle bg-surface px-2 py-0.5 text-[11.5px] text-foreground outline-none"
          >
            {columns.map((c, i) => i !== labelIdx && (
              <option key={`${c}-${i}`} value={i}>{c}</option>
            ))}
          </select>
        </div>
        {node}
      </div>
    ) : node;

  if (chart_type === "line" && data.length >= 2) {
    const w = 560, h = 220, pad = { l: 44, r: 16, t: 16, b: 30 };
    const max = Math.max(...data.map((d) => d.value));
    const min = Math.min(...data.map((d) => d.value), 0);
    const x = (i: number) => pad.l + (i * (w - pad.l - pad.r)) / (data.length - 1);
    const y = (v: number) =>
      h - pad.b - ((v - min) / (max - min || 1)) * (h - pad.t - pad.b);
    const pts = data.map((d, i) => `${x(i)},${y(d.value)}`).join(" ");
    return withPicker(
      <svg viewBox={`0 0 ${w} ${h}`} className="w-full max-w-xl" role="img">
        <line x1={pad.l} x2={w - pad.r} y1={h - pad.b} y2={h - pad.b} stroke="var(--border-subtle, #e5e8ee)" />
        {data.map((d, i) => (
          <g key={i}>
            <circle cx={x(i)} cy={y(d.value)} r={3.5} fill="var(--accent, #059669)" />
            <text x={x(i)} y={h - 10} textAnchor="middle" fontSize={11} fill="#8b93a1">{d.label}</text>
          </g>
        ))}
        <polyline points={pts} fill="none" stroke="var(--accent, #059669)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
        <text x={pad.l} y={pad.t + 4} fontSize={11} fill="#8b93a1">{valueCol}（{fmt(max)} ↕）</text>
      </svg>
    );
  }

  if (chart_type === "pie" && data.length >= 2) {
    const total = data.reduce((s, d) => s + Math.abs(d.value), 0) || 1;
    const colors = ["#059669", "#10b981", "#34d399", "#6ee7b7", "#80C1FF", "#a7f3d0"];
    let angle = -90;
    const cx = 110, cy = 110, r = 90;
    return withPicker(
      <div className="flex flex-wrap items-center gap-6">
        <svg viewBox="0 0 220 220" className="h-48 w-48 shrink-0">
          {data.map((d, i) => {
            const frac = Math.abs(d.value) / total;
            const a2 = angle + frac * 360;
            const rad = (a: number) => ((a - 180) * Math.PI) / 180;
            const x1 = cx + r * Math.cos(rad(angle)), y1 = cy + r * Math.sin(rad(angle));
            const x2 = cx + r * Math.cos(rad(a2)), y2 = cy + r * Math.sin(rad(a2));
            const large = frac > 0.5 ? 1 : 0;
            const path = `M${cx},${cy} L${x1},${y1} A${r},${r} 0 ${large} 1 ${x2},${y2} Z`;
            angle = a2;
            return <path key={i} d={path} fill={colors[i % colors.length]} stroke="#fff" strokeWidth={1.5} />;
          })}
        </svg>
        <div className="space-y-1.5">
          {data.map((d, i) => (
            <div key={i} className="flex items-center gap-2 text-[12.5px]">
              <span className="h-2.5 w-2.5 rounded-sm" style={{ background: colors[i % colors.length] }} />
              <span className="min-w-16">{d.label}</span>
              <span className="font-medium">{fmt(d.value)}</span>
              <span className="text-muted-foreground">{((Math.abs(d.value) / total) * 100).toFixed(1)}%</span>
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (chart_type === "bar" && data.length >= 1) {
    const max = Math.max(...data.map((d) => Math.abs(d.value))) || 1;
    return withPicker(
      <div className="max-w-xl space-y-2.5">
        {data.map((d, i) => (
          <div key={i} className="flex items-center gap-3">
            <span className="w-20 shrink-0 truncate text-right text-[12.5px] text-muted-foreground" title={d.label}>{d.label}</span>
            <div className="h-6 flex-1 overflow-hidden rounded-md bg-muted">
              <div
                className="flex h-full items-center justify-end rounded-md bg-accent/85 px-2 text-[11px] font-medium text-white transition-all"
                style={{ width: `${Math.max((Math.abs(d.value) / max) * 100, 12)}%` }}
              >
                {fmt(d.value)}
              </div>
            </div>
          </div>
        ))}
      </div>
    );
  }

  // table / 兜底（表格展示全列，不需要数值列切换）
  return (
    <div className="hide-scrollbar overflow-x-auto">
      <table className="w-full border-collapse text-[12.5px]">
        <thead>
          <tr>
            {columns.map((c, i) => (
              <th key={i} className="border border-border-subtle bg-muted px-2.5 py-1.5 text-left font-medium">{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, 15).map((r, i) => (
            <tr key={i}>
              {r.map((v, j) => (
                <td key={j} className="border border-border-subtle px-2.5 py-1.5">{v === null ? "-" : String(v)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > 15 && <div className="mt-1.5 text-xs text-muted-foreground">仅显示前 15 行（共 {rows.length} 行）</div>}
    </div>
  );
}
