import { useEffect, useState } from "react";

import { ApiError, api } from "../api";
import type { Cinema as CinemaData, Guild } from "../types";

export function Cinema({ guild }: { guild: Guild }) {
  const [data, setData] = useState<CinemaData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setData(null);
    setError(null);
    api
      .cinema(guild.id)
      .then(setData)
      .catch((e) => {
        if (e instanceof ApiError && e.status === 404) setError("Попоси нет на этом сервере.");
        else setError(e instanceof Error ? e.message : "Не удалось загрузить киноклуб");
      });
  }, [guild.id]);

  if (error) return <div className="error-banner">{error}</div>;
  if (!data)
    return (
      <div className="center" style={{ minHeight: 200 }}>
        <div className="spinner" aria-label="Загрузка" />
      </div>
    );

  return (
    <div>
      <h2 className="section-title">Вотчлист</h2>
      <div className="card leader-card">
        {data.watchlist.length === 0 ? (
          <div className="pad muted">Вотчлист пуст.</div>
        ) : (
          data.watchlist.map((m, i) => (
            <div className="cine-row" key={i}>
              <span className="cine-title">
                {m.title}
                {m.year && <span className="faint"> · {m.year}</span>}
              </span>
              <span className="cine-side mono">
                👍 {m.up} · 👎 {m.down}
              </span>
            </div>
          ))
        )}
      </div>

      <h2 className="section-title">Золотой фонд</h2>
      <div className="card leader-card">
        {data.watched.length === 0 ? (
          <div className="pad muted">Пока ничего не досмотрели.</div>
        ) : (
          data.watched.map((m, i) => (
            <div className="cine-row" key={i}>
              <span className="cine-title">
                {m.title}
                {m.year && <span className="faint"> · {m.year}</span>}
                {m.poposya_review && <span className="cine-review">«{m.poposya_review}»</span>}
              </span>
              <span className="cine-side mono">
                {m.avg_score != null ? `★ ${m.avg_score}` : "—"}
                <span className="faint"> ({m.ratings_count})</span>
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
