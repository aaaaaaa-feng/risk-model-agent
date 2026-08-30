import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      /* 路径含中文,pathname 是百分号编码,必须解码 */
      "@": decodeURIComponent(new URL("./src", import.meta.url).pathname),
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    emptyOutDir: true,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.indexOf("node_modules") < 0) return undefined;
          if (/[\\/]node_modules[\\/](react|react-dom|scheduler)[\\/]/.test(id))
            return "vendor-react";
          if (/react-markdown|remark-|micromark|mdast|hast|unified/.test(id))
            return "vendor-markdown";
          if (id.indexOf("lucide-react") >= 0) return "vendor-icons";
          if (/radix-ui|class-variance-authority|tailwind-merge|clsx/.test(id)) return "vendor-ui";
          return "vendor";
        },
      },
    },
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8765",
    },
  },
});
