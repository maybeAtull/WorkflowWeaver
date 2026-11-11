import type { Config } from "tailwindcss";

// Tailwind v3 (pinned): scans these files to decide which utility classes to generate.
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: { extend: {} },
  plugins: [],
} satisfies Config;
