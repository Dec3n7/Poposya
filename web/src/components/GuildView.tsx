import { useState } from "react";

import type { Guild } from "../types";
import { Dashboard } from "./Dashboard";
import { GuildSettings } from "./GuildSettings";

type Tab = "overview" | "settings";

export function GuildView({ guild }: { guild: Guild }) {
  const [tab, setTab] = useState<Tab>("overview");
  return (
    <div>
      <h1 className="h1">{guild.name}</h1>
      <div className="tabs">
        <button
          className={`tab${tab === "overview" ? " active" : ""}`}
          onClick={() => setTab("overview")}
        >
          Обзор
        </button>
        <button
          className={`tab${tab === "settings" ? " active" : ""}`}
          onClick={() => setTab("settings")}
        >
          Настройки
        </button>
      </div>
      {tab === "overview" ? <Dashboard guild={guild} /> : <GuildSettings guild={guild} />}
    </div>
  );
}
