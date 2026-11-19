import { act, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, reportDownloadUrl } from "../api/client";
import type { RunRecord, RunReport } from "../api/types";
import ReportPanel from "./ReportPanel";
import { POLL_ATTEMPTS, POLL_DELAY_MS } from "./useRunRecord";

const api = vi.hoisted(() => ({ getRun: vi.fn() }));
vi.mock("../api/client", async (original) => ({
  ...(await original<typeof import("../api/client")>()),
  getRun: api.getRun,
}));

const REPORT: RunReport = {
  schema_version: 1,
  run_id: "r1",
  status: "completed",
  headline: "Done: all 3 steps finished.",
  error: null,
  title: "Project Falcon weekly sync",
  summary: "Notes turned into a page.",
  duration_seconds: 20.3,
  retries: 1,
  counts: { total: 3, succeeded: 3, failed: 0, skipped: 0, pending: 0 },
  steps: [
    { id: "s1", tool: "notion", action: "create_page", description: "Create the page", status: "succeeded", attempts: 1, summary: "Created page", url: "https://notion.example/p", error: null },
    { id: "s2", tool: "drive", action: "create_text_file", description: "Save a copy", status: "succeeded", attempts: 3, summary: "Saved a copy", url: "javascript:alert(1)", error: null },
    { id: "s3", tool: "notifier", action: "send", description: "Tell the team", status: "succeeded", attempts: 1, summary: "Message sent", url: null, error: null },
  ],
  created: [{ step_id: "s1", tool: "notion", label: "Created page", url: "https://notion.example/p" }],
  checks: [{ step_id: "s1", tool: "notion", action: "create_page", status: "verified", detail: "The page exists in Notion." }],
  rollback: [],
  needs_cleanup: [],
};

const record = (over: Partial<RunRecord> = {}): RunRecord => ({
  run_id: "r1",
  request: "req",
  status: "completed",
  created_at: "2026-11-19T12:00:00Z",
  finished_at: "2026-11-19T12:00:20Z",
  report: REPORT,
  trace_url: "https://langfuse.example/trace/1",
  ...over,
});

const running = () => record({ status: "running", report: null, finished_at: null });

// Lets pending promises and timers up to `ms` run, inside act so React updates.
const advance = (ms: number) => act(async () => void (await vi.advanceTimersByTimeAsync(ms)));

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe("while waiting", () => {
  it("renders nothing and asks for nothing until the run has finished", async () => {
    const { container } = render(<ReportPanel runId="r1" finished={false} />);
    await advance(2000);
    expect(container).toBeEmptyDOMElement();
    expect(api.getRun).not.toHaveBeenCalled();
  });

  it("says it is preparing, then asks again until the server has stored the report", async () => {
    api.getRun.mockResolvedValueOnce(running()).mockResolvedValue(record());
    render(<ReportPanel runId="r1" finished />);
    await advance(0);
    expect(screen.getByRole("status")).toHaveTextContent("Preparing your report…");
    await advance(POLL_DELAY_MS);
    expect(screen.getByRole("heading", { name: "Project Falcon weekly sync" })).toBeInTheDocument();
    await advance(5000);
    expect(api.getRun).toHaveBeenCalledTimes(2); // it stops asking once it has the report
  });

  it("keeps trying through a failed request", async () => {
    api.getRun.mockRejectedValueOnce(new ApiError("down", 0)).mockResolvedValue(record());
    render(<ReportPanel runId="r1" finished />);
    await advance(POLL_DELAY_MS);
    expect(screen.getByRole("heading", { name: "Project Falcon weekly sync" })).toBeInTheDocument();
  });

  it("gives up after the last attempt with a plain message that keeps the result valid", async () => {
    api.getRun.mockResolvedValue(running());
    render(<ReportPanel runId="r1" finished />);
    await advance(POLL_DELAY_MS * POLL_ATTEMPTS);
    expect(api.getRun).toHaveBeenCalledTimes(POLL_ATTEMPTS);
    expect(screen.getByRole("status")).toHaveTextContent("The report isn't available yet.");
    await advance(5000);
    expect(api.getRun).toHaveBeenCalledTimes(POLL_ATTEMPTS);
  });

  it("stops asking when the screen is left", async () => {
    api.getRun.mockResolvedValue(running());
    const { unmount } = render(<ReportPanel runId="r1" finished />);
    await advance(POLL_DELAY_MS);
    const calls = api.getRun.mock.calls.length;
    unmount();
    await advance(5000);
    expect(api.getRun).toHaveBeenCalledTimes(calls);
  });
});

describe("a successful report", () => {
  beforeEach(async () => {
    api.getRun.mockResolvedValue(record());
    render(<ReportPanel runId="r1" finished />);
    await advance(0);
  });

  it("shows the summary and the key numbers", () => {
    expect(screen.getByText("Notes turned into a page.")).toBeInTheDocument();
    const stats = screen.getByRole("region", { name: "Report" });
    expect(stats).toHaveTextContent("Took20s");
    expect(stats).toHaveTextContent("Steps3 of 3");
    expect(stats).toHaveTextContent("Retries1");
    expect(stats).toHaveTextContent("Items created1");
  });

  it("lists every step with its tool, status, tries and result", () => {
    const rows = within(screen.getByRole("table")).getAllByRole("row");
    expect(rows).toHaveLength(4); // header + 3 steps
    expect(rows[2]).toHaveTextContent("Save a copy");
    expect(rows[2]).toHaveTextContent("Google Drive");
    expect(rows[2]).toHaveTextContent("Done");
    expect(rows[2]).toHaveTextContent("3");
    expect(within(rows[1]).getByRole("link", { name: /Created page/ })).toHaveAttribute(
      "href",
      "https://notion.example/p",
    );
  });

  it("never turns an unsafe address into a link", () => {
    expect(screen.queryByRole("link", { name: /Saved a copy/ })).not.toBeInTheDocument();
    expect(screen.getByText("Saved a copy")).toBeInTheDocument();
  });

  it("shows the checks", () => {
    expect(screen.getByText("Checks before telling the team")).toBeInTheDocument();
    expect(screen.getByText("The page exists in Notion.")).toBeInTheDocument();
    expect(screen.queryByText("What was undone")).not.toBeInTheDocument();
  });

  it("offers the download and the Langfuse trace", () => {
    expect(screen.getByRole("link", { name: /Download report/ })).toHaveAttribute(
      "href",
      reportDownloadUrl("r1"),
    );
    const trace = screen.getByRole("link", { name: /View trace in Langfuse/ });
    expect(trace).toHaveAttribute("href", "https://langfuse.example/trace/1");
    expect(trace).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("has the raw JSON behind a toggle", () => {
    const details = screen.getByText("Show raw JSON").closest("details");
    expect(details).not.toHaveAttribute("open");
    expect(details).toHaveTextContent('"schema_version": 1');
  });
});

describe("other reports", () => {
  it("shows what was undone after a rollback", async () => {
    const rolled: RunReport = {
      ...REPORT,
      status: "rolled_back",
      created: [],
      rollback: [{ step_id: "s1", tool: "notion", action: "create_page", status: "undone", detail: "Moved to the trash" }],
    };
    api.getRun.mockResolvedValue(record({ status: "rolled_back", report: rolled }));
    render(<ReportPanel runId="r1" finished />);
    await advance(0);
    expect(screen.getByText("What was undone")).toBeInTheDocument();
    expect(screen.getByText("Moved to the trash")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Report" })).toHaveTextContent("Items created0");
  });

  it("hides the trace button when there is no usable trace link", async () => {
    api.getRun.mockResolvedValue(record({ trace_url: null }));
    render(<ReportPanel runId="r1" finished />);
    await advance(0);
    expect(screen.queryByRole("link", { name: /Langfuse/ })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Download report/ })).toBeInTheDocument();
  });

  it("still renders a report that has none of the optional sections", async () => {
    const bare: RunReport = {
      run_id: "r1",
      status: "failed",
      headline: "Stopped.",
      error: "boom",
      title: null,
      summary: null,
      duration_seconds: 2,
      retries: 0,
      created: [],
      needs_cleanup: [],
    };
    api.getRun.mockResolvedValue(record({ status: "failed", report: bare }));
    render(<ReportPanel runId="r1" finished />);
    await advance(0);
    expect(screen.getByRole("region", { name: "Report" })).toHaveTextContent("Took2s");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});
