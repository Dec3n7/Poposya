import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, api } from "../api";
import {
  IconPause,
  IconPlay,
  IconRepeat,
  IconShuffle,
  IconSkipNext,
  IconSkipPrev,
  IconStop,
  IconX,
} from "../icons";
import type { CommandResult, Guild, NowPlaying as NP, NowTrack } from "../types";
import { useToast } from "./Toast";

function fmt(sec: number): string {
  const s = Math.max(0, Math.floor(sec));
  const m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, "0")}`;
}

const REPEAT_TITLE: Record<string, string> = {
  off: "выключен",
  one: "текущий трек",
  all: "вся очередь",
};

// прошедшее время трека с клиентской интерполяцией между опросами
function elapsedOf(np: NP, nowMs: number): number {
  if (np.is_paused || !np.position_at) return np.position_seconds;
  const e = np.position_seconds + (nowMs - Date.parse(np.position_at)) / 1000;
  return np.current.duration ? Math.min(e, np.current.duration) : e;
}

export function NowPlaying({ guild }: { guild: Guild }) {
  const [np, setNp] = useState<NP | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [, setTick] = useState(0);
  const [busy, setBusy] = useState(false);
  // локальная позиция слайдера громкости: живёт плавно при перетаскивании, а с
  // сервером синхронизируется опросом, только пока пользователь НЕ тащит
  const [vol, setVol] = useState(1);
  const draggingVol = useRef(false);
  const toast = useToast();

  const load = useCallback(async () => {
    try {
      const d = await api.nowPlaying(guild.id);
      setNp(d);
      setLoaded(true);
      if (d && !draggingVol.current) setVol(d.volume);
    } catch {
      /* сеть моргнула — оставляем что было */
    }
  }, [guild.id]);

  useEffect(() => {
    setLoaded(false);
    load();
    // фоновая вкладка не поллит; вернулись — сразу освежаем
    const poll = setInterval(() => {
      if (!document.hidden) load();
    }, 3000);
    const onVisible = () => {
      if (!document.hidden) load();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(poll);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [load]);

  // тик раз в секунду — двигаем прогресс-бар между опросами
  useEffect(() => {
    const t = setInterval(() => {
      if (!document.hidden) setTick((x) => x + 1);
    }, 1000);
    return () => clearInterval(t);
  }, []);

  // общий обёртчик команды плеера: гасит кнопки, ловит ошибку, освежает состояние
  const act = useCallback(
    async (fn: () => Promise<CommandResult>) => {
      setBusy(true);
      try {
        const r = await fn();
        if (r.status === "failed") toast.error(r.result ?? "Не вышло");
        await load();
      } catch (e) {
        toast.error(e instanceof ApiError ? e.message : "Ошибка");
      } finally {
        setBusy(false);
      }
    },
    [load, toast],
  );

  if (!loaded) return null;
  if (!np) {
    return <div className="card pad muted np-idle">▫️ Сейчас ничего не играет.</div>;
  }

  const c = np.current;
  const elapsed = elapsedOf(np, Date.now());
  const pct = c.duration ? Math.min(100, (elapsed / c.duration) * 100) : 100;
  const by = c.requested_name ?? `ID ${c.requested_by}`;
  const seekable = c.duration != null;

  function onSeek(e: React.MouseEvent<HTMLDivElement>) {
    if (!seekable || busy || !c.duration) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    act(() => api.musicSeek(guild.id, Math.floor(ratio * c.duration!)));
  }

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
            <span className="faint small">заказал {by}</span>
          </div>
        </div>
      </div>

      <div
        className={`np-progress${seekable ? " seekable" : ""}`}
        onClick={onSeek}
        role={seekable ? "slider" : undefined}
        aria-label={seekable ? "Перемотка трека" : undefined}
        aria-valuenow={seekable ? Math.round(elapsed) : undefined}
        aria-valuemax={seekable ? c.duration! : undefined}
        title={seekable ? "Кликни, чтобы перемотать" : undefined}
      >
        <div className="np-bar" style={{ width: `${pct}%` }} />
        {seekable && <span className="np-knob" style={{ left: `${pct}%` }} aria-hidden />}
      </div>
      <div className="np-times mono faint">
        <span>{fmt(elapsed)}</span>
        <span>{c.duration ? fmt(c.duration) : "🔴 прямой эфир"}</span>
      </div>

      <div className="np-controls" role="group" aria-label="Управление плеером">
        <button
          className="mc-btn"
          title="Перемешать очередь"
          aria-label="Перемешать очередь"
          disabled={busy}
          onClick={() => act(() => api.musicControl(guild.id, "shuffle"))}
        >
          <IconShuffle />
        </button>
        <button
          className="mc-btn"
          title="Предыдущий трек"
          aria-label="Предыдущий трек"
          disabled={busy}
          onClick={() => act(() => api.musicControl(guild.id, "previous"))}
        >
          <IconSkipPrev />
        </button>
        <button
          className="mc-btn main"
          title={np.is_paused ? "Играть" : "Пауза"}
          aria-label={np.is_paused ? "Играть" : "Пауза"}
          disabled={busy}
          onClick={() => act(() => api.musicControl(guild.id, np.is_paused ? "resume" : "pause"))}
        >
          {np.is_paused ? <IconPlay /> : <IconPause />}
        </button>
        <button
          className="mc-btn"
          title="Следующий трек"
          aria-label="Следующий трек"
          disabled={busy}
          onClick={() => act(() => api.musicControl(guild.id, "skip"))}
        >
          <IconSkipNext />
        </button>
        <button
          className="mc-btn"
          title="Стоп"
          aria-label="Стоп"
          disabled={busy}
          onClick={() => act(() => api.musicControl(guild.id, "stop"))}
        >
          <IconStop />
        </button>
        <button
          className={`mc-btn${np.repeat !== "off" ? " active" : ""}`}
          title={`Повтор: ${REPEAT_TITLE[np.repeat] ?? np.repeat}`}
          aria-label={`Повтор: ${REPEAT_TITLE[np.repeat] ?? np.repeat}`}
          disabled={busy}
          onClick={() => act(() => api.musicControl(guild.id, "repeat"))}
        >
          <IconRepeat />
          {np.repeat === "one" && <span className="mc-badge">1</span>}
        </button>
      </div>

      <div className="np-volume">
        <span className="np-vol-ic" aria-hidden>
          🔊
        </span>
        <input
          className="np-vol-slider"
          type="range"
          min={0}
          max={100}
          value={Math.round(vol * 100)}
          aria-label="Громкость"
          disabled={busy}
          onPointerDown={() => {
            draggingVol.current = true;
          }}
          onChange={(e) => setVol(Number(e.target.value) / 100)}
          onPointerUp={() => {
            draggingVol.current = false;
            act(() => api.musicVolume(guild.id, vol));
          }}
          onKeyUp={() => act(() => api.musicVolume(guild.id, vol))}
        />
        <span className="np-vol-val mono faint">{Math.round(vol * 100)}%</span>
      </div>

      {np.queue.length > 0 && (
        <div className="np-queue">
          <div className="section-title" style={{ margin: "8px 0 4px" }}>
            Очередь · {np.queue.length}
          </div>
          <ol className="track-list">
            {np.queue.slice(0, 20).map((t: NowTrack, i) => (
              <li className="track-item" key={i}>
                <span className="track-title">
                  {t.title}
                  {t.uploader && <span className="faint"> · {t.uploader}</span>}
                </span>
                <span className="track-dur mono faint">
                  {t.duration ? fmt(t.duration) : "эфир"}
                </span>
                <button
                  className="mc-remove"
                  title="Убрать из очереди"
                  aria-label={`Убрать из очереди: ${t.title}`}
                  disabled={busy}
                  onClick={() => act(() => api.musicRemove(guild.id, i + 1))}
                >
                  <IconX />
                </button>
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}
