import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Фронт и API — на localhost, но разные порты. В dev ходим напрямую на API
// (VITE_API_URL), CORS на бэкенде уже разрешает этот origin. В прод VITE_API_URL
// пустой -> тот же origin (API раздаёт статику).
export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
});
