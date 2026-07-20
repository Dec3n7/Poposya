import { useEffect, useState } from "react";

import { ApiError, api } from "./api";
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
  const route = useHashRoute();
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
  if (status === "anon") return <Login />;
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
