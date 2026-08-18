import { useCallback, useEffect, useState } from "react";

import { ApiError, api } from "../api";
import type { Guild, PersonaDetail, PersonaDraftState, PersonaIdentity } from "../types";
import { Collapsible } from "./Collapsible";
import { PersonaPhrases } from "./PersonaPhrases";

const toHex = (c: number) => `#${c.toString(16).padStart(6, "0")}`;
const fromHex = (h: string) => parseInt(h.replace("#", ""), 16) || 0;

// Вкладка «Персона» для АДМИНА сервера (не оператора): кастомная персона своего
// сервера под ручной модерацией оператора. Черновик — отдельная персона, которая
// НЕ назначена серверу, пока оператор её не одобрит (бот её не видит). Доступно
// только на сервере с активной подпиской. См. persona_service (модерация 0044).
export function PersonaCustom({ guild }: { guild: Guild }) {
  const [state, setState] = useState<PersonaDraftState | null>(null);
  const [detail, setDetail] = useState<PersonaDetail | null>(null);
  const [identity, setIdentity] = useState<PersonaIdentity | null>(null);
  const [name, setName] = useState("");
  const [prompt, setPrompt] = useState("");
  const [chime, setChime] = useState("");
  const [idName, setIdName] = useState("");
  const [idSignature, setIdSignature] = useState("");
  const [idAccent, setIdAccent] = useState(0);
  const [idPresence, setIdPresence] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const loadDetail = useCallback(async (id: number) => {
    const [d, i] = await Promise.all([api.persona(id), api.personaIdentity(id)]);
    setDetail(d);
    setName(d.name);
    setPrompt(d.prompt);
    setChime(d.chime_prompt);
    applyIdentity(i);
  }, []);

  const loadState = useCallback(async () => {
    const s = await api.personaDraft(guild.id);
    setState(s);
    if (s.draft_id != null) await loadDetail(s.draft_id);
    else setDetail(null);
  }, [guild.id, loadDetail]);

  useEffect(() => {
    setState(null);
    setDetail(null);
    setError(null);
    loadState().catch((e) => {
      if (e instanceof ApiError && e.status === 403)
        setError("Доступ только у администраторов этого сервера.");
      else setError(e instanceof Error ? e.message : "Не удалось загрузить");
    });
  }, [loadState]);

  function applyIdentity(i: PersonaIdentity) {
    setIdentity(i);
    setIdName(i.display_name);
    setIdSignature(i.signature);
    setIdAccent(i.accent_color);
    setIdPresence(i.presence.join("\n"));
  }

  async function run(fn: () => Promise<void>, ok?: string) {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      await fn();
      if (ok) setNote(ok);
    } catch (e) {
      const msg =
        e instanceof ApiError && e.status === 402
          ? "Нужна активная подписка сервера (Premium)."
          : e instanceof Error
            ? e.message
            : "Не удалось выполнить";
      setError(msg);
    } finally {
      setBusy(false);
    }
  }

  const createDraft = () =>
    run(async () => {
      const s = await api.createPersonaDraft(guild.id);
      setState(s);
      if (s.draft_id != null) await loadDetail(s.draft_id);
    }, "Черновик создан — заполните персону и отправьте на проверку.");

  const submit = () =>
    run(async () => {
      const s = await api.submitPersonaDraft(guild.id);
      setState(s);
    }, "Отправлено на проверку оператору.");

  const idDirty =
    identity != null &&
    (idName !== identity.display_name ||
      idSignature !== identity.signature ||
      idAccent !== identity.accent_color ||
      idPresence !== identity.presence.join("\n"));

  const saveIdentity = () =>
    run(async () => {
      if (state?.draft_id == null) return;
      const saved = await api.setPersonaIdentity(state.draft_id, {
        display_name: idName.trim(),
        signature: idSignature.trim(),
        accent_color: idAccent,
        presence: idPresence
          .split("\n")
          .map((s) => s.trim())
          .filter(Boolean),
      });
      applyIdentity(saved);
    }, "Личность сохранена");

  const savePrompt = () =>
    run(async () => {
      if (state?.draft_id == null) return;
      setDetail(await api.setPersonaPrompt(state.draft_id, prompt));
    }, "Промпт сохранён");

  const saveChime = () =>
    run(async () => {
      if (state?.draft_id == null) return;
      setDetail(await api.setPersonaChimePrompt(state.draft_id, chime));
    }, "Промпт сохранён");

  const rename = () =>
    run(async () => {
      if (state?.draft_id == null) return;
      const d = await api.renamePersona(state.draft_id, name.trim());
      setDetail(d);
      setName(d.name);
    }, "Переименовано");

  if (error && !state) return <div className="error-banner">{error}</div>;
  if (!state) return <div className="pad muted small">Загружаю…</div>;

  // без подписки — заглушка с апселлом
  if (!state.has_premium) {
    return (
      <div className="card acc">
        <div className="acc-pad">
          <div className="acc-title" style={{ marginBottom: 6 }}>
            🎭 Своя персона бота
          </div>
          <p className="muted small">
            Кастомная персона на «{guild.name}» — фича серверов с активной подпиской. Оформите
            Premium/Pro, и здесь появится редактор: свой промпт, характер, фразы и голос бота. Всё
            уходит на ручную проверку оператора перед включением.
          </p>
        </div>
      </div>
    );
  }

  const status = state.status;
  const statusBanner =
    status === "pending" ? (
      <div className="ok-banner">⏳ Заявка на проверке у оператора. Изменения вступят в силу после одобрения.</div>
    ) : status === "rejected" ? (
      <div className="error-banner">
        ✕ Заявка отклонена{state.review_note ? `: ${state.review_note}` : ""}. Поправьте и отправьте
        снова.
      </div>
    ) : status === "draft" ? (
      <div className="muted small">✎ Черновик. Заполните персону и отправьте на проверку.</div>
    ) : null;

  return (
    <div className="persona">
      <p className="muted small persona-intro">
        Своя персона бота на «{guild.name}». Черновик виден только вам; на сервере он включится
        после одобрения оператором.
      </p>

      {error && <div className="error-banner">{error}</div>}
      {note && <div className="ok-banner">{note}</div>}
      {statusBanner}

      {state.draft_id == null ? (
        <div className="card acc">
          <div className="acc-pad">
            <p className="muted small" style={{ marginTop: 0 }}>
              Создайте черновик персоны — за основу возьмётся то, что сейчас звучит на сервере.
            </p>
            <button className="btn primary small" disabled={busy} onClick={createDraft}>
              Создать персону сервера
            </button>
          </div>
        </div>
      ) : (
        detail && (
          <>
            {/* Личность */}
            <Collapsible
              outerClass="card acc"
              headClass="acc-head"
              bodyClass="acc-body"
              storageKey="persona-custom.identity"
              defaultOpen
              header={
                <>
                  <span className="acc-icon" aria-hidden>
                    🎭
                  </span>
                  <span className="acc-titles">
                    <span className="acc-title">Личность</span>
                    <span className="acc-summary">
                      <span className="acc-sum-text">
                        {idName || identity?.default_display_name || "имя, подпись, цвет, статусы"}
                      </span>
                    </span>
                  </span>
                  <span className="chev" aria-hidden>
                    ▸
                  </span>
                </>
              }
            >
              <div className="acc-pad">
                <label className="field-label">Имя персоны</label>
                <div className="row-between" style={{ marginBottom: 4 }}>
                  <input
                    className="input"
                    style={{ maxWidth: 320 }}
                    value={name}
                    disabled={busy}
                    onChange={(e) => setName(e.target.value)}
                  />
                  <button
                    className="btn ghost small"
                    disabled={busy || !name.trim() || name === detail.name}
                    onClick={rename}
                  >
                    Переименовать
                  </button>
                </div>
                {identity && (
                  <>
                    <hr className="acc-divider" />
                    <div className="identity-grid">
                      <label className="identity-field">
                        <span className="muted small">Имя в тексте</span>
                        <input
                          className="input"
                          value={idName}
                          disabled={busy}
                          placeholder={identity.default_display_name}
                          onChange={(e) => setIdName(e.target.value)}
                        />
                      </label>
                      <label className="identity-field">
                        <span className="muted small">Подпись-эмодзи</span>
                        <input
                          className="input"
                          value={idSignature}
                          disabled={busy}
                          placeholder={identity.default_signature}
                          onChange={(e) => setIdSignature(e.target.value)}
                        />
                      </label>
                      <label className="identity-field">
                        <span className="muted small">Цвет эмбедов</span>
                        <span className="identity-color">
                          <input
                            type="color"
                            value={toHex(idAccent)}
                            disabled={busy}
                            onChange={(e) => setIdAccent(fromHex(e.target.value))}
                          />
                          <code className="muted small">{toHex(idAccent)}</code>
                        </span>
                      </label>
                    </div>
                    <div className="field-label" style={{ marginTop: 12 }}>
                      Discord-статусы (по строке на статус)
                    </div>
                    <textarea
                      className="input mono"
                      rows={4}
                      value={idPresence}
                      disabled={busy}
                      placeholder={"пусто = встроенные занятия Попоси"}
                      onChange={(e) => setIdPresence(e.target.value)}
                    />
                    <div className="btn-row" style={{ marginTop: 8 }}>
                      <button className="btn primary small" disabled={busy || !idDirty} onClick={saveIdentity}>
                        Сохранить
                      </button>
                    </div>
                  </>
                )}
              </div>
            </Collapsible>

            {/* Промпты */}
            <Collapsible
              outerClass="card acc"
              headClass="acc-head"
              bodyClass="acc-body"
              storageKey="persona-custom.prompts"
              defaultOpen={false}
              header={
                <>
                  <span className="acc-icon" aria-hidden>
                    💬
                  </span>
                  <span className="acc-titles">
                    <span className="acc-title">Промпты</span>
                    <span className="acc-summary">
                      <span className={`chip${detail.prompt ? " accent" : ""}`}>
                        системный · {detail.prompt ? "изменён" : "дефолт"}
                      </span>
                    </span>
                  </span>
                  <span className="chev" aria-hidden>
                    ▸
                  </span>
                </>
              }
            >
              <div className="acc-pad">
                <label className="field-label">Системный промпт</label>
                <p className="muted small acc-hint">
                  Голос и характер бота. Пусто = встроенный характер Попоси.
                </p>
                <textarea
                  className="input mono"
                  rows={10}
                  value={prompt}
                  disabled={busy}
                  placeholder={detail.default_prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                />
                <div className="btn-row" style={{ margin: "8px 0 20px" }}>
                  <button
                    className="btn primary small"
                    disabled={busy || prompt === detail.prompt}
                    onClick={savePrompt}
                  >
                    Сохранить
                  </button>
                </div>
                <label className="field-label">Промпт решения «вклиниться в разговор»</label>
                <p className="muted small acc-hint">
                  Когда бот сам решает вступить в чат. Пусто = встроенное поведение.
                </p>
                <textarea
                  className="input mono"
                  rows={6}
                  value={chime}
                  disabled={busy}
                  placeholder={detail.default_chime_prompt}
                  onChange={(e) => setChime(e.target.value)}
                />
                <div className="btn-row" style={{ marginTop: 8 }}>
                  <button
                    className="btn primary small"
                    disabled={busy || chime === detail.chime_prompt}
                    onClick={saveChime}
                  >
                    Сохранить
                  </button>
                </div>
              </div>
            </Collapsible>

            {/* Фразы (переиспользуем операторский компонент) */}
            <PersonaPhrases personaId={detail.id} />

            {/* Отправка на модерацию */}
            <div className="card acc">
              <div className="acc-pad">
                {status === "pending" ? (
                  <p className="muted small" style={{ margin: 0 }}>
                    Заявка уже на проверке у оператора. Можно продолжать править — оператор увидит
                    актуальную версию.
                  </p>
                ) : (
                  <div className="row-between" style={{ flexWrap: "wrap", gap: 10 }}>
                    <p className="muted small" style={{ margin: 0, maxWidth: "42ch" }}>
                      Когда персона готова — отправьте её оператору на проверку. Он одобрит или
                      вернёт с комментарием.
                    </p>
                    <button className="btn primary small" disabled={busy} onClick={submit}>
                      Отправить на проверку
                    </button>
                  </div>
                )}
              </div>
            </div>
          </>
        )
      )}
    </div>
  );
}
