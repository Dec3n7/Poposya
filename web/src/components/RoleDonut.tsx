import type { CSSProperties } from "react";

import { roleColor } from "../roles";
import type { RoleSlice } from "../types";

// Пончик распределения по ролям-статусам («пирамида сервера»). Чистый SVG без
// внешних либ (CSP-дружелюбно): сектора кольца по долям + легенда. Один сектор
// (все в одной роли) рисуем полным кольцом — дуга из точки в себя вырождается.
const CX = 60;
const CY = 60;
const R = 52; // внешний радиус
const RI = 34; // внутренний радиус

function polar(angle: number, radius: number): [number, number] {
  return [CX + radius * Math.cos(angle), CY + radius * Math.sin(angle)];
}

export function RoleDonut({ slices }: { slices: RoleSlice[] }) {
  const total = slices.reduce((s, x) => s + x.count, 0);
  if (total === 0) return null;

  let acc = 0;
  const arcs = slices.map((s) => {
    const frac = s.count / total;
    const a0 = acc * 2 * Math.PI - Math.PI / 2;
    acc += frac;
    const a1 = acc * 2 * Math.PI - Math.PI / 2;
    const large = frac > 0.5 ? 1 : 0;
    const [xo0, yo0] = polar(a0, R);
    const [xo1, yo1] = polar(a1, R);
    const [xi1, yi1] = polar(a1, RI);
    const [xi0, yi0] = polar(a0, RI);
    const d =
      `M${xo0.toFixed(2)} ${yo0.toFixed(2)} ` +
      `A${R} ${R} 0 ${large} 1 ${xo1.toFixed(2)} ${yo1.toFixed(2)} ` +
      `L${xi1.toFixed(2)} ${yi1.toFixed(2)} ` +
      `A${RI} ${RI} 0 ${large} 0 ${xi0.toFixed(2)} ${yi0.toFixed(2)} Z`;
    return { key: s.index, color: roleColor(s.index), frac, d };
  });

  return (
    <div className="donut-wrap">
      <svg width="120" height="120" viewBox="0 0 120 120" className="donut" aria-hidden="true">
        {arcs.map((a) =>
          a.frac >= 0.999 ? (
            <circle
              key={a.key}
              cx={CX}
              cy={CY}
              r={(R + RI) / 2}
              fill="none"
              stroke={a.color}
              strokeWidth={R - RI}
            />
          ) : (
            <path key={a.key} d={a.d} fill={a.color} />
          ),
        )}
        <text x={CX} y={CY - 1} className="donut-total">
          {total}
        </text>
        <text x={CX} y={CY + 15} className="donut-sub">
          с ролью
        </text>
      </svg>
      <ul className="donut-legend">
        {slices.map((s) => (
          <li key={s.index}>
            <span
              className="role-dot"
              style={{ "--role": roleColor(s.index) } as CSSProperties}
            />
            <span className="donut-name">{s.name ?? "без роли"}</span>
            <span className="donut-count mono">{s.count}</span>
            <span className="donut-pct mono">{Math.round((s.count / total) * 100)}%</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
