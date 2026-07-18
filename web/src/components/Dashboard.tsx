import { useEffect, useState } from "react";

import { ApiError, api } from "../api";
import type { Guild, LeaderEntry, Overview } from "../types";

function StatCard({ label, value }: { label: string; value: number }) {
  return (
    <div className="stat-card tilt">
      <div className="stat-value mono">{value}</div>
      <div className="stat-label">{label}</div>
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
        {name}
        {e.role && <span className="leader-role">{e.role}</span>}
      </span>
      {e.is_exclusive && <span className="badge">🖤 Единственный</span>}
      <span className="leader-points mono">{e.points}</span>
    </div>
  );
}

export function Dashboard({ guild }: { guild: Guild }) {
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setData(null);
    setError(null);
    api
      .overview(guild.id)
      .then(setData)
      .catch((e) => {
        if (e instanceof ApiError && e.status === 404) setError("Попоси нет на этом сервере.");
        else setError(e instanceof Error ? e.message : "Не удалось загрузить сводку");
      });
  }, [guild.id]);

  if (error) return <div className="error-banner">{error}</div>;
  if (!data)
    return (
      <div className="center" style={{ minHeight: 200 }}>
        <div className="spinner" aria-label="Загрузка" />
      </div>
    );

  return (
    <div>
      <div className="stat-row">
        <StatCard label="В вотчлисте" value={data.counts.watchlist} />
        <StatCard label="Просмотрено" value={data.counts.watched} />
        <StatCard label="Плейлистов" value={data.counts.playlists} />
      </div>

      <h2 className="section-title">Топ по очкам</h2>
      <div className="card leader-card">
        {data.leaderboard.length === 0 ? (
          <div className="pad muted">Пока никто не набрал очков.</div>
        ) : (
          data.leaderboard.map((e, i) => <LeaderRow key={e.user_id} rank={i + 1} e={e} />)
        )}
      </div>
    </div>
  );
}
