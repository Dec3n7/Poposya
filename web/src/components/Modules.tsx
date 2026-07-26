import { useEffect, useState } from "react";

import { ApiError, api } from "../api";
import type { Guild, GuildModule } from "../types";
import { SkeletonRows } from "./Skeleton";
import { useToast } from "./Toast";

function Toggle({
  on,
  disabled,
  busy,
  onChange,
}: {
  on: boolean;
  disabled?: boolean;
  busy?: boolean;
  onChange: () => void;
}) {
  return (
    <button
      type="button"
      className={`toggle${on ? " on" : ""}`}
      disabled={disabled || busy}
      onClick={onChange}
      role="switch"
      aria-checked={on}
    >
      <span className="knob" />
    </button>
  );
}

export function Modules({ guild }: { guild: Guild }) {
  const [mods, setMods] = useState<GuildModule[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState<string | null>(null);
  const toast = useToast();

  useEffect(() => {
    setMods(null);
    setError(null);
    api
      .modules(guild.id)
      .then(setMods)
      .catch((e) => {
        if (e instanceof ApiError && e.status === 404) setError("Попоси нет на этом сервере.");
        else setError(e instanceof Error ? e.message : "Не удалось загрузить модули");
      });
  }, [guild.id]);

  async function toggle(moduleKey: string, flagKey: string, next: boolean) {
    setSaving(flagKey);
    try {
      await api.setSetting(guild.id, flagKey, next);
      setMods((ms) =>
        (ms ?? []).map((m) => {
          if (m.key !== moduleKey) return m;
          const upd = (f: typeof m.master) =>
            f.key === flagKey ? { ...f, value: next, is_override: true } : f;
          return { ...m, master: upd(m.master), subs: m.subs.map(upd) };
        }),
      );
    } catch (e) {
      // ошибка действия (сохранение тумблера) → тост; загруженный список не рушим
      toast.error(e instanceof ApiError ? e.message : "Не удалось сохранить");
    } finally {
      setSaving(null);
    }
  }

  if (error) return <div className="error-banner">{error}</div>;
  if (!mods)
    return (
      <div className="pad">
        <SkeletonRows rows={4} avatar={false} />
      </div>
    );

  return (
    <div>
      <p className="muted" style={{ marginTop: 0, marginBottom: 16 }}>
        Включай и выключай возможности бота на этом сервере. Выключенный модуль гасит все свои
        подфункции. Применяется сразу, без перезапуска.
      </p>
      {mods.map((m) => (
        <div className="card mod-card" key={m.key}>
          <div className="mod-master">
            <div className="mod-master-head">
              <div className="mod-master-label">{m.label}</div>
              {m.description && <div className="mod-desc">{m.description}</div>}
            </div>
            <Toggle
              on={m.master.value}
              busy={saving === m.master.key}
              onChange={() => toggle(m.key, m.master.key, !m.master.value)}
            />
          </div>
          <div className={`mod-subs${m.master.value ? "" : " off"}`}>
            {m.subs.map((s) => (
              <div className="mod-sub" key={s.key}>
                <span className="mod-sub-label">{s.label}</span>
                <Toggle
                  on={m.master.value && s.value}
                  disabled={!m.master.value}
                  busy={saving === s.key}
                  onChange={() => toggle(m.key, s.key, !s.value)}
                />
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
