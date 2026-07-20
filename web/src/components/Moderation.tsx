import { useEffect, useState } from "react";

import { ApiError, api } from "../api";
import type { Ban, Guild, GuildWarn } from "../types";

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
  const name = w.username ?? `ID ${w.user_id}`;

  async function clear() {
    setBusy(true);
    try {
      await api.clearWarns(guildId, w.user_id);
      onDone();
    } catch {
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
  const [msg, setMsg] = useState<string | null>(null);
  const name = ban.username ?? `ID ${ban.user_id}`;

  async function unban() {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.unban(guildId, ban.user_id);
      if (r.status === "done") {
        onDone();
        return;
      }
      setMsg(r.status === "failed" ? (r.result ?? "Не вышло") : "Отправлено — применяется…");
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : "Ошибка");
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
        {msg && <span className="faint small"> · {msg}</span>}
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

export function Moderation({ guild }: { guild: Guild }) {
  const [bans, setBans] = useState<Ban[] | null>(null);
  const [warns, setWarns] = useState<GuildWarn[] | null>(null);
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
  }

  useEffect(() => {
    setBans(null);
    setWarns(null);
    setError(null);
    load();
  }, [guild.id]);

  if (error) return <div className="error-banner">{error}</div>;
  if (!bans)
    return (
      <div className="center" style={{ minHeight: 200 }}>
        <div className="spinner" aria-label="Загрузка" />
      </div>
    );

  return (
    <div>
      <h2 className="section-title">Активные временные баны</h2>
      <p className="muted" style={{ marginTop: -8, marginBottom: 16 }}>
        Бан/мут выдаются из карточки человека (вкладка «Люди»). Здесь — разбан. Автоматический
        разбан по истечении срока ведёт бот.
      </p>
      <div className="card leader-card">
        {bans.length === 0 ? (
          <div className="pad muted">Активных временных банов нет.</div>
        ) : (
          bans.map((b) => <BanRow key={b.user_id} guildId={guild.id} ban={b} onDone={load} />)
        )}
      </div>

      <h2 className="section-title">С варнами</h2>
      <div className="card leader-card">
        {warns === null ? (
          <div className="pad muted">Загрузка…</div>
        ) : warns.length === 0 ? (
          <div className="pad muted">Ни у кого нет активных варнов.</div>
        ) : (
          warns.map((w) => <WarnRow key={w.user_id} guildId={guild.id} w={w} onDone={load} />)
        )}
      </div>
    </div>
  );
}
