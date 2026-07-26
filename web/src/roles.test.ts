import { afterEach, describe, expect, it, vi } from "vitest";

import { activityMeta, discordColor, roleColor, silentDays } from "./roles";

describe("discordColor", () => {
  it("нулевой цвет -> null (без цвета)", () => {
    expect(discordColor(0)).toBeNull();
  });
  it("int -> #rrggbb с ведущими нулями", () => {
    expect(discordColor(0x5865f2)).toBe("#5865f2");
    expect(discordColor(0xff)).toBe("#0000ff");
  });
});

describe("roleColor", () => {
  it("нет роли -> нейтральный токен", () => {
    expect(roleColor(null)).toBe("var(--fg-faint)");
  });
  it("ступень 0 (отрицательный индекс) -> туман", () => {
    expect(roleColor(-1)).toBe("#5b6470");
  });
  it("индекс за палитрой клэмпится к последнему цвету", () => {
    expect(roleColor(0)).toBe("#8b93a7");
    expect(roleColor(99)).toBe("#b57be0");
  });
});

describe("silentDays / activityMeta", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("null/битая дата -> null", () => {
    expect(silentDays(null)).toBeNull();
    expect(silentDays("не-дата")).toBeNull();
    expect(activityMeta(null)).toBeNull();
  });

  it("считает целые дни тишины от текущего времени", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-11T00:00:00Z"));
    expect(silentDays("2026-01-01T00:00:00Z")).toBe(10);
    expect(activityMeta("2026-01-11T00:00:00Z")).toMatchObject({ tone: "fresh" });
    expect(activityMeta("2026-01-01T00:00:00Z")).toMatchObject({ tone: "cool" });
  });
});
