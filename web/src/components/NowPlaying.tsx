import { useEffect, useState } from "react";

import { api } from "../api";
import type { Guild, NowPlaying as NP, NowTrack } from "../types";

function fmt(sec: number): string {
  const s = Math.max(0, Math.floor(sec));
  const m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, "0")}`;
}

const REPEAT_LABEL: Record<string, string> = { one: "🔂 один", all: "🔁 всё" };

// прошедшее время трека с клиентской интерполяцией между опросами
function elapsedOf(np: NP, nowMs: number): number {
  if (np.is_paused || !np.position_at) return np.position_seconds;
  const e = np.position_seconds + (nowMs - Date.parse(np.position_at)) / 1000;
  return np.current.duration ? Math.min(e, np.current.duration) : e;
}

function QueueItem({ t }: { t: NowTrack }) {
  return (
    <li className="track-item">
      <span className="track-title">
        {t.title}
        {t.uploader && <span className="faint"> · {t.uploader}</span>}
      </span>
      <span className="track-dur mono faint">
        {t.duration ? fmt(t.duration) : "эфир"}
      </span>
    </li>
  );
}

export function NowPlaying({ guild }: { guild: Guild }) {
  const [np, setNp] = useState<NP | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [, setTick] = useState(0);

  useEffect(() => {
    let alive = true;
    setLoaded(false);
    const load = () =>
      api
        .nowPlaying(guild.id)
        .then((d) => {
          if (alive) {
            setNp(d);
            setLoaded(true);
          }
        })
        .catch(() => {});
    load();
    const poll = setInterval(load, 3000);
    return () => {
      alive = false;
      clearInterval(poll);
    };
  }, [guild.id]);

  // тик раз в секунду — двигаем прогресс-бар между опросами
  useEffect(() => {
    const t = setInterval(() => setTick((x) => x + 1), 1000);
    return () => clearInterval(t);
  }, []);

  if (!loaded) return null;
  if (!np) {
    return <div className="card pad muted np-idle">▫️ Сейчас ничего не играет.</div>;
  }

  const c = np.current;
  const elapsed = elapsedOf(np, Date.now());
  const pct = c.duration ? Math.min(100, (elapsed / c.duration) * 100) : 100;
  const by = c.requested_name ?? `ID ${c.requested_by}`;

  return (
    <div className="card pad now-playing">
      <div className="np-head">
        {c.thumbnail ? (
          <img className="np-cover" src={c.thumbnail} alt="" />
        ) : (
          <span className="np-cover fallback">🎵</span>
        )}
        <div className="np-meta">
          <a className="np-title" href={c.url} target="_blank" rel="noreferrer">
            {c.title}
          </a>
          {c.uploader && <div className="np-uploader faint">{c.uploader}</div>}
          <div className="np-tags">
            {np.is_paused && <span className="badge">⏸️ пауза</span>}
            {REPEAT_LABEL[np.repeat] && <span className="badge">{REPEAT_LABEL[np.repeat]}</span>}
            <span className="badge">🔊 {Math.round(np.volume * 100)}%</span>
            <span className="faint small">заказал {by}</span>
          </div>
        </div>
      </div>

      <div className="np-progress">
        <div className="np-bar" style={{ width: `${pct}%` }} />
      </div>
      <div className="np-times mono faint">
        <span>{fmt(elapsed)}</span>
        <span>{c.duration ? fmt(c.duration) : "🔴 прямой эфир"}</span>
      </div>

      {np.queue.length > 0 && (
        <div className="np-queue">
          <div className="section-title" style={{ margin: "8px 0 4px" }}>
            Очередь · {np.queue.length}
          </div>
          <ol className="track-list">
            {np.queue.slice(0, 20).map((t, i) => (
              <QueueItem key={i} t={t} />
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}
