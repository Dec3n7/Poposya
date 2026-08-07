import { useEffect, useMemo, useRef, useState } from "react";

// Глобальная палитра команд (Ctrl/Cmd+K): быстрый переход по вкладкам панели.
// Записи собирает вызывающий (GuildView) из списка вкладок + ключевые слова,
// чтобы «музыка/плеер», «правила/фразы», «бан/мут» вели к нужному разделу.
export type PaletteEntry = {
  id: string;
  label: string;
  hint?: string;
  keywords?: string;
  run: () => void;
};

export function CommandPalette({ entries }: { entries: PaletteEntry[] }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  // Ctrl/Cmd+K — открыть/закрыть (перехватываем дефолт браузера)
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.ctrlKey || e.metaKey) && (e.key === "k" || e.key === "K")) {
        e.preventDefault();
        setOpen((v) => !v);
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (!open) return;
    setQ("");
    setSel(0);
    const id = requestAnimationFrame(() => inputRef.current?.focus());
    return () => cancelAnimationFrame(id);
  }, [open]);

  const filtered = useMemo(() => {
    const s = q.trim().toLowerCase();
    if (!s) return entries;
    return entries.filter((e) =>
      `${e.label} ${e.hint ?? ""} ${e.keywords ?? ""}`.toLowerCase().includes(s),
    );
  }, [q, entries]);

  // держим выбранный индекс в границах отфильтрованного списка
  useEffect(() => {
    setSel((i) => Math.min(Math.max(0, i), Math.max(0, filtered.length - 1)));
  }, [filtered.length]);

  if (!open) return null;

  const choose = (e?: PaletteEntry) => {
    const item = e ?? filtered[sel];
    if (item) item.run();
    setOpen(false);
  };

  return (
    <div className="cmdk-scrim" onClick={() => setOpen(false)}>
      <div
        className="cmdk"
        role="dialog"
        aria-modal="true"
        aria-label="Поиск по панели"
        onClick={(e) => e.stopPropagation()}
      >
        <input
          ref={inputRef}
          className="cmdk-input"
          placeholder="Поиск по панели — вкладки, разделы…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setSel((i) => Math.min(i + 1, filtered.length - 1));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setSel((i) => Math.max(i - 1, 0));
            } else if (e.key === "Enter") {
              e.preventDefault();
              choose();
            } else if (e.key === "Escape") {
              e.preventDefault();
              setOpen(false);
            }
          }}
        />
        <div className="cmdk-list">
          {filtered.length === 0 && <div className="cmdk-empty">Ничего не найдено</div>}
          {filtered.map((e, i) => (
            <button
              key={e.id}
              type="button"
              className={`cmdk-item${i === sel ? " sel" : ""}`}
              onMouseEnter={() => setSel(i)}
              onClick={() => choose(e)}
            >
              <span className="cmdk-item-label">{e.label}</span>
              {e.hint && <span className="cmdk-item-hint">{e.hint}</span>}
            </button>
          ))}
        </div>
        <div className="cmdk-foot">
          <span><kbd>↑</kbd><kbd>↓</kbd> выбор</span>
          <span><kbd>↵</kbd> открыть</span>
          <span><kbd>esc</kbd> закрыть</span>
        </div>
      </div>
    </div>
  );
}
