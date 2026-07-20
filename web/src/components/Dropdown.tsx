import { Fragment, useEffect, useRef, useState } from "react";

export interface Option {
  value: string;
  label: string;
  group?: string; // необязательный заголовок-группа (как <optgroup>)
}

/** Стеклянный дропдаун в стиле панели — замена нативному <select>,
 * который нельзя застилизовать (список рисует ОС). Поддерживает группы:
 * опции с одинаковым `group` идут под общим заголовком. */
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
  const ref = useRef<HTMLDivElement>(null);
  const current = options.find((o) => o.value === value) ?? options[0];

  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
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
      {open && (
        <ul className="dd-menu" role="listbox">
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
        </ul>
      )}
    </div>
  );
}
