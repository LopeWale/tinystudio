import { visualTruthSourcePlugin } from 'visual-truth/vite'
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { fileURLToPath, URL } from "node:url";
export default defineConfig({
  base: "/ui/",
  envDir: false,
  plugins: [visualTruthSourcePlugin(), react(), tailwindcss()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  build: { outDir: "../tinystudio/ui/dist", emptyOutDir: true, manifest: true },
  server: {
    proxy: { "/api": "http://127.0.0.1:8000", "/v1": "http://127.0.0.1:8000" },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./tests/setup.ts"],
    restoreMocks: true,
  },
});
