import { resolve } from "node:path";

import { defineConfig } from "vite";

export default defineConfig({
  base: "./",
  build: {
    emptyOutDir: true,
    outDir: "dist",
    rollupOptions: {
      input: {
        main: resolve(import.meta.dirname, "main.html"),
        splash: resolve(import.meta.dirname, "index.html"),
      },
    },
  },
  clearScreen: false,
});
