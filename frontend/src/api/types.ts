// The shapes the backend sends. They mirror backend/app/events.py (the event stream),
// backend/app/api/*.py (the REST endpoints) and backend/app/templates/ (the catalogue).

export type RunStatus =
  | "running"
  | "completed"
  | "completed_with_warning"
  | "failed"
  | "rolled_back";

export type FinalStatus = Exclude<RunStatus, "running">;

export interface PlanStepInfo {
  id: string;
  tool: string;
  action: string;
  description: string;
}

// Each event is tagged with its `type`, so a `switch` on it narrows `data` to the right shape.
interface EventBase {
  run_id: string;
  seq: number; // 1, 2, 3... per run; lets the client ignore a duplicate after a reconnect
  timestamp: string;
}

export type RunEvent = EventBase &
  (
    | { type: "run_started"; data: { request?: string } }
    | { type: "plan_ready"; data: { steps: PlanStepInfo[] } }
    | {
        type: "step_started";
        data: {
          step_id: string;
          position: number;
          tool: string;
          action: string;
          description: string;
        };
      }
    | {
        type: "step_retry";
        data: {
          step_id: string;
          attempt: number;
          delay_seconds: number;
          reason: string;
        };
      }
    | {
        type: "step_succeeded";
        data: {
          step_id: string;
          summary: string;
          url: string | null;
          attempts: number;
        };
      }
    | {
        type: "step_failed";
        data: { step_id: string; error: string; attempts: number };
      }
    | { type: "verify_started"; data: { total: number } }
    | {
        type: "verify_step";
        data: {
          step_id: string;
          tool: string;
          action: string;
          status: "verified" | "failed" | "unconfirmed";
          detail: string;
        };
      }
    | { type: "rollback_started"; data: { total: number; to_undo: number } }
    | {
        type: "rollback_step";
        data: {
          step_id: string;
          tool: string;
          action: string;
          status: "undone" | "failed" | "skipped";
          detail: string;
        };
      }
    | {
        type: "run_completed";
        data: { status: FinalStatus; error: string | null; headline: string };
      }
  );

export type RunEventType = RunEvent["type"];

// Every event type the stream can send. EventSource needs one listener per name.
export const RUN_EVENT_TYPES: readonly RunEventType[] = [
  "run_started",
  "plan_ready",
  "step_started",
  "step_retry",
  "step_succeeded",
  "step_failed",
  "verify_started",
  "verify_step",
  "rollback_started",
  "rollback_step",
  "run_completed",
];

export interface TemplateField {
  key: string;
  label: string;
  help: string;
  kind: "text" | "longtext" | "choice";
  required: boolean;
  max_length: number;
  options: string[];
}

export interface TemplateInfo {
  id: string;
  name: string;
  description: string;
  fields: TemplateField[];
  example: Record<string, string>;
}

// Free text, or a template with its field values: never both (the backend refuses a mix).
export type StartRunBody =
  | { request: string; simulate?: string }
  | { template_id: string; values: Record<string, string>; simulate?: string };

export interface RunListItem {
  run_id: string;
  status: RunStatus;
  created_at: string;
  finished_at: string | null;
  request_preview: string;
  headline: string | null;
  title: string | null;
  duration_seconds: number | null;
}

export interface ReportCreatedItem {
  step_id: string;
  tool: string;
  label: string;
  url: string;
}

export interface RunReportStep {
  id: string;
  tool: string;
  action: string;
  description: string;
  status: "succeeded" | "failed" | "skipped" | "pending";
  attempts: number;
  summary: string | null;
  url: string | null;
  error: string | null;
}

export interface RunReportCheck {
  step_id: string;
  tool: string;
  action: string;
  status: "verified" | "failed" | "unconfirmed";
  detail: string;
}

export interface RunReportUndo {
  step_id: string;
  tool: string;
  action: string;
  status: "undone" | "failed" | "skipped";
  detail: string;
}

// The stored report. The sections are optional because the page must still render if a report
// is older or newer than this code expects (the server versions it with schema_version).
export interface RunReport {
  schema_version?: number;
  run_id: string;
  status: FinalStatus;
  headline: string;
  error: string | null;
  title: string | null;
  summary: string | null;
  duration_seconds: number;
  retries: number;
  counts?: { total: number; succeeded: number; failed: number; skipped: number; pending: number };
  steps?: RunReportStep[];
  created: ReportCreatedItem[];
  checks?: RunReportCheck[];
  rollback?: RunReportUndo[];
  needs_cleanup: unknown[];
  [key: string]: unknown;
}

export interface RunRecord {
  run_id: string;
  request: string;
  status: RunStatus;
  created_at: string;
  finished_at: string | null;
  report: RunReport | null;
  trace_url: string | null;
}
