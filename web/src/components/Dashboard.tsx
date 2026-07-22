import { type ReactNode, useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../api";
import type {
  ActivityStats,
  BirthdayEntry,
  Guild,
  Overview,
  TrendPoint,
  Trends,
  VoiceEntry,
} from "../types";
import { Coverflow } from "./Coverflow";
import { Heatmap } from "./Heatmap";
import { MiniBars } from "./MiniBars";
import { PulseHero, type PulseItem } from "./PulseHero";
import { RoleDonut } from "./RoleDonut";
import { Spark } from "./Spark";

type BoardTab = "points" | "voice" | "birthdays";

const MONTHS_RU = [
  "янв", "фев", "мар", "апр", "мая", "июн",
  "июл", "авг", "сен", "окт", "ноя", "дек",
];

function birthdayWhen(b: BirthdayEntry): string {
  if (b.in_days === 0) return "сегодня 🎂";
  if (b.in_days === 1) return "завтра";
  return `через ${b.in_days} дн`;
}

function Avatar({ src, name }: { src: string | null; name: string }) {
  return src ? (
    <img className="leader-avatar" src={src} alt="" />
  ) : (
    <span className="leader-avatar fallback">{name.slice(0, 1).toUpperCase()}</span>
  );
}

const fmt = (n: number): string => Math.round(n).toLocaleString("ru");

// компактно для крупных чисел: 218000 -> «218k», 24500 -> «24.5k»
function fmtCompact(n: number): string {
  const a = Math.abs(n);
  if (a >= 100000) return `${Math.round(n / 1000)}k`;
  if (a >= 10000) return `${(n / 1000).toFixed(1)}k`;
  return fmt(n);
}

const last = (s: TrendPoint[]): number => s[s.length - 1][1];

// дельта за период = последнее минус первое
function delta(series?: TrendPoint[]): number | null {
  if (!series || series.length < 2) return null;
  return last(series) - series[0][1];
}

// изменение в процентах first -> last; null если серии нет или старт нулевой
function deltaPct(series?: TrendPoint[]): number | null {
  if (!series || series.length < 2) return null;
  const first = series[0][1];
  if (first === 0) return null;
  return Math.round(((last(series) - first) / Math.abs(first)) * 100);
}

function KpiTile({
  label,
  value,
  series,
  color,
  wide,
  big,
}: {
  label: string;
  value: string;
  series?: TrendPoint[];
  color: string;
  wide?: boolean;
  big?: boolean;
}) {
  const pct = deltaPct(series);
  return (
    <div className={`card kpi${wide ? " wide" : ""}${big ? " big" : ""}`}>
      <div className="kpi-top">
        <span className="kpi-label">{label}</span>
        {pct != null && pct !== 0 && (
          <span className={`kpi-delta ${pct > 0 ? "up" : "down"}`}>
            {pct > 0 ? "▲ +" : "▼ "}
            {Math.abs(pct)}%
          </span>
        )}
      </div>
      <div className="kpi-value">{value}</div>
      <div className="kpi-spark">
        <Spark series={series ?? []} color={color} height={big ? 54 : 40} />
      </div>
    </div>
  );
}

export function Dashboard({ guild }: { guild: Guild }) {
  const [data, setData] = useState<Overview | null>(null);
  const [trends, setTrends] = useState<Trends>({});
  const [activity, setActivity] = useState<ActivityStats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [days, setDays] = useState<number>(30);
  const [board, setBoard] = useState<BoardTab>("points");
  const [heatMode, setHeatMode] = useState<"messages" | "voice">("voice");

  useEffect(() => {
    setData(null);
    setError(null);
    setBoard("points");
    api
      .overview(guild.id)
      .then(setData)
      .catch((e) => {
        if (e instanceof ApiError && e.status === 404) setError("Попоси нет на этом сервере.");
        else setError(e instanceof Error ? e.message : "Не удалось загрузить сводку");
      });
  }, [guild.id]);

  useEffect(() => {
    setTrends({});
    api.trends(guild.id, days).then(setTrends).catch(() => setTrends({}));
  }, [guild.id, days]);

  useEffect(() => {
    setActivity(null);
    api.activity(guild.id, days).then(setActivity).catch(() => setActivity(null));
  }, [guild.id, days]);

  // прирост участников по дням = разности соседних точек серии members
  const memberDeltas = useMemo<TrendPoint[]>(() => {
    const s = trends.members;
    if (!s || s.length < 2) return [];
    const out: TrendPoint[] = [];
    for (let i = 1; i < s.length; i++) out.push([s[i][0], s[i][1] - s[i - 1][1]]);
    return out;
  }, [trends]);

  if (error) return <div className="error-banner">{error}</div>;
  if (!data)
    return (
      <div className="center" style={{ minHeight: 200 }}>
        <div className="spinner" aria-label="Загрузка" />
      </div>
    );

  // последнее значение метрики из снапшотов (для карточек без live-счётчика)
  const latest = (metric: string): number | null => {
    const s = trends[metric];
    return s && s.length > 0 ? last(s) : null;
  };

  // прирост участников за неделю (последняя точка минус ~7 дней назад)
  const memberSeries = trends.members;
  const weekDelta =
    memberSeries && memberSeries.length >= 2
      ? last(memberSeries) - memberSeries[Math.max(0, memberSeries.length - 8)][1]
      : null;

  // строка «пульса» — метрики момента (появляются по мере накопления данных)
  const pulses: PulseItem[] = [];
  if (activity && activity.daily.length > 0) {
    pulses.push({
      k: "Сообщений сегодня",
      value: fmt(last(activity.daily)),
      trend: deltaPct(activity.daily),
    });
  }
  const voiceH = latest("voice_hours");
  if (voiceH != null) {
    pulses.push({ k: "Часов в войсе", value: fmt(voiceH), unit: "ч", trend: deltaPct(trends.voice_hours) });
  }
  const ptsDelta = delta(trends.points_total);
  if (ptsDelta != null) {
    pulses.push({ k: "Очков за период", value: `${ptsDelta >= 0 ? "+" : ""}${fmt(ptsDelta)}` });
  }
  const findsN = latest("finds_collected");
  if (findsN != null) {
    pulses.push({ k: "Находок собрано", value: fmt(findsN), trend: deltaPct(trends.finds_collected) });
  }

  // vibe-строка из активности (только если данные есть — без выдумок)
  let vibe: string | null = null;
  if (activity && activity.daily.length >= 5) {
    const vals = activity.daily.map(([, v]) => v);
    const lastV = vals[vals.length - 1];
    const avg = vals.reduce((a, b) => a + b, 0) / vals.length;
    if (avg > 0) {
      const pct = Math.round(((lastV - avg) / avg) * 100);
      if (pct >= 12) vibe = `Оживлённо — на ${pct}% больше сообщений, чем в среднем за период.`;
      else if (pct <= -12) vibe = `Потише обычного — на ${Math.abs(pct)}% меньше сообщений, чем в среднем.`;
      else vibe = "Ровный ритм — активность около среднего за период.";
    }
  }

  // KPI-плитки «Сообщество/Вовлечённость» (появляются по мере накопления снапшотов)
  const community: ReactNode[] = [];
  const mem = latest("members");
  if (mem != null)
    community.push(
      <KpiTile key="members" label="Участников" value={fmtCompact(mem)} series={trends.members} color="var(--accent-hover)" wide big />,
    );
  const pts = latest("points_total");
  if (pts != null)
    community.push(
      <KpiTile key="points" label="Всего очков" value={fmtCompact(pts)} series={trends.points_total} color="var(--magenta)" />,
    );
  if (activity && activity.daily.length > 0)
    community.push(
      <KpiTile key="messages" label="Сообщений/день" value={fmtCompact(last(activity.daily))} series={activity.daily} color="var(--accent-hover)" />,
    );
  if (voiceH != null)
    community.push(
      <KpiTile key="voice" label="Часов в войсе" value={fmtCompact(voiceH)} series={trends.voice_hours} color="#7dd3fc" wide />,
    );
  if (findsN != null)
    community.push(
      <KpiTile key="finds" label="Находок" value={fmtCompact(findsN)} series={trends.finds_collected} color="#e6b24d" />,
    );

  return (
    <div className="dashboard">
      <PulseHero
        guild={guild}
        online={data.online}
        weekDelta={weekDelta}
        pulses={pulses}
        vibe={vibe}
        days={days}
        onDays={setDays}
      />

      {community.length > 0 && (
        <>
          <h2 className="group-label">Сообщество и вовлечённость</h2>
          <div className="bento">{community}</div>
        </>
      )}

      <h2 className="group-label">Киноклуб</h2>
      <div className="bento">
        <KpiTile label="В вотчлисте" value={fmt(data.counts.watchlist)} series={trends.watchlist} color="var(--accent-hover)" />
        <KpiTile label="Просмотрено" value={fmt(data.counts.watched)} series={trends.watched} color="var(--success)" />
        <KpiTile label="Плейлистов" value={fmt(data.counts.playlists)} series={trends.playlists} color="var(--magenta)" wide />
      </div>

      <div className="board">
        <section className="card">
          <div className="card-head">
            <div className="card-title">Лидерборд</div>
            <div className="seg lb-tabs" role="tablist" aria-label="Лидерборды">
              <button
                role="tab"
                aria-selected={board === "points"}
                className={`seg-item${board === "points" ? " active" : ""}`}
                onClick={() => setBoard("points")}
              >
                Очки
              </button>
              {data.voice.length > 0 && (
                <button
                  role="tab"
                  aria-selected={board === "voice"}
                  className={`seg-item${board === "voice" ? " active" : ""}`}
                  onClick={() => setBoard("voice")}
                >
                  Войс
                </button>
              )}
              {data.birthdays.length > 0 && (
                <button
                  role="tab"
                  aria-selected={board === "birthdays"}
                  className={`seg-item${board === "birthdays" ? " active" : ""}`}
                  onClick={() => setBoard("birthdays")}
                >
                  Дни рождения
                </button>
              )}
            </div>
          </div>
          <div className="lb">
            {board === "points" &&
              (data.leaderboard.length === 0 ? (
                <div className="muted lb-empty">Пока никто не набрал очков.</div>
              ) : (
                <Coverflow entries={data.leaderboard} />
              ))}
            {board === "voice" && (
              <div className="lb-list">
                {data.voice.map((v, i) => (
                  <VoiceRow key={v.user_id} rank={i + 1} v={v} />
                ))}
              </div>
            )}
            {board === "birthdays" && (
              <div className="lb-list">
                {data.birthdays.map((b) => (
                  <BirthdayRow key={b.user_id} b={b} />
                ))}
              </div>
            )}
          </div>
        </section>

        {data.distribution.length > 0 && (
          <section className="card">
            <div className="card-head">
              <div className="card-title">Роли сервера</div>
            </div>
            <div className="roles-body">
              <RoleDonut slices={data.distribution} />
            </div>
          </section>
        )}
      </div>

      {activity &&
        (activity.heatmap.some((row) => row.some((v) => v > 0)) ||
          activity.voice.some((row) => row.some((v) => v > 0))) && (
          <section className="card activity">
            <div className="card-head">
              <div className="card-title">Ритм активности</div>
              <div className="seg" role="group" aria-label="Тип активности">
                <button
                  className={`seg-item${heatMode === "voice" ? " active" : ""}`}
                  onClick={() => setHeatMode("voice")}
                >
                  Войс
                </button>
                <button
                  className={`seg-item${heatMode === "messages" ? " active" : ""}`}
                  onClick={() => setHeatMode("messages")}
                >
                  Сообщения
                </button>
              </div>
            </div>
            <div className="act-body">
              {heatMode === "voice" ? (
                <Heatmap grid={activity.voice} unit="мин" />
              ) : (
                <Heatmap grid={activity.heatmap} unit="сообщ." />
              )}
              {heatMode === "voice" && !activity.voice.some((row) => row.some((v) => v > 0)) && (
                <div className="muted small">Пока нет времени в войсе за период.</div>
              )}
              {heatMode === "messages" &&
                !activity.heatmap.some((row) => row.some((v) => v > 0)) && (
                  <div className="muted small">Пока нет сообщений за период.</div>
                )}

              {memberDeltas.length >= 5 && (
                <div className="growth">
                  <div className="growth-title">Прирост участников по дням</div>
                  <MiniBars series={memberDeltas} />
                </div>
              )}
            </div>
          </section>
        )}
    </div>
  );
}

function VoiceRow({ rank, v }: { rank: number; v: VoiceEntry }) {
  const name = v.username ?? `ID ${v.user_id}`;
  return (
    <div className="leader-row">
      <span className="leader-rank mono">{rank}</span>
      <Avatar src={v.avatar} name={name} />
      <span className="leader-name">{name}</span>
      <span className="leader-points mono">{fmt(v.hours)} ч</span>
    </div>
  );
}

function BirthdayRow({ b }: { b: BirthdayEntry }) {
  const name = b.username ?? `ID ${b.user_id}`;
  return (
    <div className="leader-row">
      <Avatar src={b.avatar} name={name} />
      <span className="leader-name">{name}</span>
      <span className="leader-role">
        {b.day} {MONTHS_RU[b.month - 1]}
      </span>
      <span className="leader-points mono">{birthdayWhen(b)}</span>
    </div>
  );
}
