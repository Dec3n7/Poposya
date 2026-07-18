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
  is_exclusive: boolean;
}

export interface Overview {
  leaderboard: LeaderEntry[];
  counts: { watchlist: number; watched: number; playlists: number };
}

export interface WatchlistItem {
  title: string;
  year: number | null;
  up: number;
  down: number;
}

export interface WatchedItem {
  title: string;
  year: number | null;
  avg_score: number | null;
  ratings_count: number;
  poposya_score: number | null;
  poposya_review: string;
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
  is_exclusive: boolean;
  frozen: boolean;
  has_profile: boolean;
  last_dialog_at: string | null;
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

export interface PersonDetail {
  user_id: string;
  username: string | null;
  avatar: string | null;
  points: number;
  level: number;
  role: string | null;
  is_exclusive: boolean;
  frozen: boolean;
  next_threshold: number | null;
  deep_dialogs: number;
  birthday_day: number | null;
  birthday_month: number | null;
  last_dialog_at: string | null;
}
