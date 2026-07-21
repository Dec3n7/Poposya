import { useCallback, useEffect, useState } from "react";

import { ApiError, api } from "../api";
import type { Guild, PersonaDetail, PersonaIdentity, PersonaSummary } from "../types";
import { Dropdown } from "./Dropdown";

// int-цвет ↔ hex для <input type="color">
const toHex = (color: number) => `#${color.toString(16).padStart(6, "0")}`;
const fromHex = (hex: string) => parseInt(hex.replace("#", ""), 16) || 0;

// Вкладка «Персона» (только оператор бота): назначение персоны этому серверу +
// глобальная библиотека (создать/дублировать/импорт/экспорт) и редактор промптов.
// Вся запись идёт в PersonaService (БД + NOTIFY) — бот перечитывает без рестарта.
export function Persona({ guild }: { guild: Guild }) {
  const [list, setList] = useState<PersonaSummary[] | null>(null);
  const [assignedId, setAssignedId] = useState<number | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<PersonaDetail | null>(null);
  const [prompt, setPrompt] = useState("");
  const [chime, setChime] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [importText, setImportText] = useState("");
  // мягкая личность выбранной персоны (имя/подпись/цвет/presence)
  const [identity, setIdentity] = useState<PersonaIdentity | null>(null);
  const [idName, setIdName] = useState("");
  const [idSignature, setIdSignature] = useState("");
  const [idAccent, setIdAccent] = useState(0);
  const [idPresence, setIdPresence] = useState("");

  const loadList = useCallback(async () => {
    const [personas, assigned] = await Promise.all([api.personas(), api.guildPersona(guild.id)]);
    setList(personas);
    setAssignedId(assigned.persona_id);
    setSelectedId((cur) => cur ?? assigned.persona_id);
  }, [guild.id]);

  useEffect(() => {
    setList(null);
    setSelectedId(null);
    setError(null);
    loadList().catch((e) => {
      if (e instanceof ApiError && e.status === 403) setError("Доступ только у оператора бота.");
      else setError(e instanceof Error ? e.message : "Не удалось загрузить персоны");
    });
  }, [loadList]);

  // подгрузка деталей выбранной персоны в редактор
  useEffect(() => {
    if (selectedId == null) return;
    let alive = true;
    api
      .persona(selectedId)
      .then((d) => {
        if (!alive) return;
        setDetail(d);
        setPrompt(d.prompt);
        setChime(d.chime_prompt);
        setName(d.name);
      })
      .catch(() => alive && setDetail(null));
    api
      .personaIdentity(selectedId)
      .then((i) => alive && applyIdentity(i))
      .catch(() => alive && setIdentity(null));
    return () => {
      alive = false;
    };
  }, [selectedId]);

  function applyIdentity(i: PersonaIdentity) {
    setIdentity(i);
    setIdName(i.display_name);
    setIdSignature(i.signature);
    setIdAccent(i.accent_color);
    setIdPresence(i.presence.join("\n"));
  }

  const identityDirty =
    identity != null &&
    (idName !== identity.display_name ||
      idSignature !== identity.signature ||
      idAccent !== identity.accent_color ||
      idPresence !== identity.presence.join("\n"));

  const saveIdentity = () =>
    run(async () => {
      if (selectedId == null) return;
      const saved = await api.setPersonaIdentity(selectedId, {
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

  const resetIdentity = () =>
    run(async () => {
      if (selectedId == null || identity == null) return;
      const saved = await api.setPersonaIdentity(selectedId, {
        display_name: identity.default_display_name,
        signature: identity.default_signature,
        accent_color: identity.default_accent_color,
        presence: [],
      });
      applyIdentity(saved);
    }, "Сброшено к дефолту");

  async function run(fn: () => Promise<void>, ok?: string) {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      await fn();
      if (ok) setNote(ok);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось выполнить");
    } finally {
      setBusy(false);
    }
  }

  const refreshDetail = async (d: PersonaDetail) => {
    setDetail(d);
    setPrompt(d.prompt);
    setChime(d.chime_prompt);
    setName(d.name);
    await loadList();
  };

  const assign = (id: number) =>
    run(async () => {
      const res = await api.assignPersona(guild.id, id);
      setAssignedId(res.persona_id);
      await loadList();
    }, "Назначено этому серверу");

  const create = () =>
    run(async () => {
      const d = await api.createPersona("Новая персона");
      await loadList();
      setSelectedId(d.id);
    });

  const duplicate = () =>
    run(async () => {
      if (selectedId == null) return;
      const d = await api.duplicatePersona(selectedId, `${detail?.name ?? "Персона"} — копия`);
      await loadList();
      setSelectedId(d.id);
    });

  const doImport = () =>
    run(async () => {
      const data = JSON.parse(importText);
      const d = await api.importPersona(data);
      setImportText("");
      setImportOpen(false);
      await loadList();
      setSelectedId(d.id);
    }, "Импортировано");

  const doExport = () =>
    run(async () => {
      if (selectedId == null) return;
      const data = await api.exportPersona(selectedId);
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `persona-${detail?.name ?? selectedId}.json`;
      a.click();
      URL.revokeObjectURL(url);
    });

  const remove = () =>
    run(async () => {
      if (selectedId == null) return;
      await api.deletePersona(selectedId);
      setSelectedId(assignedId);
      await loadList();
    });

  if (error && !list) return <div className="error-banner">{error}</div>;
  if (!list)
    return (
      <div className="center" style={{ minHeight: 200 }}>
        <div className="spinner" aria-label="Загрузка" />
      </div>
    );

  const options = list.map((p) => ({
    value: String(p.id),
    label: p.is_default ? `${p.name} (по умолчанию)` : p.name,
  }));

  return (
    <div className="persona">
      <p className="muted" style={{ marginTop: 0 }}>
        Персона задаёт голос и характер бота: системный промпт для общения и решения о
        вклинивании. Изменения применяются сразу, без перезапуска. Пустой промпт = встроенный
        характер Попоси.
      </p>

      {error && <div className="error-banner">{error}</div>}
      {note && <div className="ok-banner">{note}</div>}

      {/* назначение серверу */}
      <div className="card pad" style={{ marginBottom: 16 }}>
        <div className="row-between">
          <div>
            <div className="field-label">Активная персона на «{guild.name}»</div>
            <div className="muted small">Что бот использует именно на этом сервере.</div>
          </div>
          <Dropdown
            value={assignedId != null ? String(assignedId) : ""}
            options={options}
            ariaLabel="Персона сервера"
            onChange={(v) => assign(Number(v))}
          />
        </div>
      </div>

      {/* библиотека */}
      <div className="card pad" style={{ marginBottom: 16 }}>
        <div className="row-between" style={{ marginBottom: 12 }}>
          <div className="field-label">Библиотека персон</div>
          <div className="btn-row">
            <button className="btn ghost small" disabled={busy} onClick={create}>
              + Создать
            </button>
            <button className="btn ghost small" disabled={busy} onClick={() => setImportOpen((o) => !o)}>
              Импорт
            </button>
          </div>
        </div>

        <div className="persona-list">
          {list.map((p) => (
            <button
              key={p.id}
              className={`persona-row${p.id === selectedId ? " active" : ""}`}
              onClick={() => setSelectedId(p.id)}
            >
              <span className="persona-row-name">{p.name}</span>
              {p.is_default && <span className="chip">по умолчанию</span>}
              {p.id === assignedId && <span className="chip accent">активна здесь</span>}
              {p.assigned_count > 0 && (
                <span className="muted small">на {p.assigned_count} серв.</span>
              )}
            </button>
          ))}
        </div>

        {importOpen && (
          <div style={{ marginTop: 12 }}>
            <textarea
              className="input mono"
              rows={5}
              placeholder="Вставьте JSON выгрузки персоны…"
              value={importText}
              onChange={(e) => setImportText(e.target.value)}
            />
            <div className="btn-row" style={{ marginTop: 8 }}>
              <button className="btn primary small" disabled={busy || !importText.trim()} onClick={doImport}>
                Импортировать
              </button>
            </div>
          </div>
        )}
      </div>

      {/* редактор выбранной персоны */}
      {detail && (
        <div className="card pad">
          <div className="row-between" style={{ marginBottom: 12 }}>
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
              onClick={() =>
                run(async () => refreshDetail(await api.renamePersona(detail.id, name.trim())))
              }
            >
              Переименовать
            </button>
          </div>

          <div className="field-label">Системный промпт</div>
          <textarea
            className="input mono"
            rows={12}
            value={prompt}
            disabled={busy}
            placeholder={detail.default_prompt}
            onChange={(e) => setPrompt(e.target.value)}
          />
          <div className="btn-row" style={{ margin: "8px 0 20px" }}>
            <button
              className="btn primary small"
              disabled={busy || prompt === detail.prompt}
              onClick={() =>
                run(async () => refreshDetail(await api.setPersonaPrompt(detail.id, prompt)), "Промпт сохранён")
              }
            >
              Сохранить
            </button>
            <button
              className="btn ghost small"
              disabled={busy || detail.prompt === ""}
              onClick={() =>
                run(async () => refreshDetail(await api.setPersonaPrompt(detail.id, "")), "Сброшено к дефолту")
              }
            >
              Сбросить к дефолту
            </button>
          </div>

          <div className="field-label">Промпт решения «вклиниться в разговор»</div>
          <textarea
            className="input mono"
            rows={8}
            value={chime}
            disabled={busy}
            placeholder={detail.default_chime_prompt}
            onChange={(e) => setChime(e.target.value)}
          />
          <div className="btn-row" style={{ margin: "8px 0 20px" }}>
            <button
              className="btn primary small"
              disabled={busy || chime === detail.chime_prompt}
              onClick={() =>
                run(
                  async () => refreshDetail(await api.setPersonaChimePrompt(detail.id, chime)),
                  "Промпт сохранён",
                )
              }
            >
              Сохранить
            </button>
            <button
              className="btn ghost small"
              disabled={busy || detail.chime_prompt === ""}
              onClick={() =>
                run(
                  async () => refreshDetail(await api.setPersonaChimePrompt(detail.id, "")),
                  "Сброшено к дефолту",
                )
              }
            >
              Сбросить к дефолту
            </button>
          </div>

          {identity && (
            <>
              <div className="field-label">Мягкая личность</div>
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
                rows={5}
                value={idPresence}
                disabled={busy}
                placeholder={"пусто = встроенные занятия Попоси\nнапр.: читает Мураками"}
                onChange={(e) => setIdPresence(e.target.value)}
              />
              <div className="btn-row" style={{ margin: "8px 0 20px" }}>
                <button
                  className="btn primary small"
                  disabled={busy || !identityDirty}
                  onClick={saveIdentity}
                >
                  Сохранить
                </button>
                <button className="btn ghost small" disabled={busy} onClick={resetIdentity}>
                  Сбросить к дефолту
                </button>
              </div>
            </>
          )}

          <div className="row-between persona-footer">
            <div className="btn-row">
              <button className="btn ghost small" disabled={busy} onClick={duplicate}>
                Дублировать
              </button>
              <button className="btn ghost small" disabled={busy} onClick={doExport}>
                Экспорт JSON
              </button>
            </div>
            {!detail.is_default && (
              <button className="btn danger small" disabled={busy} onClick={remove}>
                Удалить персону
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
