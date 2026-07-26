import { useEffect, useState } from "react";

import { api } from "../api";
import type { WardenSnapshot, WardenTarget } from "../types";
import { EmptyState } from "./EmptyState";
import { SkeletonCards } from "./Skeleton";

// Опрос чаще, чем сторож проверяет цели (10с), смысла не имеет: новых данных
// между его циклами не появляется.
const POLL_MS = 5000;

// Кольца из Visual Identity сторожа: форма дублирует цвет, чтобы статус
// читался и на монохромном экране, и при дальтонизме.
const RING: Record<string, string> = {
  READY: "◉",
  DEGRADED: "◍",
  CRITICAL: "◌",
  INCIDENT: "◌",
  UNKNOWN: "○",
};

const TONE: Record<string, string> = {
  READY: "ok",
  DEGRADED: "warn",
  CRITICAL: "bad",
  INCIDENT: "bad",
  UNKNOWN: "muted",
};

const short = (name: string): string =>
  name.replace(/^poposyap-/, "").replace(/-1$/, "");

function ago(ts: number | null): string {
  if (!ts) return "—";
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (s < 60) return `${s} с назад`;
  if (s < 3600) return `${Math.round(s / 60)} мин назад`;
  return `${Math.round(s / 3600)} ч назад`;
}

function uptime(seconds: number): string {
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return d ? `${d} д ${h} ч` : h ? `${h} ч ${m} мин` : `${m} мин`;
}

function TargetCard({ t }: { t: WardenTarget }) {
  const tone = TONE[t.status] ?? "muted";
  return (
    <div className={`warden-card ${tone}`}>
      <div className="warden-card-head">
        <span className={`warden-ring ${tone}`}>{RING[t.status] ?? "○"}</span>
        <span className="warden-name">{short(t.name)}</span>
        <span className={`warden-status ${tone}`}>{t.status}</span>
        {/* score есть не у всех: у nginx и Postgres нет метрик, и рисовать им
            число было бы враньём — за ним не стоит измерений */}
        {t.score !== null && <span className="warden-score">{t.score}</span>}
      </div>

      {t.in_grace && (
        <div className="warden-note">перезапускается — проверки отложены</div>
      )}
      {t.victim && (
        <div className="warden-note">жертва каскада — действий не предпринято</div>
      )}

      {t.gates.length > 0 && (
        <ul className="warden-reasons">
          {t.gates.map((g, i) => (
            <li key={i} className="gate">
              {g.label}
            </li>
          ))}
        </ul>
      )}
      {t.penalties.length > 0 && (
        <ul className="warden-reasons">
          {t.penalties
            .slice()
            .sort((a, b) => b.points - a.points)
            .map((p, i) => (
              <li key={i}>
                <span className="warden-penalty">−{Math.round(p.points)}</span> {p.label}
              </li>
            ))}
        </ul>
      )}
      {t.gates.length === 0 && t.penalties.length === 0 && !t.in_grace && (
        <div className="warden-note ok">все проверки пройдены</div>
      )}
    </div>
  );
}

export function Warden() {
  const [snap, setSnap] = useState<WardenSnapshot | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    const tick = () =>
      api
        .wardenStatus()
        .then((d) => alive && (setSnap(d), setFailed(false)))
        .catch(() => alive && setFailed(true));
    tick();
    const id = setInterval(tick, POLL_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  if (!snap && failed) {
    return <div className="error-banner">Не удалось получить состояние сторожа</div>;
  }
  if (!snap) {
    return (
      <div style={{ padding: 8 }}>
        <SkeletonCards count={5} height={120} />
      </div>
    );
  }

  // Сторож недоступен — это факт о системе, а не ошибка панели: показываем его
  // как состояние, иначе «нет связи со сторожем» выглядело бы как баг панели.
  if (!snap.available) {
    return (
      <div className="card pad">
        <h2 className="warden-title">
          <span className="warden-ring muted">○</span> WARDEN недоступен
        </h2>
        <p className="muted">
          Сторож не отвечает{snap.error ? ` (${snap.error})` : ""}. Наблюдение за
          контейнерами сейчас не ведётся.
        </p>
      </div>
    );
  }

  const w = snap.warden!;
  return (
    <div className="warden">
      <div className="warden-head card pad">
        <div className="warden-head-main">
          <span className="warden-ring ok">◉</span>
          <div>
            <div className="warden-title">WARDEN · {w.status}</div>
            <div className="muted small">
              v{w.version} · аптайм {uptime(w.uptime_seconds)} · проверка каждые{" "}
              {w.interval_seconds} с
            </div>
          </div>
        </div>
        <div className="warden-chips">
          {w.dry_run && <span className="chip warn">режим наблюдения</span>}
          <span className={`chip ${w.gateway_connected ? "ok" : "bad"}`}>
            gateway {w.gateway_connected ? "✓" : "✗"}
          </span>
          <span className={`chip ${w.notifier_ready ? "ok" : "bad"}`}>
            ЛС {w.notifier_ready ? "✓" : "✗"}
          </span>
          {w.queued_notifications > 0 && (
            <span className="chip warn">очередь {w.queued_notifications}</span>
          )}
          {snap.incidents_open! > 0 && (
            <span className="chip bad">инцидентов {snap.incidents_open}</span>
          )}
        </div>
      </div>

      <div className="warden-grid">
        {snap.targets!.map((t) => (
          <TargetCard key={t.name} t={t} />
        ))}
      </div>

      <div className="card pad">
        <div className="warden-subtitle">
          Последние переходы
          <span className="muted small"> · проверка {ago(snap.last_check ?? null)}</span>
        </div>
        {snap.transitions!.length === 0 ? (
          <EmptyState compact title="Переходов не было" hint="Здесь появятся смены состояния целей — как только сторож их заметит." />
        ) : (
          <ul className="warden-log">
            {snap.transitions!.map((t, i) => (
              <li key={i}>
                <span className={`warden-ring ${TONE[t.new] ?? "muted"}`}>
                  {RING[t.new] ?? "○"}
                </span>
                <span className="warden-log-target">{short(t.target)}</span>
                <span className="muted">
                  {t.old} → {t.new}
                  {t.score !== null ? ` · ${t.score}` : ""}
                </span>
                <span className="warden-log-reason">{t.reason}</span>
                <span className="muted small">{ago(t.at)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
