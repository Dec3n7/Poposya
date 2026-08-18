import { useCallback, useEffect, useState } from "react";

import { api } from "../api";
import { pl } from "../plural";
import type { PersonaSubmission } from "../types";
import { Collapsible } from "./Collapsible";

// Очередь модерации персон (только оператор): заявки серверов «на проверке».
// Оператор открывает заявку в редакторе (onOpen → выбор персоны), читает промпт/
// фразы и одобряет (персона назначается серверу) или отклоняет с причиной.
export function PersonaQueue({
  onOpen,
  onChanged,
}: {
  onOpen: (personaId: number) => void;
  onChanged: () => void;
}) {
  const [items, setItems] = useState<PersonaSubmission[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rejectingId, setRejectingId] = useState<number | null>(null);
  const [note, setNote] = useState("");

  const load = useCallback(async () => {
    setItems(await api.personaSubmissions());
  }, []);

  useEffect(() => {
    load().catch((e) => setError(e instanceof Error ? e.message : "Не удалось загрузить заявки"));
  }, [load]);

  async function act(fn: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await load();
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось выполнить");
    } finally {
      setBusy(false);
    }
  }

  const approve = (id: number) => act(() => api.approvePersonaSubmission(id).then(() => undefined));
  const reject = (id: number) =>
    act(async () => {
      await api.rejectPersonaSubmission(id, note.trim());
      setRejectingId(null);
      setNote("");
    });

  if (!items || items.length === 0) return null; // пусто — секцию не показываем

  return (
    <Collapsible
      outerClass="card acc"
      headClass="acc-head"
      bodyClass="acc-body"
      storageKey="persona.sec.queue"
      defaultOpen
      header={
        <>
          <span className="acc-icon" aria-hidden>
            📥
          </span>
          <span className="acc-titles">
            <span className="acc-title">Заявки на персону</span>
            <span className="acc-summary">
              <span className="chip count accent">
                {pl(items.length, ["заявка", "заявки", "заявок"])}
              </span>
              <span className="acc-sum-text">на проверке</span>
            </span>
          </span>
          <span className="chev" aria-hidden>
            ▸
          </span>
        </>
      }
    >
      <div className="acc-pad">
        {error && <div className="error-banner">{error}</div>}
        <div className="persona-list">
          {items.map((s) => (
            <div key={s.persona_id} className="persona-row" style={{ flexWrap: "wrap", gap: 8 }}>
              <span className="persona-row-name">{s.name}</span>
              <span className="muted small">сервер {s.guild_id}</span>
              {s.updated_at && (
                <span className="muted small">· {new Date(s.updated_at).toLocaleString()}</span>
              )}
              <span className="pc-spacer" style={{ flex: 1 }} />
              <button className="btn ghost small" disabled={busy} onClick={() => onOpen(s.persona_id)}>
                Открыть
              </button>
              <button className="btn primary small" disabled={busy} onClick={() => approve(s.persona_id)}>
                Одобрить
              </button>
              <button
                className="btn small"
                disabled={busy}
                onClick={() => {
                  setRejectingId((cur) => (cur === s.persona_id ? null : s.persona_id));
                  setNote("");
                }}
              >
                Отклонить
              </button>
              {rejectingId === s.persona_id && (
                <div style={{ flexBasis: "100%", marginTop: 6 }}>
                  <textarea
                    className="input"
                    rows={2}
                    placeholder="Причина отказа (увидит админ сервера)…"
                    value={note}
                    disabled={busy}
                    onChange={(e) => setNote(e.target.value)}
                  />
                  <div className="btn-row" style={{ marginTop: 6 }}>
                    <button className="btn danger small" disabled={busy} onClick={() => reject(s.persona_id)}>
                      Отклонить заявку
                    </button>
                    <button className="btn ghost small" disabled={busy} onClick={() => setRejectingId(null)}>
                      Отмена
                    </button>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </Collapsible>
  );
}
