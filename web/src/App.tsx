import { useEffect, useRef, useState } from "react";

import { ApiError, api, setUnauthorizedHandler } from "./api";
import { GuildPicker } from "./components/GuildPicker";
import { GuildView } from "./components/GuildView";
import { Login } from "./components/Login";
import { useHashRoute } from "./route";
import type { Me } from "./types";

type Status = "loading" | "anon" | "ready" | "error";

export default function App() {
  const [status, setStatus] = useState<Status>("loading");
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState("");
  const [sessionExpired, setSessionExpired] = useState(false);
  const route = useHashRoute();

  // Любой 401 из api.ts (протухшая/отозванная сессия) -> на экран логина.
  // statusRef, чтобы отличить обрыв активной сессии (показать «сессия истекла»)
  // от обычного анонимного старта.
  const statusRef = useRef(status);
  statusRef.current = status;
  useEffect(() => {
    setUnauthorizedHandler(() => {
      if (statusRef.current === "ready") setSessionExpired(true);
      setStatus("anon");
      setMe(null);
    });
    return () => setUnauthorizedHandler(null);
  }, []);
  // выбранный сервер выводится из URL (переживает F5, шарится ссылкой)
  const guild = me?.guilds.find((g) => g.id === route.guildId) ?? null;

  useEffect(() => {
    api
      .me()
      .then((data) => {
        setMe(data);
        setStatus("ready");
      })
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) setStatus("anon");
        else {
          setError(e instanceof Error ? e.message : "Не удалось связаться с сервером");
          setStatus("error");
        }
      });
  }, []);

  // авто-высота: любой textarea в панели растёт под свой текст (без внутреннего скролла).
  // Глобально, через делегирование + наблюдатель — не нужно трогать каждый компонент.
  useEffect(() => {
    const fit = (el: HTMLTextAreaElement) => {
      el.style.height = "auto";
      el.style.height = `${el.scrollHeight}px`;
    };
    const fitAll = () =>
      document.querySelectorAll("textarea").forEach((el) => fit(el as HTMLTextAreaElement));
    const onInput = (e: Event) => {
      const t = e.target as HTMLElement | null;
      if (t?.tagName === "TEXTAREA") fit(t as HTMLTextAreaElement);
    };
    let raf = 0;
    const schedule = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(fitAll);
    };
    document.addEventListener("input", onInput, true);
    const mo = new MutationObserver(schedule);
    mo.observe(document.body, { childList: true, subtree: true });
    window.addEventListener("resize", schedule);
    schedule();
    return () => {
      document.removeEventListener("input", onInput, true);
      mo.disconnect();
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", schedule);
    };
  }, []);

  async function logout() {
    try {
      await api.logout();
    } finally {
      setMe(null);
      route.setGuildId(null);
      setStatus("anon");
    }
  }

  if (status === "loading") {
    return (
      <div className="center">
        <div className="spinner" aria-label="Загрузка" />
      </div>
    );
  }
  if (status === "anon") return <Login sessionExpired={sessionExpired} />;
  if (status === "error") {
    return (
      <div className="center">
        <div className="card pad" style={{ maxWidth: 440 }}>
          <div className="error-banner">{error}</div>
          <button className="btn primary" style={{ marginTop: 16 }} onClick={() => location.reload()}>
            Обновить
          </button>
        </div>
      </div>
    );
  }

  // сервер выбран — полноэкранная оболочка с сайдбаром
  if (guild && me) {
    return (
      <GuildView
        guild={guild}
        me={me}
        tab={route.tab}
        onTab={route.setTab}
        onBack={() => route.setGuildId(null)}
        onLogout={logout}
      />
    );
  }

  // выбор сервера — простой экран с лёгкой шапкой
  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">
          <span className="mark">✂️👁🖤</span> Попося · панель
        </span>
        <span className="spacer" />
        {me && <span className="who">{me.username}</span>}
        <button className="btn ghost small" onClick={logout}>
          Выйти
        </button>
      </header>

      <main className="container">
        <GuildPicker guilds={me?.guilds ?? []} onPick={(g) => route.setGuildId(g.id)} />
      </main>
    </div>
  );
}
