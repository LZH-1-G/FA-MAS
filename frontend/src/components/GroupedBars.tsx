import { useState } from "react";
import { SYSTEM_COLORS } from "../util";

export interface Series {
  key: string;
  label: string;
}

/** Grouped column chart (one axis), rounded data-ends, 2px gaps, legend + hover tooltip. */
export default function GroupedBars({
  groups,
  series,
  value,
  format,
  height = 260,
}: {
  groups: { key: string; label: string }[];
  series: Series[];
  value: (group: string, series: string) => number | null | undefined;
  format: (v: number) => string;
  height?: number;
}) {
  const [tip, setTip] = useState<{ x: number; y: number; text: string } | null>(null);
  const W = 900;
  const H = height;
  const pad = { l: 56, r: 12, t: 12, b: 34 };
  const innerW = W - pad.l - pad.r;
  const innerH = H - pad.t - pad.b;
  let max = 0;
  for (const g of groups) for (const s of series) max = Math.max(max, value(g.key, s.key) ?? 0);
  const step = niceStep(max / 4 || 1);
  const top = Math.ceil(max / step) * step || 1;
  const ticks = Array.from({ length: Math.round(top / step) + 1 }, (_, i) => i * step);
  const groupW = innerW / Math.max(groups.length, 1);
  const barW = Math.min(18, (groupW * 0.8) / Math.max(series.length, 1) - 2);
  const y = (v: number) => pad.t + innerH - (v / top) * innerH;

  return (
    <div className="chart-wrap">
      <div className="legend" style={{ marginBottom: 6 }}>
        {series.map((s) => (
          <span key={s.key}>
            <span className="swatch" style={{ background: SYSTEM_COLORS[s.key] ?? "var(--text-3)" }} />
            {s.label}
          </span>
        ))}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" onMouseLeave={() => setTip(null)}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={pad.l} x2={W - pad.r} y1={y(t)} y2={y(t)} stroke="var(--border)" strokeWidth={1} />
            <text x={pad.l - 8} y={y(t) + 4} textAnchor="end" fontSize={11} fill="var(--text-3)">
              {compact(t)}
            </text>
          </g>
        ))}
        {groups.map((g, gi) => {
          const total = series.length * (barW + 2) - 2;
          const x0 = pad.l + gi * groupW + (groupW - total) / 2;
          return (
            <g key={g.key}>
              {series.map((s, si) => {
                const v = value(g.key, s.key);
                if (v == null) return null;
                const x = x0 + si * (barW + 2);
                const yTop = y(v);
                const h = Math.max(0, pad.t + innerH - yTop);
                const r = Math.min(4, h, barW / 2);
                const d = `M${x},${yTop + h} L${x},${yTop + r} Q${x},${yTop} ${x + r},${yTop} L${x + barW - r},${yTop} Q${x + barW},${yTop} ${x + barW},${yTop + r} L${x + barW},${yTop + h} Z`;
                return (
                  <g key={s.key}>
                    <path d={d} fill={SYSTEM_COLORS[s.key] ?? "var(--text-3)"} />
                    <rect
                      x={x - 1}
                      y={pad.t}
                      width={barW + 2}
                      height={innerH}
                      fill="transparent"
                      onMouseMove={() => setTip({ x: ((x + barW / 2) / W) * 100, y: (yTop / H) * 100, text: `${g.label} · ${s.label}: ${format(v)}` })}
                    />
                  </g>
                );
              })}
              <text x={pad.l + gi * groupW + groupW / 2} y={H - 12} textAnchor="middle" fontSize={11} fill="var(--text-2)">
                {g.label}
              </text>
            </g>
          );
        })}
      </svg>
      {tip && (
        <div className="chart-tip" style={{ left: `${tip.x}%`, top: `calc(${tip.y}% + 10px)`, transform: "translate(-50%, -100%)" }}>
          {tip.text}
        </div>
      )}
    </div>
  );
}

function niceStep(raw: number): number {
  const p = Math.pow(10, Math.floor(Math.log10(raw)));
  const n = raw / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
}

function compact(v: number): string {
  if (v >= 1_000_000) return `${+(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1000) return `${+(v / 1000).toFixed(1)}K`;
  return `${+v.toFixed(2)}`;
}
