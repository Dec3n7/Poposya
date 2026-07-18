import { useEffect, useState } from "react";

import { ApiError, api } from "../api";
import type { Ban, Guild } from "../types";

function fmtExpires(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString("ru-RU", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function Moderation({ guild }: { guild: Guild }) {
  const [bans, setBans] = useState<Ban[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setBans(null);
    setError(null);
    api
      .bans(guild.id)
      .then(setBans)
      .catch((e) => {
        if (e instanceof ApiError && e.status === 404) setError("Попоси нет на этом сервере.");
        else setError(e instanceof Error ? e.message : "Не удалось загрузить баны");
      });
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
        Разбан произойдёт автоматически по истечении срока. Досрочный бан/разбан — командами бота в
        Discord.
      </p>
      <div className="card leader-card">
        {bans.length === 0 ? (
          <div className="pad muted">Активных временных банов нет.</div>
        ) : (
          bans.map((b) => {
            const name = b.username ?? `ID ${b.user_id}`;
            return (
              <div className="cine-row" key={b.user_id}>
                <span className="cine-title">
                  {b.avatar ? (
                    <img className="leader-avatar sm" src={b.avatar} alt="" />
                  ) : (
                    <span className="leader-avatar sm fallback">
                      {name.slice(0, 1).toUpperCase()}
                    </span>
                  )}
                  {name}
                  {b.reason && <span className="cine-review">«{b.reason}»</span>}
                  {b.moderator_name && (
                    <span className="faint"> · выдал {b.moderator_name}</span>
                  )}
                </span>
                <span className="cine-side mono">до {fmtExpires(b.expires_at)}</span>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
