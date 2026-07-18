import type { Me, SettingField } from "./types";

// dev: VITE_API_URL=http://localhost:8081; прод: пусто -> тот же origin
const BASE: string = import.meta.env.VITE_API_URL ?? "";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    credentials: "include", // слать сессию-куку кросс-origin
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let message = res.statusText;
    try {
      const body = await res.json();
      if (body?.detail) message = String(body.detail);
    } catch {
      /* тело не JSON — оставляем statusText */
    }
    throw new ApiError(res.status, message);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  loginUrl: (): string => `${BASE}/api/auth/login`,
  me: (): Promise<Me> => req<Me>("/api/auth/me"),
  logout: (): Promise<void> => req<void>("/api/auth/logout", { method: "POST" }),
  settings: (guildId: string): Promise<SettingField[]> =>
    req<SettingField[]>(`/api/guilds/${guildId}/settings`),
  setSetting: (
    guildId: string,
    key: string,
    value: boolean | number | string,
  ): Promise<SettingField> =>
    req<SettingField>(`/api/guilds/${guildId}/settings/${key}`, {
      method: "PUT",
      body: JSON.stringify({ value }),
    }),
  resetSetting: (guildId: string, key: string): Promise<SettingField> =>
    req<SettingField>(`/api/guilds/${guildId}/settings/${key}`, { method: "DELETE" }),
};
