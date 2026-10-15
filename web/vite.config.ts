import { defineConfig, loadEnv } from "vite";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const target = env.API_URL || "http://127.0.0.1:8000";

  return {
    plugins: [tailwindcss()],
    server: {
      port: Number(env.PORT || 5173),
      proxy: {
        "/api": { target, changeOrigin: true },
        "/provide": { target, changeOrigin: true },
        "/health": { target, changeOrigin: true },
      },
    },
  };
});
