import type { Guild } from "../types";

export interface PulseItem {
  k: string; // подпись
  value: string; // готовое значение (уже отформатировано)
  unit?: string; // единица (ч и т.п.)
  trend?: number | null; // дельта в %; знак -> стрелка/цвет
}

const PERIODS = [7, 30, 90] as const;

// Хиро «Обзора»: идентичность сервера + онлайн + строка «пульса» (метрики
// момента) + селектор периода трендов. «В сети» приходит из /overview.online.
export function PulseHero({
  guild,
  online,
  weekDelta,
  pulses,
  vibe,
  days,
  onDays,
}: {
  guild: Guild;
  online: number | null;
  weekDelta: number | null;
  pulses: PulseItem[];
  vibe: string | null;
  days: number;
  onDays: (d: number) => void;
}) {
  const meta: string[] = [];
  if (weekDelta != null && weekDelta !== 0) {
    meta.push(`${weekDelta > 0 ? "+" : ""}${weekDelta} участников за неделю`);
  }
  if (online != null) meta.push(`${online.toLocaleString("ru")} в сети сейчас`);

  return (
    <div className="card hero">
      <div className="hero-top">
        <div className="hero-icon">
          {guild.icon ? <img src={guild.icon} alt="" /> : guild.name.slice(0, 1).toUpperCase()}
        </div>
        <div className="hero-id">
          <div className="hero-name">{guild.name}</div>
          {meta.length > 0 && <div className="hero-meta">{meta.join(" · ")}</div>}
        </div>
        <div className="hero-spacer" />
        <div className="seg" role="group" aria-label="Период трендов">
          {PERIODS.map((p) => (
            <button
              key={p}
              className={`seg-item${days === p ? " active" : ""}`}
              onClick={() => onDays(p)}
            >
              {p} дн
            </button>
          ))}
        </div>
      </div>

      {pulses.length > 0 && (
        <div className="pulse">
          {pulses.map((p) => (
            <div className="pulse-item" key={p.k}>
              <div className="pulse-k">{p.k}</div>
              <div className="pulse-v">
                {p.value}
                {p.unit && <span className="pulse-u">{p.unit}</span>}
                {p.trend != null && p.trend !== 0 && (
                  <span className={`trend ${p.trend > 0 ? "up" : "down"}`}>
                    {p.trend > 0 ? "▲" : "▼"} {Math.abs(p.trend)}%
                  </span>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {vibe && (
        <div className="vibe">
          <span aria-hidden>🖤</span>
          <div>{vibe}</div>
        </div>
      )}
    </div>
  );
}
