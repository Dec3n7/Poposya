import type { TrendPoint } from "../types";

// Мини-гистограмма знаковых значений (прирост/убыль) — чистый SVG без либ.
// Положительные бары растут вверх от нулевой линии (успех), отрицательные — вниз
// (danger). Используется для «прирост участников/день» из разностей серии.
export function MiniBars({
  series,
  width = 280,
  height = 64,
}: {
  series: TrendPoint[];
  width?: number;
  height?: number;
}) {
  if (series.length === 0) return null;

  const max = Math.max(1, ...series.map(([, v]) => Math.abs(v)));
  const n = series.length;
  // Раскладываем по МИНИМУМ 30 слотам, иначе при 1–2 точках (снапшоты только
  // начали копиться) единственный бар растягивается через пол-экрана.
  const slots = Math.max(n, 30);
  const gap = slots > 60 ? 1 : 2;
  const bw = Math.max(1.5, (width - gap * (slots - 1)) / slots);
  // Прижимаем реальные бары к правому краю — свежие дни справа.
  const x0 = width - (n * bw + (n - 1) * gap);
  const mid = height / 2;

  return (
    <svg
      className="minibars"
      width="100%"
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      aria-hidden="true"
    >
      <line x1="0" y1={mid} x2={width} y2={mid} stroke="var(--border-soft)" strokeWidth="1" />
      {series.map(([day, v], i) => {
        const x = x0 + i * (bw + gap);
        const h = (Math.abs(v) / max) * (mid - 2);
        const y = v >= 0 ? mid - h : mid;
        return (
          <rect
            key={day}
            x={x.toFixed(2)}
            y={y.toFixed(2)}
            width={bw.toFixed(2)}
            height={Math.max(v === 0 ? 0 : 1.5, h).toFixed(2)}
            rx="1"
            fill={v >= 0 ? "var(--success)" : "var(--danger)"}
          />
        );
      })}
    </svg>
  );
}
