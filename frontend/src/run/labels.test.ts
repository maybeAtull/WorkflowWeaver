import { describe, expect, it } from "vitest";

import type { RunEvent } from "../api/types";
import { ev } from "../test/fakeEventSource";
import {
  announcement,
  createdLinks,
  formatElapsed,
  isSafeUrl,
  leftBehind,
  outcomeTitle,
  outcomeTone,
  phaseMessage,
  stepStatusText,
  toolLabel,
} from "./labels";
import { initialRunView, reduceRun } from "./model";
import type { RunView, StepView } from "./model";

const PLAN = {
  steps: [
    { id: "s1", tool: "notion", action: "create_page", description: "Create the page" },
    { id: "s2", tool: "drive", action: "create_text_file", description: "Save a copy" },
  ],
};

function build(events: RunEvent[]): RunView {
  return events.reduce(reduceRun, initialRunView("r1"));
}

const ok = (seq: number, id: string, url: string | null) =>
  ev(seq, "step_succeeded", { step_id: id, summary: `Made ${id}`, url, attempts: 1 });

const finished = (seq: number, status: string) =>
  ev(seq, "run_completed", { status, error: null, headline: "Headline" });

describe("plain words", () => {
  it("names the tools for a business user", () => {
    expect(toolLabel("notion")).toBe("Notion");
    expect(toolLabel("drive")).toBe("Google Drive");
    expect(toolLabel("notifier")).toBe("Team chat");
    expect(toolLabel("other")).toBe("other");
  });

  it("has a banner for each phase that needs one", () => {
    expect(phaseMessage("planning")).toBe("Planning your workflow…");
    expect(phaseMessage("verifying")).toContain("Checking what was created");
    expect(phaseMessage("rolling_back")).toContain("Undoing what was created");
    expect(phaseMessage("running")).toBeNull();
    expect(phaseMessage("finished")).toBeNull();
  });

  it("maps every final status to a tone and a title", () => {
    expect(outcomeTone("completed")).toBe("success");
    expect(outcomeTone("completed_with_warning")).toBe("warning");
    expect(outcomeTone("rolled_back")).toBe("neutral");
    expect(outcomeTone("failed")).toBe("danger");
    expect(outcomeTitle("rolled_back")).toBe("Nothing was changed");
    expect(outcomeTitle("failed")).toBe("The workflow didn't finish");
  });

  it("formats elapsed time", () => {
    expect(formatElapsed(0)).toBe("0s");
    expect(formatElapsed(12.9)).toBe("12s");
    expect(formatElapsed(65)).toBe("1m 05s");
    expect(formatElapsed(-3)).toBe("0s");
  });
});

describe("step status text", () => {
  const step = (over: Partial<StepView>): StepView => ({
    id: "s1",
    tool: "notion",
    action: "a",
    description: "d",
    status: "queued",
    attempts: 0,
    summary: null,
    url: null,
    error: null,
    retry: null,
    undo: null,
    ...over,
  });

  it("says what each state means", () => {
    expect(stepStatusText(step({ status: "queued" }))).toBe("Waiting");
    expect(stepStatusText(step({ status: "running" }))).toBe("Running…");
    expect(stepStatusText(step({ status: "done" }))).toBe("Done");
    expect(stepStatusText(step({ status: "failed" }))).toBe("This step didn't work");
    expect(
      stepStatusText(step({ status: "retrying", retry: { attempt: 2, delaySeconds: 1.6, reason: "x" } })),
    ).toBe("Retrying (attempt 2) in 2s");
  });

  it("lets what happened in the undo override the step's own state", () => {
    expect(stepStatusText(step({ status: "done", undo: "undone" }))).toBe("Undone");
    expect(stepStatusText(step({ status: "done", undo: "failed" }))).toBe("Could not be undone");
  });
});

describe("links", () => {
  it("only offers http(s) links", () => {
    expect(isSafeUrl("https://notion.example/p")).toBe(true);
    expect(isSafeUrl("http://x.example")).toBe(true);
    expect(isSafeUrl("javascript:alert(1)")).toBe(false);
    expect(isSafeUrl("data:text/html,x")).toBe(false);
    expect(isSafeUrl(null)).toBe(false);
  });

  it("lists what a successful run created", () => {
    const view = build([
      ev(1, "plan_ready", PLAN),
      ok(2, "s1", "https://n.example/1"),
      ok(3, "s2", "javascript:alert(1)"),
      finished(4, "completed"),
    ]);
    expect(createdLinks(view)).toEqual([
      { id: "s1", label: "Made s1", tool: "notion", url: "https://n.example/1" },
    ]);
  });

  it("offers nothing as created while running, after a failure or after a rollback", () => {
    const steps = [ev(1, "plan_ready", PLAN), ok(2, "s1", "https://n.example/1")];
    expect(createdLinks(build(steps))).toEqual([]);
    expect(createdLinks(build([...steps, finished(3, "failed")]))).toEqual([]);
    expect(createdLinks(build([...steps, finished(3, "rolled_back")]))).toEqual([]);
  });

  it("never offers an item that was undone, even in a successful finish", () => {
    const view = build([
      ev(1, "plan_ready", PLAN),
      ok(2, "s1", "https://n.example/1"),
      ev(3, "rollback_step", { step_id: "s1", tool: "notion", action: "a", status: "undone", detail: "" }),
      finished(4, "completed_with_warning"),
    ]);
    expect(createdLinks(view)).toEqual([]);
  });

  it("reports items whose undo failed, with a link only if it is safe", () => {
    const view = build([
      ev(1, "plan_ready", PLAN),
      ok(2, "s1", "https://n.example/1"),
      ok(3, "s2", "javascript:x"),
      ev(4, "rollback_step", { step_id: "s1", tool: "notion", action: "a", status: "failed", detail: "" }),
      ev(5, "rollback_step", { step_id: "s2", tool: "drive", action: "a", status: "failed", detail: "" }),
      finished(6, "failed"),
    ]);
    expect(leftBehind(view).map((i) => [i.id, i.url])).toEqual([
      ["s1", "https://n.example/1"],
      ["s2", ""],
    ]);
  });
});

describe("announcements for screen readers", () => {
  it("announces the result when finished", () => {
    const view = build([ev(1, "plan_ready", PLAN), finished(2, "completed")]);
    expect(announcement(view)).toBe("All done. Headline");
  });

  it("announces the phase, then the step being worked on", () => {
    expect(announcement(build([ev(1, "run_started")]))).toBe("Planning your workflow…");
    const running = build([
      ev(1, "plan_ready", PLAN),
      ev(2, "step_started", { step_id: "s1", position: 1, tool: "notion", action: "a", description: "Create the page" }),
    ]);
    expect(announcement(running)).toBe("Running…: Create the page");
  });
});
