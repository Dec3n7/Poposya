import { useEffect, useState } from "react";

import { ApiError, api } from "../api";
import type { Cinema as CinemaData, Guild, MovieRating, WatchedItem, WatchlistItem } from "../types";

function Rater({ r }: { r: MovieRating }) {
  const name = r.username ?? `ID ${r.user_id}`;
  return (
    <div className="rater-row">
      {r.avatar ? (
        <img className="leader-avatar sm" src={r.avatar} alt="" />
      ) : (
        <span className="leader-avatar sm fallback">{name.slice(0, 1).toUpperCase()}</span>
      )}
      <span className="rater-name">{name}</span>
      {r.score != null && <span className="rater-score mono">★ {r.score}</span>}
      {r.review && <span className="rater-review">«{r.review}»</span>}
    </div>
  );
}

function WatchedRow({ guildId, m }: { guildId: string; m: WatchedItem }) {
  const [open, setOpen] = useState(false);
  const [ratings, setRatings] = useState<MovieRating[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || ratings !== null) return;
    api
      .movieRatings(guildId, m.id)
      .then((d) => setRatings(d.ratings))
      .catch((e) => setError(e instanceof Error ? e.message : "Не удалось загрузить оценки"));
  }, [open, ratings, guildId, m.id]);

  return (
    <div className="pl-row">
      <button className="pl-head" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <svg className={`pl-caret${open ? " open" : ""}`} width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
          <path d="M9 6l6 6-6 6" />
        </svg>
        <span className="pl-name">
          {m.title}
          {m.year && <span className="faint"> · {m.year}</span>}
        </span>
        {m.poposya_review && <span className="cine-review">«{m.poposya_review}»</span>}
        <span className="pl-meta mono">
          {m.avg_score != null ? `★ ${m.avg_score}` : "—"}
          <span className="faint"> ({m.ratings_count})</span>
        </span>
      </button>
      {open && (
        <div className="pl-tracks">
          {error ? (
            <div className="error-banner">{error}</div>
          ) : ratings === null ? (
            <div className="muted small">Загрузка…</div>
          ) : ratings.length === 0 ? (
            <div className="muted small">Пока никто не оценил.</div>
          ) : (
            <div className="rater-list">
              {ratings.map((r) => (
                <Rater key={r.user_id} r={r} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function WatchlistRow({
  guildId,
  m,
  onRemoved,
}: {
  guildId: string;
  m: WatchlistItem;
  onRemoved: () => void;
}) {
  const [busy, setBusy] = useState(false);

  async function remove() {
    if (!window.confirm(`Убрать «${m.title}» из вотчлиста?`)) return;
    setBusy(true);
    try {
      await api.removeMovie(guildId, m.id);
      onRemoved();
    } catch {
      setBusy(false);
    }
  }

  return (
    <div className="cine-row">
      <span className="cine-title">
        {m.title}
        {m.year && <span className="faint"> · {m.year}</span>}
      </span>
      <span className="cine-side">
        <span className="mono">
          👍 {m.up} · 👎 {m.down}
        </span>
        <button className="btn ghost small" onClick={remove} disabled={busy} title="Убрать из вотчлиста">
          ✕
        </button>
      </span>
    </div>
  );
}

export function Cinema({ guild }: { guild: Guild }) {
  const [data, setData] = useState<CinemaData | null>(null);
  const [error, setError] = useState<string | null>(null);

  function load() {
    api
      .cinema(guild.id)
      .then(setData)
      .catch((e) => {
        if (e instanceof ApiError && e.status === 404) setError("Попоси нет на этом сервере.");
        else setError(e instanceof Error ? e.message : "Не удалось загрузить киноклуб");
      });
  }

  useEffect(() => {
    setData(null);
    setError(null);
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
      <h2 className="section-title">Вотчлист</h2>
      <div className="card leader-card">
        {data.watchlist.length === 0 ? (
          <div className="pad muted">Вотчлист пуст.</div>
        ) : (
          data.watchlist.map((m) => (
            <WatchlistRow key={m.id} guildId={guild.id} m={m} onRemoved={load} />
          ))
        )}
      </div>

      <h2 className="section-title">Золотой фонд</h2>
      <div className="card leader-card">
        {data.watched.length === 0 ? (
          <div className="pad muted">Пока ничего не досмотрели.</div>
        ) : (
          data.watched.map((m) => <WatchedRow key={m.id} guildId={guild.id} m={m} />)
        )}
      </div>
    </div>
  );
}
