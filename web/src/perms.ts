// Подсказки для кнопок, гашёных по правам Discord. Текст совпадает с ярлыками
// гвардов на бэке (src/api/dependencies.py) — чтобы тултип и реальная причина
// 403 не расходились. Гвард на бэке всё равно стережёт границу: это лишь UX.
import type { GuildPerms } from "./types";

export const GATE = {
  ban: "Нужно право Discord: Банить участников",
  kick: "Нужно право Discord: Выгонять участников",
  moderate: "Нужно право Discord: Тайм-аут участникам",
  manageRoles: "Нужно право Discord: Управление ролями",
  anyMod: "Нужно право модерации (бан, кик или тайм-аут)",
} as const;

// достаточно любого мод-права — так же, как require_any_moderator на бэке
// (апелляции: одобрение снимает разные наказания)
export function anyModerator(p: GuildPerms): boolean {
  return p.can_ban || p.can_kick || p.can_moderate;
}
