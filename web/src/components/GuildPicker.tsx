import type { Guild } from "../types";

function iconUrl(g: Guild): string | null {
  return g.icon ? `https://cdn.discordapp.com/icons/${g.id}/${g.icon}.png?size=64` : null;
}

export function GuildPicker({
  guilds,
  onPick,
}: {
  guilds: Guild[];
  onPick: (g: Guild) => void;
}) {
  return (
    <div>
      <h1 className="h1">Твои серверы</h1>
      <p className="sub">Серверы, где у тебя есть права и где стоит Попося.</p>
      {guilds.length === 0 ? (
        <div className="card pad muted">
          Нет серверов, которыми ты можешь управлять и где есть бот. Пригласи Попосю на сервер и
          вернись.
        </div>
      ) : (
        <div className="guild-grid">
          {guilds.map((g) => {
            const url = iconUrl(g);
            return (
              <button key={g.id} className="guild-card" onClick={() => onPick(g)}>
                {url ? (
                  <img src={url} alt="" className="guild-icon" />
                ) : (
                  <span className="guild-icon fallback">{g.name.slice(0, 1).toUpperCase()}</span>
                )}
                <span className="guild-name">{g.name}</span>
                <span className="guild-go">→</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
