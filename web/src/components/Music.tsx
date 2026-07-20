import { useEffect, useState } from "react";

import { ApiError, api } from "../api";
import type { Guild, PlaylistDetail, PlaylistItem } from "../types";
import { NowPlaying } from "./NowPlaying";

function fmtDuration(sec: number | null): string {
  if (sec == null) return "—";
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function PlaylistRow({
  guildId,
  pl,
  onDeleted,
}: {
  guildId: string;
  pl: PlaylistItem;
  onDeleted: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<PlaylistDetail | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

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

  async function del() {
    if (!window.confirm(`Удалить плейлист «${pl.name}»? Это необратимо.`)) return;
    setBusy(true);
    try {
      await api.deletePlaylist(guildId, pl.name);
      onDeleted();
    } catch {
      setBusy(false);
    }
  }

  return (
    <div className="pl-row">
      <div className="pl-row-head">
        <button className="pl-head" onClick={toggle} aria-expanded={open}>
          <span className={`pl-caret${open ? " open" : ""}`}>▸</span>
          <span className="pl-name">{pl.name}</span>
          <span className="pl-meta faint">
            {pl.track_count} трек(ов)
            {pl.author_name && ` · ${pl.author_name}`}
          </span>
        </button>
        <button
          className="btn ghost small pl-del"
          onClick={del}
          disabled={busy}
          title="Удалить плейлист"
        >
          🗑
        </button>
      </div>
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

const CONTROLS: { action: "pause" | "resume" | "skip" | "stop"; label: string }[] = [
  { action: "pause", label: "⏸️ Пауза" },
  { action: "resume", label: "▶️ Играть" },
  { action: "skip", label: "⏭️ Пропустить" },
  { action: "stop", label: "⏹️ Стоп" },
];

function PlayerControls({ guildId }: { guildId: string }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  async function send(action: "pause" | "resume" | "skip" | "stop") {
    setBusy(action);
    setMsg(null);
    try {
      const r = await api.musicControl(guildId, action);
      if (r.status === "done") setMsg(r.result ?? "Готово");
      else if (r.status === "failed") setMsg(r.result ?? "Не вышло");
      else setMsg("Отправлено — применяется…");
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : "Ошибка");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="card pad player-controls">
      <div className="control-buttons">
        {CONTROLS.map((c) => (
          <button
            key={c.action}
            className="btn"
            onClick={() => send(c.action)}
            disabled={busy !== null}
          >
            {c.label}
          </button>
        ))}
      </div>
      {msg && <div className="control-msg faint small">{msg}</div>}
    </div>
  );
}

export function Music({ guild }: { guild: Guild }) {
  const [list, setList] = useState<PlaylistItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  function load() {
    api
      .playlists(guild.id)
      .then(setList)
      .catch((e) => {
        if (e instanceof ApiError && e.status === 404) setError("Попоси нет на этом сервере.");
        else setError(e instanceof Error ? e.message : "Не удалось загрузить плейлисты");
      });
  }

  useEffect(() => {
    setList(null);
    setError(null);
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
      <h2 className="section-title">Сейчас играет</h2>
      <NowPlaying guild={guild} />

      <h2 className="section-title">Управление плеером</h2>
      <PlayerControls guildId={guild.id} />

      <h2 className="section-title">Плейлисты</h2>
      <p className="muted" style={{ marginTop: -8, marginBottom: 16 }}>
        Создаются и играют в Discord. «Сейчас играет» и очередь появятся позже — для них нужен
        живой канал к боту.
      </p>
      <div className="card leader-card">
        {list.length === 0 ? (
          <div className="pad muted">На сервере пока нет плейлистов.</div>
        ) : (
          list.map((pl) => (
            <PlaylistRow key={pl.name} guildId={guild.id} pl={pl} onDeleted={load} />
          ))
        )}
      </div>
    </div>
  );
}
