// Shared look-and-feel class names, so every screen uses the same glass panels, inputs and buttons.

// A frosted panel on the dark background.
export const panel =
  "rounded-3xl border border-white/10 bg-white/[0.04] backdrop-blur-sm";

export const inputBase =
  "w-full rounded-2xl border bg-ink-900/80 px-4 py-3 text-[15px] text-slate-100 transition-colors " +
  "border-white/10 hover:border-white/20 focus-visible:border-violet-400/60";

export const inputInvalid = "!border-rose-400/70";

// White pill: the one main action on a screen.
export const primaryButton =
  "inline-flex items-center justify-center gap-2 rounded-full bg-white px-6 py-3 text-sm font-semibold " +
  "text-ink-950 transition hover:bg-white/90 disabled:cursor-not-allowed disabled:opacity-60";

// Dark pill: everything secondary.
export const secondaryButton =
  "inline-flex items-center justify-center gap-2 rounded-full border border-white/10 bg-ink-700/80 " +
  "px-5 py-2.5 text-sm font-medium text-slate-200 transition hover:bg-ink-700 disabled:opacity-60";
