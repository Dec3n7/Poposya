import { useCallback, useEffect, useState } from "react";

import { ApiError, api } from "../api";
import { pl } from "../plural";
import type {
  Guild,
  PersonaDetail,
  PersonaIdentity,
  PersonaImportReport,
  PersonaSummary,
} from "../types";
import { Collapsible } from "./Collapsible";
import { Dropdown } from "./Dropdown";
import { PersonaPhrases } from "./PersonaPhrases";
import { SkeletonRows } from "./Skeleton";

// int-цвет ↔ hex для <input type="color">
const toHex = (color: number) => `#${color.toString(16).padStart(6, "0")}`;
const fromHex = (hex: string) => parseInt(hex.replace("#", ""), 16) || 0;

// выгрузка JSON файлом (шаблон/экспорт персоны)
function downloadJson(data: unknown, filename: string) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

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
  // отчёт последнего импорта (что отброшено) — показываем, если было отброшено
  const [importReport, setImportReport] = useState<PersonaImportReport | null>(null);
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

  const downloadTemplate = () =>
    run(async () => {
      downloadJson(await api.personaTemplate(), "persona-template.json");
    }, "Шаблон скачан — отдайте его тому, кто будет заполнять персонажа");

  const doImport = () =>
    run(async () => {
      const data = JSON.parse(importText);
      const { persona, report } = await api.importPersona(data);
      setImportText("");
      setImportOpen(false);
      await loadList();
      setSelectedId(persona.id);
      const ignored = report.phrases_ignored.length + report.attributes_ignored.length;
      setImportReport(ignored > 0 ? report : null);
      setNote(
        `Импортирована «${persona.name}». Принято фраз: ${report.phrases_accepted}` +
          (ignored > 0 ? `, отброшено: ${ignored} — подробности ниже.` : "."),
      );
    });

  const doExport = () =>
    run(async () => {
      if (selectedId == null) return;
      const data = await api.exportPersona(selectedId);
      downloadJson(data, `persona-${detail?.name ?? selectedId}.json`);
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
      <div className="pad">
        <SkeletonRows rows={4} avatar={false} />
      </div>
    );

  const options = list.map((p) => ({
    value: String(p.id),
    label: p.is_default ? `${p.name} (по умолчанию)` : p.name,
  }));

  // сводки для свёрнутых заголовков (всё из уже загруженных данных)
  const defaultName = list.find((p) => p.is_default)?.name;
  const presenceCount = idPresence
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean).length;

  return (
    <div className="persona">
      <p className="muted small persona-intro">
        Голос и характер бота на «{guild.name}». Изменения применяются сразу, без перезапуска.
      </p>

      {error && <div className="error-banner">{error}</div>}
      {note && <div className="ok-banner">{note}</div>}

      {importReport && (
        <div className="card persona-import-report">
          <div className="row-between">
            <strong className="small">Импорт: часть строк не принята</strong>
            <button className="btn ghost small" onClick={() => setImportReport(null)}>
              Скрыть
            </button>
          </div>
          <p className="muted small" style={{ margin: "6px 0" }}>
            Принято фраз: {importReport.phrases_accepted}. Остальные строки заполнены с ошибкой и
            пропущены — исправьте в файле и импортируйте заново.
          </p>
          <ul className="muted small persona-import-issues">
            {[...importReport.attributes_ignored, ...importReport.phrases_ignored].map((iss, i) => (
              <li key={`${iss.key ?? "?"}-${i}`}>
                <code>{iss.key ?? "—"}</code>: {iss.reason}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* шапка контекста — всегда видна */}
      <div className="card persona-context">
        <div className="pc-who">
          <div className="pc-k">Редактирую персону</div>
          <Dropdown
            value={selectedId != null ? String(selectedId) : ""}
            options={options}
            ariaLabel="Выбрать персону для редактирования"
            onChange={(v) => setSelectedId(Number(v))}
          />
        </div>
        <div className="pc-spacer" />
        {selectedId != null && selectedId === assignedId ? (
          <span className="active-badge">
            <span className="dot" /> активна на «{guild.name}»
          </span>
        ) : (
          <button
            className="btn primary small"
            disabled={busy || selectedId == null}
            onClick={() => selectedId != null && assign(selectedId)}
          >
            Сделать активной здесь
          </button>
        )}
      </div>

      {/* библиотека */}
      <Collapsible
        outerClass="card acc"
        headClass="acc-head"
        bodyClass="acc-body"
        storageKey="persona.sec.library"
        defaultOpen={false}
        header={
          <>
            <span className="acc-icon" aria-hidden>
              📚
            </span>
            <span className="acc-titles">
              <span className="acc-title">Библиотека персон</span>
              <span className="acc-summary">
                <span className="chip count">{pl(list.length, ["персона", "персоны", "персон"])}</span>
                {defaultName && <span className="acc-sum-text">по умолчанию: {defaultName}</span>}
              </span>
            </span>
            <span className="chev" aria-hidden>
              ▸
            </span>
          </>
        }
      >
        <div className="acc-pad">
          <div className="btn-row acc-actions">
            <button className="btn ghost small" disabled={busy} onClick={create}>
              + Создать
            </button>
            <button className="btn ghost small" disabled={busy} onClick={downloadTemplate}>
              Скачать шаблон
            </button>
            <button className="btn ghost small" disabled={busy} onClick={() => setImportOpen((o) => !o)}>
              Импорт
            </button>
          </div>
          <p className="muted small" style={{ margin: "8px 0 0" }}>
            «Скачать шаблон» даёт пустую заготовку персонажа (JSON). Отдайте её тому, кто придумывает
            персону; заполненный файл загрузите обратно кнопкой «Импорт».
          </p>
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
              <div className="btn-row" style={{ marginBottom: 8 }}>
                <label className="btn ghost small" style={{ cursor: "pointer" }}>
                  Выбрать файл…
                  <input
                    type="file"
                    accept="application/json,.json"
                    style={{ display: "none" }}
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      e.currentTarget.value = ""; // разрешить повторный выбор того же файла
                      if (file)
                        file
                          .text()
                          .then(setImportText)
                          .catch(() => setError("Не удалось прочитать файл"));
                    }}
                  />
                </label>
                <span className="muted small">или вставьте JSON вручную ниже</span>
              </div>
              <textarea
                className="input mono"
                rows={5}
                placeholder="Содержимое файла персоны (JSON)…"
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
      </Collapsible>

      {/* редактор выбранной персоны */}
      {detail && (
        <>
          {/* Личность */}
          <Collapsible
            outerClass="card acc"
            headClass="acc-head"
            bodyClass="acc-body"
            storageKey="persona.sec.identity"
            defaultOpen
            header={
              <>
                <span className="acc-icon" aria-hidden>
                  🎭
                </span>
                <span className="acc-titles">
                  <span className="acc-title">Личность</span>
                  <span className="acc-summary">
                    {identity ? (
                      <>
                        <span className="acc-sum-text">{idName || identity.default_display_name}</span>
                        <span className="acc-swatch" style={{ background: toHex(idAccent) }} aria-hidden />
                        <code className="acc-sum-code">{toHex(idAccent)}</code>
                        {presenceCount > 0 && (
                          <span className="acc-sum-text">
                            · {pl(presenceCount, ["статус", "статуса", "статусов"])}
                          </span>
                        )}
                      </>
                    ) : (
                      <span className="acc-sum-text">имя, подпись, цвет, статусы</span>
                    )}
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
                  onClick={() =>
                    run(async () => refreshDetail(await api.renamePersona(detail.id, name.trim())))
                  }
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
                    rows={5}
                    value={idPresence}
                    disabled={busy}
                    placeholder={"пусто = встроенные занятия Попоси\nнапр.: читает Мураками"}
                    onChange={(e) => setIdPresence(e.target.value)}
                  />
                  <div className="btn-row" style={{ marginTop: 8 }}>
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
            </div>
          </Collapsible>

          {/* Промпты */}
          <Collapsible
            outerClass="card acc"
            headClass="acc-head"
            bodyClass="acc-body"
            storageKey="persona.sec.prompts"
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
                    <span className={`chip${detail.chime_prompt ? " accent" : ""}`}>
                      вклинивание · {detail.chime_prompt ? "изменён" : "дефолт"}
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
                Голос и характер бота в общении. Пусто = встроенный характер Попоси.
              </p>
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

              <label className="field-label">Промпт решения «вклиниться в разговор»</label>
              <p className="muted small acc-hint">
                Когда бот сам решает вступить в чат. Пусто = встроенное поведение.
              </p>
              <textarea
                className="input mono"
                rows={8}
                value={chime}
                disabled={busy}
                placeholder={detail.default_chime_prompt}
                onChange={(e) => setChime(e.target.value)}
              />
              <div className="btn-row" style={{ marginTop: 8 }}>
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
            </div>
          </Collapsible>

          {/* Фразы бота (сам оборачивается в сворачиваемый блок) */}
          <PersonaPhrases personaId={detail.id} />

          {/* Управление персоной */}
          <Collapsible
            outerClass="card acc"
            headClass="acc-head"
            bodyClass="acc-body"
            storageKey="persona.sec.manage"
            defaultOpen={false}
            header={
              <>
                <span className="acc-icon" aria-hidden>
                  ⚙️
                </span>
                <span className="acc-titles">
                  <span className="acc-title">Управление персоной</span>
                  <span className="acc-summary">
                    <span className="acc-sum-text">дублировать · экспорт · удалить</span>
                  </span>
                </span>
                <span className="chev" aria-hidden>
                  ▸
                </span>
              </>
            }
          >
            <div className="acc-pad">
              <div className="row-between" style={{ flexWrap: "wrap", gap: 10 }}>
                <div className="btn-row" style={{ marginTop: 0 }}>
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
          </Collapsible>
        </>
      )}
    </div>
  );
}
