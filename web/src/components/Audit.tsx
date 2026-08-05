import { useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../api";
import { useRefetchOnFocus } from "../refresh";
import type { AuditEntry, Guild } from "../types";
import { Dropdown } from "./Dropdown";
import { EmptyState } from "./EmptyState";
import { RefreshButton } from "./RefreshButton";
import { SkeletonRows } from "./Skeleton";

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
  "profile.apply": "Профиль бота",
  "role.assign": "Выдана роль",
  "role.unassign": "Снята роль",
  "role.create": "Создана роль",
  "role.edit": "Изменена роль",
  "role.delete": "Удалена роль",
  "role.reorder": "Порядок ролей",
  "role.permissions": "Права роли",
  "role.bulk": "Массово по роли",
};

// группа действия для фильтра (по префиксу)
function group(action: string): "roles" | "moderation" | "settings" | "music" | "other" {
  if (action.startsWith("role.")) return "roles";
  if (action.startsWith("mod.") || action === "warns.clear") return "moderation";
  if (action.startsWith("settings.")) return "settings";
  if (action.startsWith("music.")) return "music";
  return "other";
}

function label(action: string): string {
  return ACTION_LABELS[action] ?? action;
}

// подписи ключей в деталях
const DETAIL_KEYS: Record<string, string> = {
  name: "имя",
  hoist: "выделить",
  mentionable: "упоминаемая",
  count: "ролей",
  minutes: "мин",
  reason: "причина",
  value: "значение",
  role_id: "роль",
};

function fmtTime(iso: string): string {
  return new Date(iso).toLocaleString("ru-RU", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// компактно показать JSON-детали. Особые случаи: битовое поле прав не
// вываливаем числом, цвет показываем как #hex.
function fmtDetails(raw: string | null): string {
  if (!raw) return "";
  try {
    const obj = JSON.parse(raw) as Record<string, unknown>;
    const parts: string[] = [];
    for (const [k, v] of Object.entries(obj)) {
      if (v === "" || v === null) continue;
      if (k === "permissions") {
        parts.push("права обновлены");
        continue;
      }
      if (k === "color") {
        parts.push(
          typeof v === "number"
            ? `цвет ${v ? `#${v.toString(16).padStart(6, "0")}` : "нет"}`
            : `цвет: ${String(v)}`,
        );
        continue;
      }
      if (k === "op") {
        parts.push(v === "assign" ? "выдача всем" : v === "unassign" ? "снятие у всех" : String(v));
        continue;
      }
      const key = DETAIL_KEYS[k] ?? k;
      parts.push(`${key}: ${Array.isArray(v) ? v.join(", ") : String(v)}`);
    }
    return parts.join(" · ");
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
        {e.result && e.result !== "ok" && e.result !== "done" && (
          <span className="cine-review">«{e.result}»</span>
        )}
      </span>
      <span className="cine-side mono faint">{fmtTime(e.created_at)}</span>
    </div>
  );
}

const GROUP_OPTIONS = [
  { value: "", label: "Все действия" },
  { value: "roles", label: "Роли" },
  { value: "moderation", label: "Модерация" },
  { value: "settings", label: "Настройки" },
  { value: "music", label: "Музыка" },
  { value: "other", label: "Прочее" },
];

export function Audit({ guild }: { guild: Guild }) {
  const [entries, setEntries] = useState<AuditEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState(""); // "" = все; иначе группа
  const [limit, setLimit] = useState(100);
  const [busy, setBusy] = useState(false);

  function load() {
    setBusy(true);
    api
      .audit(guild.id, limit)
      .then(setEntries)
      .catch((e) => {
        if (e instanceof ApiError && e.status === 404) setError("Попоси нет на этом сервере.");
        else setError(e instanceof Error ? e.message : "Не удалось загрузить журнал");
      })
      .finally(() => setBusy(false));
  }

  useEffect(() => {
    setEntries(null);
    setError(null);
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [guild.id, limit]);
  useRefetchOnFocus(load);

  const shown = useMemo(() => {
    if (!entries) return [];
    return filter ? entries.filter((e) => group(e.action) === filter) : entries;
  }, [entries, filter]);

  if (error) return <div className="error-banner">{error}</div>;
  if (!entries)
    return (
      <div>
        <h2 className="section-title">Действия через панель</h2>
        <div className="card leader-card">
          <div className="pad">
            <SkeletonRows rows={6} avatar={false} />
          </div>
        </div>
      </div>
    );

  return (
    <div>
      <div className="tab-tools">
        <RefreshButton onClick={load} busy={busy} />
      </div>
      <h2 className="section-title">Действия через панель</h2>
      <p className="muted" style={{ marginTop: -8, marginBottom: 16 }}>
        Кто и что делал из панели: роли, модерация, очки, настройки, удаления. Действия командами
        бота прямо в Discord сюда не попадают.
      </p>

      <div className="people-filters" style={{ marginBottom: 12 }}>
        <Dropdown ariaLabel="Тип действия" value={filter} onChange={setFilter} options={GROUP_OPTIONS} />
        <Dropdown
          ariaLabel="Сколько записей"
          value={String(limit)}
          onChange={(v) => setLimit(Number(v))}
          options={[
            { value: "100", label: "Последние 100" },
            { value: "300", label: "Последние 300" },
            { value: "1000", label: "Последние 1000" },
          ]}
        />
        <span className="people-count faint small">
          {shown.length}
          {filter && entries.length !== shown.length ? ` из ${entries.length}` : ""}
        </span>
      </div>

      <div className="card leader-card">
        {shown.length === 0 ? (
          <EmptyState
            compact
            title={entries.length === 0 ? "Журнал пуст" : "Под фильтр ничего не попало"}
            hint={
              entries.length === 0
                ? "Здесь появятся действия через панель: роли, модерация, очки, настройки."
                : "Сбрось фильтр по типу действия, чтобы увидеть все записи."
            }
          />
        ) : (
          shown.map((e) => <AuditRow key={e.id} e={e} />)
        )}
      </div>
    </div>
  );
}
