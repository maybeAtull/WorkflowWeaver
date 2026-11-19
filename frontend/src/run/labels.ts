// The words and rules the run screen shows, kept out of the components so they can be tested alone.
// Everything here is written for a business user: no status codes, no tool jargon.
import type { FinalStatus } from "../api/types";
import type { RunPhase, RunView, StepView } from "./model";

export function toolLabel(tool: string): string {
  switch (tool) {
    case "notion":
      return "Notion";
    case "drive":
      return "Google Drive";
    case "notifier":
      return "Team chat";
    default:
      return tool;
  }
}

// The banner above the steps. `null` means there is nothing extra to say (the steps speak for themselves).
export function phaseMessage(phase: RunPhase): string | null {
  switch (phase) {
    case "starting":
      return "Starting…";
    case "planning":
      return "Planning your workflow…";
    case "verifying":
      return "Checking what was created before telling the team…";
    case "rolling_back":
      return "Something went wrong. Undoing what was created…";
    case "running":
    case "finished":
      return null;
  }
}

// What a step's status line says, including what happened to it during an undo.
export function stepStatusText(step: StepView): string {
  if (step.undo === "undone") return "Undone";
  if (step.undo === "failed") return "Could not be undone";
  switch (step.status) {
    case "queued":
      return "Waiting";
    case "running":
      return "Running…";
    case "retrying":
      return step.retry
        ? `Retrying (attempt ${step.retry.attempt}) in ${Math.max(0, Math.round(step.retry.delaySeconds))}s`
        : "Retrying";
    case "done":
      return "Done";
    case "failed":
      return "This step didn't work";
  }
}

export type Tone = "success" | "warning" | "neutral" | "danger";

export function outcomeTone(status: FinalStatus): Tone {
  switch (status) {
    case "completed":
      return "success";
    case "completed_with_warning":
      return "warning";
    case "rolled_back":
      return "neutral";
    case "failed":
      return "danger";
  }
}

export function outcomeTitle(status: FinalStatus): string {
  switch (status) {
    case "completed":
      return "All done";
    case "completed_with_warning":
      return "Done, with a warning";
    case "rolled_back":
      return "Nothing was changed";
    case "failed":
      return "The workflow didn't finish";
  }
}

// "12s", "1m 05s".
export function formatElapsed(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  if (whole < 60) return `${whole}s`;
  return `${Math.floor(whole / 60)}m ${String(whole % 60).padStart(2, "0")}s`;
}

// Only ordinary web links are ever shown as links (never javascript: or anything else).
export function isSafeUrl(url: string | null): url is string {
  return url !== null && /^https?:\/\//i.test(url);
}

export interface ItemLink {
  id: string;
  label: string;
  tool: string;
  url: string;
}

const SUCCESS: readonly FinalStatus[] = ["completed", "completed_with_warning"];

// The items the run created that still exist: only after a successful finish, and never one that
// was undone. After a rollback or failure, nothing is offered as "created".
export function createdLinks(view: RunView): ItemLink[] {
  if (!view.outcome || !SUCCESS.includes(view.outcome.status)) return [];
  return view.steps
    .filter((s) => s.status === "done" && s.undo !== "undone" && isSafeUrl(s.url))
    .map((s) => ({
      id: s.id,
      label: s.summary ?? s.description,
      tool: s.tool,
      url: s.url as string,
    }));
}

// Items that should have been removed but could not be, so the user knows to clean them up.
export function leftBehind(view: RunView): ItemLink[] {
  return view.steps
    .filter((s) => s.undo === "failed")
    .map((s) => ({
      id: s.id,
      label: s.summary ?? s.description,
      tool: s.tool,
      url: isSafeUrl(s.url) ? s.url : "",
    }));
}

// A short sentence for screen readers whenever something changes (not on every timer tick).
export function announcement(view: RunView): string {
  if (view.outcome) return `${outcomeTitle(view.outcome.status)}. ${view.outcome.headline}`;
  const phase = phaseMessage(view.phase);
  if (phase && view.phase !== "running") return phase;
  const current = view.steps.find((s) => s.status === "running" || s.status === "retrying");
  if (current) return `${stepStatusText(current)}: ${current.description}`;
  return phase ?? "";
}
