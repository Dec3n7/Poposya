import { useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../api";
import type {
  ActivityStats,
  BirthdayEntry,
  Guild,
  LeaderEntry,
  Overview,
  TrendPoint,
  Trends,
  VoiceEntry,
} from "../types";
import { Heatmap } from "./Heatmap";
import { MiniBars } from "./MiniBars";
import { RoleChip } from "./RoleChip";
import { RoleDonut } from "./RoleDonut";
import { Sparkline } from "./Sparkline";

type BoardTab = "points" | "voice" | "birthdays";
const PERIODS = [7, 30, 90] as const;

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

function fmt(n: number): string {
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}

// дельта за период = последнее значение минус первое в серии
function delta(series: TrendPoint[]): number | null {
  if (series.length < 2) return null;
  return series[series.length - 1][1] - series[0][1];
}

function StatCard({
  label,
  value,
  series,
}: {
  label: string;
  value: number;
  series?: TrendPoint[];
}) {
  const d = series ? delta(series) : null;
  return (
    <div className="stat-card tilt">
      <div className="stat-head">
        <div className="stat-value mono">{fmt(value)}</div>
        {d !== null && d !== 0 && (
          <span className={`stat-delta ${d > 0 ? "up" : "down"}`}>
            {d > 0 ? "▲" : "▼"} {fmt(Math.abs(d))}
          </span>
        )}
      </div>
      <div className="stat-label">{label}</div>
      {series && series.length >= 2 && <Sparkline series={series} />}
    </div>
  );
}

function LeaderRow({ rank, e }: { rank: number; e: LeaderEntry }) {
  const name = e.username ?? `ID ${e.user_id}`;
  return (
    <div className={`leader-row${e.is_exclusive ? " exclusive" : ""}`}>
      <span className="leader-rank mono">{rank}</span>
      {e.avatar ? (
        <img className="leader-avatar" src={e.avatar} alt="" />
      ) : (
        <span className="leader-avatar fallback">{name.slice(0, 1).toUpperCase()}</span>
      )}
      <span className="leader-name">
        <span>{name}</span>
        {e.role && (
          <span className="person-meta">
            <RoleChip name={e.role} index={e.role_index} />
          </span>
        )}
      </span>
      {e.is_exclusive && <span className="badge">🖤 Единственный</span>}
      <span className="leader-points mono">{e.points}</span>
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
  const [heatMode, setHeatMode] = useState<"messages" | "voice">("messages");

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

  // тренды — не критичны (если снапшотов ещё нет, просто без спарклайнов) и
  // перезапрашиваются при смене периода
  useEffect(() => {
    setTrends({});
    api
      .trends(guild.id, days)
      .then(setTrends)
      .catch(() => setTrends({}));
  }, [guild.id, days]);

  // активность (сообщения/день + хитмап) — тоже не критична, копится ботом
  useEffect(() => {
    setActivity(null);
    api
      .activity(guild.id, days)
      .then(setActivity)
      .catch(() => setActivity(null));
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

  // текущее значение метрики из последнего снапшота (для карточек без live-счётчика)
  const latest = (metric: string): number | null => {
    const s = trends[metric];
    return s && s.length > 0 ? s[s.length - 1][1] : null;
  };

  // карточки на основе снапшотов появляются, только когда данные накопились
  const snapshotCards: { metric: string; label: string }[] = [
    { metric: "members", label: "Участников" },
    { metric: "points_total", label: "Всего очков" },
    { metric: "voice_hours", label: "Часов в войсе" },
    { metric: "finds_collected", label: "Находок собрано" },
  ];

  return (
    <div>
      <div className="dash-head">
        <span className="faint small">Тренды за</span>
        <div className="seg" role="group" aria-label="Период трендов">
          {PERIODS.map((p) => (
            <button
              key={p}
              className={`seg-item${days === p ? " active" : ""}`}
              onClick={() => setDays(p)}
            >
              {p} дн
            </button>
          ))}
        </div>
      </div>

      <div className="stat-row">
        <StatCard label="В вотчлисте" value={data.counts.watchlist} series={trends.watchlist} />
        <StatCard label="Просмотрено" value={data.counts.watched} series={trends.watched} />
        <StatCard label="Плейлистов" value={data.counts.playlists} series={trends.playlists} />
        {snapshotCards.map(({ metric, label }) => {
          const v = latest(metric);
          return v === null ? null : (
            <StatCard key={metric} label={label} value={v} series={trends[metric]} />
          );
        })}
        {activity && activity.daily.length > 0 && (
          <StatCard
            label="Сообщений/день"
            value={activity.daily[activity.daily.length - 1][1]}
            series={activity.daily}
          />
        )}
      </div>

      <div className="board">
        <div className="board-main">
          <div className="seg" role="tablist" aria-label="Лидерборды">
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

          <div className="card leader-card">
            {board === "points" &&
              (data.leaderboard.length === 0 ? (
                <div className="pad muted">Пока никто не набрал очков.</div>
              ) : (
                data.leaderboard.map((e, i) => <LeaderRow key={e.user_id} rank={i + 1} e={e} />)
              ))}
            {board === "voice" &&
              (data.voice.length === 0 ? (
                <div className="pad muted">Нет данных по войсу.</div>
              ) : (
                data.voice.map((v, i) => <VoiceRow key={v.user_id} rank={i + 1} v={v} />)
              ))}
            {board === "birthdays" &&
              (data.birthdays.length === 0 ? (
                <div className="pad muted">Ближайших дней рождения нет.</div>
              ) : (
                data.birthdays.map((b) => <BirthdayRow key={b.user_id} b={b} />)
              ))}
          </div>
        </div>

        {data.distribution.length > 0 && (
          <div className="board-side">
            <h2 className="section-title">Роли сервера</h2>
            <div className="card pad">
              <RoleDonut slices={data.distribution} />
            </div>
          </div>
        )}
      </div>

      {memberDeltas.length >= 5 && (
        <>
          <h2 className="section-title">Прирост участников</h2>
          <div className="card pad">
            <MiniBars series={memberDeltas} />
            <div className="faint small" style={{ marginTop: 8 }}>
              Изменение числа участников по дням за выбранный период
            </div>
          </div>
        </>
      )}

      {activity &&
        (activity.heatmap.some((row) => row.some((v) => v > 0)) ||
          activity.voice.some((row) => row.some((v) => v > 0))) && (
          <>
            <div className="section-head">
              <h2 className="section-title">Активность по часам</h2>
              <div className="seg" role="group" aria-label="Тип активности">
                <button
                  className={`seg-item${heatMode === "messages" ? " active" : ""}`}
                  onClick={() => setHeatMode("messages")}
                >
                  Сообщения
                </button>
                <button
                  className={`seg-item${heatMode === "voice" ? " active" : ""}`}
                  onClick={() => setHeatMode("voice")}
                >
                  Войс
                </button>
              </div>
            </div>
            <div className="card pad">
              {heatMode === "messages" ? (
                <Heatmap grid={activity.heatmap} unit="сообщ." />
              ) : (
                <Heatmap grid={activity.voice} unit="мин" />
              )}
              {heatMode === "messages" &&
                !activity.heatmap.some((row) => row.some((v) => v > 0)) && (
                  <div className="muted small">Пока нет сообщений за период.</div>
                )}
              {heatMode === "voice" &&
                !activity.voice.some((row) => row.some((v) => v > 0)) && (
                  <div className="muted small">Пока нет времени в войсе за период.</div>
                )}
            </div>
          </>
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
