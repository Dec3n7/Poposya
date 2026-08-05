import { useEffect, useRef } from "react";

// Перечитывает данные вкладки, когда пользователь возвращается к окну панели:
// переключение вкладки браузера (visibilitychange) или альт-таб из Discord
// обратно (focus). Панель грузит большинство вкладок один раз при монтировании,
// поэтому в фоне список мог устареть — бот кого-то забанил, срок бана истёк.
// `load` держим в ref: подписываемся один раз, но зовём всегда свежий.
// Короткий анти-дребезг — visibilitychange и focus часто приходят парой.
export function useRefetchOnFocus(load: () => void): void {
  const ref = useRef(load);
  ref.current = load;
  useEffect(() => {
    let last = 0;
    const refetch = () => {
      if (document.visibilityState !== "visible") return;
      const now = Date.now();
      if (now - last < 1000) return;
      last = now;
      ref.current();
    };
    document.addEventListener("visibilitychange", refetch);
    window.addEventListener("focus", refetch);
    return () => {
      document.removeEventListener("visibilitychange", refetch);
      window.removeEventListener("focus", refetch);
    };
  }, []);
}
