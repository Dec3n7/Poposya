/**
 * Liquid Glass интерактив: под курсором грань стекла подсвечивается со стороны
 * указателя (реагирующий кант). Делегирование на document — работает и с блоками,
 * которые React монтирует/размонтирует, без пере-подписки на каждый ре-рендер.
 */

const GLASS = ".card, .rail, .guild-chip";

export function initGlass(): void {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

  document.addEventListener(
    "pointermove",
    (e) => {
      const target = e.target as Element | null;
      const el = target?.closest?.(GLASS) as HTMLElement | null;
      if (!el) return;
      const r = el.getBoundingClientRect();
      el.style.setProperty("--mx", ((e.clientX - r.left) / r.width) * 100 + "%");
      el.style.setProperty("--my", ((e.clientY - r.top) / r.height) * 100 + "%");
    },
    { passive: true },
  );
}
