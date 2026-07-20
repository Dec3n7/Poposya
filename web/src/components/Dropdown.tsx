import { Fragment, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
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
 * выпадающий список соседними модулями.
 *
 * Клавиатура (нативный select это умел, руками возвращаем): фокус остаётся
 * на триггере, подсветку ведём через aria-activedescendant. ↑/↓ — курсор по
 * опциям, Home/End — края, Enter/Space — выбор, Esc — закрыть, печать букв —
 * typeahead-прыжок к первой опции с этим началом. */
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
  const [active, setActive] = useState(0); // индекс подсвеченной опции (клавиатура)
  const uid = useId(); // стабильный префикс id опций для aria-activedescendant
  const typeahead = useRef<{ buf: string; t: number }>({ buf: "", t: 0 });
  const selectedIndex = Math.max(0, options.findIndex((o) => o.value === value));
  const current = options.find((o) => o.value === value) ?? options[0];

  // при открытии подсветку ставим на текущее значение
  useLayoutEffect(() => {
    if (open) setActive(selectedIndex);
    // selectedIndex намеренно вне зависимостей — берём срез на момент открытия
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

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

  // фейд у нижнего края меню: скроллбар скрыт, поэтому подсказываем
  // «ниже есть ещё» градиентом; у конца списка он гаснет (класс more)
  const updateFade = () => {
    const m = menuRef.current;
    if (!m) return;
    m.classList.toggle("more", m.scrollHeight - m.scrollTop - m.clientHeight > 4);
  };
  useLayoutEffect(updateFade, [open, pos, options]);

  // держим подсвеченную опцию в поле зрения при навигации с клавиатуры
  useLayoutEffect(() => {
    if (!open) return;
    menuRef.current
      ?.querySelector<HTMLElement>(`#${CSS.escape(`${uid}-${active}`)}`)
      ?.scrollIntoView({ block: "nearest" });
  }, [active, open, uid]);

  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      const t = e.target as Node;
      if (ref.current?.contains(t) || menuRef.current?.contains(t)) return;
      setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  function commit(i: number) {
    const o = options[i];
    if (o) onChange(o.value);
    setOpen(false);
  }

  function onKeyDown(e: React.KeyboardEvent) {
    // typeahead: печать буквы прыгает к первой опции с этим началом
    if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey && /\S/.test(e.key)) {
      const ta = typeahead.current;
      window.clearTimeout(ta.t);
      ta.buf += e.key.toLowerCase();
      ta.t = window.setTimeout(() => (ta.buf = ""), 600);
      const hit = options.findIndex((o) => o.label.toLowerCase().startsWith(ta.buf));
      if (hit >= 0) {
        if (!open) setOpen(true);
        setActive(hit);
      }
      e.preventDefault();
      return;
    }

    switch (e.key) {
      case "ArrowDown":
        e.preventDefault();
        if (!open) setOpen(true);
        else setActive((i) => Math.min(options.length - 1, i + 1));
        break;
      case "ArrowUp":
        e.preventDefault();
        if (!open) setOpen(true);
        else setActive((i) => Math.max(0, i - 1));
        break;
      case "Home":
        if (open) {
          e.preventDefault();
          setActive(0);
        }
        break;
      case "End":
        if (open) {
          e.preventDefault();
          setActive(options.length - 1);
        }
        break;
      case "Enter":
      case " ":
        e.preventDefault();
        if (open) commit(active);
        else setOpen(true);
        break;
      case "Escape":
        if (open) {
          e.preventDefault();
          setOpen(false);
        }
        break;
      case "Tab":
        if (open) setOpen(false); // уводим фокус — закрываем
        break;
    }
  }

  const menu =
    open && pos
      ? createPortal(
          <ul
            ref={menuRef}
            className="dd-menu"
            role="listbox"
            style={{ position: "fixed", top: pos.top, left: pos.left, minWidth: pos.width }}
            onScroll={updateFade}
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
                    id={`${uid}-${i}`}
                    role="option"
                    aria-selected={o.value === value}
                    className={`dd-item${o.value === value ? " active" : ""}${
                      i === active ? " hl" : ""
                    }`}
                    onMouseEnter={() => setActive(i)}
                    onClick={() => commit(i)}
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
        aria-activedescendant={open ? `${uid}-${active}` : undefined}
        aria-label={ariaLabel}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={onKeyDown}
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
