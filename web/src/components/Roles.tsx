import { useCallback, useEffect, useState } from "react";

import { ApiError, api } from "../api";
import {
  IconCheck,
  IconChevronDown,
  IconChevronUp,
  IconGrip,
  IconPencil,
  IconPlus,
  IconShield,
  IconTrash,
} from "../icons";
import { discordColor } from "../roles";
import type { CommandResult, Guild, GuildRole, RoleInput, RolesView } from "../types";
import { RolePermsEditor } from "./RolePerms";

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

function toHex(c: number): string {
  return `#${c.toString(16).padStart(6, "0")}`;
}

// Перестановка порядка ровно как её сделает бот: редактируемые роли
// перераспределяются по СВОИМ же позициям (слотам), заблокированные (managed,
// @everyone, выше бота) остаются на месте. ids — редактируемые сверху вниз.
function applyEditableOrder(all: GuildRole[], ids: string[]): GuildRole[] {
  const slots = all
    .filter((r) => r.editable)
    .map((r) => r.position)
    .sort((a, b) => b - a); // сверху вниз: самый высокий слот первым
  const pos = new Map<string, number>();
  ids.forEach((id, i) => pos.set(id, slots[i]));
  return all
    .map((r) => (pos.has(r.id) ? { ...r, position: pos.get(r.id)! } : r))
    .sort((a, b) => b.position - a.position);
}

function RoleBadges({ r }: { r: GuildRole }) {
  return (
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
  );
}

// форма создания/правки роли. Права роли (permissions) намеренно отсутствуют —
// их редактирование появится отдельным этапом.
function RoleEditor({
  initial,
  submitLabel,
  busy,
  onSubmit,
  onCancel,
  onDelete,
  onBulk,
  holders,
}: {
  initial: RoleInput;
  submitLabel: string;
  busy: boolean;
  onSubmit: (v: RoleInput) => void;
  onCancel: () => void;
  onDelete?: () => void;
  onBulk?: (op: "assign" | "unassign") => void;
  holders?: number | null;
}) {
  const [name, setName] = useState(initial.name);
  const [colorOn, setColorOn] = useState(initial.color != null && initial.color !== 0);
  const [hex, setHex] = useState(initial.color ? toHex(initial.color) : "#8b5cf6");
  const [hoist, setHoist] = useState(initial.hoist);
  const [mentionable, setMentionable] = useState(initial.mentionable);
  const [confirmDel, setConfirmDel] = useState(false);
  const [bulkConfirm, setBulkConfirm] = useState<"assign" | "unassign" | null>(null);

  const trimmed = name.trim();

  function submit() {
    if (!trimmed) return;
    onSubmit({
      name: trimmed,
      color: colorOn ? parseInt(hex.slice(1), 16) : null,
      hoist,
      mentionable,
    });
  }

  return (
    <div className="role-editor">
      <div className="role-editor-row">
        <input
          className="input role-name-input"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Имя роли"
          maxLength={100}
          aria-label="Имя роли"
          autoFocus
        />
        <label className={`role-color-pick${colorOn ? "" : " off"}`} title="Цвет роли">
          <input
            type="color"
            value={hex}
            onChange={(e) => {
              setHex(e.target.value);
              setColorOn(true);
            }}
            aria-label="Цвет роли"
          />
          <span
            className="role-color-preview"
            style={{ background: colorOn ? hex : "transparent" }}
            aria-hidden="true"
          />
        </label>
        <button type="button" className="btn ghost small" onClick={() => setColorOn((v) => !v)}>
          {colorOn ? "Без цвета" : "Задать цвет"}
        </button>
      </div>

      <div className="role-editor-row toggles">
        <span className="role-toggle">
          <button
            type="button"
            className={`toggle${hoist ? " on" : ""}`}
            role="switch"
            aria-checked={hoist}
            aria-label="Отдельной группой"
            onClick={() => setHoist((v) => !v)}
          >
            <span className="knob" />
          </button>
          <span className="faint small">Отдельной группой (hoist)</span>
        </span>
        <span className="role-toggle">
          <button
            type="button"
            className={`toggle${mentionable ? " on" : ""}`}
            role="switch"
            aria-checked={mentionable}
            aria-label="Можно упоминать"
            onClick={() => setMentionable((v) => !v)}
          >
            <span className="knob" />
          </button>
          <span className="faint small">Можно упоминать (@)</span>
        </span>
      </div>

      <div className="role-editor-actions">
        <button className="btn primary small" onClick={submit} disabled={busy || !trimmed}>
          <IconCheck /> {submitLabel}
        </button>
        <button className="btn ghost small" onClick={onCancel} disabled={busy}>
          Отмена
        </button>
        {onDelete &&
          (confirmDel ? (
            <span className="role-del-confirm">
              <span className="faint small">Снимет роль со всех и удалит.</span>
              <button className="btn danger small" onClick={onDelete} disabled={busy}>
                Да, удалить
              </button>
              <button
                className="btn ghost small"
                onClick={() => setConfirmDel(false)}
                disabled={busy}
              >
                Нет
              </button>
            </span>
          ) : (
            <button
              className="btn danger ghost small role-del-btn"
              onClick={() => setConfirmDel(true)}
              disabled={busy}
            >
              <IconTrash /> Удалить
            </button>
          ))}
        <span className="role-editor-hint faint small">Права роли — по кнопке щита 🛡.</span>
      </div>

      {onBulk && (
        <div className="role-bulk">
          <span className="faint small">
            Массово{typeof holders === "number" ? ` · носителей: ${holders}` : ""}:
          </span>
          {bulkConfirm ? (
            <span className="role-del-confirm">
              <span className="faint small">
                {bulkConfirm === "assign" ? "Выдать всем, у кого её нет?" : "Снять у всех носителей?"}
              </span>
              <button
                className="btn primary small"
                onClick={() => {
                  onBulk(bulkConfirm);
                  setBulkConfirm(null);
                }}
                disabled={busy}
              >
                Да
              </button>
              <button
                className="btn ghost small"
                onClick={() => setBulkConfirm(null)}
                disabled={busy}
              >
                Нет
              </button>
            </span>
          ) : (
            <>
              <button
                className="btn ghost small"
                onClick={() => setBulkConfirm("assign")}
                disabled={busy}
              >
                Выдать всем
              </button>
              <button
                className="btn ghost small"
                onClick={() => setBulkConfirm("unassign")}
                disabled={busy}
              >
                Снять у всех
              </button>
            </>
          )}
        </div>
      )}
    </div>
  );
}

export function Roles({ guild }: { guild: Guild }) {
  const [view, setView] = useState<RolesView | null>(null);
  const [roles, setRoles] = useState<GuildRole[]>([]);
  // копия порядка до первой перестановки; null = порядок совпадает с сервером
  const [snapshot, setSnapshot] = useState<GuildRole[] | null>(null);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [permsId, setPermsId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [dragId, setDragId] = useState<string | null>(null);
  const [overId, setOverId] = useState<string | null>(null);

  const load = useCallback(() => {
    setView(null);
    setErr("");
    setMsg(null);
    setSnapshot(null);
    setEditingId(null);
    setPermsId(null);
    setCreating(false);
    api
      .roles(guild.id)
      .then((v) => {
        setView(v);
        setRoles(v.roles);
      })
      .catch((e) => setErr(e instanceof ApiError ? e.message : "Не удалось загрузить роли"));
  }, [guild.id]);

  useEffect(load, [load]);

  // мягкий рефетч без спиннера (после создания роли — подхватить её из зеркала)
  const refresh = useCallback(() => {
    api
      .roles(guild.id)
      .then((v) => {
        setView(v);
        setRoles(v.roles);
        setSnapshot(null);
      })
      .catch(() => {
        /* оставляем что есть */
      });
  }, [guild.id]);

  function report(r: CommandResult): boolean {
    if (r.status === "failed") setMsg(r.result ?? "Не вышло");
    else if (r.status === "done") setMsg(r.result ?? "Готово");
    else setMsg("Отправлено — применяется…");
    return r.status !== "failed";
  }

  const orderDirty = snapshot !== null;
  const editableIds = roles.filter((r) => r.editable).map((r) => r.id);
  const canReorder = !orderDirty ? editableIds.length >= 2 : true;
  // любой редактор открыт — порядок не трогаем
  const rowsLocked = editingId !== null || permsId !== null || creating;

  // --- порядок ---

  function commitOrder(ids: string[]) {
    setSnapshot((s) => s ?? roles);
    setRoles((rs) => applyEditableOrder(rs, ids));
  }

  function move(id: string, dir: -1 | 1) {
    const i = editableIds.indexOf(id);
    const j = i + dir;
    if (i < 0 || j < 0 || j >= editableIds.length) return;
    const ids = [...editableIds];
    [ids[i], ids[j]] = [ids[j], ids[i]];
    commitOrder(ids);
  }

  function handleDrop(targetId: string) {
    if (!dragId || dragId === targetId) {
      setDragId(null);
      setOverId(null);
      return;
    }
    const ids = editableIds.filter((id) => id !== dragId);
    const ti = ids.indexOf(targetId);
    if (ti >= 0) {
      ids.splice(ti, 0, dragId);
      commitOrder(ids);
    }
    setDragId(null);
    setOverId(null);
  }

  async function saveOrder() {
    setBusy(true);
    setMsg(null);
    try {
      const ok = report(await api.reorderRoles(guild.id, editableIds));
      if (ok) setSnapshot(null);
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : "Ошибка");
    } finally {
      setBusy(false);
    }
  }

  function cancelOrder() {
    if (snapshot) setRoles(snapshot);
    setSnapshot(null);
  }

  // --- CRUD ---

  async function doCreate(input: RoleInput) {
    setBusy(true);
    setMsg(null);
    try {
      const ok = report(await api.createRole(guild.id, input));
      if (ok) {
        setCreating(false);
        window.setTimeout(refresh, 900); // дать зеркалу догнать gateway-событие
      }
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : "Ошибка");
    } finally {
      setBusy(false);
    }
  }

  async function doEdit(role: GuildRole, input: RoleInput) {
    setBusy(true);
    setMsg(null);
    try {
      const ok = report(await api.editRole(guild.id, role.id, input));
      if (ok) {
        setRoles((rs) =>
          rs.map((r) =>
            r.id === role.id
              ? {
                  ...r,
                  name: input.name,
                  color: input.color ?? 0,
                  hoist: input.hoist,
                  mentionable: input.mentionable,
                }
              : r,
          ),
        );
        setEditingId(null);
      }
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : "Ошибка");
    } finally {
      setBusy(false);
    }
  }

  async function doBulk(role: GuildRole, op: "assign" | "unassign") {
    setBusy(true);
    setMsg(null);
    try {
      report(await api.bulkRole(guild.id, role.id, op));
      window.setTimeout(refresh, 900); // носители изменились — подтянуть счётчики
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : "Ошибка");
    } finally {
      setBusy(false);
    }
  }

  async function doDelete(role: GuildRole) {
    setBusy(true);
    setMsg(null);
    try {
      const ok = report(await api.deleteRole(guild.id, role.id));
      if (ok) {
        setRoles((rs) => rs.filter((r) => r.id !== role.id));
        setEditingId(null);
      }
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : "Ошибка");
    } finally {
      setBusy(false);
    }
  }

  if (err) return <div className="error-banner">{err}</div>;
  if (!view)
    return (
      <div className="center" style={{ minHeight: 200 }}>
        <div className="spinner" aria-label="Загрузка" />
      </div>
    );

  const botTop = view.bot_top_position;
  // роли отсортированы сверху вниз; линия бота — перед первой ролью ниже его
  // высшей (position < botTop): всё под ней боту доступно
  const lineBefore = botTop == null ? -1 : roles.findIndex((r) => r.position < botTop);

  return (
    <div className="card pad">
      <div className="roles-head">
        <div className="h1">Роли сервера</div>
        <div className="roles-head-right">
          {msg && <span className="faint small">{msg}</span>}
          <span className="faint small">
            {roles.length} ролей · зеркало {syncedAgo(view.synced_at)}
          </span>
          <button
            className="btn primary small"
            onClick={() => {
              setCreating(true);
              setEditingId(null);
            }}
            disabled={orderDirty || creating || busy}
          >
            <IconPlus /> Новая роль
          </button>
        </div>
      </div>
      <p className="muted small roles-hint">
        Всё, что ниже линии Попоси, можно переименовать, перекрасить, переставить, удалить и
        настроить права (щит). Перетаскивай за ручку или жми стрелки — порядок применится по
        кнопке. Administrator панель не выдаёт, опасные права просят подтверждения.
      </p>

      {creating && (
        <RoleEditor
          initial={{ name: "", color: null, hoist: false, mentionable: false }}
          submitLabel="Создать"
          busy={busy}
          onSubmit={doCreate}
          onCancel={() => setCreating(false)}
        />
      )}

      {orderDirty && (
        <div className="order-bar">
          <span className="faint small">Порядок изменён — не сохранён.</span>
          <div className="order-bar-actions">
            <button className="btn primary small" onClick={saveOrder} disabled={busy}>
              <IconCheck /> Сохранить порядок
            </button>
            <button className="btn ghost small" onClick={cancelOrder} disabled={busy}>
              Отменить
            </button>
          </div>
        </div>
      )}

      {roles.length === 0 ? (
        <div className="muted small pad">
          {botTop == null
            ? "Зеркало ещё не синхронизировано — загляни через минуту после перезапуска бота."
            : "Ролей нет."}
        </div>
      ) : (
        <div className="roles-list">
          {roles.map((r, i) => {
            const color = discordColor(r.color);
            const canDrag = r.editable && !rowsLocked;
            if (editingId === r.id) {
              return (
                <div key={r.id}>
                  {i === lineBefore && (
                    <div className="bot-line">
                      <span>линия Попоси · ниже — доступно боту</span>
                    </div>
                  )}
                  <RoleEditor
                    initial={{
                      name: r.name,
                      color: r.color || null,
                      hoist: r.hoist,
                      mentionable: r.mentionable,
                    }}
                    submitLabel="Сохранить"
                    busy={busy}
                    onSubmit={(v) => doEdit(r, v)}
                    onCancel={() => setEditingId(null)}
                    onDelete={() => doDelete(r)}
                    onBulk={(op) => doBulk(r, op)}
                    holders={r.holders}
                  />
                </div>
              );
            }
            if (permsId === r.id) {
              return (
                <div key={r.id}>
                  {i === lineBefore && (
                    <div className="bot-line">
                      <span>линия Попоси · ниже — доступно боту</span>
                    </div>
                  )}
                  <RolePermsEditor
                    guildId={guild.id}
                    role={r}
                    onClose={() => setPermsId(null)}
                    onSaved={(permissions) => {
                      setRoles((rs) =>
                        rs.map((x) => (x.id === r.id ? { ...x, permissions } : x)),
                      );
                      setPermsId(null);
                    }}
                  />
                </div>
              );
            }
            return (
              <div key={r.id}>
                {i === lineBefore && (
                  <div className="bot-line">
                    <span>линия Попоси · ниже — доступно боту</span>
                  </div>
                )}
                <div
                  className={`role-row${r.editable ? "" : " locked"}${
                    overId === r.id ? " drag-over" : ""
                  }${dragId === r.id ? " dragging" : ""}`}
                  draggable={canDrag}
                  onDragStart={() => canDrag && setDragId(r.id)}
                  onDragOver={(e) => {
                    if (dragId && r.editable && r.id !== dragId) {
                      e.preventDefault();
                      setOverId(r.id);
                    }
                  }}
                  onDragLeave={() => overId === r.id && setOverId(null)}
                  onDrop={() => handleDrop(r.id)}
                  onDragEnd={() => {
                    setDragId(null);
                    setOverId(null);
                  }}
                >
                  {r.editable && canReorder && !rowsLocked ? (
                    <span className="role-grip" title="Перетащить для смены порядка" aria-hidden="true">
                      <IconGrip />
                    </span>
                  ) : (
                    <span className="role-grip empty" aria-hidden="true" />
                  )}
                  <span
                    className={`role-swatch${color ? "" : " empty"}`}
                    style={color ? { background: color } : undefined}
                    aria-hidden="true"
                  />
                  <span className="role-row-name" style={color ? { color } : undefined}>
                    {r.name}
                  </span>
                  <span className="role-holders faint small" title="носителей на сервере">
                    {r.is_default ? "—" : (r.holders ?? 0)}
                  </span>
                  <RoleBadges r={r} />
                  {r.editable && (
                    <span className="role-actions">
                      {canReorder && !rowsLocked && (
                        <>
                          <button
                            className="icon-btn"
                            onClick={() => move(r.id, -1)}
                            disabled={busy || editableIds.indexOf(r.id) === 0}
                            aria-label={`Поднять роль ${r.name}`}
                            title="Выше"
                          >
                            <IconChevronUp />
                          </button>
                          <button
                            className="icon-btn"
                            onClick={() => move(r.id, 1)}
                            disabled={busy || editableIds.indexOf(r.id) === editableIds.length - 1}
                            aria-label={`Опустить роль ${r.name}`}
                            title="Ниже"
                          >
                            <IconChevronDown />
                          </button>
                        </>
                      )}
                      <button
                        className="icon-btn"
                        onClick={() => {
                          setEditingId(r.id);
                          setPermsId(null);
                          setCreating(false);
                        }}
                        disabled={busy || orderDirty}
                        aria-label={`Редактировать роль ${r.name}`}
                        title="Редактировать"
                      >
                        <IconPencil />
                      </button>
                      <button
                        className="icon-btn"
                        onClick={() => {
                          setPermsId(r.id);
                          setEditingId(null);
                          setCreating(false);
                        }}
                        disabled={busy || orderDirty}
                        aria-label={`Права роли ${r.name}`}
                        title="Права роли"
                      >
                        <IconShield />
                      </button>
                    </span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
