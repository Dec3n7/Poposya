import { useEffect, useState } from "react";

import { api } from "../api";
import type { PersonaPhrase, PhraseChange } from "../types";
import { Dropdown } from "./Dropdown";

// Каталог фраз персоны (P4): категории → строки «дефолт → override», режим на
// блок, сброс по ключу и глобальный find-and-replace с предпросмотром.
// Наполняется волнами по когам — здесь только отрисовка того, что в реестре.

const CATEGORY_LABELS: Record<string, string> = {
  activity: "Активность",
  ai_chat: "Общение (AI)",
};

const MODE_LABELS: Record<string, string> = {
  ai_then_static: "AI, потом статика",
  static: "Только статика",
  silent: "Молчать",
};

// значение фразы <-> текст в редакторе (списки — по строке на элемент)
function toText(phrase: PersonaPhrase, value: unknown): string {
  if (phrase.kind === "list" && Array.isArray(value)) return value.join("\n");
  if (phrase.kind === "dict") return JSON.stringify(value ?? {}, null, 2);
  return String(value ?? "");
}

// короткий текст значения для предпросмотра замены
function previewText(value: unknown): string {
  if (Array.isArray(value)) return value.join(" · ");
  if (value !== null && typeof value === "object") return JSON.stringify(value);
  return String(value ?? "");
}

function fromText(phrase: PersonaPhrase, text: string): unknown {
  if (phrase.kind === "list")
    return text
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean);
  if (phrase.kind === "dict") return JSON.parse(text);
  return text;
}

function PhraseRow({
  phrase,
  busy,
  onSave,
  onReset,
}: {
  phrase: PersonaPhrase;
  busy: boolean;
  onSave: (value: unknown, mode: string) => void;
  onReset: () => void;
}) {
  const effective = phrase.value ?? phrase.default;
  const [text, setText] = useState(() => toText(phrase, effective));
  const [mode, setMode] = useState(phrase.mode);

  // персона сменилась / пришёл свежий ответ — перечитать локальный черновик
  useEffect(() => {
    setText(toText(phrase, phrase.value ?? phrase.default));
    setMode(phrase.mode);
  }, [phrase]);

  const dirty = text !== toText(phrase, effective) || mode !== phrase.mode;
  const rows = Math.min(6, Math.max(phrase.kind === "list" ? 3 : 1, text.split("\n").length));

  return (
    <div className="phrase-row">
      <div className="phrase-head">
        <span className="phrase-label">{phrase.label || phrase.key}</span>
        {phrase.is_override && <span className="chip accent">изменено</span>}
        {phrase.placeholders.length > 0 && (
          <span className="muted small phrase-ph">
            {phrase.placeholders.map((p) => `{${p}}`).join(" ")}
          </span>
        )}
      </div>
      <div className="phrase-body">
        <textarea
          className="input mono phrase-input"
          rows={rows}
          value={text}
          disabled={busy}
          placeholder={toText(phrase, phrase.default) || "пусто = молчать"}
          onChange={(e) => setText(e.target.value)}
        />
        <div className="phrase-controls">
          {phrase.allowed_modes.length > 1 && (
            <Dropdown
              value={mode}
              options={phrase.allowed_modes.map((m) => ({
                value: m,
                label: MODE_LABELS[m] ?? m,
              }))}
              ariaLabel={`Режим: ${phrase.label || phrase.key}`}
              onChange={setMode}
            />
          )}
          <button
            className="btn primary small"
            disabled={busy || !dirty}
            onClick={() => {
              try {
                onSave(fromText(phrase, text), mode);
              } catch {
                // кривой JSON словаря — не сохраняем
              }
            }}
          >
            Сохранить
          </button>
          {phrase.is_override && (
            <button className="btn ghost small" disabled={busy} onClick={onReset}>
              Сбросить
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export function PersonaPhrases({ personaId }: { personaId: number }) {
  const [phrases, setPhrases] = useState<PersonaPhrase[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [find, setFind] = useState("");
  const [replace, setReplace] = useState("");
  const [preview, setPreview] = useState<PhraseChange[] | null>(null);

  useEffect(() => {
    let alive = true;
    setPhrases(null);
    setPreview(null);
    api
      .personaPhrases(personaId)
      .then((list) => alive && setPhrases(list))
      .catch((e) => alive && setError(e instanceof Error ? e.message : "Не удалось загрузить"));
    return () => {
      alive = false;
    };
  }, [personaId]);

  async function run(fn: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось выполнить");
    } finally {
      setBusy(false);
    }
  }

  const patch = (updated: PersonaPhrase) =>
    setPhrases((cur) => cur && cur.map((p) => (p.key === updated.key ? updated : p)));

  const save = (phrase: PersonaPhrase, value: unknown, mode: string) =>
    run(async () => patch(await api.setPersonaPhrase(personaId, phrase.key, value, mode)));

  const reset = (phrase: PersonaPhrase) =>
    run(async () => patch(await api.resetPersonaPhrase(personaId, phrase.key)));

  const doPreview = () =>
    run(async () => setPreview(await api.replacePersonaPhrases(personaId, find, replace, true)));

  const doReplace = () =>
    run(async () => {
      await api.replacePersonaPhrases(personaId, find, replace, false);
      setPreview(null);
      setFind("");
      setReplace("");
      setPhrases(await api.personaPhrases(personaId));
    });

  if (error && !phrases) return <div className="error-banner">{error}</div>;
  if (!phrases) return <p className="muted">Загружаю каталог фраз…</p>;

  const categories = [...new Set(phrases.map((p) => p.category))];

  return (
    <div className="card pad" style={{ marginTop: 16 }}>
      <div className="field-label">Фразы бота</div>
      <p className="muted small" style={{ marginTop: 0 }}>
        Весь текст, которым говорит бот. Пустое поле = молчать; «Сбросить» возвращает
        встроенный дефолт. Каталог пополняется по мере выноса текста из модулей.
      </p>

      {error && <div className="error-banner">{error}</div>}

      {/* find-and-replace по всем фразам */}
      <div className="phrase-replace">
        <input
          className="input"
          placeholder="Найти…"
          value={find}
          disabled={busy}
          onChange={(e) => setFind(e.target.value)}
        />
        <input
          className="input"
          placeholder="Заменить на…"
          value={replace}
          disabled={busy}
          onChange={(e) => setReplace(e.target.value)}
        />
        <button className="btn ghost small" disabled={busy || !find} onClick={doPreview}>
          Предпросмотр
        </button>
        <button
          className="btn primary small"
          disabled={busy || !find || !preview || preview.length === 0}
          onClick={doReplace}
        >
          Заменить
        </button>
      </div>
      {preview && (
        <div className="phrase-preview">
          {preview.length === 0 && <span className="muted small">Совпадений нет.</span>}
          {preview.map((c) => (
            <div key={c.key} className="phrase-preview-row">
              <code className="muted small">{c.key}</code>
              <div className="small">
                <s>{previewText(c.before)}</s>
                {" → "}
                {previewText(c.after)}
              </div>
            </div>
          ))}
        </div>
      )}

      {categories.map((cat) => (
        <div key={cat} className="phrase-cat">
          <div className="group-head">{CATEGORY_LABELS[cat] ?? cat}</div>
          {phrases
            .filter((p) => p.category === cat)
            .map((p) => (
              <PhraseRow
                key={p.key}
                phrase={p}
                busy={busy}
                onSave={(value, mode) => save(p, value, mode)}
                onReset={() => reset(p)}
              />
            ))}
        </div>
      ))}
    </div>
  );
}
