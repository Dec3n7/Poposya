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

/** Стрелка вниз в лоток: экспорт/скачать. */
export function IconDownload() {
  return (
    <svg {...I}>
      <path d="M12 4v10m0 0l-4-4m4 4l4-4M5 18h14" />
    </svg>
  );
}

/** Стрелка вверх из лотка: импорт/загрузить. */
export function IconUpload() {
  return (
    <svg {...I}>
      <path d="M12 20V10m0 0l-4 4m4-4l4 4M5 6h14" />
    </svg>
  );
}

/** Искорки: шаблоны/пресеты. */
export function IconSparkle() {
  return (
    <svg {...I}>
      <path d="M12 3l1.8 4.7L18.5 9.5 13.8 11.3 12 16l-1.8-4.7L5.5 9.5l4.7-1.8L12 3zM18 15l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8.8-2z" />
    </svg>
  );
}

/** Человек с плюсом: автовыдача роли при входе. */
export function IconUserPlus() {
  return (
    <svg {...I}>
      <path d="M13 20v-1a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v1M8 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM18 8v6M15 11h6" />
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
