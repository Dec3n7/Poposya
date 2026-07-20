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
