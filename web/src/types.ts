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
