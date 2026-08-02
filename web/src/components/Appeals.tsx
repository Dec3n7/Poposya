import { useEffect, useState } from "react";

import { ApiError, api } from "../api";
import type { Appeal, Guild } from "../types";
import { EmptyState } from "./EmptyState";
import { SkeletonRows } from "./Skeleton";
import { useToast } from "./Toast";

const APPEAL_ACTION: Record<string, string> = {
  ban: "бан",
  tempban: "временный бан",
  mute: "мут",
};

function fmtWhen(iso: string): string {
  return new Date(iso).toLocaleString("ru-RU", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function AppealCard({
  guildId,
  appeal,
  onDone,
}: {
  guildId: string;
  appeal: Appeal;
  onDone: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const name = appeal.username ?? `ID ${appeal.user_id}`;

  async function decide(approve: boolean) {
    setBusy(true);
    try {
      const r = approve
        ? await api.approveAppeal(guildId, appeal.id)
        : await api.rejectAppeal(guildId, appeal.id);
      if (r.status === "failed") toast.error(r.result ?? "Не вышло");
      else toast.success(r.result ?? (approve ? "Апелляция принята" : "Апелляция отклонена"));
      onDone();
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Ошибка");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card pad">
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
        {appeal.avatar ? (
          <img className="leader-avatar sm" src={appeal.avatar} alt="" />
        ) : (
          <span className="leader-avatar sm fallback">{name.slice(0, 1).toUpperCase()}</span>
        )}
        <div>
          <div style={{ fontWeight: 600 }}>{name}</div>
          <div className="muted small">
            {APPEAL_ACTION[appeal.action] ?? appeal.action} · {fmtWhen(appeal.created_at)}
          </div>
        </div>
      </div>
      {appeal.original_reason && (
        <div className="muted small" style={{ marginBottom: 6 }}>
          Причина наказания: {appeal.original_reason}
        </div>
      )}
      <div style={{ whiteSpace: "pre-wrap", margin: "2px 0 12px" }}>{appeal.text}</div>
      <div className="role-panel-actions">
        <button className="btn primary small" onClick={() => decide(true)} disabled={busy}>
          Принять — снять наказание
        </button>
        <button className="btn ghost small" onClick={() => decide(false)} disabled={busy}>
          Отклонить
        </button>
      </div>
    </div>
  );
}

export function Appeals({ guild }: { guild: Guild }) {
  const [appeals, setAppeals] = useState<Appeal[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    setAppeals(null);
    setError(null);
    api
      .appeals(guild.id)
      .then(setAppeals)
      .catch((e) =>
        setError(e instanceof Error ? e.message : "Не удалось загрузить апелляции"),
      );
  }, [guild.id, reloadKey]);

  const reload = () => setReloadKey((k) => k + 1);

  return (
    <div>
      <p className="sub">
        Открытые апелляции на наказания. «Принять» снимает бан/мут и уведомляет участника в ЛС.
      </p>

      {error && <div className="error-banner">{error}</div>}

      {!error && !appeals && <SkeletonRows rows={3} />}

      {appeals && appeals.length === 0 && (
        <EmptyState title="Апелляций нет" hint="Открытых обращений на разбор пока нет." />
      )}

      {appeals && appeals.length > 0 && (
        <div style={{ display: "grid", gap: 12 }}>
          {appeals.map((a) => (
            <AppealCard key={a.id} guildId={guild.id} appeal={a} onDone={reload} />
          ))}
        </div>
      )}
    </div>
  );
}
