import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Port is pinned so it always matches the origin the backend's CORS rules allow.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true },
});
