import { useEffect, useState } from "react";

import { ApiError, api } from "../api";
import type { Guild, PersonDetail, PersonListItem, Warn } from "../types";

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

function ModActions({ guildId, userId }: { guildId: string; userId: string }) {
  const [minutes, setMinutes] = useState("60");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  function show(r: { status: string; result: string | null }) {
    if (r.status === "done") setMsg(r.result ?? "Готово");
    else if (r.status === "failed") setMsg(r.result ?? "Не вышло");
    else setMsg("Отправлено — применяется…");
  }

  async function act(kind: "mute" | "unmute" | "ban", fn: () => Promise<{ status: string; result: string | null }>) {
    setBusy(kind);
    setMsg(null);
    try {
      show(await fn());
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : "Ошибка");
    } finally {
      setBusy(null);
    }
  }

  const mins = () => Math.max(1, parseInt(minutes, 10) || 0);

  return (
    <div className="person-warns">
      <div className="person-warns-head">
        <span className="faint">Модерация</span>
        {msg && <span className="faint small">{msg}</span>}
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
          placeholder="причина (для бана)"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
        <div className="mod-buttons">
          <button
            className="btn small"
            disabled={busy !== null}
            onClick={() => act("mute", () => api.mute(guildId, userId, mins(), reason))}
          >
            🔇 Мут
          </button>
          <button
            className="btn ghost small"
            disabled={busy !== null}
            onClick={() => act("unmute", () => api.unmute(guildId, userId))}
          >
            Снять мут
          </button>
          <button
            className="btn danger small"
            disabled={busy !== null}
            onClick={() => act("ban", () => api.ban(guildId, userId, mins(), reason))}
          >
            🔨 Бан
          </button>
        </div>
      </div>
    </div>
  );
}

function PersonCard({
  guildId,
  userId,
  onClose,
  onChanged,
}: {
  guildId: string;
  userId: string;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [d, setD] = useState<PersonDetail | null>(null);
  const [warns, setWarns] = useState<Warn[]>([]);
  const [pts, setPts] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

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
      await api.clearWarns(guildId, userId);
      setWarns([]);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Ошибка");
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
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Ошибка");
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
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Ошибка");
    } finally {
      setBusy(false);
    }
  }

  const name = d?.username ?? `ID ${userId}`;
  return (
    <div className="card pad person-card">
      <button className="btn ghost small person-close" onClick={onClose} aria-label="закрыть">
        ✕
      </button>
      {!d ? (
        <div className="center" style={{ minHeight: 120 }}>
          <div className="spinner" />
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

          <div className="person-warns">
            <div className="person-warns-head">
              <span className="faint">Варны {warns.length > 0 && `(${warns.length})`}</span>
              {warns.length > 0 && (
                <button className="btn ghost small" onClick={clearWarns} disabled={busy}>
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
          <ModActions guildId={guildId} userId={userId} />
          {err && <div className="error-banner" style={{ marginTop: 12 }}>{err}</div>}
        </>
      )}
    </div>
  );
}

export function People({ guild }: { guild: Guild }) {
  const [list, setList] = useState<PersonListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

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

  if (error) return <div className="error-banner">{error}</div>;
  if (!list)
    return (
      <div className="center" style={{ minHeight: 200 }}>
        <div className="spinner" aria-label="Загрузка" />
      </div>
    );

  return (
    <div>
      {selected && (
        <PersonCard
          guildId={guild.id}
          userId={selected}
          onClose={() => setSelected(null)}
          onChanged={load}
        />
      )}

      <h2 className="section-title">Все с очками</h2>
      <div className="card leader-card">
        {list.length === 0 ? (
          <div className="pad muted">Пока никто не набрал очков.</div>
        ) : (
          list.map((e, i) => {
            const name = e.username ?? `ID ${e.user_id}`;
            return (
              <button
                className={`leader-row as-button${e.is_exclusive ? " exclusive" : ""}`}
                key={e.user_id}
                onClick={() => setSelected(e.user_id)}
              >
                <span className="leader-rank mono">{i + 1}</span>
                {e.avatar ? (
                  <img className="leader-avatar" src={e.avatar} alt="" />
                ) : (
                  <span className="leader-avatar fallback">{name.slice(0, 1).toUpperCase()}</span>
                )}
                <span className="leader-name">
                  {name}
                  {e.role && <span className="leader-role">{e.role}</span>}
                </span>
                {e.is_exclusive && <span className="badge">🖤</span>}
                <span className="leader-points mono">{e.points}</span>
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}
