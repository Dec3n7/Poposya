import { useEffect, useState } from "react";

import { api } from "../api";
import type { Channel, Guild } from "../types";
import { Dropdown } from "./Dropdown";

// Публикация правил в канал из панели (мост панель→бот). Текст берётся из фразы
// «/rules: текст правил» этого сервера; бот постит обычным сообщением — без
// «использовал /rules». Живёт на вкладке «Персона» рядом с редактором фраз.
export function RulesPublish({ guild }: { guild: Guild }) {
  const [channels, setChannels] = useState<Channel[] | null>(null);
  const [channelId, setChannelId] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setChannels(null);
    setChannelId("");
    setNote(null);
    setError(null);
    api
      .channels(guild.id)
      .then((cs) => {
        setChannels(cs);
        if (cs.length) setChannelId((cur) => cur || cs[0].id);
      })
      .catch(() => setChannels([]));
  }, [guild.id]);

  async function publish() {
    if (!channelId) return;
    setBusy(true);
    setNote(null);
    setError(null);
    try {
      const cmd = await api.publishRules(guild.id, channelId);
      if (cmd.status === "done") setNote(cmd.result ?? "Правила опубликованы.");
      else if (cmd.status === "failed") setError(cmd.result ?? "Не удалось опубликовать.");
      else setNote("Отправлено — применяется…");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось опубликовать.");
    } finally {
      setBusy(false);
    }
  }

  const options = (channels ?? []).map((c) => ({ value: c.id, label: `#${c.name}` }));

  return (
    <div className="card acc rules-publish">
      <div className="rules-publish-head">
        <span className="acc-icon" aria-hidden>
          📜
        </span>
        <div>
          <div className="acc-title">Публикация правил</div>
          <div className="muted small">
            Бот запостит правила (фраза «/rules: текст правил») в выбранный канал — чисто, от своего
            лица, без «использовал /rules».
          </div>
        </div>
      </div>

      {channels === null ? (
        <p className="muted small">Загружаю каналы…</p>
      ) : channels.length === 0 ? (
        <p className="muted small">Каналы недоступны.</p>
      ) : (
        <div className="rules-publish-row">
          <Dropdown
            value={channelId}
            options={options}
            ariaLabel="Канал для публикации правил"
            onChange={setChannelId}
          />
          <button className="btn primary small" disabled={busy || !channelId} onClick={publish}>
            Опубликовать
          </button>
        </div>
      )}

      {note && (
        <div className="muted small" style={{ marginTop: 8 }}>
          {note}
        </div>
      )}
      {error && (
        <div className="error-banner" style={{ marginTop: 8 }}>
          {error}
        </div>
      )}
    </div>
  );
}
