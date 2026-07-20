import { useEffect, useState } from "react";

import { ApiError, api } from "../api";
import type { Guild, GuildRole, RolesView } from "../types";

// int цвета Discord -> #rrggbb; 0 = «без цвета» (null -> нейтральный свотч)
function hexColor(c: number): string | null {
  if (!c) return null;
  return `#${c.toString(16).padStart(6, "0")}`;
}

// битовое поле прав не влезает в number — сравниваем через BigInt.
// 0x8 = Administrator: единственное право, которое стоит подсветить даже в
// режиме просмотра (выдать роль с ним = отдать сервер).
function hasAdmin(permissions: string): boolean {
  try {
    return (BigInt(permissions) & 8n) === 8n;
  } catch {
    return false;
  }
}

function syncedAgo(iso: string | null): string {
  if (!iso) return "не синхронизировано";
  const sec = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (sec < 60) return `${sec} с назад`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min} мин назад`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} ч назад`;
  return `${Math.floor(hr / 24)} дн назад`;
}

function RoleRow({ r }: { r: GuildRole }) {
  const color = hexColor(r.color);
  return (
    <div className={`role-row${r.editable ? "" : " locked"}`}>
      <span
        className={`role-swatch${color ? "" : " empty"}`}
        style={color ? { background: color } : undefined}
        aria-hidden="true"
      />
      <span className="role-row-name" style={color ? { color } : undefined}>
        {r.name}
      </span>
      <span className="role-badges">
        {hasAdmin(r.permissions) && (
          <span className="role-badge admin" title="Право Administrator — полный доступ">
            ADMIN
          </span>
        )}
        {r.hoist && (
          <span className="role-badge" title="Показана отдельной группой в списке участников">
            выделена
          </span>
        )}
        {r.mentionable && (
          <span className="role-badge" title="Роль можно упоминать (@)">
            @
          </span>
        )}
        {r.managed && (
          <span className="role-badge" title="Роль интеграции/бустов — Discord не даёт её менять">
            managed
          </span>
        )}
        {r.is_default && <span className="role-badge">@everyone</span>}
        {!r.editable && !r.managed && !r.is_default && (
          <span className="role-badge lock" title="Выше роли Попоси — ей недоступна">
            🔒
          </span>
        )}
      </span>
    </div>
  );
}

export function Roles({ guild }: { guild: Guild }) {
  const [data, setData] = useState<RolesView | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    setData(null);
    setErr("");
    api
      .roles(guild.id)
      .then(setData)
      .catch((e) => setErr(e instanceof ApiError ? e.message : "Не удалось загрузить роли"));
  }, [guild.id]);

  if (err) return <div className="error-banner">{err}</div>;
  if (!data)
    return (
      <div className="center" style={{ minHeight: 200 }}>
        <div className="spinner" aria-label="Загрузка" />
      </div>
    );

  const botTop = data.bot_top_position;
  // роли уже отсортированы сверху вниз; линия бота — перед первой ролью, что
  // ниже его высшей (position < botTop): всё под ней боту доступно
  const lineBefore = botTop == null ? -1 : data.roles.findIndex((r) => r.position < botTop);

  return (
    <div className="card pad">
      <div className="roles-head">
        <div className="h1">Роли сервера</div>
        <div className="faint small">
          {data.roles.length} ролей · зеркало {syncedAgo(data.synced_at)}
        </div>
      </div>
      <p className="muted small roles-hint">
        Пока только просмотр: бот держит этот список в зеркале и знает актуальную иерархию. Всё, что
        ниже линии Попоси, будет доступно для управления (имя, цвет, порядок, выдача) на следующем
        шаге.
      </p>

      {data.roles.length === 0 ? (
        <div className="muted small pad">
          {botTop == null
            ? "Зеркало ещё не синхронизировано — загляни через минуту после перезапуска бота."
            : "Ролей нет."}
        </div>
      ) : (
        <div className="roles-list">
          {data.roles.map((r, i) => (
            <div key={r.id}>
              {i === lineBefore && (
                <div className="bot-line">
                  <span>линия Попоси · ниже — доступно боту</span>
                </div>
              )}
              <RoleRow r={r} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
