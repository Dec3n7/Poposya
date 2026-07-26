import type { ReactNode } from "react";

// Единый вид «здесь пока пусто»: мягкая иконка + заголовок + опциональная
// подсказка/действие. Заменяет разрозненные однострочники `.muted`, у которых
// не было ни акцента, ни объяснения, что делать дальше.

const DEFAULT_ICON = (
  <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" strokeWidth="1.8">
    <path d="M3 7.5 12 3l9 4.5-9 4.5-9-4.5Z" strokeLinejoin="round" />
    <path d="M3 12l9 4.5L21 12M3 16.5 12 21l9-4.5" strokeLinejoin="round" />
  </svg>
);

export function EmptyState({
  icon,
  title,
  hint,
  action,
  compact = false,
}: {
  icon?: ReactNode;
  title: string;
  hint?: string;
  action?: ReactNode;
  // compact — для узких мест (внутри строки/малой карточки): меньше воздуха
  compact?: boolean;
}) {
  return (
    <div className={`empty-state${compact ? " compact" : ""}`}>
      <div className="empty-ic" aria-hidden>
        {icon ?? DEFAULT_ICON}
      </div>
      <div className="empty-title">{title}</div>
      {hint && <div className="empty-hint">{hint}</div>}
      {action && <div className="empty-action">{action}</div>}
    </div>
  );
}
