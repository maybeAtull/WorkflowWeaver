import { describe, expect, it } from "vitest";

import type { RunEvent } from "../api/types";
import { elapsedSeconds, initialRunView, isFinished, reduceRun } from "./model";
import type { RunView } from "./model";

// Builds an event with a timestamp one second apart per sequence number.
function ev(seq: number, type: RunEvent["type"], data: object = {}): RunEvent {
  const timestamp = new Date(Date.UTC(2026, 0, 1, 12, 0, seq)).toISOString();
  return { run_id: "r1", seq, type, data, timestamp } as RunEvent;
}

function replay(events: RunEvent[]): RunView {
  return events.reduce(reduceRun, initialRunView("r1"));
}

const PLAN = {
  steps: [
    { id: "s1", tool: "notion", action: "create_page", description: "Create the page" },
    { id: "s2", tool: "drive", action: "create_text_file", description: "Save a copy" },
    { id: "s3", tool: "notifier", action: "send", description: "Tell the team" },
  ],
};

const started = (seq: number, id: string, position: number) =>
  ev(seq, "step_started", {
    step_id: id,
    position,
    tool: "x",
    action: "y",
    description: "d",
  });

describe("a successful run", () => {
  const events = [
    ev(1, "run_started", { request: "Do it" }),
    ev(2, "plan_ready", PLAN),
    started(3, "s1", 1),
    ev(4, "step_succeeded", {
      step_id: "s1",
      summary: "Created page",
      url: "https://notion.example/p",
      attempts: 1,
    }),
    started(5, "s2", 2),
    ev(6, "step_succeeded", { step_id: "s2", summary: "Saved", url: null, attempts: 1 }),
    ev(7, "verify_started", { total: 2 }),
    ev(8, "verify_step", {
      step_id: "s1",
      tool: "notion",
      action: "create_page",
      status: "verified",
      detail: "Page found",
    }),
    started(9, "s3", 3),
    ev(10, "step_succeeded", { step_id: "s3", summary: "Sent", url: null, attempts: 1 }),
    ev(11, "run_completed", { status: "completed", error: null, headline: "All done" }),
  ];

  it("walks through the phases in order", () => {
    const phases = events.map((_, i) => replay(events.slice(0, i + 1)).phase);
    expect(phases).toEqual([
      "planning",
      "running",
      "running",
      "running",
      "running",
      "running",
      "verifying",
      "verifying",
      "running",
      "running",
      "finished",
    ]);
  });

  it("ends with every step done, the links kept and the outcome set", () => {
    const view = replay(events);
    expect(view.steps.map((s) => s.status)).toEqual(["done", "done", "done"]);
    expect(view.steps[0].url).toBe("https://notion.example/p");
    expect(view.steps[0].summary).toBe("Created page");
    expect(view.request).toBe("Do it");
    expect(view.outcome).toEqual({
      status: "completed",
      headline: "All done",
      error: null,
    });
    expect(view.checks).toHaveLength(1);
    expect(isFinished(view)).toBe(true);
  });

  it("starts with every step queued as soon as the plan arrives", () => {
    const view = replay(events.slice(0, 2));
    expect(view.steps.map((s) => s.status)).toEqual(["queued", "queued", "queued"]);
    expect(view.steps.map((s) => s.description)).toEqual([
      "Create the page",
      "Save a copy",
      "Tell the team",
    ]);
  });

  it("measures elapsed time from the first to the last event", () => {
    expect(elapsedSeconds(replay(events))).toBe(10);
    expect(elapsedSeconds(initialRunView("r1"))).toBe(0);
  });
});

describe("retries", () => {
  const base = [
    ev(1, "run_started"),
    ev(2, "plan_ready", PLAN),
    started(3, "s2", 2),
    ev(4, "step_retry", { step_id: "s2", attempt: 2, delay_seconds: 1.5, reason: "busy" }),
  ];

  it("shows a waiting step with the attempt, delay and reason", () => {
    const step = replay(base).steps[1];
    expect(step.status).toBe("retrying");
    expect(step.retry).toEqual({ attempt: 2, delaySeconds: 1.5, reason: "busy" });
  });

  it("counts retries and clears the waiting note once the step succeeds", () => {
    const view = replay([
      ...base,
      ev(5, "step_retry", { step_id: "s2", attempt: 3, delay_seconds: 3, reason: "busy" }),
      ev(6, "step_succeeded", { step_id: "s2", summary: "Saved", url: null, attempts: 3 }),
    ]);
    expect(view.retries).toBe(2);
    expect(view.steps[1]).toMatchObject({ status: "done", attempts: 3, retry: null });
  });
});

describe("a failure and rollback", () => {
  const events = [
    ev(1, "run_started"),
    ev(2, "plan_ready", PLAN),
    started(3, "s1", 1),
    ev(4, "step_succeeded", { step_id: "s1", summary: "Created", url: "https://n/p", attempts: 1 }),
    started(5, "s2", 2),
    ev(6, "step_failed", { step_id: "s2", error: "No access", attempts: 1 }),
    ev(7, "rollback_started", { total: 1, to_undo: 1 }),
    ev(8, "rollback_step", {
      step_id: "s1",
      tool: "notion",
      action: "create_page",
      status: "undone",
      detail: "Moved to the trash",
    }),
    ev(9, "run_completed", { status: "rolled_back", error: "No access", headline: "Undone" }),
  ];

  it("marks the failed step and the undone one, and never touches the unreached step", () => {
    const view = replay(events);
    expect(view.steps.map((s) => s.status)).toEqual(["done", "failed", "queued"]);
    expect(view.steps[1].error).toBe("No access");
    expect(view.steps[0].undo).toBe("undone");
    expect(view.steps[2].undo).toBeNull();
    expect(view.undone).toEqual([
      {
        stepId: "s1",
        tool: "notion",
        action: "create_page",
        status: "undone",
        detail: "Moved to the trash",
      },
    ]);
    expect(view.outcome?.status).toBe("rolled_back");
  });

  it("shows the rolling-back phase before the end", () => {
    expect(replay(events.slice(0, 7)).phase).toBe("rolling_back");
  });

  it("records an undo that failed, so the screen can say something was left behind", () => {
    const view = replay([
      ...events.slice(0, 7),
      ev(8, "rollback_step", {
        step_id: "s1",
        tool: "notion",
        action: "create_page",
        status: "failed",
        detail: "Could not reach Notion",
      }),
    ]);
    expect(view.steps[0].undo).toBe("failed");
    expect(view.steps[0].status).toBe("done");
  });
});

describe("robustness", () => {
  it("ignores an event it has already applied (a repeat after reconnecting)", () => {
    const first = [ev(1, "run_started"), ev(2, "plan_ready", PLAN), started(3, "s1", 1)];
    const once = replay(first);
    const twice = [ev(3, "step_started", { step_id: "s1", position: 1, tool: "x", action: "y", description: "d" })].reduce(reduceRun, once);
    expect(twice).toBe(once);
  });

  it("gives the same view when the same events are replayed", () => {
    const events = [ev(1, "run_started"), ev(2, "plan_ready", PLAN), started(3, "s1", 1)];
    expect(replay(events)).toEqual(replay(events));
  });

  it("does not change the input view", () => {
    const view = replay([ev(1, "run_started"), ev(2, "plan_ready", PLAN)]);
    const frozen = JSON.stringify(view);
    reduceRun(view, started(3, "s1", 1));
    expect(JSON.stringify(view)).toBe(frozen);
  });

  it("adds a step it was never told about instead of losing the event", () => {
    const view = replay([ev(1, "run_started"), started(2, "s9", 1)]);
    expect(view.steps).toHaveLength(1);
    expect(view.steps[0]).toMatchObject({ id: "s9", status: "running" });
  });

  it("ignores an update for a step that does not exist", () => {
    const view = replay([
      ev(1, "run_started"),
      ev(2, "plan_ready", PLAN),
      ev(3, "step_succeeded", { step_id: "nope", summary: "x", url: null, attempts: 1 }),
    ]);
    expect(view.steps.every((s) => s.status === "queued")).toBe(true);
  });

  it("keeps a missing request as null", () => {
    expect(replay([ev(1, "run_started")]).request).toBeNull();
  });
});
