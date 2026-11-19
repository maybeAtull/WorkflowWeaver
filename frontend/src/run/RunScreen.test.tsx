import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FakeEventSource, ev } from "../test/fakeEventSource";
import RunScreen from "./RunScreen";

const PLAN = {
  steps: [
    { id: "s1", tool: "notion", action: "create_page", description: "Create the Notion page" },
    { id: "s2", tool: "drive", action: "create_text_file", description: "Save a copy in Drive" },
    { id: "s3", tool: "notifier", action: "send", description: "Tell the team" },
  ],
};

// A finished run also asks the server for its stored report (covered in ReportPanel.test.tsx);
// here that request simply never answers, so these tests stay about the timeline.
vi.mock("../api/client", async (original) => ({
  ...(await original<typeof import("../api/client")>()),
  getRun: vi.fn(() => new Promise(() => {})),
}));

const latest = () => FakeEventSource.latest();
const send = (...events: ReturnType<typeof ev>[]) =>
  act(() => {
    for (const event of events) latest().send(event);
  });

const started = (seq: number, id: string, position: number, tool: string, description: string) =>
  ev(seq, "step_started", { step_id: id, position, tool, action: "x", description });
const done = (seq: number, id: string, summary: string, url: string | null = null, attempts = 1) =>
  ev(seq, "step_succeeded", { step_id: id, summary, url, attempts });
const finished = (seq: number, status: string, headline: string, error: string | null = null) =>
  ev(seq, "run_completed", { status, error, headline });

function renderRun(onStartAnother = vi.fn()) {
  render(<RunScreen runId="r1" onStartAnother={onStartAnother} />);
  act(() => latest().open());
  return onStartAnother;
}

beforeEach(() => {
  FakeEventSource.reset();
  vi.stubGlobal("EventSource", FakeEventSource);
});
afterEach(() => vi.unstubAllGlobals());

describe("while the run is going", () => {
  it("shows a planning banner, then every step as waiting once the plan arrives", () => {
    renderRun();
    expect(screen.getByRole("heading", { name: "Your workflow is running" })).toBeInTheDocument();
    send(ev(1, "run_started", { request: "x" }));
    expect(screen.getByText("Planning your workflow…", { selector: "p[role=status]" })).toBeInTheDocument();
    send(ev(2, "plan_ready", PLAN));
    const steps = within(screen.getByRole("list", { name: "Workflow steps" })).getAllByRole("listitem");
    expect(steps).toHaveLength(3);
    for (const step of steps) expect(step).toHaveTextContent("Waiting");
    expect(screen.getByText("Live")).toBeInTheDocument();
  });

  it("marks the working step, with its tool, and the finished one with a link", () => {
    renderRun();
    send(
      ev(1, "run_started"),
      ev(2, "plan_ready", PLAN),
      started(3, "s1", 1, "notion", "Create the Notion page"),
      done(4, "s1", "Created page “Falcon”", "https://notion.example/p"),
      started(5, "s2", 2, "drive", "Save a copy in Drive"),
    );
    const [first, second] = screen.getAllByRole("listitem");
    expect(first).toHaveTextContent("Done");
    expect(within(first).getByRole("link", { name: /Created page “Falcon”/ })).toHaveAttribute(
      "href",
      "https://notion.example/p",
    );
    expect(second).toHaveTextContent("Running…");
    expect(second).toHaveTextContent("Google Drive");
  });

  it("shows a retry with its reason, then 'after 3 attempts' once it works", () => {
    renderRun();
    send(
      ev(1, "run_started"),
      ev(2, "plan_ready", PLAN),
      started(3, "s2", 2, "drive", "Save a copy in Drive"),
      ev(4, "step_retry", { step_id: "s2", attempt: 2, delay_seconds: 2, reason: "Drive was busy" }),
    );
    const row = screen.getAllByRole("listitem")[1];
    expect(row).toHaveTextContent("Retrying (attempt 2) in 2s");
    expect(row).toHaveTextContent("Reason: Drive was busy");
    send(done(5, "s2", "Saved", null, 3));
    expect(screen.getAllByRole("listitem")[1]).toHaveTextContent("Done after 3 attempts");
  });

  it("shows the checks before the team is told", () => {
    renderRun();
    send(
      ev(1, "run_started"),
      ev(2, "plan_ready", PLAN),
      ev(3, "verify_started", { total: 2 }),
      ev(4, "verify_step", { step_id: "s1", tool: "notion", action: "x", status: "verified", detail: "Page found" }),
      ev(5, "verify_step", { step_id: "s2", tool: "drive", action: "x", status: "unconfirmed", detail: "Drive was slow" }),
    );
    expect(screen.getByText("Checking what was created before telling the team…", { selector: "p[role=status]" })).toBeInTheDocument();
    const checks = screen.getByRole("region", { name: "Checks" });
    expect(checks).toHaveTextContent("Confirmed");
    expect(checks).toHaveTextContent("Page found");
    expect(checks).toHaveTextContent("Couldn't confirm");
  });

  it("announces progress to screen readers", () => {
    renderRun();
    send(ev(1, "run_started"), ev(2, "plan_ready", PLAN), started(3, "s1", 1, "notion", "Create the Notion page"));
    expect(document.querySelector("[aria-live=polite]")).toHaveTextContent("Running…: Create the Notion page");
  });
});

describe("a finished run", () => {
  const success = [
    ev(1, "run_started"),
    ev(2, "plan_ready", PLAN),
    started(3, "s1", 1, "notion", "Create the Notion page"),
    done(4, "s1", "Created page", "https://notion.example/p"),
    started(5, "s2", 2, "drive", "Save a copy in Drive"),
    done(6, "s2", "Saved a copy", "https://drive.example/f"),
    started(7, "s3", 3, "notifier", "Tell the team"),
    done(8, "s3", "Message sent"),
    finished(9, "completed", "Done: all 3 steps finished."),
  ];

  it("shows the headline and links to what was created, and lets the user start again", () => {
    const onStartAnother = renderRun();
    send(...success);
    expect(screen.getByRole("heading", { name: "Your workflow has finished" })).toBeInTheDocument();
    const result = screen.getByRole("region", { name: "Result" });
    expect(result).toHaveTextContent("All done");
    expect(result).toHaveTextContent("Done: all 3 steps finished.");
    const items = within(screen.getByRole("list", { name: "Created items" }));
    expect(items.getByRole("link", { name: /Created page/ })).toHaveAttribute("href", "https://notion.example/p");
    expect(items.getByRole("link", { name: /Saved a copy/ })).toHaveAttribute("href", "https://drive.example/f");
    expect(items.queryByText(/Message sent/)).not.toBeInTheDocument(); // nothing to open for the chat message
    fireEvent.click(screen.getByRole("button", { name: "Start another workflow" }));
    expect(onStartAnother).toHaveBeenCalledTimes(1);
  });

  it("stops the live indicator and closes the stream", () => {
    renderRun();
    send(...success);
    expect(screen.queryByText("Live")).not.toBeInTheDocument();
    expect(latest().closed).toBe(true);
  });

  it("explains a rollback: steps marked undone, no links, 'Nothing was changed'", () => {
    renderRun();
    send(
      ev(1, "run_started"),
      ev(2, "plan_ready", PLAN),
      started(3, "s1", 1, "notion", "Create the Notion page"),
      done(4, "s1", "Created page", "https://notion.example/p"),
      started(5, "s2", 2, "drive", "Save a copy in Drive"),
      ev(6, "step_failed", { step_id: "s2", error: "No access to the folder", attempts: 1 }),
      ev(7, "rollback_started", { total: 1, to_undo: 1 }),
    );
    expect(screen.getByText("Something went wrong. Undoing what was created…", { selector: "p[role=status]" })).toBeInTheDocument();
    send(
      ev(8, "rollback_step", { step_id: "s1", tool: "notion", action: "x", status: "undone", detail: "Moved to the trash" }),
      finished(9, "rolled_back", "Stopped. Everything that had been created was undone.", "No access to the folder"),
    );
    const [first, second, third] = screen.getAllByRole("listitem");
    expect(first).toHaveTextContent("Undone");
    expect(first).toHaveTextContent("Moved to the trash");
    expect(within(first).queryByRole("link")).not.toBeInTheDocument();
    expect(second).toHaveTextContent("This step didn't work");
    expect(second).toHaveTextContent("Details: No access to the folder");
    expect(third).toHaveTextContent("Waiting");
    const result = screen.getByRole("region", { name: "Result" });
    expect(result).toHaveTextContent("Nothing was changed");
    expect(screen.queryByRole("list", { name: "Created items" })).not.toBeInTheDocument();
    expect(result).toHaveTextContent("Technical details");
  });

  it("tells the user what to delete by hand when an undo failed", () => {
    renderRun();
    send(
      ev(1, "run_started"),
      ev(2, "plan_ready", PLAN),
      started(3, "s1", 1, "notion", "Create the Notion page"),
      done(4, "s1", "Created page", "https://notion.example/p"),
      ev(5, "step_failed", { step_id: "s2", error: "boom", attempts: 1 }),
      ev(6, "rollback_step", { step_id: "s1", tool: "notion", action: "x", status: "failed", detail: "Notion unreachable" }),
      finished(7, "failed", "Stopped, and one item could not be undone."),
    );
    const result = screen.getByRole("region", { name: "Result" });
    expect(result).toHaveTextContent("These items could not be removed. Please delete them by hand:");
    expect(within(result).getByRole("link", { name: "open" })).toHaveAttribute("href", "https://notion.example/p");
    expect(screen.getAllByRole("listitem")[0]).toHaveTextContent("Could not be undone");
  });

  it("shows a warning outcome with its links", () => {
    renderRun();
    send(
      ev(1, "run_started"),
      ev(2, "plan_ready", PLAN),
      done(3, "s1", "Created page", "https://notion.example/p"),
      finished(4, "completed_with_warning", "Done, but the chat message could not be sent."),
    );
    const result = screen.getByRole("region", { name: "Result" });
    expect(result).toHaveTextContent("Done, with a warning");
    expect(within(result).getByRole("link", { name: /Created page/ })).toBeInTheDocument();
  });

  it("never renders an unsafe link", () => {
    renderRun();
    send(
      ev(1, "run_started"),
      ev(2, "plan_ready", PLAN),
      done(3, "s1", "Created page", "javascript:alert(1)"),
      finished(4, "completed", "Done"),
    );
    expect(document.querySelector("a[href^='javascript']")).toBeNull();
  });
});

describe("the connection", () => {
  it("shows a reconnecting notice that goes away when the connection returns", () => {
    renderRun();
    send(ev(1, "run_started"), ev(2, "plan_ready", PLAN));
    act(() => latest().fail());
    expect(screen.getByText(/Connection lost\. Reconnecting/)).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(3); // what was already shown stays
    act(() => latest().open());
    expect(screen.queryByText(/Connection lost/)).not.toBeInTheDocument();
  });

  it("does not claim the run is running or starting once the connection is lost for good", () => {
    renderRun();
    act(() => latest().fail(true));
    expect(screen.getByRole("heading", { name: "We can't show this run right now" })).toBeInTheDocument();
    expect(screen.queryByText("Starting…")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Your workflow is running" })).not.toBeInTheDocument();
  });

  it("gives up with a message and 'Try again', which starts over from the server's replay", () => {
    renderRun();
    send(ev(1, "run_started"), ev(2, "plan_ready", PLAN));
    act(() => latest().fail(true));
    expect(screen.getByRole("alert")).toHaveTextContent("We couldn't load this run's progress");
    const before = FakeEventSource.instances.length;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(FakeEventSource.instances.length).toBe(before + 1);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    act(() => latest().open());
    send(ev(1, "run_started"), ev(2, "plan_ready", PLAN), finished(3, "completed", "Done"));
    expect(screen.getByRole("region", { name: "Result" })).toBeInTheDocument();
  });
});
