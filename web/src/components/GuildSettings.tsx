import { useEffect, useState } from "react";

import { ApiError, api } from "../api";
import type { Guild, SettingField as Field } from "../types";
import { ComplexSettings } from "./ComplexSettings";
import { SettingField } from "./SettingField";

const SECTIONS: { title: string; match: (k: string) => boolean }[] = [
  { title: "Модерация", match: (k) => k.startsWith("warn_") || k.startsWith("spam_") },
  {
    title: "Отношения",
    match: (k) =>
      k.startsWith("relationship_") ||
      k.startsWith("secret_room_") ||
      k.startsWith("survey_") ||
      k.startsWith("birthday_") ||
      k.startsWith("holiday_"),
  },
  { title: "AI-общение", match: (k) => k.startsWith("ai_") },
  {
    title: "Активность",
    match: (k) => k.startsWith("voice_") || k === "lonely_hours" || k === "absent_days_threshold",
  },
  { title: "Киноклуб", match: (k) => k.startsWith("cinema_") },
  { title: "Находки", match: (k) => k.startsWith("finds_") },
  { title: "Музыка", match: (k) => k.startsWith("music_") },
  { title: "Каморки", match: (k) => k.startsWith("tempvoice_") },
  { title: "Остаться или уйти", match: (k) => k.startsWith("staykick_") },
];

function group(fields: Field[]): { title: string; items: Field[] }[] {
  const out = SECTIONS.map((s) => ({ title: s.title, items: [] as Field[] }));
  const other: Field[] = [];
  for (const f of fields) {
    const i = SECTIONS.findIndex((s) => s.match(f.key));
    if (i >= 0) out[i].items.push(f);
    else other.push(f);
  }
  if (other.length) out.push({ title: "Прочее", items: other });
  return out.filter((s) => s.items.length > 0);
}

export function GuildSettings({ guild }: { guild: Guild }) {
  const [fields, setFields] = useState<Field[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setFields(null);
    setError(null);
    api
      .settings(guild.id)
      .then(setFields)
      .catch((e) => {
        if (e instanceof ApiError && e.status === 404)
          setError("Попоси нет на этом сервере — пригласи её и вернись.");
        else if (e instanceof ApiError && e.status === 403)
          setError("У тебя нет прав управлять этим сервером.");
        else setError(e instanceof Error ? e.message : "Не удалось загрузить настройки");
      });
  }, [guild.id]);

  function update(updated: Field) {
    setFields((fs) => fs?.map((f) => (f.key === updated.key ? updated : f)) ?? fs);
  }

  return (
    <div>
      <h1 className="h1">{guild.name}</h1>
      <p className="sub">
        Настройки поведения Попоси на этом сервере. Меняются на лету — бот применяет сразу.
      </p>

      {error && <div className="error-banner">{error}</div>}
      {!error && !fields && (
        <div className="center" style={{ minHeight: 200 }}>
          <div className="spinner" aria-label="Загрузка" />
        </div>
      )}

      {fields &&
        group(fields).map((section) => (
          <section key={section.title}>
            <h2 className="section-title">{section.title}</h2>
            <div className="card fields-card">
              {section.items.map((f) => (
                <SettingField key={f.key} guildId={guild.id} field={f} onChange={update} />
              ))}
            </div>
          </section>
        ))}

      {fields && <ComplexSettings guildId={guild.id} />}
    </div>
  );
}
