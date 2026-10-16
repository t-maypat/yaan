import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The API runs on :8000 (`uv run --project backend uvicorn app.main:app --reload`).
// In dev, Vite forwards the API paths to it, so the browser sees one origin and needs no CORS.
const API = process.env.LORE_API ?? "http://127.0.0.1:8000";
const apiPaths = ["/ask", "/artifact", "/products", "/query", "/health"];

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: Object.fromEntries(apiPaths.map((p) => [p, { target: API, changeOrigin: true }])),
  },
});
