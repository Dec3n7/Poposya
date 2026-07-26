import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";

// Единая точка обратной связи для действий: сохранил настройку, замутил, забанил
// — тост подтверждает результат. Ошибки действий тоже сюда (ошибки ЗАГРУЗКИ
// вкладки по-прежнему рисуются инлайн через .error-banner — там нечего скрывать
// за исчезающим тостом).

type ToastKind = "success" | "error" | "info";
type ToastItem = { id: number; kind: ToastKind; message: string };

type ToastApi = {
  success: (message: string) => void;
  error: (message: string) => void;
  info: (message: string) => void;
};

const ToastCtx = createContext<ToastApi | null>(null);

export function useToast(): ToastApi {
  const ctx = useContext(ToastCtx);
  if (!ctx) throw new Error("useToast вызван вне <ToastProvider>");
  return ctx;
}

// сколько тост висит до авто-скрытия; ошибка живёт дольше — её успевают прочесть
const DURATION: Record<ToastKind, number> = { success: 3500, info: 4000, error: 6000 };

const ICON: Record<ToastKind, ReactNode> = {
  success: (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2.2">
      <path d="M20 6 9 17l-5-5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  ),
  error: (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2.2">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 8v5M12 16.5v.01" strokeLinecap="round" />
    </svg>
  ),
  info: (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2.2">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v5M12 7.5v.01" strokeLinecap="round" />
    </svg>
  ),
};

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const seq = useRef(0);
  const timers = useRef<Map<number, number>>(new Map());

  const dismiss = useCallback((id: number) => {
    setItems((cur) => cur.filter((t) => t.id !== id));
    const handle = timers.current.get(id);
    if (handle !== undefined) {
      clearTimeout(handle);
      timers.current.delete(id);
    }
  }, []);

  const push = useCallback(
    (kind: ToastKind, message: string) => {
      const id = ++seq.current;
      setItems((cur) => [...cur, { id, kind, message }]);
      const handle = window.setTimeout(() => dismiss(id), DURATION[kind]);
      timers.current.set(id, handle);
    },
    [dismiss],
  );

  // снять все таймеры при размонтировании провайдера
  useEffect(() => {
    const map = timers.current;
    return () => map.forEach((h) => clearTimeout(h));
  }, []);

  const value = useMemo<ToastApi>(
    () => ({
      success: (m) => push("success", m),
      error: (m) => push("error", m),
      info: (m) => push("info", m),
    }),
    [push],
  );

  return (
    <ToastCtx.Provider value={value}>
      {children}
      {createPortal(
        <div className="toast-stack" role="region" aria-label="Уведомления" aria-live="polite">
          {items.map((t) => (
            <div key={t.id} className={`toast toast-${t.kind}`} role="status">
              <span className="toast-ic" aria-hidden>
                {ICON[t.kind]}
              </span>
              <span className="toast-msg">{t.message}</span>
              <button className="toast-x" onClick={() => dismiss(t.id)} aria-label="Закрыть">
                ×
              </button>
            </div>
          ))}
        </div>,
        document.body,
      )}
    </ToastCtx.Provider>
  );
}
