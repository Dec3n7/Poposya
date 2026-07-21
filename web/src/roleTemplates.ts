// Пресеты для управления ролями: палитра «цветов Попоси» для быстрой окраски и
// встроенные наборы ролей. Наборы применяются тем же путём, что импорт файла —
// бот создаёт недостающие роли по имени (права не переносятся).

import type { RoleInput } from "./types";

// Палитра для свотчей в редакторе роли. Тёплая гамма панели: фиолеты, бирюза,
// золото, роза, плюс нейтральные слейты.
export const POPOSYA_COLORS: string[] = [
  "#8b5cf6", // фирменный фиолет
  "#9b8cf0", // лаванда
  "#b57be0", // аметист
  "#6ab0d8", // небо
  "#57c4b6", // бирюза
  "#57c46a", // зелень
  "#e6b24d", // золото
  "#ff8a5c", // коралл
  "#f0899f", // роза
  "#e05c6a", // алый
  "#99aab5", // слейт
  "#4a4f57", // графит
];

// hex "#rrggbb" -> int для RoleInput.color
function hx(hex: string): number {
  return parseInt(hex.slice(1), 16);
}

function role(name: string, color: string | null, opts?: Partial<RoleInput>): RoleInput {
  return {
    name,
    color: color ? hx(color) : null,
    hoist: opts?.hoist ?? false,
    mentionable: opts?.mentionable ?? false,
  };
}

export interface RoleTemplate {
  key: string;
  name: string;
  description: string;
  roles: RoleInput[];
}

// Наборы намеренно без прав — их выставляют вручную щитом после создания.
// «hoist» там, где роль-статус логично видеть отдельной группой.
export const ROLE_TEMPLATES: RoleTemplate[] = [
  {
    key: "activity_ranks",
    name: "Ранги активности",
    description: "Пять ступеней «свой человек» — от новичка до легенды сервера.",
    roles: [
      role("Новичок", "#99aab5"),
      role("Свой", "#6ab0d8"),
      role("Активный", "#57c4b6", { hoist: true }),
      role("Ветеран", "#e6b24d", { hoist: true }),
      role("Легенда", "#b57be0", { hoist: true }),
    ],
  },
  {
    key: "server_team",
    name: "Команда сервера",
    description: "Роли для персонала. Права выставь щитом после создания.",
    roles: [
      role("Модератор", "#e05c6a", { hoist: true }),
      role("Хелпер", "#57c46a", { hoist: true }),
      role("Медиа", "#f0899f", { mentionable: true }),
    ],
  },
  {
    key: "color_nicks",
    name: "Цветные ники",
    description: "Косметические роли-цвета, которые участники носят для оттенка ника.",
    roles: [
      role("Фиолет", "#8b5cf6"),
      role("Небо", "#6ab0d8"),
      role("Бирюза", "#57c4b6"),
      role("Золото", "#e6b24d"),
      role("Коралл", "#ff8a5c"),
      role("Роза", "#f0899f"),
    ],
  },
];
