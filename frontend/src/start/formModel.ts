// The start screen's logic with no React in it: checking what the user typed, building the request
// the backend expects, and the demo options. Kept separate so it can be tested on its own.
import type { StartRunBody, TemplateInfo } from "../api/types";

// Matches the limit the backend enforces on a free-text request.
export const FREE_TEXT_LIMIT = 6000;
// The backend plans at most ten steps, so a failure can only be aimed at one of those.
export const MAX_DEMO_STEP = 10;
export const DEFAULT_DEMO_STEP = 2;
// Show a character counter once a field is this full, so it stays out of the way until it matters.
const COUNTER_THRESHOLD = 0.8;

const formatNumber = (n: number) => n.toLocaleString("en-US");

// One plain-language message per field that has a problem, worded like the backend's own.
export function validateValues(
  template: TemplateInfo,
  values: Record<string, string>,
): Record<string, string> {
  const problems: Record<string, string> = {};
  for (const field of template.fields) {
    const value = (values[field.key] ?? "").trim();
    if (!value && field.required) {
      problems[field.key] = `Please fill in “${field.label}”.`;
    } else if (value.length > field.max_length) {
      problems[field.key] =
        `“${field.label}” is too long: ${formatNumber(value.length)} characters, ` +
        `the limit is ${formatNumber(field.max_length)}.`;
    } else if (value && field.kind === "choice" && !field.options.includes(value)) {
      problems[field.key] = `“${field.label}” must be one of: ${field.options.join(", ")}.`;
    }
  }
  return problems;
}

export function validateFreeText(text: string): string | null {
  const value = text.trim();
  if (!value) return "Describe the workflow you want to run.";
  if (value.length > FREE_TEXT_LIMIT) {
    return (
      `Your description is too long: ${formatNumber(value.length)} characters, ` +
      `the limit is ${formatNumber(FREE_TEXT_LIMIT)}.`
    );
  }
  return null;
}

export interface Counter {
  visible: boolean;
  over: boolean;
  text: string;
}

export function counterState(length: number, limit: number): Counter {
  return {
    visible: length >= limit * COUNTER_THRESHOLD,
    over: length > limit,
    text: `${formatNumber(length)} / ${formatNumber(limit)}`,
  };
}

// ---- demo options: break a step on purpose so retry and rollback can be shown ------------------

export type DemoMode = "off" | "retry" | "rollback";

// The backend's failure injection format is STEP:MODE, for example "s2:transient".
export function simulateValue(mode: DemoMode, step: number): string | undefined {
  if (mode === "off") return undefined;
  return `s${step}:${mode === "retry" ? "transient" : "fatal"}`;
}

export const DEMO_MODE_LABELS: Record<DemoMode, string> = {
  off: "Normal run",
  retry: "Simulate a temporary failure (shows a retry)",
  rollback: "Simulate a failure (shows rollback)",
};

// ---- the request ---------------------------------------------------------------------------------

export function buildTemplateBody(
  template: TemplateInfo,
  values: Record<string, string>,
  simulate: string | undefined,
): StartRunBody {
  const body: StartRunBody = {
    template_id: template.id,
    values: Object.fromEntries(
      template.fields.map((field) => [field.key, (values[field.key] ?? "").trim()]),
    ),
  };
  return simulate ? { ...body, simulate } : body;
}

export function buildFreeTextBody(text: string, simulate: string | undefined): StartRunBody {
  const body: StartRunBody = { request: text.trim() };
  return simulate ? { ...body, simulate } : body;
}
