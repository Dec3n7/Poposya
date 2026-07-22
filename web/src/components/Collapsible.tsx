import { type ReactNode, useEffect, useId, useState } from "react";

// Сворачиваемый блок (disclosure): кнопка-заголовок с aria-expanded управляет
// областью-телом. Открыто/свёрнуто запоминается в localStorage по storageKey.
// Разметка гибкая — заголовок (включая шеврон) передаёт вызывающий; анимация
// раскрытия и поворот шеврона — через класс `open` на внешнем элементе в CSS.
//
// Используется и главными разделами «Персоны» (outerClass="card acc"), и
// под-блоками категорий фраз (outerClass="subacc").
export function Collapsible({
  storageKey,
  defaultOpen = false,
  outerClass,
  headClass,
  bodyClass,
  header,
  children,
}: {
  storageKey?: string;
  defaultOpen?: boolean;
  outerClass: string; // напр. "card acc" или "subacc"
  headClass: string; // напр. "acc-head" или "subhead"
  bodyClass: string; // напр. "acc-body" или "subbody"
  header: ReactNode; // содержимое заголовка (иконка/тексты/сводка/шеврон)
  children: ReactNode;
}) {
  const [open, setOpen] = useState<boolean>(() => {
    if (storageKey) {
      try {
        const v = localStorage.getItem(storageKey);
        if (v != null) return v === "1";
      } catch {
        // localStorage недоступен (приватный режим) — берём дефолт
      }
    }
    return defaultOpen;
  });

  useEffect(() => {
    if (!storageKey) return;
    try {
      localStorage.setItem(storageKey, open ? "1" : "0");
    } catch {
      // запись недоступна — не критично, состояние живёт в памяти
    }
  }, [open, storageKey]);

  const rid = useId();

  return (
    <section className={`${outerClass}${open ? " open" : ""}`}>
      <button
        type="button"
        id={`${rid}-h`}
        className={headClass}
        aria-expanded={open}
        aria-controls={rid}
        onClick={() => setOpen((o) => !o)}
      >
        {header}
      </button>
      <div className={bodyClass} id={rid} role="region" aria-labelledby={`${rid}-h`}>
        <div className="acc-inner">{children}</div>
      </div>
    </section>
  );
}
