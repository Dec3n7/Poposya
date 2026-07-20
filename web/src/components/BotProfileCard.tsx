import { useEffect, useState } from "react";

import { ApiError, api } from "../api";
import { IconX } from "../icons";
import type { BotProfile } from "../types";
import { ImageCropper } from "./ImageCropper";

const EMPTY: BotProfile = {
  nick: "",
  avatar_url: "",
  banner_url: "",
  avatar_data: "",
  banner_data: "",
};

// Discord: аватар 1:1 (512), баннер профиля ~2.5:1 (600×240).
const AVATAR = { w: 512, h: 512 };
const BANNER = { w: 600, h: 240 };

export function BotProfileCard({ guildId }: { guildId: string }) {
  const [form, setForm] = useState<BotProfile | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [ok, setOk] = useState(false);
  const [cropping, setCropping] = useState<null | "avatar" | "banner">(null);

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
          профиль). Аватар/баннер — ссылкой на картинку или загрузи файл (обрежется по правилам
          Discord). Меняется через бота, применяется сразу.
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
          <button className="btn ghost small" onClick={() => setCropping("avatar")}>
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
              <IconX />
            </button>
          )}
        </div>

        <div className="bp-row">
          <span className="bp-label">Баннер</span>
          <input
            className="input bp-input"
            value={form.banner_url}
            placeholder="ссылка на картинку или загрузи файл →"
            disabled={!!form.banner_data}
            onChange={(e) => setForm((f) => (f ? { ...f, banner_url: e.target.value } : f))}
          />
          <button className="btn ghost small" onClick={() => setCropping("banner")}>
            Загрузить фото
          </button>
          {(form.banner_data || form.banner_url) && (
            <img className="bp-preview banner" src={form.banner_data || form.banner_url} alt="" />
          )}
          {form.banner_data && (
            <button
              className="btn ghost small"
              title="Убрать загруженный баннер"
              onClick={() => setForm((f) => (f ? { ...f, banner_data: "" } : f))}
            >
              <IconX />
            </button>
          )}
        </div>

        <div className="bp-actions">
          <button className="btn primary" onClick={save} disabled={busy}>
            Сохранить и применить
          </button>
          {msg && <span className={`bp-msg ${ok ? "ok" : "bad"}`}>{msg}</span>}
        </div>
      </div>

      {cropping === "avatar" && (
        <ImageCropper
          outW={AVATAR.w}
          outH={AVATAR.h}
          round
          title="Обрезка аватара"
          hint="Перетаскивай для сдвига, ползунок — масштаб. Кружком показано, как увидит Discord."
          onCancel={() => setCropping(null)}
          onDone={(dataUrl) => {
            setForm((f) => (f ? { ...f, avatar_data: dataUrl, avatar_url: "" } : f));
            setCropping(null);
          }}
        />
      )}
      {cropping === "banner" && (
        <ImageCropper
          outW={BANNER.w}
          outH={BANNER.h}
          title="Обрезка баннера"
          hint="Перетаскивай для сдвига, ползунок — масштаб. Пропорции баннера Discord (2.5:1)."
          onCancel={() => setCropping(null)}
          onDone={(dataUrl) => {
            setForm((f) => (f ? { ...f, banner_data: dataUrl, banner_url: "" } : f));
            setCropping(null);
          }}
        />
      )}
    </section>
  );
}
