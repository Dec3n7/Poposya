import { useEffect, useState } from "react";

// Простой хэш-роутинг без внешних либ: `#/<guildId>/<tab>`. Держит выбранный
// сервер и активную вкладку в URL — переживает F5 и даёт ссылки прямо на раздел.
// Единый источник правды — window.location.hash; App и GuildView читают отсюда.

export interface Route {
  guildId: string | null;
  tab: string | null;
}

function parseHash(): Route {
  const raw = window.location.hash.replace(/^#\/?/, "");
  const [guildId, tab] = raw.split("/");
  return { guildId: guildId || null, tab: tab || null };
}

export function useHashRoute() {
  const [route, setRoute] = useState<Route>(parseHash);

  useEffect(() => {
    const onChange = () => setRoute(parseHash());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);

  // смена сервера сбрасывает вкладку (новый сервер — с «Обзора»)
  const setGuildId = (id: string | null) => {
    window.location.hash = id ? `/${id}` : "";
  };
  // смена вкладки сохраняет текущий сервер
  const setTab = (tab: string) => {
    const { guildId } = parseHash();
    window.location.hash = guildId ? `/${guildId}/${tab}` : "/";
  };

  return { ...route, setGuildId, setTab };
}
