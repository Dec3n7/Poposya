import { useEffect, useState } from "react";

import { ApiError, api } from "../api";
import type { ComplexSettings as CS } from "../types";

type Status = "idle" | "saving" | "saved" | "error";

function useSaver() {
  const [status, setStatus] = useState<Status>("idle");
  const [err, setErr] = useState("");
  async function run(fn: () => Promise<void>) {
    setStatus("saving");
    setErr("");
    try {
      await fn();
      setStatus("saved");
      window.setTimeout(() => setStatus((s) => (s === "saved" ? "idle" : s)), 1600);
    } catch (e) {
      setStatus("error");
      setErr(e instanceof ApiError ? e.message : "Ошибка");
    }
  }
  return { status, err, run };
}

function StatusTag({ status, err }: { status: Status; err: string }) {
  return (
    <span className="field-status">
      {status === "saving" && <span className="dot saving" />}
      {status === "saved" && <span className="ok">сохранено ✓</span>}
      {status === "error" && <span className="bad">{err}</span>}
    </span>
  );
}

// --- роли: пороги очков + имена (последнее имя — эксклюзивный «Единственный») ---

function RolesEditor({ guildId, data }: { guildId: string; data: CS }) {
  const toTiers = (th: number[], nm: string[]) =>
    th.map((t, i) => ({ threshold: t, name: nm[i] ?? "" }));
  const [tiers, setTiers] = useState(() =>
    toTiers(data.role_thresholds.value, data.role_names.value),
  );
  const [exclusive, setExclusive] = useState(
    () => data.role_names.value[data.role_names.value.length - 1] ?? "",
  );
  const { status, err, run } = useSaver();

  function setTier(i: number, patch: Partial<{ threshold: number; name: string }>) {
    setTiers((ts) => ts.map((t, j) => (j === i ? { ...t, ...patch } : t)));
  }
  function addTier() {
    const last = tiers[tiers.length - 1]?.threshold ?? 0;
    setTiers((ts) => [...ts, { threshold: last + 100, name: "Новая роль" }]);
  }
  function removeTier(i: number) {
    setTiers((ts) => ts.filter((_, j) => j !== i));
  }

  function save() {
    const thresholds = tiers.map((t) => t.threshold);
    const names = [...tiers.map((t) => t.name), exclusive];
    void run(() =>
      api.batch(guildId, {
        relationship_role_thresholds: thresholds,
        relationship_role_names: names,
      }),
    );
  }
  function reset() {
    void run(async () => {
      await api.resetSetting(guildId, "relationship_role_thresholds");
      await api.resetSetting(guildId, "relationship_role_names");
      setTiers(toTiers(data.role_thresholds.default, data.role_names.default));
      setExclusive(data.role_names.default[data.role_names.default.length - 1] ?? "");
    });
  }

  return (
    <section>
      <h2 className="section-title">Роли-статусы</h2>
      <div className="card pad">
        <p className="muted" style={{ marginTop: 0 }}>
          Пороги очков и имена ролей. Растут по очереди; последняя — эксклюзивная «Единственный».
        </p>
        <div className="tiers">
          {tiers.map((t, i) => (
            <div className="tier" key={i}>
              <input
                className="input mono tier-th"
                inputMode="numeric"
                value={t.threshold}
                onChange={(e) => setTier(i, { threshold: parseInt(e.target.value, 10) || 0 })}
              />
              <input
                className="input tier-name"
                value={t.name}
                onChange={(e) => setTier(i, { name: e.target.value })}
              />
              <button className="btn ghost small" onClick={() => removeTier(i)} aria-label="убрать">
                ✕
              </button>
            </div>
          ))}
          <div className="tier exclusive">
            <span className="tier-th faint mono">🖤</span>
            <input
              className="input tier-name"
              value={exclusive}
              onChange={(e) => setExclusive(e.target.value)}
            />
            <span className="faint" style={{ fontSize: 12 }}>
              эксклюзив
            </span>
          </div>
        </div>
        <div className="editor-actions">
          <button className="btn ghost small" onClick={addTier}>
            + порог
          </button>
          <StatusTag status={status} err={err} />
          <button className="btn ghost small" onClick={reset} disabled={status === "saving"}>
            сбросить
          </button>
          <button className="btn primary small" onClick={save} disabled={status === "saving"}>
            Сохранить роли
          </button>
        </div>
      </div>
    </section>
  );
}

// --- лимиты AI-реплик в час по уровню отношений (1..7) ---

function RateLimitsEditor({ guildId, data }: { guildId: string; data: CS }) {
  const [limits, setLimits] = useState<Record<string, number>>(() => ({
    ...data.rate_limits.value,
  }));
  const { status, err, run } = useSaver();
  const levels = Object.keys(limits).sort((a, b) => Number(a) - Number(b));

  function save() {
    void run(() => api.batch(guildId, { ai_rate_limits_by_level: limits }));
  }
  function reset() {
    void run(async () => {
      await api.resetSetting(guildId, "ai_rate_limits_by_level");
      setLimits({ ...data.rate_limits.default });
    });
  }

  return (
    <section>
      <h2 className="section-title">Лимиты AI-реплик</h2>
      <div className="card pad">
        <p className="muted" style={{ marginTop: 0 }}>
          Сколько раз в час Попося отвечает, по уровню отношений с человеком.
        </p>
        <div className="limits-grid">
          {levels.map((lvl) => (
            <label className="limit-row" key={lvl}>
              <span className="faint">Уровень {lvl}</span>
              <input
                className="input mono"
                inputMode="numeric"
                value={limits[lvl]}
                onChange={(e) =>
                  setLimits((m) => ({ ...m, [lvl]: parseInt(e.target.value, 10) || 0 }))
                }
              />
            </label>
          ))}
        </div>
        <div className="editor-actions">
          <StatusTag status={status} err={err} />
          <button className="btn ghost small" onClick={reset} disabled={status === "saving"}>
            сбросить
          </button>
          <button className="btn primary small" onClick={save} disabled={status === "saving"}>
            Сохранить лимиты
          </button>
        </div>
      </div>
    </section>
  );
}

export function ComplexSettings({ guildId }: { guildId: string }) {
  const [data, setData] = useState<CS | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .complexSettings(guildId)
      .then((d) => alive && setData(d))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [guildId]);

  if (!data) return null;
  return (
    <>
      <RolesEditor guildId={guildId} data={data} />
      <RateLimitsEditor guildId={guildId} data={data} />
    </>
  );
}
