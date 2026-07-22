import type { CSSProperties } from "react";

// Хитмап активности день-недели×час: 7 строк (Пн…Вс) × 24 столбца (час, UTC).
// Интенсивность ячейки = доля от максимума, цвет — accent через color-mix
// (CSP-дружелюбно, без внешних либ). Пустой период -> null (секция скроется).
const DOW = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
const HOUR_TICKS = [0, 6, 12, 18];

// unit — короткая подпись значения ячейки (для тултипа/сноски): «сообщ.» или «мин».
export function Heatmap({ grid, unit = "сообщ." }: { grid: number[][]; unit?: string }) {
  const max = Math.max(0, ...grid.flat());
  if (max === 0) return null;

  // ячейка-пик (для подсветки и подписи): максимум по всей сетке
  let peakD = 0;
  let peakH = 0;
  grid.forEach((row, d) =>
    row.forEach((count, h) => {
      if (count > grid[peakD][peakH]) {
        peakD = d;
        peakH = h;
      }
    }),
  );

  return (
    <div className="heatmap">
      <div className="heatmap-grid">
        <span className="heatmap-corner" />
        {Array.from({ length: 24 }, (_, h) => (
          <span key={`h${h}`} className="heatmap-hour">
            {HOUR_TICKS.includes(h) ? h : ""}
          </span>
        ))}
        {grid.map((row, d) => (
          <div key={`r${d}`} className="heatmap-row" role="row">
            <span className="heatmap-dow">{DOW[d]}</span>
            {row.map((count, h) => {
              // нелинейная шкала (sqrt): редкие пики не глушат слабую активность
              const intensity = count === 0 ? 0 : 0.12 + 0.88 * Math.sqrt(count / max);
              const style = { "--i": intensity.toFixed(3) } as CSSProperties;
              const peak = d === peakD && h === peakH;
              return (
                <span
                  key={`c${d}-${h}`}
                  className={`heatmap-cell${count > 0 ? " on" : ""}${peak ? " peak" : ""}`}
                  style={style}
                  title={`${DOW[d]} ${String(h).padStart(2, "0")}:00 — ${count} ${unit}`}
                />
              );
            })}
          </div>
        ))}
      </div>
      <div className="faint small heatmap-note">
        <span aria-hidden>🔥</span> Пик:{" "}
        <b>
          {DOW[peakD]} {String(peakH).padStart(2, "0")}:00
        </b>{" "}
        — {grid[peakD][peakH]} {unit} · время UTC
      </div>
    </div>
  );
}
