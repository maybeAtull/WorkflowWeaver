// Robustness drills for the screens (Task 6.6): a very long unbroken word and a ten-step run.
//
// jsdom has no layout, so these check the thing that decides the outcome in a real browser: does
// the element holding user or LLM text allow a long unbroken word to wrap (break-words / break-all /
// truncate), on the element itself or on a container around it? Without that, such a word pushes
// the page sideways instead of wrapping. Gaps found are marked `it.fails` with their finding
// number: the test passes while the gap exists and starts failing the moment it is fixed.
import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FakeEventSource, ev } from "./test/fakeEventSource";
import RunScreen from "./run/RunScreen";

vi.mock("./api/client", async (original) => ({
  ...(await original<typeof import("./api/client")>()),
  getRun: vi.fn(() => new Promise(() => {})), // the report request is not what is under test
}));

const LONG = "x".repeat(300);
const WRAPS = /\b(break-words|break-all|truncate|overflow-wrap-anywhere)\b/;

// True when the element, or one of its ancestors below <body>, lets a long word wrap.
function wraps(element: HTMLElement): boolean {
  for (let node: HTMLElement | null = element; node && node !== document.body; node = node.parentElement) {
    if (WRAPS.test(node.className)) return true;
  }
  return false;
}

const latest = () => FakeEventSource.latest();
const send = (...events: ReturnType<typeof ev>[]) =>
  act(() => {
    for (const event of events) latest().send(event);
  });

beforeEach(() => {
  FakeEventSource.reset();
  vi.stubGlobal("EventSource", FakeEventSource);
});
afterEach(() => vi.unstubAllGlobals());

function open() {
  render(<RunScreen runId="r1" onStartAnother={() => {}} />);
  act(() => latest().open());
}

const step = (n: number, description: string) => ({
  id: `s${n}`,
  tool: n === 10 ? "notifier" : "notion",
  action: "x",
  description,
});

describe("a ten-step run", () => {
  it("shows every step, in order, and finishes cleanly", () => {
    open();
    const steps = Array.from({ length: 10 }, (_, i) => step(i + 1, `Step number ${i + 1}`));
    send(ev(1, "run_started"), ev(2, "plan_ready", { steps }));
    for (let n = 1; n <= 10; n++) {
      send(
        ev(2 + n * 2 - 1, "step_started", { step_id: `s${n}`, position: n, tool: "x", action: "x", description: `Step number ${n}` }),
        ev(2 + n * 2, "step_succeeded", { step_id: `s${n}`, summary: `Made ${n}`, url: null, attempts: 1 }),
      );
    }
    send(ev(23, "run_completed", { status: "completed", error: null, headline: "Done" }));
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(10);
    expect(items[9]).toHaveTextContent("Step number 10");
    expect(screen.getByRole("region", { name: "Result" })).toHaveTextContent("All done");
  });
});

describe("a very long unbroken word", () => {
  beforeEach(() => {
    open();
    send(
      ev(1, "run_started"),
      ev(2, "plan_ready", { steps: [step(1, `Create ${LONG}`), step(2, "Save a copy")] }),
      ev(3, "step_started", { step_id: "s1", position: 1, tool: "notion", action: "x", description: "d" }),
      ev(4, "step_succeeded", { step_id: "s1", summary: `Created ${LONG}`, url: null, attempts: 1 }),
      ev(5, "step_started", { step_id: "s2", position: 2, tool: "drive", action: "x", description: "d" }),
      ev(6, "step_failed", { step_id: "s2", error: `Failed ${LONG}`, attempts: 1 }),
      ev(7, "run_completed", { status: "failed", error: `Failed ${LONG}`, headline: `Stopped ${LONG}` }),
    );
  });

  it("still renders everything", () => {
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(screen.getByRole("region", { name: "Result" })).toBeInTheDocument();
  });

  it("has each long text on screen (so the checks below test wrapping, not a failed lookup)", () => {
    for (const text of [`Create ${LONG}`, `Created ${LONG}`, `Details: Failed ${LONG}`, `Stopped ${LONG}`]) {
      expect(screen.getByText(text)).toBeInTheDocument();
    }
  });

  // Finding F6 (fixed): the step rows and the outcome headline now allow wrapping.
  it("wraps in a step's description", () => {
    expect(wraps(screen.getByText(`Create ${LONG}`))).toBe(true);
  });

  it("wraps in a step's result line", () => {
    expect(wraps(screen.getByText(`Created ${LONG}`))).toBe(true);
  });

  it("wraps in a failed step's details", () => {
    expect(wraps(screen.getByText(`Details: Failed ${LONG}`))).toBe(true);
  });

  it("wraps in the outcome headline", () => {
    expect(wraps(screen.getByText(`Stopped ${LONG}`))).toBe(true);
  });

  it("wraps in the technical details, which already allow it", () => {
    expect(wraps(screen.getByText(`Failed ${LONG}`, { selector: "details p" }))).toBe(true);
  });
});
