import type { Config } from "tailwindcss";

// Tailwind v3 (pinned): scans these files to decide which utility classes to generate.
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      // Inter Tight is loaded in index.html; the system fonts are the fallback (offline, slow network).
      fontFamily: {
        sans: [
          '"Inter Tight"',
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          '"Segoe UI"',
          "Roboto",
          "sans-serif",
        ],
      },
      // The near-black, slightly blue base the whole app sits on.
      colors: {
        ink: {
          950: "#08080d",
          900: "#0d0c14",
          800: "#15141d",
          700: "#1d1c27",
        },
      },
    },
  },
  plugins: [],
} satisfies Config;
