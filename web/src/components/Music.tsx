import { useEffect, useState } from "react";

import { ApiError, api } from "../api";
import type { Guild, PlaylistDetail, PlaylistItem } from "../types";

function fmtDuration(sec: number | null): string {
  if (sec == null) return "—";
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function PlaylistRow({ guildId, pl }: { guildId: string; pl: PlaylistItem }) {
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<PlaylistDetail | null>(null);
  const [err, setErr] = useState("");

  function toggle() {
    const next = !open;
    setOpen(next);
    if (next && detail === null) {
      api
        .playlist(guildId, pl.name)
        .then(setDetail)
        .catch((e) => setErr(e instanceof ApiError ? e.message : "Ошибка"));
    }
  }

  return (
    <div className="pl-row">
      <button className="pl-head" onClick={toggle} aria-expanded={open}>
        <span className={`pl-caret${open ? " open" : ""}`}>▸</span>
        <span className="pl-name">{pl.name}</span>
        <span className="pl-meta faint">
          {pl.track_count} трек(ов)
          {pl.author_name && ` · ${pl.author_name}`}
        </span>
      </button>
      {open && (
        <div className="pl-tracks">
          {err ? (
            <div className="muted small pad">{err}</div>
          ) : detail === null ? (
            <div className="center" style={{ minHeight: 60 }}>
              <div className="spinner" aria-label="Загрузка" />
            </div>
          ) : detail.tracks.length === 0 ? (
            <div className="muted small pad">Плейлист пуст.</div>
          ) : (
            <ol className="track-list">
              {detail.tracks.map((t, i) => (
                <li className="track-item" key={i}>
                  <span className="track-title">
                    {t.title}
                    {t.uploader && <span className="faint"> · {t.uploader}</span>}
                  </span>
                  <span className="track-dur mono faint">{fmtDuration(t.duration)}</span>
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
    </div>
  );
}

export function Music({ guild }: { guild: Guild }) {
  const [list, setList] = useState<PlaylistItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setList(null);
    setError(null);
    api
      .playlists(guild.id)
      .then(setList)
      .catch((e) => {
        if (e instanceof ApiError && e.status === 404) setError("Попоси нет на этом сервере.");
        else setError(e instanceof Error ? e.message : "Не удалось загрузить плейлисты");
      });
  }, [guild.id]);

  if (error) return <div className="error-banner">{error}</div>;
  if (!list)
    return (
      <div className="center" style={{ minHeight: 200 }}>
        <div className="spinner" aria-label="Загрузка" />
      </div>
    );

  return (
    <div>
      <h2 className="section-title">Плейлисты</h2>
      <p className="muted" style={{ marginTop: -8, marginBottom: 16 }}>
        Создаются и играют в Discord. «Сейчас играет» и очередь появятся позже — для них нужен
        живой канал к боту.
      </p>
      <div className="card leader-card">
        {list.length === 0 ? (
          <div className="pad muted">На сервере пока нет плейлистов.</div>
        ) : (
          list.map((pl) => <PlaylistRow key={pl.name} guildId={guild.id} pl={pl} />)
        )}
      </div>
    </div>
  );
}
