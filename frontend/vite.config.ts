import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

// Во время разработки запросы /api идут на сервер приложения: `uv run agen serve`,
// адрес — в переменной AGEN_API (по умолчанию http://127.0.0.1:8765, `agen serve --port 8765`).
const api = process.env.AGEN_API ?? "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [react()],
  base: "./",
  resolve: {
    // monaco-editor 0.57 отдаёт файлы без «esm/vs/» в пути, а воркер monaco-yaml просит старый путь
    alias: [{ find: /^monaco-editor\/esm\/vs\/(.*)$/, replacement: fileURLToPath(new URL("./node_modules/monaco-editor/esm/vs/$1", import.meta.url)) }],
  },
  server: { proxy: { "/api": { target: api } } },
  build: {
    // сервер отдаёт собранное окно по своему адресу (packages/server, папка ui)
    outDir: "../packages/server/src/autogenerator/server/ui",
    emptyOutDir: true,
    chunkSizeWarningLimit: 4000,
  },
});
