/**
 * Liquid Glass интерактив: под курсором грань стекла подсвечивается со стороны
 * указателя (реагирующий кант), а крупные блоки с классом `tilt` едва заметно
 * наклоняются. Делегирование на document — работает и с блоками, которые React
 * монтирует/размонтирует, без пере-подписки на каждый ре-рендер.
 */

const GLASS = ".card, .stat-card, .rail, .guild-chip";
const TILT = 0.5; // максимальный угол наклона, градусы

export function initGlass(): void {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

  document.addEventListener(
    "pointermove",
    (e) => {
      const target = e.target as Element | null;
      const el = target?.closest?.(GLASS) as HTMLElement | null;
      if (!el) return;
      const r = el.getBoundingClientRect();
      const px = (e.clientX - r.left) / r.width;
      const py = (e.clientY - r.top) / r.height;
      el.style.setProperty("--mx", px * 100 + "%");
      el.style.setProperty("--my", py * 100 + "%");
      if (el.classList.contains("tilt")) {
        el.style.setProperty("--rx", (px - 0.5) * 2 * TILT + "deg");
        el.style.setProperty("--ry", -(py - 0.5) * 2 * TILT + "deg");
      }
    },
    { passive: true },
  );

  document.addEventListener("pointerout", (e) => {
    const target = e.target as Element | null;
    const el = target?.closest?.(".tilt") as HTMLElement | null;
    if (el && !el.contains(e.relatedTarget as Node)) {
      el.style.setProperty("--rx", "0deg");
      el.style.setProperty("--ry", "0deg");
    }
  });
}
