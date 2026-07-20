// Цвет и «активность» ролей-статусов — общие для «Людей» и «Обзора».
//
// Цвет роли выводится из индекса тира (role_index приходит из API, уже посчитан
// политикой очков). Детерминированно, без обращения к Discord: цвет отражает
// «ступень привязанности» — от прохладного слейта к тёплому золоту. Клэмп к
// последнему при выходе за палитру; null (нет роли) -> нейтральный.

const ROLE_COLORS = [
  "#8b93a7", // 0 — случайный прохожий (слейт)
  "#6ab0d8", // 1 — небо
  "#57c4b6", // 2 — бирюза
  "#9b8cf0", // 3 — фиолет (близко к акценту панели)
  "#f0899f", // 4 — роза
  "#e6b24d", // 5 — золото (особенный)
  "#ff8a5c", // 6 — коралл (запас на кастомные тиры)
  "#b57be0", // 7 — аметист (запас)
];

export function roleColor(index: number | null): string {
  if (index == null) return "var(--fg-faint)";
  return ROLE_COLORS[Math.min(Math.max(index, 0), ROLE_COLORS.length - 1)];
}

// давность последнего диалога в целых днях; null -> null (никогда/неизвестно)
export function silentDays(iso: string | null): number | null {
  if (!iso) return null;
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms)) return null;
  return Math.max(0, Math.floor(ms / 86_400_000));
}

export type ActivityTone = "fresh" | "warm" | "cool" | "cold";

// Метка активности по последнему диалогу: тон + короткий текст. Пороги под
// угасание очков (30 дней тишины из README) — «cold» = кандидат на decay.
export function activityMeta(
  iso: string | null,
): { tone: ActivityTone; label: string; title: string } | null {
  const d = silentDays(iso);
  if (d == null) return null;
  if (d === 0) return { tone: "fresh", label: "сегодня", title: "Общались сегодня" };
  if (d < 7) return { tone: "warm", label: `${d} дн`, title: `Молчит ${d} дн.` };
  if (d < 30) return { tone: "cool", label: `${d} дн`, title: `Молчит ${d} дн.` };
  return {
    tone: "cold",
    label: d < 60 ? `${d} дн` : "месяц+",
    title: `Молчит ${d} дн. — очки угасают`,
  };
}
