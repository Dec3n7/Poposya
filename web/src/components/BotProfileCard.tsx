import { useEffect, useState } from "react";

import { ApiError, api } from "../api";
import type { BotProfile } from "../types";
import { AvatarCropper } from "./AvatarCropper";

const EMPTY: BotProfile = { nick: "", avatar_url: "", banner_url: "", avatar_data: "" };

export function BotProfileCard({ guildId }: { guildId: string }) {
  const [form, setForm] = useState<BotProfile | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [ok, setOk] = useState(false);
  const [cropping, setCropping] = useState(false);

  useEffect(() => {
    setForm(null);
    setMsg(null);
    api
      .botProfile(guildId)
      .then(setForm)
      .catch(() => setForm(EMPTY));
  }, [guildId]);

  function set<K extends keyof BotProfile>(key: K, value: string) {
    setForm((f) => (f ? { ...f, [key]: value } : f));
  }

  async function save() {
    if (!form) return;
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.setBotProfile(guildId, form);
      const s = r.command.status;
      setOk(s === "done");
      setMsg(
        s === "done"
          ? (r.command.result ?? "Применено.")
          : s === "failed"
            ? (r.command.result ?? "Не вышло применить.")
            : "Сохранено — применяется… (мост доступен только на Postgres)",
      );
    } catch (e) {
      setOk(false);
      setMsg(e instanceof ApiError ? e.message : "Ошибка");
    } finally {
      setBusy(false);
    }
  }

  if (!form) return null;

  return (
    <section>
      <h2 className="section-title">Профиль бота на этом сервере</h2>
      <div className="card pad bot-profile">
        <p className="muted small" style={{ marginTop: 0 }}>
          Ник, аватар и баннер Попоси именно на этом сервере. Пусто = как везде (глобальный
          профиль). Аватар/баннер — ссылкой на картинку. Меняется через бота, применяется сразу.
        </p>

        <label className="bp-row">
          <span className="bp-label">Ник</span>
          <input
            className="input bp-input"
            value={form.nick}
            maxLength={32}
            placeholder="как везде"
            onChange={(e) => set("nick", e.target.value)}
          />
        </label>

        <div className="bp-row">
          <span className="bp-label">Аватар</span>
          <input
            className="input bp-input"
            value={form.avatar_url}
            placeholder="ссылка на картинку или загрузи файл →"
            disabled={!!form.avatar_data}
            onChange={(e) => setForm((f) => (f ? { ...f, avatar_url: e.target.value } : f))}
          />
          <button className="btn ghost small" onClick={() => setCropping(true)}>
            Загрузить фото
          </button>
          {(form.avatar_data || form.avatar_url) && (
            <img className="bp-preview" src={form.avatar_data || form.avatar_url} alt="" />
          )}
          {form.avatar_data && (
            <button
              className="btn ghost small"
              title="Убрать загруженное фото"
              onClick={() => setForm((f) => (f ? { ...f, avatar_data: "" } : f))}
            >
              ✕
            </button>
          )}
        </div>

        <label className="bp-row">
          <span className="bp-label">Баннер (URL)</span>
          <input
            className="input bp-input"
            value={form.banner_url}
            placeholder="https://…/banner.png"
            onChange={(e) => set("banner_url", e.target.value)}
          />
        </label>

        <div className="bp-actions">
          <button className="btn primary" onClick={save} disabled={busy}>
            Сохранить и применить
          </button>
          {msg && (
            <span className={`bp-msg ${ok ? "ok" : "bad"}`}>{msg}</span>
          )}
        </div>
      </div>

      {cropping && (
        <AvatarCropper
          onCancel={() => setCropping(false)}
          onDone={(dataUrl) => {
            setForm((f) => (f ? { ...f, avatar_data: dataUrl, avatar_url: "" } : f));
            setCropping(false);
          }}
        />
      )}
    </section>
  );
}
