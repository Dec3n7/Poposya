import type { CSSProperties } from "react";

import { roleColor } from "../roles";

// Плитка роли-статуса, окрашенная по индексу тира. index приходит из API
// (role_index); имя — из настроек ролей сервера. Цвет прокидываем через CSS-
// переменную --role, чтобы точку и текст красить одним значением.
export function RoleChip({
  name,
  index,
}: {
  name: string | null;
  index: number | null;
}) {
  if (!name) return null;
  return (
    <span className="role-chip" style={{ "--role": roleColor(index) } as CSSProperties}>
      <span className="role-dot" />
      {name}
    </span>
  );
}
