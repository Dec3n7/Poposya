import type { Guild } from "../types";

function GuildCard({ g, onPick }: { g: Guild; onPick: (g: Guild) => void }) {
  return (
    <button className="guild-card" onClick={() => onPick(g)}>
      {g.icon ? (
        <img src={g.icon} alt="" className="guild-icon" />
      ) : (
        <span className="guild-icon fallback">{g.name.slice(0, 1).toUpperCase()}</span>
      )}
      <span className="guild-name">{g.name}</span>
      {g.operator_only && <span className="guild-tag">подписка</span>}
      <span className="guild-go">→</span>
    </button>
  );
}

export function GuildPicker({
  guilds,
  onPick,
}: {
  guilds: Guild[];
  onPick: (g: Guild) => void;
}) {
  // серверы, которыми оператор не управляет, но где стоит бот — отдельной секцией,
  // с урезанным доступом (только подписка). Обычным пользователям бэкенд их не шлёт.
  const managed = guilds.filter((g) => !g.operator_only);
  const operatorOnly = guilds.filter((g) => g.operator_only);

  return (
    <div>
      <h1 className="h1">Твои серверы</h1>
      <p className="sub">Серверы, где у тебя есть права и где стоит Попося.</p>
      {managed.length === 0 ? (
        <div className="card pad muted">
          Нет серверов, которыми ты можешь управлять и где есть бот. Пригласи Попосю на сервер и
          вернись.
        </div>
      ) : (
        <div className="guild-grid">
          {managed.map((g) => (
            <GuildCard key={g.id} g={g} onPick={onPick} />
          ))}
        </div>
      )}

      {operatorOnly.length > 0 && (
        <>
          <h1 className="h1" style={{ marginTop: 32 }}>
            Серверы бота
          </h1>
          <p className="sub">
            Все серверы, где стоит Попося. Доступна только выдача подписки и её статус.
          </p>
          <div className="guild-grid">
            {operatorOnly.map((g) => (
              <GuildCard key={g.id} g={g} onPick={onPick} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
