// Small pure helpers for the report and history screens: words and times for a business user.
import type { RunReport, RunReportStep, RunStatus } from "../api/types";

export type ChipTone = "success" | "warning" | "neutral" | "danger" | "info";

// The label and colour of a run's status, as shown in the history list and the report.
export function statusChip(status: RunStatus): { label: string; tone: ChipTone } {
  switch (status) {
    case "running":
      return { label: "Running", tone: "info" };
    case "completed":
      return { label: "Completed", tone: "success" };
    case "completed_with_warning":
      return { label: "Completed with a warning", tone: "warning" };
    case "rolled_back":
      return { label: "Undone", tone: "neutral" };
    case "failed":
      return { label: "Failed", tone: "danger" };
  }
}

export const CHIP_STYLES: Record<ChipTone, string> = {
  success: "border-emerald-400/30 bg-emerald-500/10 text-emerald-200",
  warning: "border-amber-400/30 bg-amber-500/10 text-amber-200",
  neutral: "border-white/15 bg-white/5 text-slate-300",
  danger: "border-rose-400/30 bg-rose-500/10 text-rose-200",
  info: "border-violet-400/30 bg-violet-500/10 text-violet-200",
};

const MINUTE = 60;
const HOUR = 3600;
const DAY = 86400;

// "just now", "5 min ago", "3 h ago", "yesterday", then a short date. Empty text for a bad date.
export function relativeTime(iso: string, now: number = Date.now()): string {
  const then = Date.parse(iso);
  if (!Number.isFinite(then)) return "";
  const seconds = Math.max(0, (now - then) / 1000);
  if (seconds < 45) return "just now";
  if (seconds < HOUR) return `${Math.max(1, Math.floor(seconds / MINUTE))} min ago`;
  if (seconds < DAY) return `${Math.floor(seconds / HOUR)} h ago`;
  if (seconds < 2 * DAY) return "yesterday";
  return new Date(then).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

// The full local date and time, for a tooltip.
export function exactTime(iso: string): string {
  const then = new Date(iso);
  return Number.isNaN(then.getTime()) ? "" : then.toLocaleString();
}

const STEP_STATUS: Record<RunReportStep["status"], string> = {
  succeeded: "Done",
  failed: "Didn't work",
  skipped: "Skipped",
  pending: "Not reached",
};

export function reportStepStatus(status: RunReportStep["status"]): string {
  return STEP_STATUS[status] ?? status;
}

// "2 of 3 steps done", from the report's counts (or its steps, if the counts are missing).
export function stepsSummary(report: RunReport): string {
  const total = report.counts?.total ?? report.steps?.length;
  if (total === undefined) return "";
  const done =
    report.counts?.succeeded ?? report.steps?.filter((s) => s.status === "succeeded").length ?? 0;
  return `${done} of ${total} step${total === 1 ? "" : "s"} done`;
}

// What to show in the "result" column of the steps table.
export function stepResult(step: RunReportStep): string {
  if (step.status === "succeeded") return step.summary ?? "";
  if (step.status === "failed") return step.error ?? "";
  return "";
}
