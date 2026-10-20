/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// В режиме разработки запросы /api проксируются на FastAPI (python -m arm_web serve).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: process.env.ARM_API ?? "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
  test: {
    environment: "node",
  },
});
