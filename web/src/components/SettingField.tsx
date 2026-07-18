import { useState } from "react";

import { ApiError, api } from "../api";
import type { Channel, SettingField as Field } from "../types";

type Status = "idle" | "saving" | "saved" | "error";

function ChannelSelect({
  value,
  channels,
  onPick,
}: {
  value: string;
  channels: Channel[];
  onPick: (id: string) => void;
}) {
  const groups: Record<string, Channel[]> = {};
  for (const c of channels) (groups[c.group] ??= []).push(c);
  const known = value === "0" || channels.some((c) => c.id === value);
  return (
    <select className="input select" value={value} onChange={(e) => onPick(e.target.value)}>
      <option value="0">— выключено —</option>
      {!known && <option value={value}>ID {value} (не в списке)</option>}
      {Object.entries(groups).map(([group, list]) => (
        <optgroup label={group} key={group}>
          {list.map((c) => (
            <option value={c.id} key={c.id}>
              {c.name}
            </option>
          ))}
        </optgroup>
      ))}
    </select>
  );
}

export function SettingField({
  guildId,
  field,
  onChange,
  channels,
}: {
  guildId: string;
  field: Field;
  onChange: (updated: Field) => void;
  channels?: Channel[];
}) {
  const [status, setStatus] = useState<Status>("idle");
  const [err, setErr] = useState("");
  const [draft, setDraft] = useState(String(field.value));

  async function commit(value: boolean | number | string) {
    setStatus("saving");
    setErr("");
    try {
      const updated = await api.setSetting(guildId, field.key, value);
      onChange(updated);
      setDraft(String(updated.value));
      flashSaved();
    } catch (e) {
      setStatus("error");
      setErr(e instanceof ApiError ? e.message : "Ошибка сохранения");
    }
  }

  async function reset() {
    setStatus("saving");
    setErr("");
    try {
      const updated = await api.resetSetting(guildId, field.key);
      onChange(updated);
      setDraft(String(updated.value));
      flashSaved();
    } catch (e) {
      setStatus("error");
      setErr(e instanceof ApiError ? e.message : "Ошибка сброса");
    }
  }

  function flashSaved() {
    setStatus("saved");
    window.setTimeout(() => setStatus((s) => (s === "saved" ? "idle" : s)), 1400);
  }

  function commitNumber() {
    const n = field.kind === "float" ? parseFloat(draft) : parseInt(draft, 10);
    if (Number.isNaN(n)) {
      setDraft(String(field.value));
      return;
    }
    if (n !== field.value) commit(n);
  }

  return (
    <div className={`field${field.is_override ? " overridden" : ""}`}>
      <div className="field-head">
        <span className="field-label">{field.label}</span>
        {field.is_override && <span className="badge">изменено</span>}
        <span className="field-status">
          {status === "saving" && <span className="dot saving" />}
          {status === "saved" && <span className="ok">сохранено ✓</span>}
          {status === "error" && <span className="bad">{err}</span>}
        </span>
      </div>

      <div className="field-control">
        {field.kind === "bool" ? (
          <button
            className={`toggle${field.value ? " on" : ""}`}
            role="switch"
            aria-checked={Boolean(field.value)}
            aria-label={field.label}
            onClick={() => commit(!field.value)}
          >
            <span className="knob" />
          </button>
        ) : field.kind === "channel" && channels ? (
          <ChannelSelect value={String(field.value)} channels={channels} onPick={commit} />
        ) : (
          <div className="input-wrap">
            <input
              className="input mono"
              inputMode={field.kind === "channel" ? "numeric" : "decimal"}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onBlur={field.kind === "channel" ? () => draft !== String(field.value) && commit(draft) : commitNumber}
              onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
            />
            {field.unit && <span className="unit">{field.unit}</span>}
          </div>
        )}

        <span className="field-meta">
          {field.kind !== "bool" && field.kind !== "channel" && field.min != null && field.max != null && (
            <span className="faint">
              {field.min}–{field.max}
            </span>
          )}
          {field.kind === "channel" && !channels && <span className="faint">ID канала (0 — выкл)</span>}
          {field.kind !== "channel" && (
            <span className="faint">
              по умолчанию: <span className="mono">{String(field.default)}</span>
            </span>
          )}
        </span>

        {field.is_override && (
          <button className="btn ghost small reset-btn" onClick={reset} disabled={status === "saving"}>
            сбросить
          </button>
        )}
      </div>
    </div>
  );
}
