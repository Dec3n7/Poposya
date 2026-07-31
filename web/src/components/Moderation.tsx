import { useEffect, useState } from "react";

import { ApiError, api } from "../api";
import type {
  Ban,
  CrossBanFlagged,
  CrossBanList,
  CrossBanRecord,
  CrossBanUser,
  Guild,
  GuildWarn,
  ModCase,
} from "../types";
import { EmptyState } from "./EmptyState";
import { SkeletonRows } from "./Skeleton";
import { useToast } from "./Toast";

// человекочитаемые ярлыки действий журнала (совпадают с ботовым _ACTION_LABELS)
const ACTION_LABELS: Record<string, string> = {
  warn: "варн",
  warn_mute: "мут по варнам",
  warn_tempban: "бан по варнам",
  mute: "мут",
  unmute: "снят мут",
  kick: "кик",
  ban: "бан",
  tempban: "врем. бан",
  unban: "разбан",
  clearwarns: "сброс варнов",
  clear: "чистка",
  spam_mute: "мут за спам",
  rage: "ярость",
};

function fmtExpires(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString("ru-RU", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
}

function WarnRow({ guildId, w, onDone }: { guildId: string; w: GuildWarn; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const name = w.username ?? `ID ${w.user_id}`;

  async function clear() {
    setBusy(true);
    try {
      const r = await api.clearWarns(guildId, w.user_id);
      toast.success(`Варны сброшены (${r.cleared}) — ${name}`);
      onDone();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Не удалось сбросить варны");
      setBusy(false);
    }
  }

  return (
    <div className="cine-row">
      <span className="cine-title">
        {w.avatar ? (
          <img className="leader-avatar sm" src={w.avatar} alt="" />
        ) : (
          <span className="leader-avatar sm fallback">{name.slice(0, 1).toUpperCase()}</span>
        )}
        {name}
        <span className="badge">{w.count} ⚠️</span>
      </span>
      <span className="cine-side">
        <span className="mono faint">последний {fmtDate(w.last_at)}</span>
        <button className="btn ghost small" onClick={clear} disabled={busy}>
          Сбросить
        </button>
      </span>
    </div>
  );
}

function BanRow({ guildId, ban, onDone }: { guildId: string; ban: Ban; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const name = ban.username ?? `ID ${ban.user_id}`;

  async function unban() {
    setBusy(true);
    try {
      const r = await api.unban(guildId, ban.user_id);
      if (r.status === "done") {
        toast.success(`Разбанен — ${name}`);
        onDone();
        return;
      }
      if (r.status === "failed") {
        toast.error(r.result ?? "Разбан не удался");
      } else {
        // команда ушла боту через мост — результат придёт асинхронно
        toast.info(`Отправлено — ${name} разбанится в течение пары секунд`);
      }
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Ошибка разбана");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="cine-row">
      <span className="cine-title">
        {ban.avatar ? (
          <img className="leader-avatar sm" src={ban.avatar} alt="" />
        ) : (
          <span className="leader-avatar sm fallback">{name.slice(0, 1).toUpperCase()}</span>
        )}
        {name}
        {ban.reason && <span className="cine-review">«{ban.reason}»</span>}
        {ban.moderator_name && <span className="faint"> · выдал {ban.moderator_name}</span>}
      </span>
      <span className="cine-side">
        <span className="mono faint">до {fmtExpires(ban.expires_at)}</span>
        <button className="btn ghost small" onClick={unban} disabled={busy}>
          Разбанить
        </button>
      </span>
    </div>
  );
}

function CrossBanRecords({ records }: { records: CrossBanRecord[] }) {
  return (
    <div style={{ padding: "4px 12px 12px 46px", display: "grid", gap: 8 }}>
      {records.map((r, i) => (
        <div key={i} style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "baseline" }}>
          <b>{r.guild_name || `Сервер ${r.guild_id}`}</b>
          <span className="cine-review">«{r.reason || "без причины"}»</span>
          <span className="mono faint">
            {r.banned_at ? fmtDate(r.banned_at) : "дата неизвестна"}
          </span>
        </div>
      ))}
    </div>
  );
}

function FlaggedRow({ f }: { f: CrossBanFlagged }) {
  const [open, setOpen] = useState(false);
  const name = f.name ?? `ID ${f.user_id}`;
  return (
    <div>
      <button
        className="cine-row"
        onClick={() => setOpen((v) => !v)}
        style={{ width: "100%", background: "none", border: "none", cursor: "pointer" }}
      >
        <span className="cine-title">
          {f.avatar ? (
            <img className="leader-avatar sm" src={f.avatar} alt="" />
          ) : (
            <span className="leader-avatar sm fallback">{name.slice(0, 1).toUpperCase()}</span>
          )}
          {name}
          <span className="badge alert">забанен на {f.count} серв.</span>
        </span>
        <span className="cine-side faint">{open ? "▾ свернуть" : "▸ причины"}</span>
      </button>
      {open && <CrossBanRecords records={f.records} />}
    </div>
  );
}

function CrossBanLookup({ guildId }: { guildId: string }) {
  const [id, setId] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<CrossBanUser | null>(null);
  const toast = useToast();

  async function check() {
    const clean = id.trim();
    if (!/^\d{5,}$/.test(clean)) {
      toast.error("Введите числовой ID пользователя Discord");
      return;
    }
    setBusy(true);
    try {
      setResult(await api.crossbanUser(guildId, clean));
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Не удалось проверить");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card leader-card" style={{ padding: 16, display: "grid", gap: 12 }}>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <input
          className="input"
          placeholder="ID пользователя Discord"
          value={id}
          inputMode="numeric"
          onChange={(e) => setId(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && check()}
          style={{ flex: 1, minWidth: 180 }}
        />
        <button className="btn" onClick={check} disabled={busy}>
          Проверить
        </button>
      </div>
      {result &&
        (result.count === 0 ? (
          <EmptyState compact title="Нигде не забанен" hint="На серверах бота банов нет." />
        ) : (
          <div>
            <p className="muted" style={{ margin: "0 0 8px" }}>
              {result.username ?? `ID ${result.user_id}`} — забанен на <b>{result.count}</b>{" "}
              сервере(ах){result.count >= result.threshold ? " ⚠️ порог пройден" : ""}.
            </p>
            <CrossBanRecords records={result.records} />
          </div>
        ))}
    </div>
  );
}

function HistoryLookup({ guildId }: { guildId: string }) {
  const [id, setId] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [cases, setCases] = useState<ModCase[] | null>(null);
  // подтверждение по второму клику: null | "kick" | "ban"
  const [confirmKind, setConfirmKind] = useState<null | "kick" | "ban">(null);
  const toast = useToast();

  function validId(): string | null {
    const clean = id.trim();
    return /^\d{5,}$/.test(clean) ? clean : null;
  }

  async function load() {
    const clean = validId();
    if (!clean) {
      toast.error("Введите числовой ID пользователя Discord");
      return;
    }
    setBusy(true);
    setConfirmKind(null);
    try {
      setCases(await api.history(guildId, clean));
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Не удалось загрузить историю");
    } finally {
      setBusy(false);
    }
  }

  async function act(kind: "kick" | "ban") {
    const clean = validId();
    if (!clean) {
      toast.error("Введите числовой ID пользователя Discord");
      return;
    }
    if (confirmKind !== kind) {
      setConfirmKind(kind); // первый клик — просим подтвердить
      return;
    }
    setConfirmKind(null);
    setBusy(true);
    try {
      const r =
        kind === "kick"
          ? await api.kick(guildId, clean, reason)
          : await api.banPermanent(guildId, clean, reason);
      if (r.status === "failed") {
        toast.error(r.result ?? "Действие не удалось");
      } else if (r.status === "done") {
        toast.success(kind === "kick" ? "Кикнут" : "Забанен навсегда");
        await load();
      } else {
        toast.info("Отправлено боту — применится через пару секунд");
      }
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Ошибка действия");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card leader-card" style={{ padding: 16, display: "grid", gap: 12 }}>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <input
          className="input"
          placeholder="ID пользователя Discord"
          value={id}
          inputMode="numeric"
          onChange={(e) => {
            setId(e.target.value);
            setConfirmKind(null);
          }}
          onKeyDown={(e) => e.key === "Enter" && load()}
          style={{ flex: 1, minWidth: 180 }}
        />
        <button className="btn" onClick={load} disabled={busy}>
          История
        </button>
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <input
          className="input"
          placeholder="Причина (для кика/бана)"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          style={{ flex: 1, minWidth: 180 }}
        />
        <button className="btn ghost small" onClick={() => act("kick")} disabled={busy}>
          {confirmKind === "kick" ? "Точно? Кикнуть" : "Кикнуть"}
        </button>
        <button className="btn danger small" onClick={() => act("ban")} disabled={busy}>
          {confirmKind === "ban" ? "Точно? Забанить" : "Забанить навсегда"}
        </button>
      </div>
      {cases !== null &&
        (cases.length === 0 ? (
          <EmptyState compact title="История пуста" hint="Действий модерации по участнику нет." />
        ) : (
          <div style={{ display: "grid", gap: 6 }}>
            {cases.map((c) => (
              <div
                key={c.id}
                style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "baseline" }}
              >
                <span className="mono faint">{fmtExpires(c.created_at)}</span>
                <b>{ACTION_LABELS[c.action] ?? c.action}</b>
                {c.duration_minutes ? (
                  <span className="mono faint">{c.duration_minutes}м</span>
                ) : null}
                {c.reason && <span className="cine-review">«{c.reason}»</span>}
                <span className="faint">
                  · {c.moderator_id ? (c.moderator_name ?? c.moderator_id) : "авто"}
                  {c.source === "panel" ? " (панель)" : ""}
                </span>
              </div>
            ))}
          </div>
        ))}
    </div>
  );
}

export function Moderation({ guild }: { guild: Guild }) {
  const [bans, setBans] = useState<Ban[] | null>(null);
  const [warns, setWarns] = useState<GuildWarn[] | null>(null);
  const [crossban, setCrossban] = useState<CrossBanList | null>(null);
  const [error, setError] = useState<string | null>(null);

  function load() {
    api
      .bans(guild.id)
      .then(setBans)
      .catch((e) => {
        if (e instanceof ApiError && e.status === 404) setError("Попоси нет на этом сервере.");
        else setError(e instanceof Error ? e.message : "Не удалось загрузить баны");
      });
    api
      .guildWarns(guild.id)
      .then(setWarns)
      .catch(() => setWarns([]));
    api
      .crossban(guild.id)
      .then(setCrossban)
      .catch(() => setCrossban(null));
  }

  useEffect(() => {
    setBans(null);
    setWarns(null);
    setCrossban(null);
    setError(null);
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [guild.id]);

  if (error) return <div className="error-banner">{error}</div>;

  return (
    <div>
      <h2 className="section-title">Активные временные баны</h2>
      <p className="muted" style={{ marginTop: -8, marginBottom: 16 }}>
        Бан/мут выдаются из карточки человека (вкладка «Люди»). Здесь — разбан. Автоматический
        разбан по истечении срока ведёт бот.
      </p>
      <div className="card leader-card">
        {bans === null ? (
          <div className="pad">
            <SkeletonRows rows={3} />
          </div>
        ) : bans.length === 0 ? (
          <EmptyState
            compact
            title="Активных банов нет"
            hint="Временные баны появятся здесь. Разбан по истечении срока бот делает сам."
          />
        ) : (
          bans.map((b) => <BanRow key={b.user_id} guildId={guild.id} ban={b} onDone={load} />)
        )}
      </div>

      <h2 className="section-title">С варнами</h2>
      <div className="card leader-card">
        {warns === null ? (
          <div className="pad">
            <SkeletonRows rows={3} />
          </div>
        ) : warns.length === 0 ? (
          <EmptyState compact title="Ни у кого нет активных варнов" />
        ) : (
          warns.map((w) => <WarnRow key={w.user_id} guildId={guild.id} w={w} onDone={load} />)
        )}
      </div>

      <h2 className="section-title">История модерации</h2>
      <p className="muted" style={{ marginTop: -8, marginBottom: 16 }}>
        Единый журнал действий по участнику (бот и панель): варны, муты, кики, баны, чистки.
        Отсюда же можно кикнуть или забанить навсегда по ID.
      </p>
      <HistoryLookup guildId={guild.id} />

      <h2 className="section-title">Кросс-серверные баны</h2>
      <p className="muted" style={{ marginTop: -8, marginBottom: 16 }}>
        Участники этого сервера, забанённые на других серверах бота. Видно только админам —
        в Discord не публикуется. Порог «отмеченного» — в настройках сервера.
      </p>
      {crossban === null ? (
        <div className="card leader-card">
          <div className="pad">
            <SkeletonRows rows={2} />
          </div>
        </div>
      ) : !crossban.enabled ? (
        <div className="card leader-card">
          <EmptyState
            compact
            title="Модуль выключен"
            hint="Включите «Кросс-серверные баны» на вкладке «Модули»."
          />
        </div>
      ) : (
        <>
          <div className="card leader-card">
            {crossban.flagged.length === 0 ? (
              <EmptyState
                compact
                title="Отмеченных нет"
                hint={`Никто из участников не забанен на ${crossban.threshold}+ других серверах бота.`}
              />
            ) : (
              crossban.flagged.map((f) => <FlaggedRow key={f.user_id} f={f} />)
            )}
          </div>
          <h3 className="section-title" style={{ fontSize: "1rem" }}>
            Проверить по ID
          </h3>
          <CrossBanLookup guildId={guild.id} />
        </>
      )}
    </div>
  );
}
