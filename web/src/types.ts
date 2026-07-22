export interface Guild {
  id: string;
  name: string;
  icon: string | null;
}

export interface Me {
  user_id: string;
  username: string;
  avatar: string | null;
  guilds: Guild[]; // серверы, где можно управлять
  is_operator: boolean; // оператор бота: доступ к вкладке «Персона»
}

// --- персоны (только оператор) ---

export interface PersonaSummary {
  id: number;
  name: string;
  is_default: boolean;
  assigned_count: number; // на скольких серверах активна
}

export interface PersonaDetail {
  id: number;
  name: string;
  is_default: boolean;
  prompt: string; // пусто = используется встроенный дефолт
  chime_prompt: string;
  default_prompt: string; // встроенный промпт (что применяется при пустом поле)
  default_chime_prompt: string;
  assigned_count: number;
}

export interface GuildPersona {
  guild_id: string;
  persona_id: number; // что реально применяется (назначенная или дефолт)
}

export interface PersonaPhrase {
  key: string;
  label: string;
  category: string;
  kind: "str" | "template" | "list" | "dict";
  default: unknown;
  value: unknown | null; // null = override нет, действует дефолт
  mode: string;
  is_override: boolean;
  placeholders: string[];
  allowed_modes: string[];
}

export interface PhraseChange {
  key: string;
  before: unknown;
  after: unknown;
}

export interface PersonaIdentity {
  display_name: string;
  signature: string;
  accent_color: number; // 0..0xFFFFFF
  presence: string[]; // строки Discord-статуса; пусто = встроенный канон
  default_display_name: string;
  default_signature: string;
  default_accent_color: number;
}

export type SettingKind = "bool" | "channel" | "float" | "int";

export interface SettingField {
  key: string;
  label: string;
  kind: SettingKind;
  unit: string;
  min: number | null;
  max: number | null;
  default: boolean | number | string;
  value: boolean | number | string;
  is_override: boolean;
}

export interface ComplexField<T> {
  label: string;
  value: T;
  default: T;
  is_override: boolean;
}

export interface ComplexSettings {
  role_thresholds: ComplexField<number[]>;
  role_names: ComplexField<string[]>;
  rate_limits: ComplexField<Record<string, number>>;
}

export interface BotProfile {
  nick: string;
  avatar_url: string;
  banner_url: string;
  avatar_data: string; // загруженный+обрезанный аватар (data-URL); приоритетнее avatar_url
  banner_data: string; // загруженный+обрезанный баннер (data-URL); приоритетнее banner_url
}

export interface ModuleFlag {
  key: string;
  label: string;
  value: boolean;
  is_override: boolean;
}

export interface GuildModule {
  key: string;
  label: string;
  description: string;
  master: ModuleFlag;
  subs: ModuleFlag[];
}

export interface Channel {
  id: string;
  name: string;
  group: string;
  position: number;
}

export interface LeaderEntry {
  user_id: string;
  username: string | null;
  avatar: string | null;
  points: number;
  role: string | null;
  role_index: number | null;
  is_exclusive: boolean;
}

// доля роли-статуса в распределении сервера (для пончика на «Обзоре»)
export interface RoleSlice {
  index: number;
  name: string | null;
  count: number;
}

export interface VoiceEntry {
  user_id: string;
  username: string | null;
  avatar: string | null;
  hours: number;
}

export interface BirthdayEntry {
  user_id: string;
  username: string | null;
  avatar: string | null;
  month: number;
  day: number;
  in_days: number;
}

export interface Overview {
  leaderboard: LeaderEntry[];
  counts: { watchlist: number; watched: number; playlists: number };
  online: number | null; // приблизительный онлайн (в сети сейчас); null = Discord не отдал
  voice: VoiceEntry[];
  birthdays: BirthdayEntry[];
  distribution: RoleSlice[];
}

// серия суточных снапшотов: [день-ISO, значение][]; ключ — имя метрики
export type TrendPoint = [string, number];
export type Trends = Record<string, TrendPoint[]>;

// активность сервера: сообщения/день + два хитмапа день-недели×час (7×24, UTC):
// сообщения и минуты присутствия в войсе
export interface ActivityStats {
  daily: TrendPoint[];
  heatmap: number[][];
  voice: number[][];
}

export interface WatchlistItem {
  id: number;
  title: string;
  year: number | null;
  up: number;
  down: number;
}

export interface WatchedItem {
  id: number;
  title: string;
  year: number | null;
  avg_score: number | null;
  ratings_count: number;
  poposya_score: number | null;
  poposya_review: string;
}

export interface MovieRating {
  user_id: string;
  username: string | null;
  avatar: string | null;
  score: number | null;
  review: string | null;
}

export interface MovieDetail {
  ratings: MovieRating[];
}

export interface Cinema {
  watchlist: WatchlistItem[];
  watched: WatchedItem[];
}

export interface PersonListItem {
  user_id: string;
  username: string | null;
  avatar: string | null;
  points: number;
  role: string | null;
  role_index: number | null;
  is_exclusive: boolean;
  frozen: boolean;
  has_profile: boolean;
  last_dialog_at: string | null;
  next_threshold: number | null;
  role_progress: number; // доля к следующей роли, 0..1
}

// лёгкие счётчики для бейджей на сайдбаре
export interface GuildSummary {
  bans: number;
  warns_users: number;
  frozen: number;
}

export interface PlaylistItem {
  name: string;
  track_count: number;
  author_id: string;
  author_name: string | null;
}

export interface PlaylistTrack {
  title: string;
  uploader: string | null;
  duration: number | null;
  thumbnail: string | null;
}

export interface PlaylistDetail {
  name: string;
  tracks: PlaylistTrack[];
}

export interface NowTrack {
  title: string;
  url: string;
  duration: number | null;
  uploader: string | null;
  thumbnail: string | null;
  requested_by: string;
  requested_name: string | null;
}

export interface NowPlaying {
  current: NowTrack;
  queue: NowTrack[];
  position_seconds: number;
  position_at: string | null;
  is_paused: boolean;
  repeat: string;
  volume: number;
}

export interface ActiveFind {
  location: string;
  location_flavor: string;
  item_emoji: string;
  item_name: string;
  rarity: string;
  rarity_emoji: string;
  expires_at: string;
}

export interface Collector {
  user_id: string;
  username: string | null;
  avatar: string | null;
  total: number;
  gifted: number;
}

export interface FindsOverview {
  active: ActiveFind | null;
  collectors: Collector[];
}

export interface CommandResult {
  id: number;
  status: "pending" | "running" | "done" | "failed";
  result: string | null;
}

export interface Ban {
  user_id: string;
  username: string | null;
  avatar: string | null;
  moderator_id: string;
  moderator_name: string | null;
  reason: string;
  expires_at: string;
}

export interface Warn {
  id: number;
  reason: string;
  moderator_id: string;
  moderator_name: string | null;
  created_at: string;
}

export interface GuildWarn {
  user_id: string;
  username: string | null;
  avatar: string | null;
  count: number;
  last_at: string;
}

export interface AuditEntry {
  id: number;
  actor_id: string;
  actor_name: string | null;
  actor_avatar: string | null;
  action: string;
  target: string | null;
  target_name: string | null;
  details: string | null; // компактный JSON
  result: string | null;
  created_at: string;
}

export interface PersonDetail {
  user_id: string;
  username: string | null;
  avatar: string | null;
  points: number;
  level: number;
  role: string | null;
  role_index: number | null;
  is_exclusive: boolean;
  frozen: boolean;
  next_threshold: number | null;
  deep_dialogs: number;
  birthday_day: number | null;
  birthday_month: number | null;
  last_dialog_at: string | null;
}

// зеркало роли Discord (бот держит актуальным). permissions — строка: битовое
// поле не влезает в JS-number. editable считает бэкенд (ниже роли бота, не
// managed/@everyone) — фронт по нему рисует границу и блокировки.
export interface GuildRole {
  id: string;
  name: string;
  color: number; // 0 = без цвета
  hoist: boolean;
  mentionable: boolean;
  position: number;
  managed: boolean;
  permissions: string;
  is_default: boolean; // @everyone
  editable: boolean;
  holders: number | null; // носителей на сервере; null — не считали (карточка человека)
}

export interface RolesView {
  bot_top_position: number | null;
  bot_user_id: string | null;
  synced_at: string | null; // ISO; null — зеркало ещё не синхронизировано
  roles: GuildRole[]; // уже отсортированы по позиции (сверху вниз)
}

// роли одного участника: что носит (held) и что можно выдать (assignable —
// доступные боту и ещё не выданные)
export interface MemberRoles {
  held: GuildRole[];
  assignable: GuildRole[];
}

// поля для создания/правки роли. color: int 0..0xFFFFFF или null (без цвета).
// Права роли (permissions) редактируются отдельным экраном (см. PermCatalog).
export interface RoleInput {
  name: string;
  color: number | null;
  hoist: boolean;
  mentionable: boolean;
}

// сохранённый в панели именованный набор ролей сервера (косметика, без прав)
export interface SavedRoleTemplate {
  id: number;
  name: string;
  created_at: string; // ISO
  roles: RoleInput[];
}

// каталог прав Discord для редактора. bit/маски — строки: битовое поле не
// влезает в JS-number, работаем через BigInt.
export interface PermDef {
  name: string;
  bit: string;
  label: string;
  dangerous: boolean; // включение требует подтверждения
}

export interface PermCategory {
  key: string;
  label: string;
  perms: PermDef[];
}

export interface PermCatalog {
  categories: PermCategory[];
  bot_mask: string; // права, доступные самому боту (остальные тумблеры гасим)
  admin_bit: string; // бит Administrator — показываем как замок, не редактируем
}
