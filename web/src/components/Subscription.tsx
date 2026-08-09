import { useEffect, useState } from "react";

import { api } from "../api";
import type { Guild, Subscription as Sub } from "../types";
import { Dropdown } from "./Dropdown";

// Подписка (тариф) сервера — выдаётся вручную оператором бота. Вкладка видна
// только оператору (require_operator стережёт бэк). Пока ENTITLEMENTS_DEFAULT_TIER
// = pro, enforcement выключен (все и так PRO) — об этом честно предупреждаем.
const TIER_LABELS: Record<string, string> = { free: "Free", premium: "Premium", pro: "Pro" };

const TIER_OPTIONS = [
  { value: "premium", label: "Premium" },
  { value: "pro", label: "Pro" },
  { value: "free", label: "Free (сбросить в базовый)" },
];

// пресеты срока -> дни (null = бессрочно)
const DURATION_OPTIONS = [
  { value: "30", label: "30 дней (месяц)" },
  { value: "90", label: "90 дней (3 месяца)" },
  { value: "180", label: "180 дней (полгода)" },
  { value: "365", label: "365 дней (год)" },
  { value: "perm", label: "Бессрочно" },
];

function fmtDate(iso: string | null): string {
  if (!iso) return "бессрочно";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

const TIER_COLORS: Record<string, { bg: string; fg: string }> = {
  free: { bg: "#3a3d44", fg: "#c9ccd4" },
  premium: { bg: "#5b3fb0", fg: "#efeaff" },
  pro: { bg: "#8a6d1f", fg: "#fff4d6" },
};

function TierBadge({ tier }: { tier: string }) {
  const c = TIER_COLORS[tier] ?? TIER_COLORS.free;
  return (
    <span
      style={{
        background: c.bg,
        color: c.fg,
        padding: "2px 10px",
        borderRadius: 999,
        fontWeight: 700,
        fontSize: "0.85em",
        letterSpacing: "0.02em",
      }}
    >
      {(TIER_LABELS[tier] ?? tier).toUpperCase()}
    </span>
  );
}

export function Subscription({ guild }: { guild: Guild }) {
  const [sub, setSub] = useState<Sub | null>(null);
  const [loading, setLoading] = useState(true);
  const [tier, setTier] = useState("premium");
  const [duration, setDuration] = useState("30");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setNote(null);
    setError(null);
    api
      .entitlement(guild.id)
      .then((s) => setSub(s))
      .catch((e) => setError(e instanceof Error ? e.message : "Не удалось загрузить."))
      .finally(() => setLoading(false));
  }, [guild.id]);

  async function grant() {
    setBusy(true);
    setNote(null);
    setError(null);
    const days = duration === "perm" ? null : Number(duration);
    try {
      const s = await api.grantEntitlement(guild.id, tier, days);
      setSub(s);
      setNote(
        `Выдано: ${TIER_LABELS[s.tier] ?? s.tier}` +
          (s.expires_at ? ` до ${fmtDate(s.expires_at)}.` : " бессрочно."),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось выдать.");
    } finally {
      setBusy(false);
    }
  }

  async function revoke() {
    setBusy(true);
    setNote(null);
    setError(null);
    try {
      const s = await api.revokeEntitlement(guild.id);
      setSub(s);
      setNote("Подписка снята — сервер вернулся к базовому тарифу.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось снять.");
    } finally {
      setBusy(false);
    }
  }

  async function trial() {
    setBusy(true);
    setNote(null);
    setError(null);
    try {
      const s = await api.grantEntitlement(guild.id, "premium", 14);
      setSub(s);
      setNote(`Триал Premium на 14 дней активирован — до ${fmtDate(s.expires_at)}.`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось активировать триал.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card acc">
      <div className="rules-publish-head">
        <span className="acc-icon" aria-hidden>
          🎟️
        </span>
        <div>
          <div className="acc-title">Подписка сервера</div>
          <div className="muted small">
            Ручная выдача тарифа этому серверу на выбранный срок. Доступно только оператору бота.
          </div>
        </div>
      </div>

      {loading ? (
        <p className="muted small">Загружаю…</p>
      ) : (
        <>
          {sub && (
            <div className="muted small" style={{ marginTop: 4, lineHeight: 1.7 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                Текущий тариф: <TierBadge tier={sub.tier} />
                {sub.active ? (
                  <span>· активна до <b>{fmtDate(sub.expires_at)}</b></span>
                ) : (
                  <span>· явной подписки нет (базовый тариф)</span>
                )}
              </div>
              {!sub.enforced && (
                <div className="error-banner" style={{ marginTop: 8 }}>
                  Enforcement выключен: тариф по умолчанию — <b>{TIER_LABELS[sub.default_tier]}</b>.
                  Пока это так, все серверы получают максимум возможностей, и выданная подписка ни на
                  что не влияет. Чтобы платность заработала, задайте{" "}
                  <code>ENTITLEMENTS_DEFAULT_TIER=free</code> в <code>.env</code> бота и панели.
                </div>
              )}
            </div>
          )}

          <div className="rules-publish-row" style={{ marginTop: 12, flexWrap: "wrap", gap: 8 }}>
            <Dropdown value={tier} options={TIER_OPTIONS} ariaLabel="Тариф" onChange={setTier} />
            <Dropdown
              value={duration}
              options={DURATION_OPTIONS}
              ariaLabel="Срок подписки"
              onChange={setDuration}
            />
            <button className="btn primary small" disabled={busy} onClick={grant}>
              Выдать
            </button>
            {!sub?.active && (
              <button className="btn small" disabled={busy} onClick={trial}>
                🎁 Триал 14 дней
              </button>
            )}
            {sub?.active && (
              <button className="btn small" disabled={busy} onClick={revoke}>
                Снять
              </button>
            )}
          </div>
        </>
      )}

      {note && (
        <div className="muted small" style={{ marginTop: 8 }}>
          {note}
        </div>
      )}
      {error && (
        <div className="error-banner" style={{ marginTop: 8 }}>
          {error}
        </div>
      )}
    </div>
  );
}
