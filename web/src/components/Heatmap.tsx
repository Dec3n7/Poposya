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
              return (
                <span
                  key={`c${d}-${h}`}
                  className={`heatmap-cell${count > 0 ? " on" : ""}`}
                  style={style}
                  title={`${DOW[d]} ${String(h).padStart(2, "0")}:00 — ${count} ${unit}`}
                />
              );
            })}
          </div>
        ))}
      </div>
      <div className="faint small heatmap-note">
        {unit === "мин" ? "Минуты в войсе" : "Сообщения"} по дню недели и часу · время UTC
      </div>
    </div>
  );
}
