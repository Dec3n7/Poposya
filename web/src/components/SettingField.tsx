import { useState } from "react";

import { ApiError, api } from "../api";
import type { Channel, SettingField as Field } from "../types";
import { Dropdown, type Option } from "./Dropdown";
import { useToast } from "./Toast";

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
  const known = value === "0" || channels.some((c) => c.id === value);
  // спец-пункты без группы идут первыми, затем каналы по группам
  const options: Option[] = [
    { value: "0", label: "— выключено —" },
    ...(known ? [] : [{ value, label: `ID ${value} (не в списке)` }]),
    ...channels.map((c) => ({ value: c.id, label: c.name, group: c.group })),
  ];
  return (
    <Dropdown
      value={value}
      options={options}
      onChange={onPick}
      ariaLabel="Канал"
      className="dd-channel"
    />
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
  const toast = useToast();

  async function commit(value: boolean | number | string) {
    setStatus("saving");
    setErr("");
    try {
      const updated = await api.setSetting(guildId, field.key, value);
      onChange(updated);
      setDraft(String(updated.value));
      flashSaved();
    } catch (e) {
      const message = e instanceof ApiError ? e.message : "Ошибка сохранения";
      setStatus("error");
      setErr(message);
      // ошибку легко пропустить в длинной форме — дублируем тостом
      toast.error(`${field.label}: ${message}`);
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
      const message = e instanceof ApiError ? e.message : "Ошибка сброса";
      setStatus("error");
      setErr(message);
      toast.error(`${field.label}: ${message}`);
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
        ) : field.kind === "text" ? (
          <input
            className="input text-setting"
            value={draft}
            placeholder="пусто = выключено"
            onChange={(e) => setDraft(e.target.value)}
            onBlur={() => draft !== String(field.value) && commit(draft)}
            onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
          />
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
          {field.kind !== "channel" && field.kind !== "text" && (
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
