import type {
  ActivityStats,
  Appeal,
  AuditEntry,
  Ban,
  BotProfile,
  Channel,
  Cinema,
  CommandResult,
  ComplexSettings,
  CrossBanList,
  CrossBanUser,
  FindsOverview,
  GuildModule,
  GuildPersona,
  GuildSummary,
  GuildWarn,
  Me,
  MemberRoles,
  ModCase,
  MovieDetail,
  NowPlaying,
  Overview,
  PermCatalog,
  PersonaDetail,
  PersonaIdentity,
  PersonaPhrase,
  PersonaSummary,
  PhraseChange,
  PersonDetail,
  PersonListItem,
  PlaylistDetail,
  PlaylistItem,
  RoleInput,
  RolePreset,
  RolesView,
  SavedRoleTemplate,
  SettingField,
  Trends,
  WardenControlResult,
  WardenSnapshot,
  Warn,
} from "./types";

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

// Централизованный выход по протухшей/отозванной сессии: ЛЮБОЙ 401 (не только
// стартовый /me) уводит на экран логина, а не сыплет разрозненными ошибками по
// компонентам. Особенно важно после серверного killswitch сессий и TTL 24ч —
// сессия может стать недействительной прямо во время работы. App регистрирует
// обработчик; api.ts его дёргает.
let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(fn: (() => void) | null): void {
  onUnauthorized = fn;
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    credentials: "include", // слать сессию-куку кросс-origin
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    if (res.status === 401) onUnauthorized?.();
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
  // оператор отзывает все веб-сессии пользователя (разжалованный админ / утёкший токен)
  revokeSessions: (userId: string): Promise<void> =>
    req<void>(`/api/auth/revoke/${userId}`, { method: "POST" }),
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
  complexSettings: (guildId: string): Promise<ComplexSettings> =>
    req<ComplexSettings>(`/api/guilds/${guildId}/settings/complex`),
  modules: (guildId: string): Promise<GuildModule[]> =>
    req<GuildModule[]>(`/api/guilds/${guildId}/settings/modules`),
  batch: (guildId: string, values: Record<string, unknown>): Promise<void> =>
    req<void>(`/api/guilds/${guildId}/settings/batch`, {
      method: "PUT",
      body: JSON.stringify({ values }),
    }),
  channels: (guildId: string): Promise<Channel[]> =>
    req<Channel[]>(`/api/guilds/${guildId}/channels`),
  botProfile: (guildId: string): Promise<BotProfile> =>
    req<BotProfile>(`/api/guilds/${guildId}/bot-profile`),
  setBotProfile: (
    guildId: string,
    body: BotProfile,
  ): Promise<{ profile: BotProfile; command: CommandResult }> =>
    req<{ profile: BotProfile; command: CommandResult }>(`/api/guilds/${guildId}/bot-profile`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  overview: (guildId: string): Promise<Overview> =>
    req<Overview>(`/api/guilds/${guildId}/overview`),
  summary: (guildId: string): Promise<GuildSummary> =>
    req<GuildSummary>(`/api/guilds/${guildId}/summary`),
  trends: (guildId: string, days = 30): Promise<Trends> =>
    req<Trends>(`/api/guilds/${guildId}/overview/trends?days=${days}`),
  activity: (guildId: string, days = 30): Promise<ActivityStats> =>
    req<ActivityStats>(`/api/guilds/${guildId}/activity?days=${days}`),
  cinema: (guildId: string): Promise<Cinema> => req<Cinema>(`/api/guilds/${guildId}/cinema`),
  movieRatings: (guildId: string, entryId: number): Promise<MovieDetail> =>
    req<MovieDetail>(`/api/guilds/${guildId}/cinema/movies/${entryId}`),
  removeMovie: (guildId: string, entryId: number): Promise<{ status: string }> =>
    req<{ status: string }>(`/api/guilds/${guildId}/cinema/movies/${entryId}`, {
      method: "DELETE",
    }),
  people: (guildId: string): Promise<PersonListItem[]> =>
    req<PersonListItem[]>(`/api/guilds/${guildId}/people`),
  person: (guildId: string, userId: string): Promise<PersonDetail> =>
    req<PersonDetail>(`/api/guilds/${guildId}/people/${userId}`),
  setPersonPoints: (guildId: string, userId: string, value: number): Promise<PersonDetail> =>
    req<PersonDetail>(`/api/guilds/${guildId}/people/${userId}/points`, {
      method: "PUT",
      body: JSON.stringify({ value }),
    }),
  toggleFreeze: (guildId: string, userId: string): Promise<{ frozen: boolean }> =>
    req<{ frozen: boolean }>(`/api/guilds/${guildId}/people/${userId}/freeze`, { method: "POST" }),
  bans: (guildId: string): Promise<Ban[]> =>
    req<Ban[]>(`/api/guilds/${guildId}/moderation/bans`),
  guildWarns: (guildId: string): Promise<GuildWarn[]> =>
    req<GuildWarn[]>(`/api/guilds/${guildId}/moderation/warns`),
  warns: (guildId: string, userId: string): Promise<Warn[]> =>
    req<Warn[]>(`/api/guilds/${guildId}/moderation/warns/${userId}`),
  clearWarns: (guildId: string, userId: string): Promise<{ cleared: number }> =>
    req<{ cleared: number }>(`/api/guilds/${guildId}/moderation/warns/${userId}`, {
      method: "DELETE",
    }),
  crossban: (guildId: string): Promise<CrossBanList> =>
    req<CrossBanList>(`/api/guilds/${guildId}/moderation/crossban`),
  crossbanUser: (guildId: string, userId: string): Promise<CrossBanUser> =>
    req<CrossBanUser>(`/api/guilds/${guildId}/moderation/crossban/${userId}`),
  history: (guildId: string, userId: string): Promise<ModCase[]> =>
    req<ModCase[]>(`/api/guilds/${guildId}/moderation/history/${userId}`),
  playlists: (guildId: string): Promise<PlaylistItem[]> =>
    req<PlaylistItem[]>(`/api/guilds/${guildId}/music/playlists`),
  playlist: (guildId: string, name: string): Promise<PlaylistDetail> =>
    req<PlaylistDetail>(`/api/guilds/${guildId}/music/playlists/${encodeURIComponent(name)}`),
  nowPlaying: (guildId: string): Promise<NowPlaying | null> =>
    req<NowPlaying | null>(`/api/guilds/${guildId}/music/now`),
  deletePlaylist: (guildId: string, name: string): Promise<{ status: string }> =>
    req<{ status: string }>(`/api/guilds/${guildId}/music/playlists/${encodeURIComponent(name)}`, {
      method: "DELETE",
    }),
  finds: (guildId: string): Promise<FindsOverview> =>
    req<FindsOverview>(`/api/guilds/${guildId}/finds/overview`),
  roles: (guildId: string): Promise<RolesView> => req<RolesView>(`/api/guilds/${guildId}/roles`),
  memberRoles: (guildId: string, userId: string): Promise<MemberRoles> =>
    req<MemberRoles>(`/api/guilds/${guildId}/roles/members/${userId}`),
  assignRole: (guildId: string, userId: string, roleId: string): Promise<CommandResult> =>
    req<CommandResult>(`/api/guilds/${guildId}/roles/members/${userId}`, {
      method: "POST",
      body: JSON.stringify({ role_id: roleId }),
    }),
  unassignRole: (guildId: string, userId: string, roleId: string): Promise<CommandResult> =>
    req<CommandResult>(`/api/guilds/${guildId}/roles/members/${userId}/${roleId}`, {
      method: "DELETE",
    }),
  createRole: (guildId: string, body: RoleInput): Promise<CommandResult> =>
    req<CommandResult>(`/api/guilds/${guildId}/roles`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  editRole: (guildId: string, roleId: string, body: Partial<RoleInput>): Promise<CommandResult> =>
    req<CommandResult>(`/api/guilds/${guildId}/roles/${roleId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deleteRole: (guildId: string, roleId: string): Promise<CommandResult> =>
    req<CommandResult>(`/api/guilds/${guildId}/roles/${roleId}`, { method: "DELETE" }),
  reorderRoles: (guildId: string, order: string[]): Promise<CommandResult> =>
    req<CommandResult>(`/api/guilds/${guildId}/roles/order`, {
      method: "PUT",
      body: JSON.stringify({ order }),
    }),
  rolePermissions: (guildId: string): Promise<PermCatalog> =>
    req<PermCatalog>(`/api/guilds/${guildId}/roles/permissions`),
  setRolePermissions: (
    guildId: string,
    roleId: string,
    permissions: string,
  ): Promise<CommandResult> =>
    req<CommandResult>(`/api/guilds/${guildId}/roles/${roleId}/permissions`, {
      method: "PUT",
      body: JSON.stringify({ permissions }),
    }),
  bulkRole: (
    guildId: string,
    roleId: string,
    op: "assign" | "unassign",
  ): Promise<CommandResult> =>
    req<CommandResult>(`/api/guilds/${guildId}/roles/${roleId}/bulk`, {
      method: "POST",
      body: JSON.stringify({ op }),
    }),
  importRoles: (guildId: string, roles: RoleInput[]): Promise<CommandResult> =>
    req<CommandResult>(`/api/guilds/${guildId}/roles/import`, {
      method: "POST",
      body: JSON.stringify({ roles }),
    }),
  autorole: (guildId: string): Promise<{ role_ids: string[] }> =>
    req<{ role_ids: string[] }>(`/api/guilds/${guildId}/roles/autorole`),
  setAutorole: (guildId: string, roleIds: string[]): Promise<{ role_ids: string[] }> =>
    req<{ role_ids: string[] }>(`/api/guilds/${guildId}/roles/autorole`, {
      method: "PUT",
      body: JSON.stringify({ role_ids: roleIds }),
    }),
  interestRoles: (
    guildId: string,
  ): Promise<{ interests: string[]; mapping: Record<string, string> }> =>
    req<{ interests: string[]; mapping: Record<string, string> }>(
      `/api/guilds/${guildId}/roles/interest-roles`,
    ),
  setInterestRoles: (
    guildId: string,
    mapping: Record<string, string>,
  ): Promise<{ mapping: Record<string, string> }> =>
    req<{ mapping: Record<string, string> }>(`/api/guilds/${guildId}/roles/interest-roles`, {
      method: "PUT",
      body: JSON.stringify({ mapping }),
    }),
  roleTemplates: (guildId: string): Promise<{ templates: SavedRoleTemplate[] }> =>
    req<{ templates: SavedRoleTemplate[] }>(`/api/guilds/${guildId}/roles/templates`),
  saveRoleTemplate: (guildId: string, name: string): Promise<SavedRoleTemplate> =>
    req<SavedRoleTemplate>(`/api/guilds/${guildId}/roles/templates`, {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  applyRoleTemplate: (guildId: string, id: number): Promise<CommandResult> =>
    req<CommandResult>(`/api/guilds/${guildId}/roles/templates/${id}/apply`, { method: "POST" }),
  deleteRoleTemplate: (guildId: string, id: number): Promise<{ deleted: boolean }> =>
    req<{ deleted: boolean }>(`/api/guilds/${guildId}/roles/templates/${id}`, { method: "DELETE" }),
  appeals: (guildId: string): Promise<Appeal[]> =>
    req<Appeal[]>(`/api/guilds/${guildId}/appeals`),
  approveAppeal: (guildId: string, id: number): Promise<CommandResult> =>
    req<CommandResult>(`/api/guilds/${guildId}/appeals/${id}/approve`, { method: "POST" }),
  rejectAppeal: (guildId: string, id: number): Promise<CommandResult> =>
    req<CommandResult>(`/api/guilds/${guildId}/appeals/${id}/reject`, { method: "POST" }),
  rolePresets: (guildId: string): Promise<{ presets: RolePreset[] }> =>
    req<{ presets: RolePreset[] }>(`/api/guilds/${guildId}/roles/presets`),
  applyRolePreset: (guildId: string, key: string): Promise<CommandResult> =>
    req<CommandResult>(`/api/guilds/${guildId}/roles/presets/${key}/apply`, { method: "POST" }),
  audit: (guildId: string, limit = 100): Promise<AuditEntry[]> =>
    req<AuditEntry[]>(`/api/guilds/${guildId}/audit?limit=${limit}`),
  ban: (guildId: string, userId: string, minutes: number, reason: string): Promise<CommandResult> =>
    req<CommandResult>(`/api/guilds/${guildId}/moderation/ban`, {
      method: "POST",
      body: JSON.stringify({ user_id: userId, minutes, reason }),
    }),
  unban: (guildId: string, userId: string): Promise<CommandResult> =>
    req<CommandResult>(`/api/guilds/${guildId}/moderation/unban`, {
      method: "POST",
      body: JSON.stringify({ user_id: userId }),
    }),
  mute: (guildId: string, userId: string, minutes: number, reason: string): Promise<CommandResult> =>
    req<CommandResult>(`/api/guilds/${guildId}/moderation/mute`, {
      method: "POST",
      body: JSON.stringify({ user_id: userId, minutes, reason }),
    }),
  unmute: (guildId: string, userId: string): Promise<CommandResult> =>
    req<CommandResult>(`/api/guilds/${guildId}/moderation/unmute`, {
      method: "POST",
      body: JSON.stringify({ user_id: userId }),
    }),
  kick: (guildId: string, userId: string, reason: string): Promise<CommandResult> =>
    req<CommandResult>(`/api/guilds/${guildId}/moderation/kick`, {
      method: "POST",
      body: JSON.stringify({ user_id: userId, reason }),
    }),
  banPermanent: (
    guildId: string,
    userId: string,
    reason: string,
    deleteDays = 0,
  ): Promise<CommandResult> =>
    req<CommandResult>(`/api/guilds/${guildId}/moderation/ban_permanent`, {
      method: "POST",
      body: JSON.stringify({ user_id: userId, reason, delete_days: deleteDays }),
    }),
  musicControl: (
    guildId: string,
    action: "pause" | "resume" | "skip" | "stop" | "previous" | "shuffle" | "repeat",
  ): Promise<CommandResult> =>
    req<CommandResult>(`/api/guilds/${guildId}/music/control`, {
      method: "POST",
      body: JSON.stringify({ action }),
    }),
  musicVolume: (guildId: string, volume: number): Promise<CommandResult> =>
    req<CommandResult>(`/api/guilds/${guildId}/music/volume`, {
      method: "POST",
      body: JSON.stringify({ volume }),
    }),
  musicSeek: (guildId: string, position: number): Promise<CommandResult> =>
    req<CommandResult>(`/api/guilds/${guildId}/music/seek`, {
      method: "POST",
      body: JSON.stringify({ position }),
    }),
  musicRemove: (guildId: string, position: number): Promise<CommandResult> =>
    req<CommandResult>(`/api/guilds/${guildId}/music/queue/${position}`, {
      method: "DELETE",
    }),

  // --- персоны (только оператор) ---
  personas: (): Promise<PersonaSummary[]> => req<PersonaSummary[]>("/api/personas"),
  persona: (id: number): Promise<PersonaDetail> => req<PersonaDetail>(`/api/personas/${id}`),
  createPersona: (name: string, duplicateOf?: number): Promise<PersonaDetail> =>
    req<PersonaDetail>("/api/personas", {
      method: "POST",
      body: JSON.stringify({ name, duplicate_of: duplicateOf ?? null }),
    }),
  duplicatePersona: (id: number, name: string): Promise<PersonaDetail> =>
    req<PersonaDetail>(`/api/personas/${id}/duplicate`, {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  renamePersona: (id: number, name: string): Promise<PersonaDetail> =>
    req<PersonaDetail>(`/api/personas/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ name }),
    }),
  deletePersona: (id: number): Promise<void> =>
    req<void>(`/api/personas/${id}`, { method: "DELETE" }),
  setPersonaPrompt: (id: number, prompt: string): Promise<PersonaDetail> =>
    req<PersonaDetail>(`/api/personas/${id}/prompt`, {
      method: "PUT",
      body: JSON.stringify({ prompt }),
    }),
  setPersonaChimePrompt: (id: number, prompt: string): Promise<PersonaDetail> =>
    req<PersonaDetail>(`/api/personas/${id}/chime_prompt`, {
      method: "PUT",
      body: JSON.stringify({ prompt }),
    }),
  exportPersona: (id: number): Promise<unknown> => req<unknown>(`/api/personas/${id}/export`),
  personaPhrases: (id: number): Promise<PersonaPhrase[]> =>
    req<PersonaPhrase[]>(`/api/personas/${id}/phrases`),
  setPersonaPhrase: (
    id: number,
    key: string,
    value: unknown,
    mode: string,
  ): Promise<PersonaPhrase> =>
    req<PersonaPhrase>(`/api/personas/${id}/phrases/${encodeURIComponent(key)}`, {
      method: "PUT",
      body: JSON.stringify({ value, mode }),
    }),
  resetPersonaPhrase: (id: number, key: string): Promise<PersonaPhrase> =>
    req<PersonaPhrase>(`/api/personas/${id}/phrases/${encodeURIComponent(key)}`, {
      method: "DELETE",
    }),
  replacePersonaPhrases: (
    id: number,
    find: string,
    replace: string,
    dryRun: boolean,
  ): Promise<PhraseChange[]> =>
    req<PhraseChange[]>(`/api/personas/${id}/phrases/replace`, {
      method: "POST",
      body: JSON.stringify({ find, replace, dry_run: dryRun }),
    }),
  personaIdentity: (id: number): Promise<PersonaIdentity> =>
    req<PersonaIdentity>(`/api/personas/${id}/identity`),
  setPersonaIdentity: (
    id: number,
    body: Pick<PersonaIdentity, "display_name" | "signature" | "accent_color" | "presence">,
  ): Promise<PersonaIdentity> =>
    req<PersonaIdentity>(`/api/personas/${id}/identity`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  importPersona: (data: unknown): Promise<PersonaDetail> =>
    req<PersonaDetail>("/api/personas/import", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  guildPersona: (guildId: string): Promise<GuildPersona> =>
    req<GuildPersona>(`/api/guilds/${guildId}/persona`),
  assignPersona: (guildId: string, personaId: number): Promise<GuildPersona> =>
    req<GuildPersona>(`/api/guilds/${guildId}/persona`, {
      method: "PUT",
      body: JSON.stringify({ persona_id: personaId }),
    }),
  wardenStatus: (): Promise<WardenSnapshot> => req<WardenSnapshot>("/api/warden/status"),
  wardenEnabled: (): Promise<{ enabled: boolean }> =>
    req<{ enabled: boolean }>("/api/warden/enabled"),
  wardenPause: (minutes: number): Promise<WardenControlResult> =>
    req<WardenControlResult>("/api/warden/pause", {
      method: "POST",
      body: JSON.stringify({ minutes }),
    }),
  wardenResume: (): Promise<WardenControlResult> =>
    req<WardenControlResult>("/api/warden/resume", { method: "POST" }),
};
