import { type ReactNode, useEffect, useRef, useState } from "react";

import { api } from "../api";
import type { Guild, GuildSummary, Me } from "../types";
import { Audit } from "./Audit";
import { Cinema } from "./Cinema";
import { Dashboard } from "./Dashboard";
import { ErrorBoundary } from "./ErrorBoundary";
import { Finds } from "./Finds";
import { GuildSettings } from "./GuildSettings";
import { Moderation } from "./Moderation";
import { Modules } from "./Modules";
import { Music } from "./Music";
import { People } from "./People";
import { Persona } from "./Persona";
import { Roles } from "./Roles";
import { Warden } from "./Warden";

type Tab =
  | "overview"
  | "people"
  | "roles"
  | "cinema"
  | "music"
  | "finds"
  | "moderation"
  | "audit"
  | "modules"
  | "settings"
  | "persona"
  | "warden";

const I = { fill: "none", stroke: "currentColor", strokeWidth: 2, viewBox: "0 0 24 24" } as const;

const TABS: { id: Tab; label: string; icon: ReactNode }[] = [
  {
    id: "overview",
    label: "Обзор",
    icon: (
      <svg {...I}><rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" /></svg>
    ),
  },
  {
    id: "people",
    label: "Люди",
    icon: (
      <svg {...I}><circle cx="9" cy="8" r="3.2" /><path d="M3.5 19c.6-3 2.9-4.5 5.5-4.5s4.9 1.5 5.5 4.5" /><path d="M16 5.5a3 3 0 0 1 0 5.4M17.5 14.4c2 .6 3.4 2 3.8 4.1" /></svg>
    ),
  },
  {
    id: "roles",
    label: "Роли",
    icon: (
      <svg {...I}><path d="M20.6 13.4l-7.2 7.2a2 2 0 0 1-2.8 0l-6.2-6.2a2 2 0 0 1-.6-1.4V6a2 2 0 0 1 2-2h6.8a2 2 0 0 1 1.4.6l6.6 6.6a2 2 0 0 1 0 2.2Z" /><circle cx="8.5" cy="8.5" r="1.4" /></svg>
    ),
  },
  {
    id: "cinema",
    label: "Киноклуб",
    icon: (
      <svg {...I}><rect x="3" y="4" width="18" height="16" rx="2.5" /><path d="M3 9h18M8 4v16M16 4v16" /></svg>
    ),
  },
  {
    id: "music",
    label: "Музыка",
    icon: (
      <svg {...I}><path d="M9 18V6l10-2v12" /><circle cx="6" cy="18" r="2.6" /><circle cx="16" cy="16" r="2.6" /></svg>
    ),
  },
  {
    id: "finds",
    label: "Находки",
    icon: (
      <svg {...I}><path d="M20 14.5A8 8 0 1 1 9.5 4a6.3 6.3 0 0 0 10.5 10.5Z" /></svg>
    ),
  },
  {
    id: "moderation",
    label: "Модерация",
    icon: (
      <svg {...I}><path d="M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6z" /></svg>
    ),
  },
  {
    id: "audit",
    label: "Журнал",
    icon: (
      <svg {...I}><path d="M5 4h11l3 3v13H5z" /><path d="M8 9h8M8 13h8M8 17h5" /></svg>
    ),
  },
  {
    id: "modules",
    label: "Модули",
    icon: (
      <svg {...I}><rect x="3" y="3" width="8" height="8" rx="1.5" /><rect x="13" y="3" width="8" height="8" rx="1.5" /><rect x="3" y="13" width="8" height="8" rx="1.5" /><path d="M17 14v6M14 17h6" /></svg>
    ),
  },
  {
    id: "settings",
    label: "Настройки",
    icon: (
      <svg {...I}><path d="M4 7h10M18 7h2M4 12h2M10 12h10M4 17h7M15 17h5" /><circle cx="16" cy="7" r="2" /><circle cx="8" cy="12" r="2" /><circle cx="13" cy="17" r="2" /></svg>
    ),
  },
];

// вкладка только для оператора бота (управление персонами); назначение —
// per-guild, поэтому живёт в рельсе сервера, но видна лишь оператору
const OPERATOR_TABS: { id: Tab; label: string; icon: ReactNode }[] = [
  {
    id: "persona",
    label: "Персона",
    icon: (
      <svg {...I}><path d="M4 5c0 6 3 9 8 9s8-3 8-9c-3 1.5-5 2-8 2s-5-.5-8-2Z" /><path d="M9 9h.01M15 9h.01M9.5 12.5c.8.7 4.2.7 5 0" /></svg>
    ),
  },
  {
    // Состояние инфраструктуры общее для всех серверов, но рельса у панели
    // одна — держим здесь, показывая только оператору.
    id: "warden",
    label: "WARDEN",
    icon: (
      <svg {...I}><circle cx="12" cy="12" r="8.2" /><circle cx="12" cy="12" r="3" /></svg>
    ),
  },
];

// бейдж-счётчик на пункте рельса для вкладки (null = без бейджа)
function tabBadge(id: Tab, s: GuildSummary | null): number | null {
  if (!s) return null;
  if (id === "moderation") return s.bans || null;
  if (id === "people") return s.frozen || null;
  return null;
}

export function GuildView({
  guild,
  me,
  tab: tabProp,
  onTab,
  onBack,
  onLogout,
}: {
  guild: Guild;
  me: Me;
  tab: string | null;
  onTab: (tab: string) => void;
  onBack: () => void;
  onLogout: () => void;
}) {
  // персона-вкладка видна только оператору бота (доступ к роутам всё равно
  // стережёт require_operator на бэке; здесь — только видимость)
  const tabs = me.is_operator ? [...TABS, ...OPERATOR_TABS] : TABS;
  const tab: Tab = tabs.find((t) => t.id === tabProp)?.id ?? "overview";
  const active = tabs.find((t) => t.id === tab)!;

  const [summary, setSummary] = useState<GuildSummary | null>(null);
  useEffect(() => {
    setSummary(null);
    api
      .summary(guild.id)
      .then(setSummary)
      .catch(() => setSummary(null)); // бейджи не критичны
  }, [guild.id]);

  // мобильная навигация: рельс превращается в шторку (drawer), открываемую бургером
  const [navOpen, setNavOpen] = useState(false);
  const railRef = useRef<HTMLElement>(null);

  // переход по вкладке закрывает шторку; бренд ведёт на «Обзор»
  function go(id: Tab) {
    onTab(id);
    setNavOpen(false);
  }

  // пока шторка открыта: фокус внутрь, ловушка Tab, Esc закрывает, блок скролла фона
  useEffect(() => {
    if (!navOpen) return;
    const rail = railRef.current;
    if (!rail) return;
    const focusables = () =>
      Array.from(
        rail.querySelectorAll<HTMLElement>(
          'button, a[href], [tabindex]:not([tabindex="-1"])',
        ),
      );
    focusables()[0]?.focus();
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setNavOpen(false);
        return;
      }
      if (e.key !== "Tab") return;
      const items = focusables();
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [navOpen]);

  // если экран вырос до десктопа с открытой шторкой — закрыть её: иначе рельс
  // снова становится сайдбаром, а блок скролла фона (body.overflow:hidden)
  // остался бы висеть, и страница «залипала» бы без прокрутки
  useEffect(() => {
    const mq = window.matchMedia("(min-width: 861px)");
    const onChange = (e: MediaQueryListEvent) => {
      if (e.matches) setNavOpen(false);
    };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  // красная точка на бургере, если есть что-то тревожное (баны) — дублирует бейдж в шторке
  const hasAlert = (summary?.bans ?? 0) > 0;

  return (
    <div className={`shell${navOpen ? " nav-open" : ""}`}>
      <header className="m-topbar">
        <button
          className="m-burger"
          onClick={() => setNavOpen(true)}
          aria-label="Открыть меню"
          aria-expanded={navOpen}
        >
          <svg {...I} width="22" height="22" strokeLinecap="round">
            <path d="M4 7h16M4 12h16M4 17h16" />
          </svg>
          {hasAlert && <span className="m-burger-dot" aria-hidden />}
        </button>
        <div className="m-heading">
          <div className="m-eyebrow">{active.label}</div>
          <div className="m-guild">{guild.name}</div>
        </div>
        {me.avatar ? (
          <img className="m-av" src={me.avatar} alt="" />
        ) : (
          <span className="m-av fallback">{(me.username ?? "?").slice(0, 1).toUpperCase()}</span>
        )}
      </header>

      <button
        className="scrim"
        aria-label="Закрыть меню"
        tabIndex={navOpen ? 0 : -1}
        onClick={() => setNavOpen(false)}
      />

      <aside className="rail" ref={railRef} aria-label="Меню сервера">
        <button className="rail-brand" onClick={() => go("overview")} title="На обзор">
          <span className="rail-mark">🖤</span>
          <div>
            <div className="rail-name">Попося</div>
            <div className="rail-sub">панель сервера</div>
          </div>
        </button>

        <div className="rail-label">Меню</div>
        <nav className="rail-nav">
          {tabs.map((t) => {
            const badge = tabBadge(t.id, summary);
            return (
              <button
                key={t.id}
                className={`rail-item${tab === t.id ? " active" : ""}`}
                onClick={() => go(t.id)}
              >
                {t.icon}
                {t.label}
                {badge != null && (
                  <span className={`rail-badge${t.id === "moderation" ? " alert" : ""}`}>
                    {badge}
                  </span>
                )}
              </button>
            );
          })}
        </nav>

        <div className="rail-user">
          {me.avatar ? (
            <img className="rail-avatar" src={me.avatar} alt="" />
          ) : (
            <span className="rail-avatar fallback">
              {(me.username ?? "?").slice(0, 1).toUpperCase()}
            </span>
          )}
          <span className="rail-username">{me.username}</span>
          <div className="rail-user-actions">
            <button className="btn ghost small" onClick={onBack}>
              ← Серверы
            </button>
            <button className="btn ghost small" onClick={onLogout}>
              Выйти
            </button>
          </div>
        </div>
      </aside>

      <main className="stage">
        <div className="stage-top">
          <button className="guild-chip" onClick={onBack} title="Сменить сервер">
            {guild.icon ? (
              <img className="guild-chip-ic" src={guild.icon} alt="" />
            ) : (
              <span className="guild-chip-ic fallback">✂️</span>
            )}
            <span className="guild-chip-name">{guild.name}</span>
            <svg className="guild-chip-swap" width="15" height="15" {...I}>
              <path d="M7 4L3 8l4 4M3 8h13M17 20l4-4-4-4M21 16H8" />
            </svg>
          </button>
        </div>

        <div className="stage-body">
          <div className="stage-head">
            <div className="eyebrow">{active.label}</div>
            <h1 className="stage-title">{guild.name}</h1>
          </div>

          {/* key={tab}: барьер сбрасывается при переходе — упавшая вкладка
              чинится сама, как только уходишь на другую и возвращаешься */}
          <ErrorBoundary key={tab}>
            {tab === "overview" && <Dashboard guild={guild} />}
            {tab === "people" && <People guild={guild} />}
            {tab === "roles" && <Roles guild={guild} />}
            {tab === "cinema" && <Cinema guild={guild} />}
            {tab === "music" && <Music guild={guild} />}
            {tab === "finds" && <Finds guild={guild} />}
            {tab === "moderation" && <Moderation guild={guild} />}
            {tab === "audit" && <Audit guild={guild} />}
            {tab === "modules" && <Modules guild={guild} />}
            {tab === "settings" && <GuildSettings guild={guild} />}
            {tab === "persona" && <Persona guild={guild} />}
            {tab === "warden" && <Warden />}
          </ErrorBoundary>
        </div>
      </main>
    </div>
  );
}
