// Single place that knows where the backend lives and how to talk to it. The backend allows this
// frontend's dev origin via CORS (see backend/app/main.py), so we call it directly.
import type {
  RunListItem,
  RunRecord,
  StartRunBody,
  TemplateInfo,
} from "./types";

export const API_BASE_URL = "http://localhost:8000";

export interface HealthResponse {
  status: string;
  llm_provider?: string;
  notifier_provider?: string;
  database?: string;
}

// An error whose message is already written for a business user (never a status code or a stack).
export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export const SERVER_UNREACHABLE =
  "We can't reach the WorkflowWeaver server. Make sure the backend is running, then try again.";
const SERVER_PROBLEM = "Something went wrong on the server. Please try again.";

// FastAPI sends `detail` as a string for errors we raise, and as a list of problems for input
// that fails validation. Both become one plain sentence.
export function messageFromDetail(detail: unknown): string | null {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    const first: unknown = detail[0];
    const message =
      typeof first === "object" && first !== null && "msg" in first
        ? String((first as { msg: unknown }).msg)
        : "";
    // Pydantic prefixes our own messages with "Value error, ".
    const cleaned = message.replace(/^Value error,\s*/i, "").trim();
    return cleaned || null;
  }
  return null;
}

async function failureOf(response: Response): Promise<ApiError> {
  let detail: unknown = null;
  try {
    detail = ((await response.json()) as { detail?: unknown }).detail;
  } catch {
    // The body was not JSON; fall back to the generic messages below.
  }
  const fromServer = messageFromDetail(detail);
  if (response.status >= 500) return new ApiError(SERVER_PROBLEM, response.status);
  if (fromServer) return new ApiError(fromServer, response.status);
  if (response.status === 404) {
    return new ApiError("We couldn't find that.", response.status);
  }
  return new ApiError(
    "That request couldn't be completed. Please check it and try again.",
    response.status,
  );
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, init);
  } catch (error) {
    // A cancelled request is not a failure; let the caller see the abort itself.
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(SERVER_UNREACHABLE, 0);
  }
  if (!response.ok) throw await failureOf(response);
  return (await response.json()) as T;
}

export function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return request<HealthResponse>("/api/health", { signal });
}

export function getTemplates(signal?: AbortSignal): Promise<TemplateInfo[]> {
  return request<TemplateInfo[]>("/api/templates", { signal });
}

export async function startRun(body: StartRunBody): Promise<string> {
  const result = await request<{ run_id: string }>("/api/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return result.run_id;
}

export function listRuns(limit = 20, signal?: AbortSignal): Promise<RunListItem[]> {
  return request<RunListItem[]>(`/api/runs?limit=${limit}`, { signal });
}

export function getRun(runId: string, signal?: AbortSignal): Promise<RunRecord> {
  return request<RunRecord>(`/api/runs/${encodeURIComponent(runId)}`, { signal });
}

// Plain URLs (not fetches): one is opened by EventSource, the other is a download link.
export function eventsUrl(runId: string): string {
  return `${API_BASE_URL}/api/runs/${encodeURIComponent(runId)}/events`;
}

export function reportDownloadUrl(runId: string): string {
  return `${API_BASE_URL}/api/runs/${encodeURIComponent(runId)}/report`;
}
