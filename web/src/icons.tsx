// Мелкие SVG-иконки для кнопок-контролов — в одном стиле с набором сайдбара
// (stroke 2, viewBox 24). Эмодзи в контенте (🖤, находки, ❄️) — не сюда:
// это идентичность Попоси; здесь только элементы управления.
const I = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 2,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  viewBox: "0 0 24 24",
  width: 14,
  height: 14,
  "aria-hidden": true,
} as const;

/** Крестик: закрыть/убрать. */
export function IconX() {
  return (
    <svg {...I}>
      <path d="M6 6l12 12M18 6L6 18" />
    </svg>
  );
}

/** Мусорка: удалить безвозвратно. */
export function IconTrash() {
  return (
    <svg {...I}>
      <path d="M4 7h16M10 4h4M7 7l1 13h8l1-13M10 11v6M14 11v6" />
    </svg>
  );
}

/** Карандаш: редактировать. */
export function IconPencil() {
  return (
    <svg {...I}>
      <path d="M4 20h4L18 10l-4-4L4 16v4zM13 7l4 4" />
    </svg>
  );
}

/** Плюс: создать/добавить. */
export function IconPlus() {
  return (
    <svg {...I}>
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}

/** Галочка: сохранить/подтвердить. */
export function IconCheck() {
  return (
    <svg {...I}>
      <path d="M5 13l4 4L19 7" />
    </svg>
  );
}

/** Шеврон вверх: поднять в порядке. */
export function IconChevronUp() {
  return (
    <svg {...I}>
      <path d="M6 15l6-6 6 6" />
    </svg>
  );
}

/** Шеврон вниз: опустить в порядке. */
export function IconChevronDown() {
  return (
    <svg {...I}>
      <path d="M6 9l6 6 6-6" />
    </svg>
  );
}

/** Щит: права роли. */
export function IconShield() {
  return (
    <svg {...I}>
      <path d="M12 3l7 3v5c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6l7-3z" />
    </svg>
  );
}

/** Ручка перетаскивания (grip): шесть точек. */
export function IconGrip() {
  return (
    <svg {...I} strokeWidth={0} fill="currentColor">
      <circle cx="9" cy="6" r="1.4" />
      <circle cx="15" cy="6" r="1.4" />
      <circle cx="9" cy="12" r="1.4" />
      <circle cx="15" cy="12" r="1.4" />
      <circle cx="9" cy="18" r="1.4" />
      <circle cx="15" cy="18" r="1.4" />
    </svg>
  );
}
