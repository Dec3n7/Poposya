import type { CSSProperties } from "react";

// Плейсхолдеры загрузки вместо голого «Загрузка…»: контент не «прыгает», когда
// данные приходят — каркас занимает то же место. При prefers-reduced-motion
// мерцание гаснет (см. styles.css), блок остаётся статичным.

export function Skeleton({
  w,
  h = 14,
  r = 8,
  className = "",
  style,
}: {
  w?: number | string;
  h?: number | string;
  r?: number | string;
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <span
      className={`skeleton ${className}`.trim()}
      style={{ width: w, height: h, borderRadius: r, ...style }}
      aria-hidden
    />
  );
}

// Несколько строк текста; последняя короче — как настоящий абзац.
export function SkeletonText({ lines = 3 }: { lines?: number }) {
  return (
    <div className="skeleton-text" aria-hidden>
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton key={i} h={12} w={i === lines - 1 ? "55%" : "100%"} />
      ))}
    </div>
  );
}

// Список строк «аватар + две строки» — под лидерборды, людей, баны, аудит.
export function SkeletonRows({ rows = 5, avatar = true }: { rows?: number; avatar?: boolean }) {
  return (
    <div aria-hidden aria-busy="true">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="skeleton-row">
          {avatar && <Skeleton className="skeleton-av" w={36} h={36} r="50%" />}
          <div className="skeleton-lines">
            <Skeleton h={12} w="45%" />
            <Skeleton h={10} w="70%" />
          </div>
        </div>
      ))}
    </div>
  );
}

// Сетка карточек-заглушек — под дашборд/статистику.
export function SkeletonCards({ count = 4, height = 96 }: { count?: number; height?: number }) {
  return (
    <div className="skeleton-cards" aria-hidden aria-busy="true">
      {Array.from({ length: count }).map((_, i) => (
        <Skeleton key={i} h={height} r={16} />
      ))}
    </div>
  );
}
