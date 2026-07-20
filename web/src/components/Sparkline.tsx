import type { TrendPoint } from "../types";

// Минималистичный SVG-спарклайн без внешних либ (CSP-дружелюбно). Рисует линию
// по значениям серии + мягкую заливку под ней. Одна точка -> плоская линия;
// пусто -> ничего (карточка покажет только число).
export function Sparkline({
  series,
  width = 120,
  height = 34,
}: {
  series: TrendPoint[];
  width?: number;
  height?: number;
}) {
  if (series.length < 2) return null;

  const values = series.map(([, v]) => v);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1; // защита от деления на ноль (плоская серия)
  const pad = 2;
  const w = width - pad * 2;
  const h = height - pad * 2;

  const xy = values.map((v, i) => {
    const x = pad + (values.length === 1 ? 0 : (i / (values.length - 1)) * w);
    const y = pad + h - ((v - min) / span) * h;
    return [x, y] as const;
  });

  const line = xy.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`).join(" ");
  const area = `${line} L${xy[xy.length - 1][0].toFixed(1)} ${(height - pad).toFixed(1)} L${xy[0][0].toFixed(1)} ${(height - pad).toFixed(1)} Z`;
  const gid = `spark-${Math.round(min)}-${Math.round(max)}-${values.length}`;

  return (
    <svg
      className="sparkline"
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      aria-hidden="true"
    >
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--accent-hover)" stopOpacity="0.28" />
          <stop offset="100%" stopColor="var(--accent-hover)" stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#${gid})`} />
      <path
        d={line}
        fill="none"
        stroke="var(--accent-hover)"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
