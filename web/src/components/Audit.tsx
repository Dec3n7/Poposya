import { useEffect, useState } from "react";

import { ApiError, api } from "../api";
import type { AuditEntry, Guild } from "../types";

// человекочитаемые названия действий
const ACTION_LABELS: Record<string, string> = {
  "mod.ban": "Бан",
  "mod.unban": "Разбан",
  "mod.mute": "Мут",
  "mod.unmute": "Снятие мута",
  "warns.clear": "Сброс варнов",
  "points.set": "Правка очков",
  "freeze.toggle": "Заморозка",
  "movie.remove": "Убран фильм",
  "playlist.delete": "Удалён плейлист",
  "settings.set": "Настройка",
  "settings.reset": "Сброс настройки",
  "settings.batch": "Настройки (пакет)",
  "music.pause": "Пауза",
  "music.resume": "Плей",
  "music.skip": "Пропуск",
  "music.stop": "Стоп",
};

function label(action: string): string {
  return ACTION_LABELS[action] ?? action;
}

function fmtTime(iso: string): string {
  return new Date(iso).toLocaleString("ru-RU", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// компактно показать JSON-детали: {value: 5} -> "value: 5"
function fmtDetails(raw: string | null): string {
  if (!raw) return "";
  try {
    const obj = JSON.parse(raw);
    return Object.entries(obj)
      .filter(([, v]) => v !== "" && v !== null)
      .map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(", ") : v}`)
      .join(" · ");
  } catch {
    return raw;
  }
}

function AuditRow({ e }: { e: AuditEntry }) {
  const actor = e.actor_name ?? `ID ${e.actor_id}`;
  const target = e.target_name ?? e.target;
  const details = fmtDetails(e.details);
  return (
    <div className="cine-row">
      <span className="cine-title">
        {e.actor_avatar ? (
          <img className="leader-avatar sm" src={e.actor_avatar} alt="" />
        ) : (
          <span className="leader-avatar sm fallback">{actor.slice(0, 1).toUpperCase()}</span>
        )}
        <span className="audit-actor">{actor}</span>
        <span className="badge">{label(e.action)}</span>
        {target && <span className="audit-target">→ {target}</span>}
        {details && <span className="faint small">· {details}</span>}
        {e.result && e.result !== "ok" && (
          <span className="cine-review">«{e.result}»</span>
        )}
      </span>
      <span className="cine-side mono faint">{fmtTime(e.created_at)}</span>
    </div>
  );
}

export function Audit({ guild }: { guild: Guild }) {
  const [entries, setEntries] = useState<AuditEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setEntries(null);
    setError(null);
    api
      .audit(guild.id)
      .then(setEntries)
      .catch((e) => {
        if (e instanceof ApiError && e.status === 404) setError("Попоси нет на этом сервере.");
        else setError(e instanceof Error ? e.message : "Не удалось загрузить журнал");
      });
  }, [guild.id]);

  if (error) return <div className="error-banner">{error}</div>;
  if (!entries)
    return (
      <div className="center" style={{ minHeight: 200 }}>
        <div className="spinner" aria-label="Загрузка" />
      </div>
    );

  return (
    <div>
      <h2 className="section-title">Действия через панель</h2>
      <p className="muted" style={{ marginTop: -8, marginBottom: 16 }}>
        Кто и что делал из панели: модерация, очки, настройки, удаления. Действия командами бота в
        Discord сюда не попадают.
      </p>
      <div className="card leader-card">
        {entries.length === 0 ? (
          <div className="pad muted">Пока пусто — действий через панель не было.</div>
        ) : (
          entries.map((e) => <AuditRow key={e.id} e={e} />)
        )}
      </div>
    </div>
  );
}
