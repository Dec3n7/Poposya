import { useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../api";
import { IconX } from "../icons";
import { GATE } from "../perms";
import { activityMeta, discordColor, roleColor } from "../roles";
import type {
  CommandResult,
  Guild,
  GuildPerms,
  MemberRoles,
  ModCase,
  PersonDetail,
  PersonListItem,
  Warn,
} from "../types";
import { Dropdown } from "./Dropdown";
import { EmptyState } from "./EmptyState";
import { ACTION_LABELS } from "./Moderation";
import { RoleChip } from "./RoleChip";
import { Skeleton, SkeletonRows } from "./Skeleton";
import { useToast } from "./Toast";

type SortKey = "points" | "name" | "dialog" | "silent" | "role";
const NO_ROLE = "__no_role__";
const VISIBLE_CAP = 200;

// давность диалога для сортировки: старее/никогда -> раньше в списке «дольше
// молчали». null (никогда) = -Infinity, встаёт первым.
function dialogTs(iso: string | null): number {
  if (!iso) return -Infinity;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? -Infinity : t;
}

// экранирование поля CSV (RFC 4180): кавычки удваиваем, оборачиваем при спецсимволах
function csvCell(v: string | number | boolean): string {
  const s = String(v);
  return /[",\n;]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

// выгрузка отфильтрованного списка в CSV (с BOM — Excel правильно читает кириллицу)
function downloadCsv(guildName: string, rows: PersonListItem[]): void {
  const header = ["Имя", "ID", "Очки", "Роль", "Заморожен", "Последний диалог"];
  const lines = rows.map((e) =>
    [
      e.username ?? e.user_id,
      e.user_id,
      e.points,
      e.role ?? "",
      e.frozen ? "да" : "нет",
      e.last_dialog_at ? e.last_dialog_at.slice(0, 10) : "",
    ]
      .map(csvCell)
      .join(","),
  );
  const csv = "﻿" + [header.join(","), ...lines].join("\r\n");
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = `poposya-${guildName}-люди.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

// строка активности в общей строке человека
function ActivityBadge({ iso }: { iso: string | null }) {
  const a = activityMeta(iso);
  if (!a) return null;
  return (
    <span className={`activity-badge ${a.tone}`} title={a.title}>
      {a.label}
    </span>
  );
}

const MONTHS = [
  "янв",
  "фев",
  "мар",
  "апр",
  "мая",
  "июн",
  "июл",
  "авг",
  "сен",
  "окт",
  "ноя",
  "дек",
];

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleDateString("ru-RU", { day: "numeric", month: "short", year: "numeric" });
}

function ModActions({
  guildId,
  userId,
  perms,
  onActed,
}: {
  guildId: string;
  userId: string;
  perms: GuildPerms;
  onActed?: () => void;
}) {
  const [minutes, setMinutes] = useState("60");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  // подтверждение по второму клику для необратимых действий: null | "kick" | "ban_perm"
  const [confirmKind, setConfirmKind] = useState<null | "kick" | "ban_perm">(null);
  const toast = useToast();

  function show(r: { status: string; result: string | null }) {
    if (r.status === "done") toast.success(r.result ?? "Готово");
    else if (r.status === "failed") toast.error(r.result ?? "Не вышло");
    else toast.info("Отправлено — применяется…");
  }

  async function act(
    kind: "mute" | "unmute" | "ban" | "kick" | "ban_perm",
    fn: () => Promise<{ status: string; result: string | null }>,
  ) {
    setConfirmKind(null);
    setBusy(kind);
    try {
      show(await fn());
      onActed?.(); // журнал по этому человеку мог измениться — дадим родителю обновить
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Ошибка");
    } finally {
      setBusy(null);
    }
  }

  const mins = () => Math.max(1, parseInt(minutes, 10) || 0);

  return (
    <div className="person-warns">
      <div className="person-warns-head">
        <span className="faint">Модерация</span>
      </div>
      <div className="mod-actions">
        <label className="mod-field">
          <span className="faint small">Минуты</span>
          <input
            className="input mono"
            inputMode="numeric"
            value={minutes}
            onChange={(e) => setMinutes(e.target.value)}
          />
        </label>
        <input
          className="input mod-reason"
          placeholder="причина (для бана/кика)"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
        <div className="mod-buttons">
          <button
            className="btn small"
            disabled={busy !== null || !perms.can_moderate}
            title={perms.can_moderate ? undefined : GATE.moderate}
            onClick={() => act("mute", () => api.mute(guildId, userId, mins(), reason))}
          >
            🔇 Мут
          </button>
          <button
            className="btn ghost small"
            disabled={busy !== null || !perms.can_moderate}
            title={perms.can_moderate ? undefined : GATE.moderate}
            onClick={() => act("unmute", () => api.unmute(guildId, userId))}
          >
            Снять мут
          </button>
          <button
            className="btn ghost small"
            disabled={busy !== null || !perms.can_kick}
            title={perms.can_kick ? undefined : GATE.kick}
            onClick={() =>
              confirmKind === "kick"
                ? act("kick", () => api.kick(guildId, userId, reason))
                : setConfirmKind("kick")
            }
          >
            {confirmKind === "kick" ? "Точно? Кик" : "👢 Кик"}
          </button>
          <button
            className="btn small"
            disabled={busy !== null || !perms.can_ban}
            title={perms.can_ban ? undefined : GATE.ban}
            onClick={() => act("ban", () => api.ban(guildId, userId, mins(), reason))}
          >
            🔨 Бан (врем.)
          </button>
          <button
            className="btn danger small"
            disabled={busy !== null || !perms.can_ban}
            title={perms.can_ban ? undefined : GATE.ban}
            onClick={() =>
              confirmKind === "ban_perm"
                ? act("ban_perm", () => api.banPermanent(guildId, userId, reason))
                : setConfirmKind("ban_perm")
            }
          >
            {confirmKind === "ban_perm" ? "Точно? Навсегда" : "⛔ Бан навсегда"}
          </button>
        </div>
      </div>
    </div>
  );
}

function PersonHistory({
  guildId,
  userId,
  reloadKey,
}: {
  guildId: string;
  userId: string;
  reloadKey: number;
}) {
  const [cases, setCases] = useState<ModCase[] | null>(null);
  const [busy, setBusy] = useState(false);

  function load() {
    setBusy(true);
    api
      .history(guildId, userId)
      .then(setCases)
      .catch(() => setCases([]))
      .finally(() => setBusy(false));
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [guildId, userId, reloadKey]);

  return (
    <div className="person-warns">
      <div className="person-warns-head">
        <span className="faint">
          История модерации {cases && cases.length > 0 && `(${cases.length})`}
        </span>
        <button className="btn ghost small" onClick={load} disabled={busy} title="Обновить">
          ⟳
        </button>
      </div>
      {cases === null ? (
        <div className="muted small">Загрузка…</div>
      ) : cases.length === 0 ? (
        <div className="muted small">Действий модерации нет.</div>
      ) : (
        <ul className="warn-list">
          {cases.map((c) => (
            <li className="warn-item" key={c.id}>
              <span className="warn-reason">
                <b>{ACTION_LABELS[c.action] ?? c.action}</b>
                {c.duration_minutes ? ` · ${c.duration_minutes}м` : ""}
                {c.reason ? ` · ${c.reason}` : ""}
              </span>
              <span className="faint small">
                {fmtDate(c.created_at)}
                {" · "}
                {c.moderator_id ? (c.moderator_name ?? c.moderator_id) : "авто"}
                {c.source === "panel" ? " (панель)" : ""}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// сортировка ролей по позиции сверху вниз (в held/assignable порядок держим сами)
function byPosDesc(a: { position: number }, b: { position: number }): number {
  return b.position - a.position;
}

function MemberRolesSection({
  guildId,
  userId,
  perms,
}: {
  guildId: string;
  userId: string;
  perms: GuildPerms;
}) {
  const canManage = perms.can_manage_roles;
  const [data, setData] = useState<MemberRoles | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const toast = useToast();

  useEffect(() => {
    setData(null);
    setErr("");
    api
      .memberRoles(guildId, userId)
      .then(setData)
      .catch((e) => setErr(e instanceof ApiError ? e.message : "Не удалось загрузить роли"));
  }, [guildId, userId]);

  function report(r: CommandResult): boolean {
    if (r.status === "failed") toast.error(r.result ?? "Не вышло");
    else if (r.status === "done") toast.success(r.result ?? "Готово");
    else toast.info("Отправлено — применяется…");
    return r.status !== "failed";
  }

  // выдача/снятие идут через мост (может вернуться pending); зеркало обновится
  // gateway-событием с задержкой, поэтому список правим оптимистично на месте
  async function add(roleId: string) {
    if (!data || busy) return;
    setBusy(true);
    try {
      const ok = report(await api.assignRole(guildId, userId, roleId));
      const role = data.assignable.find((x) => x.id === roleId);
      if (ok && role) {
        setData({
          held: [...data.held, role].sort(byPosDesc),
          assignable: data.assignable.filter((x) => x.id !== roleId),
        });
      }
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Ошибка");
    } finally {
      setBusy(false);
    }
  }

  async function remove(roleId: string) {
    if (!data || busy) return;
    setBusy(true);
    try {
      const ok = report(await api.unassignRole(guildId, userId, roleId));
      const role = data.held.find((x) => x.id === roleId);
      if (ok && role) {
        setData({
          held: data.held.filter((x) => x.id !== roleId),
          // вернуть в «выдать» можно только доступную боту роль
          assignable: role.editable
            ? [...data.assignable, role].sort(byPosDesc)
            : data.assignable,
        });
      }
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Ошибка");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="person-warns">
      <div className="person-warns-head">
        <span className="faint">Роли Discord</span>
      </div>
      {err ? (
        <div className="muted small">{err}</div>
      ) : !data ? (
        <div className="skeleton-text" style={{ padding: "4px 0" }}>
          <Skeleton h={12} w="60%" />
          <Skeleton h={12} w="40%" />
        </div>
      ) : (
        <>
          <div className="mrole-chips">
            {data.held.length === 0 && <span className="muted small">Ролей нет.</span>}
            {data.held.map((r) => {
              const color = discordColor(r.color);
              return (
                <span
                  key={r.id}
                  className={`mrole-chip${r.editable ? "" : " locked"}`}
                  title={r.editable ? undefined : "Выше роли Попоси — снять нельзя"}
                >
                  <span
                    className="mrole-dot"
                    style={color ? { background: color } : undefined}
                    aria-hidden="true"
                  />
                  <span className="mrole-name" style={color ? { color } : undefined}>
                    {r.name}
                  </span>
                  {r.editable && (
                    <button
                      className="mrole-x"
                      onClick={() => remove(r.id)}
                      disabled={busy || !canManage}
                      title={canManage ? undefined : GATE.manageRoles}
                      aria-label={`Снять роль ${r.name}`}
                    >
                      <IconX />
                    </button>
                  )}
                </span>
              );
            })}
          </div>
          {data.assignable.length > 0 && canManage && (
            <div className="mrole-add">
              <Dropdown
                ariaLabel="Выдать роль"
                value=""
                onChange={(v) => {
                  if (v) add(v);
                }}
                options={[
                  { value: "", label: "Выдать роль…" },
                  ...data.assignable.map((r) => ({ value: r.id, label: r.name })),
                ]}
              />
            </div>
          )}
        </>
      )}
    </div>
  );
}

function PersonCard({
  guildId,
  userId,
  perms,
  isOperator,
  onClose,
  onChanged,
}: {
  guildId: string;
  userId: string;
  perms: GuildPerms;
  isOperator: boolean;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [d, setD] = useState<PersonDetail | null>(null);
  const [warns, setWarns] = useState<Warn[]>([]);
  const [pts, setPts] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [modBump, setModBump] = useState(0); // счётчик для перезагрузки истории после действий
  const [revokeArmed, setRevokeArmed] = useState(false); // отзыв веб-сессий — по 2-му клику
  const toast = useToast();

  useEffect(() => {
    setD(null);
    setWarns([]);
    api
      .person(guildId, userId)
      .then((x) => {
        setD(x);
        setPts(String(x.points));
      })
      .catch((e) => setErr(e instanceof ApiError ? e.message : "Ошибка"));
    api
      .warns(guildId, userId)
      .then(setWarns)
      .catch(() => {
        /* варны — не критично для карточки */
      });
  }, [guildId, userId]);

  async function clearWarns() {
    setBusy(true);
    setErr("");
    try {
      const r = await api.clearWarns(guildId, userId);
      setWarns([]);
      toast.success(`Варны сброшены (${r.cleared})`);
    } catch (e) {
      const message = e instanceof ApiError ? e.message : "Ошибка";
      setErr(message);
      toast.error(message);
    } finally {
      setBusy(false);
    }
  }

  async function savePoints() {
    const n = parseInt(pts, 10);
    if (Number.isNaN(n) || !d || n === d.points) return;
    setBusy(true);
    setErr("");
    try {
      const upd = await api.setPersonPoints(guildId, userId, n);
      setD(upd);
      setPts(String(upd.points));
      onChanged();
      toast.success(`Очки обновлены: ${upd.points}`);
    } catch (e) {
      const message = e instanceof ApiError ? e.message : "Ошибка";
      setErr(message);
      toast.error(message);
    } finally {
      setBusy(false);
    }
  }

  async function toggleFreeze() {
    if (!d) return;
    setBusy(true);
    setErr("");
    try {
      const { frozen } = await api.toggleFreeze(guildId, userId);
      setD({ ...d, frozen });
      onChanged();
      toast.success(frozen ? "Очки заморожены" : "Заморозка снята");
    } catch (e) {
      const message = e instanceof ApiError ? e.message : "Ошибка";
      setErr(message);
      toast.error(message);
    } finally {
      setBusy(false);
    }
  }

  async function revokeSessions() {
    setBusy(true);
    setErr("");
    try {
      await api.revokeSessions(userId);
      setRevokeArmed(false);
      toast.success("Веб-сессии пользователя отозваны");
    } catch (e) {
      const message = e instanceof ApiError ? e.message : "Ошибка";
      setErr(message);
      toast.error(message);
    } finally {
      setBusy(false);
    }
  }

  const name = d?.username ?? `ID ${userId}`;
  return (
    <div className="card pad person-card">
      <button className="btn ghost small person-close" onClick={onClose} aria-label="закрыть">
        <IconX />
      </button>
      {!d ? (
        <div className="skeleton-text" style={{ padding: "8px 0" }}>
          <Skeleton h={40} w="55%" r={12} />
          <Skeleton h={12} w="80%" />
          <Skeleton h={12} w="65%" />
        </div>
      ) : (
        <>
          <div className="person-head">
            {d.avatar ? (
              <img className="leader-avatar" src={d.avatar} alt="" />
            ) : (
              <span className="leader-avatar fallback">{name.slice(0, 1).toUpperCase()}</span>
            )}
            <div>
              <div className="person-name">{name}</div>
              <div className="muted">
                {d.role ?? "без роли"} · уровень {d.level}
                {d.is_exclusive && " · 🖤 Единственный"}
              </div>
            </div>
          </div>

          <div className="person-grid">
            <div className="person-stat">
              <span className="faint">До след. роли</span>
              <span className="mono">{d.next_threshold != null ? d.next_threshold : "—"}</span>
            </div>
            <div className="person-stat">
              <span className="faint">Глубоких диалогов</span>
              <span className="mono">{d.deep_dialogs}</span>
            </div>
            <div className="person-stat">
              <span className="faint">День рождения</span>
              <span className="mono">
                {d.birthday_day && d.birthday_month
                  ? `${d.birthday_day} ${MONTHS[d.birthday_month - 1]}`
                  : "—"}
              </span>
            </div>
            <div className="person-stat">
              <span className="faint">Последний диалог</span>
              <span className="mono">{fmtDate(d.last_dialog_at)}</span>
            </div>
          </div>

          <div className="person-actions">
            <label className="person-action">
              <span className="faint">Очки</span>
              <input
                className="input mono"
                inputMode="numeric"
                value={pts}
                onChange={(e) => setPts(e.target.value)}
              />
              <button className="btn primary small" onClick={savePoints} disabled={busy}>
                Сохранить
              </button>
            </label>
            <label className="person-action">
              <span className="faint">Заморозка (не копит очки)</span>
              <button
                className={`toggle${d.frozen ? " on" : ""}`}
                role="switch"
                aria-checked={d.frozen}
                aria-label="Заморозка"
                onClick={toggleFreeze}
                disabled={busy}
              >
                <span className="knob" />
              </button>
            </label>
          </div>

          {isOperator && (
            <div className="person-action" style={{ marginTop: 4 }}>
              <span className="faint">
                Веб-панель · отозвать все сессии (выйдет из панели на всех устройствах)
              </span>
              <button
                className="btn ghost small"
                onClick={() => (revokeArmed ? revokeSessions() : setRevokeArmed(true))}
                onBlur={() => setRevokeArmed(false)}
                disabled={busy}
                title="Только оператор бота. Пригодится для разжалованного админа или утёкшего токена."
              >
                {revokeArmed ? "Точно? Отозвать" : "Отозвать веб-сессии"}
              </button>
            </div>
          )}

          <MemberRolesSection guildId={guildId} userId={userId} perms={perms} />

          <div className="person-warns">
            <div className="person-warns-head">
              <span className="faint">Варны {warns.length > 0 && `(${warns.length})`}</span>
              {warns.length > 0 && (
                <button
                  className="btn ghost small"
                  onClick={clearWarns}
                  disabled={busy || !perms.can_moderate}
                  title={perms.can_moderate ? undefined : GATE.moderate}
                >
                  Сбросить
                </button>
              )}
            </div>
            {warns.length === 0 ? (
              <div className="muted small">Нет активных варнов.</div>
            ) : (
              <ul className="warn-list">
                {warns.map((w) => (
                  <li className="warn-item" key={w.id}>
                    <span className="warn-reason">{w.reason || "без причины"}</span>
                    <span className="faint small">
                      {fmtDate(w.created_at)}
                      {w.moderator_name && ` · ${w.moderator_name}`}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <ModActions
            guildId={guildId}
            userId={userId}
            perms={perms}
            onActed={() => setModBump((v) => v + 1)}
          />
          <PersonHistory guildId={guildId} userId={userId} reloadKey={modBump} />
          {err && <div className="error-banner" style={{ marginTop: 12 }}>{err}</div>}
        </>
      )}
    </div>
  );
}

function PersonRow({
  e,
  rank,
  onOpen,
}: {
  e: PersonListItem;
  rank: number | null;
  onOpen: () => void;
}) {
  const name = e.username ?? `ID ${e.user_id}`;
  return (
    <button
      className={`leader-row as-button${e.is_exclusive ? " exclusive" : ""}`}
      onClick={onOpen}
    >
      <span className="leader-rank mono">{rank ?? "·"}</span>
      {e.avatar ? (
        <img className="leader-avatar" src={e.avatar} alt="" />
      ) : (
        <span className="leader-avatar fallback">{name.slice(0, 1).toUpperCase()}</span>
      )}
      <span className="leader-name">
        <span>{name}</span>
        <span className="person-meta">
          <RoleChip name={e.role} index={e.role_index} />
          {e.has_profile && e.next_threshold != null && (
            <span
              className="role-prog"
              title={`Осталось ${e.next_threshold - e.points} очк. до следующей роли`}
            >
              <span
                className="role-prog-fill"
                style={{
                  width: `${Math.round(e.role_progress * 100)}%`,
                  background: roleColor(e.role_index),
                }}
              />
            </span>
          )}
          <ActivityBadge iso={e.last_dialog_at} />
          {e.frozen && (
            <span className="frozen-tag" title="Заморожен — не копит очки">
              ❄️
            </span>
          )}
          {!e.has_profile && <span className="faint small">нет профиля</span>}
        </span>
      </span>
      {e.is_exclusive && <span className="badge">🖤</span>}
      <span className="leader-points mono">{e.points}</span>
    </button>
  );
}

export function People({ guild, isOperator }: { guild: Guild; isOperator: boolean }) {
  const [list, setList] = useState<PersonListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  const [q, setQ] = useState("");
  const [role, setRole] = useState(""); // "" = все; NO_ROLE = без роли; иначе имя роли
  const [frozen, setFrozen] = useState<"" | "yes" | "no">("");
  const [profile, setProfile] = useState<"" | "yes" | "no">("");
  const [sort, setSort] = useState<SortKey>("points");

  function load() {
    api
      .people(guild.id)
      .then(setList)
      .catch((e) => {
        if (e instanceof ApiError && e.status === 404) setError("Попоси нет на этом сервере.");
        else setError(e instanceof Error ? e.message : "Не удалось загрузить людей");
      });
  }

  useEffect(() => {
    setList(null);
    setSelected(null);
    setError(null);
    load();
  }, [guild.id]);

  const roles = useMemo(() => {
    const set = new Set<string>();
    for (const e of list ?? []) if (e.role) set.add(e.role);
    return [...set];
  }, [list]);

  const filtered = useMemo(() => {
    if (!list) return [];
    const needle = q.trim().toLowerCase();
    const out = list.filter((e) => {
      const name = (e.username ?? `ID ${e.user_id}`).toLowerCase();
      if (needle && !name.includes(needle)) return false;
      if (role === NO_ROLE && e.role) return false;
      if (role && role !== NO_ROLE && e.role !== role) return false;
      if (frozen === "yes" && !e.frozen) return false;
      if (frozen === "no" && e.frozen) return false;
      if (profile === "yes" && !e.has_profile) return false;
      if (profile === "no" && e.has_profile) return false;
      return true;
    });
    out.sort((a, b) => {
      if (sort === "name")
        return (a.username ?? a.user_id).localeCompare(b.username ?? b.user_id, "ru");
      if (sort === "dialog")
        return (b.last_dialog_at ?? "").localeCompare(a.last_dialog_at ?? "");
      if (sort === "silent") return dialogTs(a.last_dialog_at) - dialogTs(b.last_dialog_at);
      // role-группировка и «по очкам» внутри упорядочены по очкам
      return b.points - a.points;
    });
    return out;
  }, [list, q, role, frozen, profile, sort]);

  // группы по роли-статусу (только при sort==="role"): высший тир сверху,
  // «без роли» — в конце. Кап применяем ко всему списку до группировки.
  const groups = useMemo(() => {
    if (sort !== "role") return null;
    const capped = filtered.slice(0, VISIBLE_CAP);
    const byRole = new Map<number, PersonListItem[]>();
    for (const e of capped) {
      const k = e.role_index ?? -1;
      const bucket = byRole.get(k);
      if (bucket) bucket.push(e);
      else byRole.set(k, [e]);
    }
    return [...byRole.keys()]
      .sort((a, b) => b - a)
      .map((k) => {
        const items = byRole.get(k)!;
        return {
          key: k,
          index: k === -1 ? null : k,
          name: k === -1 ? "Без роли" : (items[0].role ?? "Без роли"),
          items,
        };
      });
  }, [filtered, sort]);

  if (error) return <div className="error-banner">{error}</div>;
  if (!list)
    return (
      <div className="card leader-card" style={{ padding: 16 }}>
        <SkeletonRows rows={8} />
      </div>
    );

  const shown = filtered.slice(0, VISIBLE_CAP);

  return (
    <div>
      {selected && (
        <PersonCard
          guildId={guild.id}
          userId={selected}
          perms={guild.perms}
          isOperator={isOperator}
          onClose={() => setSelected(null)}
          onChanged={load}
        />
      )}

      <div className="people-filters">
        <input
          className="input people-search"
          placeholder="Поиск по имени…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <Dropdown
          ariaLabel="Роль"
          value={role}
          onChange={setRole}
          options={[
            { value: "", label: "Все роли" },
            ...roles.map((r) => ({ value: r, label: r })),
            { value: NO_ROLE, label: "Без роли" },
          ]}
        />
        <Dropdown
          ariaLabel="Заморозка"
          value={frozen}
          onChange={(v) => setFrozen(v as "" | "yes" | "no")}
          options={[
            { value: "", label: "Все" },
            { value: "yes", label: "Заморожены" },
            { value: "no", label: "Активны" },
          ]}
        />
        <Dropdown
          ariaLabel="Профиль"
          value={profile}
          onChange={(v) => setProfile(v as "" | "yes" | "no")}
          options={[
            { value: "", label: "Профиль: все" },
            { value: "yes", label: "С профилем" },
            { value: "no", label: "Без профиля" },
          ]}
        />
        <Dropdown
          ariaLabel="Сортировка"
          value={sort}
          onChange={(v) => setSort(v as SortKey)}
          options={[
            { value: "points", label: "По очкам" },
            { value: "name", label: "По имени" },
            { value: "dialog", label: "Недавний диалог" },
            { value: "silent", label: "Дольше молчали" },
            { value: "role", label: "По роли (группы)" },
          ]}
        />
      </div>

      <div className="people-count-row">
        <span className="people-count faint small">
          {filtered.length} из {list.length}
          {filtered.length > VISIBLE_CAP && ` · показаны первые ${VISIBLE_CAP}`}
        </span>
        <button
          className="btn ghost small"
          onClick={() => downloadCsv(guild.name, filtered)}
          disabled={filtered.length === 0}
          title="Скачать отфильтрованный список в CSV"
        >
          ⬇ CSV
        </button>
      </div>

      {filtered.length === 0 ? (
        <div className="card leader-card">
          <EmptyState
            compact
            title="Никого не нашлось"
            hint="Смягчи фильтры или поиск — под текущие условия никто не подошёл."
          />
        </div>
      ) : groups ? (
        groups.map((g) => (
          <div key={g.key} className="role-group">
            <div className="group-head">
              <RoleChip name={g.name} index={g.index} />
              <span className="faint small">{g.items.length}</span>
            </div>
            <div className="card leader-card">
              {g.items.map((e) => (
                <PersonRow key={e.user_id} e={e} rank={null} onOpen={() => setSelected(e.user_id)} />
              ))}
            </div>
          </div>
        ))
      ) : (
        <div className="card leader-card">
          {shown.map((e, i) => (
            <PersonRow
              key={e.user_id}
              e={e}
              rank={sort === "points" ? i + 1 : null}
              onOpen={() => setSelected(e.user_id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
