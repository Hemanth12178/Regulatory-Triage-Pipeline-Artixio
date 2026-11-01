import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5151,
    strictPort: true,
    // The frontend talks to /api and Vite forwards it, so there is no
    // API base URL to configure and no CORS surprise in development.
    proxy: {
      "/api": {
        // Override with VITE_API_TARGET if you run the API elsewhere.
        target: process.env.VITE_API_TARGET ?? "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
