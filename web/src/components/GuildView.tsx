import { useState } from "react";

import type { Guild } from "../types";
import { Cinema } from "./Cinema";
import { Dashboard } from "./Dashboard";
import { GuildSettings } from "./GuildSettings";

type Tab = "overview" | "cinema" | "settings";

const TABS: { id: Tab; label: string }[] = [
  { id: "overview", label: "Обзор" },
  { id: "cinema", label: "Киноклуб" },
  { id: "settings", label: "Настройки" },
];

export function GuildView({ guild }: { guild: Guild }) {
  const [tab, setTab] = useState<Tab>("overview");
  return (
    <div>
      <h1 className="h1">{guild.name}</h1>
      <div className="tabs">
        {TABS.map((t) => (
          <button
            key={t.id}
            className={`tab${tab === t.id ? " active" : ""}`}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>
      {tab === "overview" && <Dashboard guild={guild} />}
      {tab === "cinema" && <Cinema guild={guild} />}
      {tab === "settings" && <GuildSettings guild={guild} />}
    </div>
  );
}
