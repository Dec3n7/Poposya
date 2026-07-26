import { type CSSProperties, useEffect, useRef, useState } from "react";

// Хитмап активности день-недели×час: 7 строк (Пн…Вс) × 24 столбца (час).
// Данные приходят в UTC (бэкенд бакетит по UTC), но показываем в МСК (UTC+3,
// без перехода на летнее время с 2014) — сдвигаем всю сетку на TZ_OFFSET часов.
// Интенсивность ячейки = доля от максимума, цвет — accent через color-mix
// (CSP-дружелюбно). Пустой период -> null (секция скроется).
const DOW = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
const HOUR_TICKS = [0, 6, 12, 18];
const TZ_OFFSET = 3; // МСК = UTC+3
const TZ_LABEL = "МСК";

// Сдвиг UTC-сетки в локальную зону: каждый бакет переносится на +offset часов,
// с переносом на следующий день недели при выходе за полночь. Сетка Mon-based.
function shiftToTz(grid: number[][], offset: number): number[][] {
  const out = Array.from({ length: 7 }, () => Array(24).fill(0));
  for (let d = 0; d < 7; d++) {
    for (let h = 0; h < 24; h++) {
      const v = grid[d][h];
      if (!v) continue;
      let nh = h + offset;
      let nd = d;
      while (nh >= 24) {
        nh -= 24;
        nd = (nd + 1) % 7;
      }
      while (nh < 0) {
        nh += 24;
        nd = (nd + 6) % 7;
      }
      out[nd][nh] += v;
    }
  }
  return out;
}

// unit — короткая подпись значения ячейки (в тултипе/сноске): «сообщ.» или «мин».
export function Heatmap({ grid: utcGrid, unit = "сообщ." }: { grid: number[][]; unit?: string }) {
  // «сейчас» по МСК; тик раз в минуту, чтобы метка переезжала на границе часа
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 60_000);
    return () => clearInterval(t);
  }, []);

  // тултип-ячейка, следующий за мышью; позиция зажата в границах контейнера,
  // чтобы у краёв тултип не вылезал (и не порождал скролл) и не обрезался
  const wrapRef = useRef<HTMLDivElement>(null);
  const tipRef = useRef<HTMLDivElement>(null);
  const [cell, setCell] = useState<{ d: number; h: number; count: number } | null>(null);
  const [pos, setPos] = useState({ x: 0, y: 0, below: false });

  const grid = shiftToTz(utcGrid, TZ_OFFSET);
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

  // текущий момент в МСК: UTC-час + смещение, с переносом дня при выходе за полночь
  let mh = now.getUTCHours() + TZ_OFFSET;
  let mDow = now.getUTCDay(); // 0=Вс
  while (mh >= 24) {
    mh -= 24;
    mDow = (mDow + 1) % 7;
  }
  const nowD = (mDow + 6) % 7; // → Mon-based
  const nowH = mh;

  // курсор → позиция тултипа, зажатая в границах враппера (по горизонтали —
  // по измеренной ширине тултипа; по вертикали — над курсором, но у верхнего
  // края переворачиваем вниз)
  function place(clientX: number, clientY: number) {
    const el = wrapRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const rx = clientX - rect.left;
    const ry = clientY - rect.top;
    const tw = tipRef.current?.offsetWidth ?? 130;
    const th = tipRef.current?.offsetHeight ?? 28;
    const x = Math.max(tw / 2 + 4, Math.min(rx, rect.width - tw / 2 - 4));
    const below = ry < th + 16;
    const y = below ? ry + 16 : ry - 10;
    setPos({ x, y, below });
  }

  return (
    <div className="heatmap" ref={wrapRef}>
      <div className="heatmap-scroll">
        <div className="heatmap-grid" onMouseLeave={() => setCell(null)}>
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
                const isNow = d === nowD && h === nowH;
                return (
                  <span
                    key={`c${d}-${h}`}
                    className={`heatmap-cell${count > 0 ? " on" : ""}${peak ? " peak" : ""}${isNow ? " now" : ""}`}
                    style={style}
                    onMouseEnter={(e) => {
                      setCell({ d, h, count });
                      place(e.clientX, e.clientY);
                    }}
                    onMouseMove={(e) => place(e.clientX, e.clientY)}
                  />
                );
              })}
            </div>
          ))}
        </div>
      </div>

      {cell && (
        <div
          ref={tipRef}
          className="heatmap-tip"
          style={{
            left: pos.x,
            top: pos.y,
            transform: pos.below ? "translate(-50%, 0)" : "translate(-50%, -100%)",
          }}
        >
          <b>
            {DOW[cell.d]} {String(cell.h).padStart(2, "0")}:00
          </b>
          <span className="heatmap-tip-val mono">
            {cell.count} {unit}
          </span>
          {cell.d === nowD && cell.h === nowH && <span className="heatmap-tip-now">сейчас</span>}
        </div>
      )}

      <div className="faint small heatmap-note">
        <span aria-hidden>🔥</span> Пик:{" "}
        <b>
          {DOW[peakD]} {String(peakH).padStart(2, "0")}:00
        </b>{" "}
        — {grid[peakD][peakH]} {unit} · время {TZ_LABEL}
      </div>
    </div>
  );
}
