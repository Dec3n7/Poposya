import { useEffect, useId, useRef, useState } from "react";

import type { TrendPoint } from "../types";

// Спарклайн KPI-плитки: заливка под линией + линия + выделенная точка конца.
// Меряет собственную ширину (ResizeObserver) и рисует viewBox 1:1 к пикселям —
// иначе на широких плитках SVG растягивается по X и круглая точка становится
// овалом. Цвет задаётся под метрику. <2 точек / нулевая ширина — пусто.
export function Spark({
  series,
  color = "var(--accent-hover)",
  height = 40,
}: {
  series: TrendPoint[];
  color?: string;
  height?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const gid = `sg-${useId().replace(/:/g, "")}`;
  const [w, setW] = useState(0);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const cw = entries[0].contentRect.width;
      if (cw > 0) setW(Math.round(cw));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const values = series.map(([, v]) => v);
  let svg = null;
  if (values.length >= 2 && w > 0) {
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = max - min || 1;
    const n = values.length;
    const H = height;
    const px = (i: number) => (i / (n - 1)) * w;
    const py = (v: number) => H - 3 - ((v - min) / span) * (H - 8);
    const line = values
      .map((v, i) => `${i === 0 ? "M" : "L"}${px(i).toFixed(1)},${py(v).toFixed(1)}`)
      .join(" ");
    const area =
      `M0,${H} ` +
      values.map((v, i) => `L${px(i).toFixed(1)},${py(v).toFixed(1)}`).join(" ") +
      ` L${w},${H} Z`;
    const ex = px(n - 1);
    const ey = py(values[n - 1]);
    svg = (
      <svg
        width={w}
        height={H}
        viewBox={`0 0 ${w} ${H}`}
        className="spark-svg"
        aria-hidden="true"
      >
        <defs>
          <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity="0.32" />
            <stop offset="100%" stopColor={color} stopOpacity="0" />
          </linearGradient>
        </defs>
        <path d={area} fill={`url(#${gid})`} />
        <path
          d={line}
          fill="none"
          stroke={color}
          strokeWidth="2"
          strokeLinejoin="round"
          strokeLinecap="round"
        />
        <circle cx={ex.toFixed(1)} cy={ey.toFixed(1)} r="3" fill={color} />
      </svg>
    );
  }

  return (
    <div ref={ref} className="spark" style={{ height }}>
      {svg}
    </div>
  );
}
