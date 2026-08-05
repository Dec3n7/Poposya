import { useEffect, useState } from "react";

import { ApiError, api } from "../api";
import { useRefetchOnFocus } from "../refresh";
import type { FindsOverview, Guild } from "../types";
import { EmptyState } from "./EmptyState";
import { RefreshButton } from "./RefreshButton";
import { Skeleton, SkeletonRows } from "./Skeleton";

function fmtExpires(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString("ru-RU", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function Finds({ guild }: { guild: Guild }) {
  const [data, setData] = useState<FindsOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function load() {
    setBusy(true);
    api
      .finds(guild.id)
      .then(setData)
      .catch((e) => {
        if (e instanceof ApiError && e.status === 404) setError("Попоси нет на этом сервере.");
        else setError(e instanceof Error ? e.message : "Не удалось загрузить находки");
      })
      .finally(() => setBusy(false));
  }

  useEffect(() => {
    setData(null);
    setError(null);
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [guild.id]);
  useRefetchOnFocus(load);

  if (error) return <div className="error-banner">{error}</div>;
  if (!data)
    return (
      <div>
        <h2 className="section-title">Сейчас на сервере</h2>
        <div className="card pad">
          <Skeleton h={64} r={16} />
        </div>
        <h2 className="section-title">Коллекционеры</h2>
        <div className="card leader-card">
          <div className="pad">
            <SkeletonRows rows={4} />
          </div>
        </div>
      </div>
    );

  return (
    <div>
      <div className="tab-tools">
        <RefreshButton onClick={load} busy={busy} />
      </div>
      <h2 className="section-title">Сейчас на сервере</h2>
      {data.active ? (
        <div className="card pad find-active">
          <div className="find-item">
            <span className="find-emoji">{data.active.item_emoji}</span>
            <div>
              <div className="find-name">{data.active.item_name}</div>
              <div className="muted">
                {data.active.rarity_emoji} {data.active.rarity} · {data.active.location}
              </div>
            </div>
          </div>
          <div className="faint find-flavor">{data.active.location_flavor}</div>
          <div className="faint small">Пропадёт {fmtExpires(data.active.expires_at)}</div>
        </div>
      ) : (
        <div className="card">
          <EmptyState
            compact
            title="Сейчас активных находок нет"
            hint="Попося выходит на ночные прогулки и приносит редкие предметы — следующая находка появится здесь."
          />
        </div>
      )}

      <h2 className="section-title">Коллекционеры</h2>
      <div className="card leader-card">
        {data.collectors.length === 0 ? (
          <EmptyState compact title="Пока никто ничего не нашёл" />
        ) : (
          data.collectors.map((c, i) => {
            const name = c.username ?? `ID ${c.user_id}`;
            return (
              <div className="leader-row" key={c.user_id}>
                <span className="leader-rank mono">{i + 1}</span>
                {c.avatar ? (
                  <img className="leader-avatar" src={c.avatar} alt="" />
                ) : (
                  <span className="leader-avatar fallback">{name.slice(0, 1).toUpperCase()}</span>
                )}
                <span className="leader-name">
                  {name}
                  {c.gifted > 0 && <span className="leader-role">🎁 подарено: {c.gifted}</span>}
                </span>
                <span className="leader-points mono">{c.total}</span>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
