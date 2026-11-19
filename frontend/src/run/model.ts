// What the screen needs to know about one run, built up from the stream of events.
//
// `reduceRun` is a plain function (no React): give it the current view and one event, get the
// next view. Replaying the same events always gives the same view, which is what makes the live
// timeline, a page refresh and an old run opened from the history all look identical.
import type { FinalStatus, PlanStepInfo, RunEvent } from "../api/types";

export type StepStatus = "queued" | "running" | "retrying" | "done" | "failed";
export type UndoStatus = "undone" | "failed" | "skipped";
// Where the run is overall; the live timeline shows a different banner for each.
export type RunPhase =
  | "starting"
  | "planning"
  | "running"
  | "verifying"
  | "rolling_back"
  | "finished";

export interface StepView {
  id: string;
  tool: string;
  action: string;
  description: string;
  status: StepStatus;
  attempts: number;
  summary: string | null;
  url: string | null;
  error: string | null;
  // Set while a step waits to try again: which attempt is next, in how long, and why.
  retry: { attempt: number; delaySeconds: number; reason: string } | null;
  // Set if the run was rolled back and this step's result was (or could not be) undone.
  undo: UndoStatus | null;
}

export interface CheckView {
  stepId: string;
  tool: string;
  action: string;
  status: "verified" | "failed" | "unconfirmed";
  detail: string;
}

export interface UndoView {
  stepId: string;
  tool: string;
  action: string;
  status: UndoStatus;
  detail: string;
}

export interface Outcome {
  status: FinalStatus;
  headline: string;
  error: string | null;
}

export interface RunView {
  runId: string;
  request: string | null;
  phase: RunPhase;
  steps: StepView[];
  checks: CheckView[];
  undone: UndoView[];
  retries: number; // how many times any step was retried
  startedAt: string | null; // timestamps of the first and last event, for elapsed time
  finishedAt: string | null;
  outcome: Outcome | null;
  lastSeq: number; // the newest event applied; anything at or below it is a duplicate
}

export function initialRunView(runId: string): RunView {
  return {
    runId,
    request: null,
    phase: "starting",
    steps: [],
    checks: [],
    undone: [],
    retries: 0,
    startedAt: null,
    finishedAt: null,
    outcome: null,
    lastSeq: 0,
  };
}

function queuedStep(info: PlanStepInfo): StepView {
  return {
    id: info.id,
    tool: info.tool,
    action: info.action,
    description: info.description,
    status: "queued",
    attempts: 0,
    summary: null,
    url: null,
    error: null,
    retry: null,
    undo: null,
  };
}

// Change one step, leaving the rest alone. An unknown id changes nothing.
function withStep(
  steps: StepView[],
  id: string,
  change: (step: StepView) => StepView,
): StepView[] {
  return steps.map((step) => (step.id === id ? change(step) : step));
}

export function reduceRun(view: RunView, event: RunEvent): RunView {
  // After a dropped connection the browser asks to resume, but a repeat must never double-count.
  if (event.seq <= view.lastSeq) return view;
  const next: RunView = {
    ...view,
    lastSeq: event.seq,
    startedAt: view.startedAt ?? event.timestamp,
    finishedAt: event.timestamp,
  };

  switch (event.type) {
    case "run_started":
      return { ...next, phase: "planning", request: event.data.request ?? null };

    case "plan_ready":
      return { ...next, phase: "running", steps: event.data.steps.map(queuedStep) };

    case "step_started": {
      const d = event.data;
      const known = view.steps.some((step) => step.id === d.step_id);
      // The plan always arrives first; adding the step here only guards against a missed event.
      const steps = known
        ? view.steps
        : [
            ...view.steps,
            queuedStep({
              id: d.step_id,
              tool: d.tool,
              action: d.action,
              description: d.description,
            }),
          ];
      return {
        ...next,
        phase: "running",
        steps: withStep(steps, d.step_id, (step) => ({
          ...step,
          status: "running",
          attempts: Math.max(step.attempts, 1),
        })),
      };
    }

    case "step_retry": {
      const d = event.data;
      return {
        ...next,
        retries: view.retries + 1,
        steps: withStep(view.steps, d.step_id, (step) => ({
          ...step,
          status: "retrying",
          attempts: d.attempt,
          retry: {
            attempt: d.attempt,
            delaySeconds: d.delay_seconds,
            reason: d.reason,
          },
        })),
      };
    }

    case "step_succeeded": {
      const d = event.data;
      return {
        ...next,
        steps: withStep(view.steps, d.step_id, (step) => ({
          ...step,
          status: "done",
          attempts: d.attempts,
          summary: d.summary,
          url: d.url,
          retry: null,
        })),
      };
    }

    case "step_failed": {
      const d = event.data;
      return {
        ...next,
        steps: withStep(view.steps, d.step_id, (step) => ({
          ...step,
          status: "failed",
          attempts: d.attempts,
          error: d.error,
          retry: null,
        })),
      };
    }

    case "verify_started":
      return { ...next, phase: "verifying" };

    case "verify_step": {
      const d = event.data;
      return {
        ...next,
        checks: [
          ...view.checks,
          {
            stepId: d.step_id,
            tool: d.tool,
            action: d.action,
            status: d.status,
            detail: d.detail,
          },
        ],
      };
    }

    case "rollback_started":
      return { ...next, phase: "rolling_back" };

    case "rollback_step": {
      const d = event.data;
      return {
        ...next,
        undone: [
          ...view.undone,
          {
            stepId: d.step_id,
            tool: d.tool,
            action: d.action,
            status: d.status,
            detail: d.detail,
          },
        ],
        steps: withStep(view.steps, d.step_id, (step) => ({
          ...step,
          undo: d.status,
        })),
      };
    }

    case "run_completed":
      return {
        ...next,
        phase: "finished",
        outcome: {
          status: event.data.status,
          headline: event.data.headline,
          error: event.data.error,
        },
      };
  }
}

// Seconds between the first and last event seen (0 until two events have arrived).
export function elapsedSeconds(view: RunView): number {
  if (!view.startedAt || !view.finishedAt) return 0;
  const ms = Date.parse(view.finishedAt) - Date.parse(view.startedAt);
  return Number.isFinite(ms) && ms > 0 ? ms / 1000 : 0;
}

export function isFinished(view: RunView): boolean {
  return view.phase === "finished";
}
