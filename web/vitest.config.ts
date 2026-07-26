import { defineConfig } from "vitest/config";

// Отдельно от vite.config.ts, чтобы прод-сборка не тянула тест-раннер. Тесты —
// чистые функции, DOM не нужен: окружение node.
export default defineConfig({
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
