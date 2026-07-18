import { useState } from "react";

import type { Guild } from "../types";
import { Cinema } from "./Cinema";
import { Dashboard } from "./Dashboard";
import { Finds } from "./Finds";
import { GuildSettings } from "./GuildSettings";
import { Moderation } from "./Moderation";
import { Music } from "./Music";
import { People } from "./People";

type Tab = "overview" | "people" | "cinema" | "music" | "finds" | "moderation" | "settings";

const TABS: { id: Tab; label: string }[] = [
  { id: "overview", label: "Обзор" },
  { id: "people", label: "Люди" },
  { id: "cinema", label: "Киноклуб" },
  { id: "music", label: "Музыка" },
  { id: "finds", label: "Находки" },
  { id: "moderation", label: "Модерация" },
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
      {tab === "people" && <People guild={guild} />}
      {tab === "cinema" && <Cinema guild={guild} />}
      {tab === "music" && <Music guild={guild} />}
      {tab === "finds" && <Finds guild={guild} />}
      {tab === "moderation" && <Moderation guild={guild} />}
      {tab === "settings" && <GuildSettings guild={guild} />}
    </div>
  );
}
