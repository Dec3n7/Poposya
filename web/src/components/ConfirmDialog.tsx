import { useEffect, useId, useRef } from "react";
import { createPortal } from "react-dom";

/** Подтверждение необратимого действия — замена нативному window.confirm,
 * который нельзя застилизовать и который ломает фокус/тему панели.
 *
 * Рендерится в портал на <body>: стеклянные карточки (backdrop-filter создаёт
 * containing block для position:fixed) иначе «ловят» оверлей внутрь себя —
 * та же причина, по которой в портал вынесено меню дропдауна.
 *
 * Доступность: role="alertdialog", фокус уводится на кнопку подтверждения и
 * возвращается на прежний элемент при закрытии, Esc — отмена, Tab заперт между
 * двумя кнопками. Открывается только когда `open`. */
export function ConfirmDialog({
  open,
  title,
  body,
  confirmLabel = "Удалить",
  cancelLabel = "Отмена",
  danger = true,
  busy = false,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  body?: React.ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const confirmRef = useRef<HTMLButtonElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const restoreRef = useRef<HTMLElement | null>(null);
  const uid = useId();

  // фокус на кнопку подтверждения при открытии; возврат — при закрытии
  useEffect(() => {
    if (!open) return;
    restoreRef.current = document.activeElement as HTMLElement | null;
    confirmRef.current?.focus();
    return () => restoreRef.current?.focus?.();
  }, [open]);

  if (!open) return null;

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Escape") {
      e.preventDefault();
      if (!busy) onCancel();
    } else if (e.key === "Tab") {
      // ловушка фокуса: две кнопки, циклим между ними
      e.preventDefault();
      const next = document.activeElement === confirmRef.current ? cancelRef.current : confirmRef.current;
      next?.focus();
    }
  }

  return createPortal(
    <div
      className="modal-overlay"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !busy) onCancel();
      }}
    >
      <div
        className="modal-box"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={`${uid}-t`}
        aria-describedby={body ? `${uid}-b` : undefined}
        onKeyDown={onKeyDown}
      >
        <h3 className="modal-title" id={`${uid}-t`}>
          {title}
        </h3>
        {body && (
          <div className="modal-body" id={`${uid}-b`}>
            {body}
          </div>
        )}
        <div className="modal-actions">
          <button
            ref={cancelRef}
            className="btn ghost small"
            onClick={onCancel}
            disabled={busy}
          >
            {cancelLabel}
          </button>
          <button
            ref={confirmRef}
            className={`btn small ${danger ? "danger" : "primary"}`}
            onClick={onConfirm}
            disabled={busy}
          >
            {busy ? "…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
