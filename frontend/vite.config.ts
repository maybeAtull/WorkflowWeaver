import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Port is pinned so it always matches the origin the backend's CORS rules allow.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true },
  // Tests run in a simulated browser (jsdom); setup.ts adds the DOM matchers.
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
  },
});
