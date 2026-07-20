import { Fragment, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

export interface Option {
  value: string;
  label: string;
  group?: string; // необязательный заголовок-группа (как <optgroup>)
}

/** Стеклянный дропдаун в стиле панели — замена нативному <select>,
 * который нельзя застилизовать (список рисует ОС). Поддерживает группы:
 * опции с одинаковым `group` идут под общим заголовком.
 *
 * Меню рендерится в портал на <body> с position:fixed — иначе стеклянные
 * карточки (backdrop-filter создаёт контекст наложения) перекрывали бы
 * выпадающий список соседними модулями. */
export function Dropdown({
  value,
  options,
  onChange,
  ariaLabel,
  className,
}: {
  value: string;
  options: Option[];
  onChange: (v: string) => void;
  ariaLabel?: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null); // контейнер триггера
  const menuRef = useRef<HTMLUListElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number; width: number } | null>(null);
  const current = options.find((o) => o.value === value) ?? options[0];

  // позиционирование меню под триггером (fixed-координаты во вьюпорте) +
  // пересчёт на скролл/ресайз; capture-скролл ловит прокрутку любого предка
  useLayoutEffect(() => {
    if (!open) return;
    function place() {
      const el = ref.current;
      if (!el) return;
      const r = el.getBoundingClientRect();
      setPos({ top: r.bottom + 6, left: r.left, width: r.width });
    }
    place();
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => {
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      const t = e.target as Node;
      if (ref.current?.contains(t) || menuRef.current?.contains(t)) return;
      setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const menu =
    open && pos
      ? createPortal(
          <ul
            ref={menuRef}
            className="dd-menu"
            role="listbox"
            style={{ position: "fixed", top: pos.top, left: pos.left, minWidth: pos.width }}
          >
            {options.map((o, i) => {
              const header = o.group && o.group !== options[i - 1]?.group ? o.group : null;
              return (
                <Fragment key={o.value}>
                  {header && (
                    <li className="dd-group" role="presentation">
                      {header}
                    </li>
                  )}
                  <li
                    role="option"
                    aria-selected={o.value === value}
                    className={`dd-item${o.value === value ? " active" : ""}`}
                    onClick={() => {
                      onChange(o.value);
                      setOpen(false);
                    }}
                  >
                    {o.label}
                  </li>
                </Fragment>
              );
            })}
          </ul>,
          document.body,
        )
      : null;

  return (
    <div className={`dd${className ? " " + className : ""}`} ref={ref}>
      <button
        type="button"
        className="dd-trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={ariaLabel}
        onClick={() => setOpen((o) => !o)}
      >
        <span className="dd-value">{current?.label}</span>
        <span className={`dd-caret${open ? " open" : ""}`} aria-hidden="true">
          ▾
        </span>
      </button>
      {menu}
    </div>
  );
}
