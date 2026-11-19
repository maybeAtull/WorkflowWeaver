import { describe, expect, it } from "vitest";

import type { RunReport, RunReportStep } from "../api/types";
import { relativeTime, reportStepStatus, statusChip, stepResult, stepsSummary } from "./format";

const NOW = Date.parse("2026-11-19T12:00:00Z");
const ago = (seconds: number) => new Date(NOW - seconds * 1000).toISOString();

describe("statusChip", () => {
  it("gives every status plain words and a tone", () => {
    expect(statusChip("running")).toEqual({ label: "Running", tone: "info" });
    expect(statusChip("completed")).toEqual({ label: "Completed", tone: "success" });
    expect(statusChip("completed_with_warning").tone).toBe("warning");
    expect(statusChip("rolled_back")).toEqual({ label: "Undone", tone: "neutral" });
    expect(statusChip("failed").tone).toBe("danger");
  });
});

describe("relativeTime", () => {
  it("speaks in friendly units", () => {
    expect(relativeTime(ago(5), NOW)).toBe("just now");
    expect(relativeTime(ago(60), NOW)).toBe("1 min ago");
    expect(relativeTime(ago(5 * 60), NOW)).toBe("5 min ago");
    expect(relativeTime(ago(3 * 3600), NOW)).toBe("3 h ago");
    expect(relativeTime(ago(30 * 3600), NOW)).toBe("yesterday");
  });

  it("falls back to a short date for older runs", () => {
    expect(relativeTime(ago(10 * 86400), NOW)).toMatch(/\d{1,2} \w{3}/);
  });

  it("never shows a negative time (a clock a little ahead) and copes with a bad date", () => {
    expect(relativeTime(ago(-30), NOW)).toBe("just now");
    expect(relativeTime("not a date", NOW)).toBe("");
  });
});

describe("report wording", () => {
  const step = (over: Partial<RunReportStep>): RunReportStep => ({
    id: "s1",
    tool: "notion",
    action: "a",
    description: "d",
    status: "succeeded",
    attempts: 1,
    summary: "Created it",
    url: null,
    error: null,
    ...over,
  });

  it("names step statuses for a business user", () => {
    expect(reportStepStatus("succeeded")).toBe("Done");
    expect(reportStepStatus("failed")).toBe("Didn't work");
    expect(reportStepStatus("skipped")).toBe("Skipped");
    expect(reportStepStatus("pending")).toBe("Not reached");
  });

  it("shows the summary for a done step, the error for a failed one, nothing otherwise", () => {
    expect(stepResult(step({}))).toBe("Created it");
    expect(stepResult(step({ status: "failed", error: "No access" }))).toBe("No access");
    expect(stepResult(step({ status: "pending" }))).toBe("");
    expect(stepResult(step({ summary: null }))).toBe("");
  });

  const report = (over: Partial<RunReport>): RunReport => ({
    run_id: "r",
    status: "completed",
    headline: "h",
    error: null,
    title: null,
    summary: null,
    duration_seconds: 1,
    retries: 0,
    created: [],
    needs_cleanup: [],
    ...over,
  });

  it("summarises how many steps are done, from the counts or from the steps", () => {
    expect(
      stepsSummary(report({ counts: { total: 3, succeeded: 2, failed: 1, skipped: 0, pending: 0 } })),
    ).toBe("2 of 3 steps done");
    expect(stepsSummary(report({ steps: [step({}), step({ id: "s2", status: "failed" })] }))).toBe(
      "1 of 2 steps done",
    );
    expect(stepsSummary(report({ steps: [step({})] }))).toBe("1 of 1 step done");
    expect(stepsSummary(report({}))).toBe("");
  });
});
