import { type ReactNode, useState } from "react";

import type { Guild, Me } from "../types";
import { Cinema } from "./Cinema";
import { Dashboard } from "./Dashboard";
import { Finds } from "./Finds";
import { GuildSettings } from "./GuildSettings";
import { Moderation } from "./Moderation";
import { Music } from "./Music";
import { People } from "./People";

type Tab = "overview" | "people" | "cinema" | "music" | "finds" | "moderation" | "settings";

const I = { fill: "none", stroke: "currentColor", strokeWidth: 2, viewBox: "0 0 24 24" } as const;

const TABS: { id: Tab; label: string; icon: ReactNode }[] = [
  {
    id: "overview",
    label: "Обзор",
    icon: (
      <svg {...I}><rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" /></svg>
    ),
  },
  {
    id: "people",
    label: "Люди",
    icon: (
      <svg {...I}><circle cx="9" cy="8" r="3.2" /><path d="M3.5 19c.6-3 2.9-4.5 5.5-4.5s4.9 1.5 5.5 4.5" /><path d="M16 5.5a3 3 0 0 1 0 5.4M17.5 14.4c2 .6 3.4 2 3.8 4.1" /></svg>
    ),
  },
  {
    id: "cinema",
    label: "Киноклуб",
    icon: (
      <svg {...I}><rect x="3" y="4" width="18" height="16" rx="2.5" /><path d="M3 9h18M8 4v16M16 4v16" /></svg>
    ),
  },
  {
    id: "music",
    label: "Музыка",
    icon: (
      <svg {...I}><path d="M9 18V6l10-2v12" /><circle cx="6" cy="18" r="2.6" /><circle cx="16" cy="16" r="2.6" /></svg>
    ),
  },
  {
    id: "finds",
    label: "Находки",
    icon: (
      <svg {...I}><path d="M20 14.5A8 8 0 1 1 9.5 4a6.3 6.3 0 0 0 10.5 10.5Z" /></svg>
    ),
  },
  {
    id: "moderation",
    label: "Модерация",
    icon: (
      <svg {...I}><path d="M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6z" /></svg>
    ),
  },
  {
    id: "settings",
    label: "Настройки",
    icon: (
      <svg {...I}><path d="M4 7h10M18 7h2M4 12h2M10 12h10M4 17h7M15 17h5" /><circle cx="16" cy="7" r="2" /><circle cx="8" cy="12" r="2" /><circle cx="13" cy="17" r="2" /></svg>
    ),
  },
];

export function GuildView({
  guild,
  me,
  onBack,
  onLogout,
}: {
  guild: Guild;
  me: Me;
  onBack: () => void;
  onLogout: () => void;
}) {
  const [tab, setTab] = useState<Tab>("overview");
  const active = TABS.find((t) => t.id === tab)!;

  return (
    <div className="shell">
      <aside className="rail">
        <div className="rail-brand">
          <span className="rail-mark">🖤</span>
          <div>
            <div className="rail-name">Попося</div>
            <div className="rail-sub">панель сервера</div>
          </div>
        </div>

        <div className="rail-label">Меню</div>
        <nav className="rail-nav">
          {TABS.map((t) => (
            <button
              key={t.id}
              className={`rail-item${tab === t.id ? " active" : ""}`}
              onClick={() => setTab(t.id)}
            >
              {t.icon}
              {t.label}
            </button>
          ))}
        </nav>

        <div className="rail-user">
          {me.avatar ? (
            <img className="rail-avatar" src={me.avatar} alt="" />
          ) : (
            <span className="rail-avatar fallback">
              {(me.username ?? "?").slice(0, 1).toUpperCase()}
            </span>
          )}
          <span className="rail-username">{me.username}</span>
          <div className="rail-user-actions">
            <button className="btn ghost small" onClick={onBack}>
              ← Серверы
            </button>
            <button className="btn ghost small" onClick={onLogout}>
              Выйти
            </button>
          </div>
        </div>
      </aside>

      <main className="stage">
        <div className="stage-top">
          <button className="guild-chip" onClick={onBack} title="Сменить сервер">
            {guild.icon ? (
              <img className="guild-chip-ic" src={guild.icon} alt="" />
            ) : (
              <span className="guild-chip-ic fallback">✂️</span>
            )}
            <span className="guild-chip-name">{guild.name}</span>
            <svg className="guild-chip-swap" width="15" height="15" {...I}>
              <path d="M7 4L3 8l4 4M3 8h13M17 20l4-4-4-4M21 16H8" />
            </svg>
          </button>
        </div>

        <div className="stage-body">
          <div className="stage-head">
            <div className="eyebrow">{active.label}</div>
            <h1 className="stage-title">{guild.name}</h1>
          </div>

          {tab === "overview" && <Dashboard guild={guild} />}
          {tab === "people" && <People guild={guild} />}
          {tab === "cinema" && <Cinema guild={guild} />}
          {tab === "music" && <Music guild={guild} />}
          {tab === "finds" && <Finds guild={guild} />}
          {tab === "moderation" && <Moderation guild={guild} />}
          {tab === "settings" && <GuildSettings guild={guild} />}
        </div>
      </main>
    </div>
  );
}
