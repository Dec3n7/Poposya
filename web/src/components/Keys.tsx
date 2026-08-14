import { type ReactNode, useCallback, useEffect, useState } from "react";

import { api } from "../api";
import type { IssuedKey, KeyActivation, KeyAttempt, KeyBatch, KeysOverview } from "../types";
import { Dropdown } from "./Dropdown";

// Операторский пул лицензионных ключей Premium/Pro: генерация партий по SKU,
// просмотр САМИХ ключей (перевыпуск из реестра), статусы, отзыв (soft/hard),
// реактивация и экспорт. Виден только оператору (require_operator стережёт бэк).

const TIER_LABELS: Record<string, string> = { premium: "Premium", pro: "Pro" };
const TIER_COLORS: Record<string, { bg: string; fg: string }> = {
  premium: { bg: "#5b3fb0", fg: "#efeaff" },
  pro: { bg: "#8a6d1f", fg: "#fff4d6" },
};
const STATUS_LABELS: Record<string, string> = {
  unredeemed: "не выкуплен",
  partial: "частично",
  full: "выкуплен",
};
const STATUS_COLORS: Record<string, string> = {
  unredeemed: "#3a7d44",
  partial: "#8a6d1f",
  full: "#6b3030",
};
const DURATION_LABELS: Record<number, string> = {
  30: "30 дней (месяц)",
  90: "90 дней (квартал)",
  180: "180 дней (полгода)",
  365: "365 дней (год)",
};

function TierBadge({ tier }: { tier: string }) {
  const c = TIER_COLORS[tier] ?? TIER_COLORS.premium;
  return (
    <span
      style={{
        background: c.bg,
        color: c.fg,
        padding: "1px 8px",
        borderRadius: 999,
        fontWeight: 700,
        fontSize: "0.8em",
      }}
    >
      {(TIER_LABELS[tier] ?? tier).toUpperCase()}
    </span>
  );
}

function StatusBadge({ status }: { status: string }) {
  return (
    <span
      style={{
        background: STATUS_COLORS[status] ?? "#3a3d44",
        color: "#f0f0f4",
        padding: "1px 8px",
        borderRadius: 999,
        fontSize: "0.78em",
      }}
    >
      {STATUS_LABELS[status] ?? status}
    </span>
  );
}

function fmtDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString();
  } catch {
    return iso;
  }
}

function download(name: string, text: string): void {
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

function CopyButton({ value, label = "копир.", ghost = true }: { value: string; label?: string; ghost?: boolean }) {
  const [ok, setOk] = useState(false);
  return (
    <button
      className={`btn ${ghost ? "ghost " : ""}small`}
      onClick={() => {
        navigator.clipboard.writeText(value);
        setOk(true);
        setTimeout(() => setOk(false), 1200);
      }}
    >
      {ok ? "✓ скопировано" : label}
    </button>
  );
}

// одна строка = один ключ: моноширинный, со скроллом по X (виден целиком) и
// точечной кнопкой «копировать» — чтобы легко взять конкретный ключ.
function KeyRow({ value, badge, meta }: { value: string; badge?: ReactNode; meta?: ReactNode }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      {badge}
      <code
        style={{
          flex: 1,
          minWidth: 0,
          fontFamily: "monospace",
          fontSize: "0.82em",
          overflowX: "auto",
          whiteSpace: "nowrap",
          padding: "5px 8px",
          background: "rgba(0,0,0,0.22)",
          borderRadius: 6,
        }}
      >
        {value}
      </code>
      {meta}
      <CopyButton value={value} />
    </div>
  );
}

export function Keys({ onBack }: { onBack: () => void }) {
  const [data, setData] = useState<KeysOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    api
      .premiumKeysOverview()
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : "Не удалось загрузить пул."))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => load(), [load]);

  return (
    <div className="app">
      <header className="topbar">
        <button className="btn ghost small" onClick={onBack}>
          ← Назад
        </button>
        <span className="brand" style={{ marginLeft: 12 }}>
          <span className="mark">🔑</span> Ключи Premium
        </span>
        <span className="spacer" />
        <button className="btn ghost small" onClick={load} disabled={loading}>
          Обновить
        </button>
      </header>

      <main className="container" style={{ display: "grid", gap: 16, maxWidth: 1000 }}>
        {error && <div className="error-banner">{error}</div>}
        {loading && !data ? (
          <p className="muted small">Загружаю…</p>
        ) : data && !data.enabled ? (
          <div className="card pad">
            <div className="error-banner">
              Ключи выключены: не задан <code>KEY_SIGNING_SECRET</code> в <code>.env</code> бота и
              панели. Сгенерируйте секрет (<code>python -c "import secrets;
              print(secrets.token_urlsafe(32))"</code>) и перезапустите — только после этого можно
              выпускать и активировать ключи.
            </div>
          </div>
        ) : data ? (
          <>
            <MintForm durations={data.durations} onMinted={load} />
            <SkuTable data={data} />
            <BatchList batches={data.batches} onChanged={load} />
            <Journal onChanged={load} />
          </>
        ) : null}
      </main>
    </div>
  );
}

function MintForm({ durations, onMinted }: { durations: number[]; onMinted: () => void }) {
  const [tier, setTier] = useState("premium");
  const [duration, setDuration] = useState(String(durations[0] ?? 30));
  const [count, setCount] = useState("100");
  const [label, setLabel] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [minted, setMinted] = useState<{ batchId: number; keys: string[] } | null>(null);

  async function mint() {
    setBusy(true);
    setError(null);
    try {
      const n = Number(count);
      const res = await api.mintKeys(tier, Number(duration), n, label.trim() || "без метки", note);
      setMinted({ batchId: res.batch_id, keys: res.keys });
      onMinted();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось выпустить.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card acc rules-publish">
      <div className="rules-publish-head">
        <span className="acc-icon" aria-hidden>
          ✨
        </span>
        <div>
          <div className="acc-title">Выпустить партию</div>
          <div className="muted small">
            Один SKU = тариф × длительность. Ключи появятся ниже — скопируй или скачай, чтобы
            выложить в пул товара.
          </div>
        </div>
      </div>

      <div className="rules-publish-row" style={{ marginTop: 12, flexWrap: "wrap", gap: 8 }}>
        <Dropdown
          value={tier}
          options={[
            { value: "premium", label: "Premium (1 сервер)" },
            { value: "pro", label: "Pro (5 серверов)" },
          ]}
          ariaLabel="Тариф"
          onChange={setTier}
        />
        <Dropdown
          value={duration}
          options={durations.map((d) => ({ value: String(d), label: DURATION_LABELS[d] ?? `${d} дней` }))}
          ariaLabel="Длительность"
          onChange={setDuration}
        />
        <input
          className="input"
          type="number"
          min={1}
          max={10000}
          value={count}
          onChange={(e) => setCount(e.target.value)}
          aria-label="Количество"
          style={{ width: 100 }}
        />
        <input
          className="input"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder="метка партии, напр. boosty-2026q3"
          aria-label="Метка"
          style={{ flex: "1 1 200px" }}
        />
        <button className="btn primary small" disabled={busy} onClick={mint}>
          Выпустить
        </button>
      </div>
      <input
        className="input"
        value={note}
        onChange={(e) => setNote(e.target.value)}
        placeholder="заметка (необязательно): кому/куда"
        aria-label="Заметка"
        style={{ marginTop: 8, width: "100%" }}
      />

      {error && (
        <div className="error-banner" style={{ marginTop: 8 }}>
          {error}
        </div>
      )}
      {minted && <MintedKeys batchId={minted.batchId} keys={minted.keys} />}
    </div>
  );
}

function MintedKeys({ batchId, keys }: { batchId: number; keys: string[] }) {
  const text = keys.join("\n");
  return (
    <div className="card" style={{ marginTop: 12, padding: 12 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8, flexWrap: "wrap" }}>
        <b>Выпущено {keys.length} ключей</b>
        <span className="muted small">партия #{batchId}</span>
        <span style={{ flex: 1 }} />
        <CopyButton value={text} label="Копировать все" ghost={false} />
        <button className="btn small" onClick={() => download(`keys-batch-${batchId}.txt`, text)}>
          Скачать .txt
        </button>
      </div>
      <div style={{ display: "grid", gap: 4, maxHeight: 340, overflowY: "auto" }}>
        {keys.map((k) => (
          <KeyRow key={k} value={k} />
        ))}
      </div>
    </div>
  );
}

function SkuTable({ data }: { data: KeysOverview }) {
  if (data.skus.length === 0) {
    return (
      <div className="card pad">
        <div className="muted small">Пул пуст — выпусти первую партию выше.</div>
      </div>
    );
  }
  return (
    <div className="card pad">
      <div className="acc-title" style={{ marginBottom: 8 }}>
        Пул по SKU
      </div>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.9em" }}>
        <thead>
          <tr style={{ textAlign: "left", color: "var(--muted)" }}>
            <th style={{ padding: "4px 8px" }}>SKU</th>
            <th style={{ padding: "4px 8px" }}>Выпущено</th>
            <th style={{ padding: "4px 8px" }}>Выкуплено</th>
            <th style={{ padding: "4px 8px" }}>Остаток</th>
          </tr>
        </thead>
        <tbody>
          {data.skus.map((s) => (
            <tr key={`${s.tier}-${s.duration_days}`} style={{ borderTop: "1px solid var(--line)" }}>
              <td style={{ padding: "6px 8px" }}>
                <TierBadge tier={s.tier} /> · {s.duration_days} дн
              </td>
              <td style={{ padding: "6px 8px" }}>{s.issued}</td>
              <td style={{ padding: "6px 8px" }}>{s.redeemed_seats}</td>
              <td style={{ padding: "6px 8px" }}>
                <b>{s.remaining}</b> / {s.capacity}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function BatchList({ batches, onChanged }: { batches: KeyBatch[]; onChanged: () => void }) {
  if (batches.length === 0) return null;
  return (
    <div className="card pad">
      <div className="acc-title" style={{ marginBottom: 8 }}>
        Партии
      </div>
      <div style={{ display: "grid", gap: 10 }}>
        {batches.map((b) => (
          <BatchRow key={b.batch_id} batch={b} onChanged={onChanged} />
        ))}
      </div>
    </div>
  );
}

function BatchRow({ batch, onChanged }: { batch: KeyBatch; onChanged: () => void }) {
  const [keys, setKeys] = useState<IssuedKey[] | null>(null);
  const [showKeys, setShowKeys] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [revoking, setRevoking] = useState(false);
  const [reason, setReason] = useState("");
  const [hard, setHard] = useState(false);

  async function toggleKeys() {
    if (showKeys) {
      setShowKeys(false);
      return;
    }
    if (keys === null) {
      setBusy(true);
      setError(null);
      try {
        setKeys(await api.batchKeys(batch.batch_id));
      } catch (e) {
        setError(e instanceof Error ? e.message : "Не удалось загрузить ключи.");
        setBusy(false);
        return;
      }
      setBusy(false);
    }
    setShowKeys(true);
  }

  async function doRevoke() {
    if (!reason.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await api.revokeBatch(batch.batch_id, reason.trim(), hard);
      setRevoking(false);
      setReason("");
      setHard(false);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось отозвать.");
    } finally {
      setBusy(false);
    }
  }

  async function doReactivate() {
    setBusy(true);
    setError(null);
    try {
      await api.reactivateBatch(batch.batch_id);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось реактивировать.");
    } finally {
      setBusy(false);
    }
  }

  function exportKeys(onlyUnredeemed: boolean) {
    const list = (keys ?? []).filter((k) => !onlyUnredeemed || k.status === "unredeemed");
    download(
      `keys-batch-${batch.batch_id}${onlyUnredeemed ? "-unredeemed" : ""}.txt`,
      list.map((k) => k.key).join("\n"),
    );
  }

  return (
    <div style={{ border: "1px solid var(--line)", borderRadius: 10, padding: 10, opacity: batch.revoked ? 0.7 : 1 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <b>#{batch.batch_id}</b>
        <TierBadge tier={batch.tier} />
        <span className="muted small">
          {batch.duration_days} дн · {batch.label} · {fmtDate(batch.created_at)}
        </span>
        {batch.revoked && (
          <span style={{ background: "#6b3030", color: "#fff", padding: "1px 8px", borderRadius: 999, fontSize: "0.78em" }}>
            отозвана
          </span>
        )}
        <span className="spacer" style={{ flex: 1 }} />
        <span className="muted small">
          выкуплено {batch.redeemed_seats}/{batch.capacity}
        </span>
      </div>

      <div className="rules-publish-row" style={{ marginTop: 8, gap: 6, flexWrap: "wrap" }}>
        <button className="btn small" onClick={toggleKeys} disabled={busy}>
          {showKeys ? "Скрыть ключи" : "Показать ключи"}
        </button>
        {!batch.revoked && (
          <button className="btn small" onClick={() => setRevoking((v) => !v)} disabled={busy}>
            Отозвать
          </button>
        )}
        {batch.revoked && (
          <button className="btn small" onClick={doReactivate} disabled={busy}>
            Реактивировать
          </button>
        )}
      </div>

      {revoking && (
        <div className="rules-publish-row" style={{ marginTop: 8, gap: 6, flexWrap: "wrap", alignItems: "center" }}>
          <input
            className="input"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="причина отзыва (обязательно)"
            aria-label="Причина"
            style={{ flex: "1 1 200px" }}
          />
          <label className="muted small" style={{ display: "flex", alignItems: "center", gap: 4 }}>
            <input type="checkbox" checked={hard} onChange={(e) => setHard(e.target.checked)} />
            hard (снять уже выданное)
          </label>
          <button className="btn primary small" onClick={doRevoke} disabled={busy || !reason.trim()}>
            Подтвердить
          </button>
        </div>
      )}

      {error && (
        <div className="error-banner" style={{ marginTop: 8 }}>
          {error}
        </div>
      )}

      {showKeys && keys && (
        <div style={{ marginTop: 10 }}>
          <div className="rules-publish-row" style={{ gap: 6, marginBottom: 6 }}>
            <button className="btn small" onClick={() => exportKeys(false)}>
              Скачать все
            </button>
            <button className="btn small" onClick={() => exportKeys(true)}>
              Скачать невыкупленные
            </button>
          </div>
          <div style={{ display: "grid", gap: 4, maxHeight: 320, overflowY: "auto" }}>
            {keys.map((k) => (
              <KeyRow
                key={k.nonce}
                value={k.key}
                badge={<StatusBadge status={k.status} />}
                meta={
                  <span className="muted small" style={{ whiteSpace: "nowrap" }}>
                    {k.seats_used}/{k.seats_total}
                  </span>
                }
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

const OUTCOME_LABELS: Record<string, string> = {
  ok: "выдан",
  extended: "продлён",
  full: "слоты заняты",
  invalid: "неверный",
  expired: "просрочен",
  revoked: "отозван",
  rate_limited: "лимит",
};
const OUTCOME_OK = new Set(["ok", "extended"]);

function fmtDateTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

// Журнал активаций (§7): кто/сервер/тариф/ключ(маска)/когда + точечное
// освобождение сита (§3). Плюс лента попыток (успех и отказ) для видимости абуза.
function Journal({ onChanged }: { onChanged: () => void }) {
  const [acts, setActs] = useState<KeyActivation[] | null>(null);
  const [attempts, setAttempts] = useState<KeyAttempt[] | null>(null);
  const [showAttempts, setShowAttempts] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadActs = useCallback(() => {
    api
      .keyActivations()
      .then(setActs)
      .catch((e) => setError(e instanceof Error ? e.message : "Не удалось загрузить журнал."));
  }, []);
  useEffect(() => loadActs(), [loadActs]);

  async function release(a: KeyActivation) {
    if (
      !window.confirm(
        `Снять сервер ${a.guild_id} с лицензии? Premium будет снят, а сит освободится — ключ можно будет активировать на другом сервере.`,
      )
    )
      return;
    setBusy(true);
    setError(null);
    try {
      await api.releaseSeat(a.nonce, a.guild_id);
      loadActs();
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось освободить сит.");
    } finally {
      setBusy(false);
    }
  }

  async function openAttempts() {
    setShowAttempts(true);
    if (attempts === null) {
      try {
        setAttempts(await api.keyAttempts());
      } catch (e) {
        setError(e instanceof Error ? e.message : "Не удалось загрузить попытки.");
      }
    }
  }

  return (
    <div className="card pad">
      <div className="acc-title" style={{ marginBottom: 8 }}>
        Активации
      </div>
      {error && (
        <div className="error-banner" style={{ marginBottom: 8 }}>
          {error}
        </div>
      )}
      {acts === null ? (
        <p className="muted small">Загружаю…</p>
      ) : acts.length === 0 ? (
        <div className="muted small">Пока никто не активировал ключи.</div>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.88em" }}>
            <thead>
              <tr style={{ textAlign: "left", color: "var(--muted)" }}>
                <th style={{ padding: "4px 8px" }}>Кто</th>
                <th style={{ padding: "4px 8px" }}>Сервер</th>
                <th style={{ padding: "4px 8px" }}>Тариф</th>
                <th style={{ padding: "4px 8px" }}>Ключ</th>
                <th style={{ padding: "4px 8px" }}>Когда</th>
                <th style={{ padding: "4px 8px" }} />
              </tr>
            </thead>
            <tbody>
              {acts.map((a) => (
                <tr key={`${a.nonce}-${a.guild_id}`} style={{ borderTop: "1px solid var(--line)" }}>
                  <td style={{ padding: "6px 8px", fontFamily: "monospace" }}>{a.user_id}</td>
                  <td style={{ padding: "6px 8px", fontFamily: "monospace" }}>{a.guild_id}</td>
                  <td style={{ padding: "6px 8px" }}>
                    <TierBadge tier={a.tier} />
                  </td>
                  <td style={{ padding: "6px 8px", fontFamily: "monospace" }}>{a.key_masked}</td>
                  <td style={{ padding: "6px 8px", whiteSpace: "nowrap" }}>
                    {fmtDateTime(a.redeemed_at)}
                  </td>
                  <td style={{ padding: "6px 8px", textAlign: "right" }}>
                    <button className="btn ghost small" disabled={busy} onClick={() => release(a)}>
                      Освободить
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div style={{ marginTop: 12 }}>
        {!showAttempts ? (
          <button className="btn ghost small" onClick={openAttempts}>
            Показать попытки активации
          </button>
        ) : (
          <AttemptsTable attempts={attempts} />
        )}
      </div>
    </div>
  );
}

function AttemptsTable({ attempts }: { attempts: KeyAttempt[] | null }) {
  if (attempts === null) return <p className="muted small">Загружаю…</p>;
  if (attempts.length === 0) return <div className="muted small">Попыток пока не было.</div>;
  return (
    <div style={{ overflowX: "auto" }}>
      <div className="muted small" style={{ marginBottom: 6 }}>
        Все попытки активации (успех и отказ) — видно перебор/абуз.
      </div>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.85em" }}>
        <thead>
          <tr style={{ textAlign: "left", color: "var(--muted)" }}>
            <th style={{ padding: "4px 8px" }}>Кто</th>
            <th style={{ padding: "4px 8px" }}>Сервер</th>
            <th style={{ padding: "4px 8px" }}>Исход</th>
            <th style={{ padding: "4px 8px" }}>Когда</th>
          </tr>
        </thead>
        <tbody>
          {attempts.map((a, i) => (
            <tr key={`${a.user_id}-${a.at}-${i}`} style={{ borderTop: "1px solid var(--line)" }}>
              <td style={{ padding: "5px 8px", fontFamily: "monospace" }}>{a.user_id}</td>
              <td style={{ padding: "5px 8px", fontFamily: "monospace" }}>{a.guild_id}</td>
              <td
                style={{ padding: "5px 8px", color: OUTCOME_OK.has(a.outcome) ? "#7bd88f" : "#e0a0a0" }}
              >
                {OUTCOME_LABELS[a.outcome] ?? a.outcome}
              </td>
              <td style={{ padding: "5px 8px", whiteSpace: "nowrap" }}>{fmtDateTime(a.at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
