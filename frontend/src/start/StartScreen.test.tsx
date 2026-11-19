import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../api/client";
import type { TemplateInfo } from "../api/types";
import StartScreen from "./StartScreen";

// The screen talks to the server only through these two functions, so they are all that is faked.
const api = vi.hoisted(() => ({ getTemplates: vi.fn(), startRun: vi.fn() }));
vi.mock("../api/client", async (original) => ({
  ...(await original<typeof import("../api/client")>()),
  getTemplates: api.getTemplates,
  startRun: api.startRun,
}));

const field = (key: string, label: string, kind: "text" | "longtext" | "choice" = "text") => ({
  key,
  label,
  help: `Help for ${label}`,
  kind,
  required: true,
  max_length: kind === "longtext" ? 100 : 20,
  options: kind === "choice" ? ["Low", "High"] : [],
});

const TEMPLATES: TemplateInfo[] = [
  {
    id: "meeting_to_tasks",
    name: "Meeting to tasks",
    description: "Notes into Notion.",
    fields: [field("meeting_title", "Meeting title"), field("notes", "Meeting notes", "longtext")],
    example: { meeting_title: "Weekly sync", notes: "Alex will ship it." },
  },
  {
    id: "incident_response",
    name: "Incident response",
    description: "Record an incident.",
    fields: [field("incident_title", "Incident title"), field("severity", "Severity", "choice")],
    example: { incident_title: "Slow page", severity: "High" },
  },
];

beforeEach(() => {
  api.getTemplates.mockResolvedValue(TEMPLATES);
  api.startRun.mockResolvedValue("run-1");
});
afterEach(() => vi.clearAllMocks());

// Small helpers so each test reads like what a person does.
const click = (name: string | RegExp) => fireEvent.click(screen.getByRole("button", { name }));
const type = (label: string, value: string) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });

async function ready(onStarted = vi.fn()) {
  render(<StartScreen onStarted={onStarted} />);
  await screen.findByRole("button", { name: /Meeting to tasks/ });
  return onStarted;
}

describe("loading the templates", () => {
  it("shows a loading message, then a card per template plus 'Write your own'", async () => {
    render(<StartScreen onStarted={vi.fn()} />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading");
    expect(await screen.findByRole("button", { name: /Meeting to tasks/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Incident response/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Write your own/ })).toBeInTheDocument();
  });

  it("selects the first template and shows its fields", async () => {
    await ready();
    expect(screen.getByRole("button", { name: /Meeting to tasks/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByLabelText("Meeting title")).toBeInTheDocument();
    expect(screen.getByLabelText("Meeting notes")).toBeInTheDocument();
  });

  it("says so plainly when the server is down, and loads again on 'Try again'", async () => {
    api.getTemplates.mockRejectedValueOnce(new ApiError("We can't reach the WorkflowWeaver server.", 0));
    render(<StartScreen onStarted={vi.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "We can't reach the WorkflowWeaver server.",
    );
    click("Try again");
    expect(await screen.findByRole("button", { name: /Meeting to tasks/ })).toBeInTheDocument();
    expect(api.getTemplates).toHaveBeenCalledTimes(2);
  });
});

describe("choosing and filling in", () => {
  it("swaps the form when another card is chosen, and keeps what was typed", async () => {
    await ready();
    type("Meeting title", "Sync");
    click(/Incident response/);
    expect(screen.getByLabelText("Incident title")).toBeInTheDocument();
    expect(screen.queryByLabelText("Meeting title")).not.toBeInTheDocument();
    click(/Meeting to tasks/);
    expect(screen.getByLabelText("Meeting title")).toHaveValue("Sync");
  });

  it("offers a pick-list for a choice field", async () => {
    await ready();
    click(/Incident response/);
    expect(screen.getByRole("option", { name: "High" })).toBeInTheDocument();
  });

  it("fills every field from the example", async () => {
    await ready();
    click("Try with an example");
    expect(screen.getByLabelText("Meeting title")).toHaveValue("Weekly sync");
    expect(screen.getByLabelText("Meeting notes")).toHaveValue("Alex will ship it.");
  });

  it("switches to a single large box for 'Write your own'", async () => {
    await ready();
    click(/Write your own/);
    expect(screen.getByLabelText("Describe your workflow")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Try with an example" })).not.toBeInTheDocument();
  });
});

describe("problems with what was typed", () => {
  it("does not nag before a field is touched, then asks once it is left empty", async () => {
    await ready();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    fireEvent.blur(screen.getByLabelText("Meeting title"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Please fill in “Meeting title”.");
  });

  it("shows every problem on submit, moves focus to the first, and sends nothing", async () => {
    await ready();
    click(/Run workflow/);
    expect(screen.getAllByRole("alert")).toHaveLength(2);
    expect(screen.getByLabelText("Meeting title")).toHaveFocus();
    expect(screen.getByLabelText("Meeting title")).toHaveAttribute("aria-invalid", "true");
    expect(api.startRun).not.toHaveBeenCalled();
  });

  it("shows a counter once a field is nearly full", async () => {
    await ready();
    type("Meeting title", "x".repeat(17));
    expect(screen.getByText("17 / 20")).toBeInTheDocument();
  });

  it("asks for a description in the free-text box", async () => {
    await ready();
    click(/Write your own/);
    click(/Run workflow/);
    expect(screen.getByRole("alert")).toHaveTextContent("Describe the workflow you want to run.");
  });
});

describe("starting a run", () => {
  it("sends the template and its values, then reports the run id", async () => {
    const onStarted = await ready();
    click("Try with an example");
    click(/Run workflow/);
    await waitFor(() => expect(onStarted).toHaveBeenCalledWith("run-1"));
    expect(api.startRun).toHaveBeenCalledWith({
      template_id: "meeting_to_tasks",
      values: { meeting_title: "Weekly sync", notes: "Alex will ship it." },
    });
  });

  it("sends free text on its own", async () => {
    const onStarted = await ready();
    click(/Write your own/);
    type("Describe your workflow", "Do the thing");
    click(/Run workflow/);
    await waitFor(() => expect(onStarted).toHaveBeenCalled());
    expect(api.startRun).toHaveBeenCalledWith({ request: "Do the thing" });
  });

  it("sends the demo option, in the backend's format, when one is chosen", async () => {
    await ready();
    click("Try with an example");
    fireEvent.click(screen.getByLabelText(/shows rollback/));
    click(/Run workflow/);
    await waitFor(() => expect(api.startRun).toHaveBeenCalled());
    expect(api.startRun.mock.calls[0][0]).toMatchObject({ simulate: "s2:fatal" });
  });

  it("sends only one request when the button is pressed twice", async () => {
    let finish: (id: string) => void = () => {};
    api.startRun.mockReturnValue(new Promise<string>((resolve) => (finish = resolve)));
    const onStarted = await ready();
    click("Try with an example");
    const run = screen.getByRole("button", { name: /Run workflow/ });
    fireEvent.click(run);
    fireEvent.click(run);
    expect(await screen.findByRole("button", { name: "Starting…" })).toBeDisabled();
    finish("run-9");
    await waitFor(() => expect(onStarted).toHaveBeenCalledWith("run-9"));
    expect(api.startRun).toHaveBeenCalledTimes(1);
  });

  it("shows the server's own message when it refuses, keeps the typed text and allows another try", async () => {
    api.startRun.mockRejectedValueOnce(
      new ApiError("Other workflows are still running. Please try again in a minute.", 429),
    );
    const onStarted = await ready();
    click("Try with an example");
    click(/Run workflow/);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Other workflows are still running. Please try again in a minute.",
    );
    expect(onStarted).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Meeting title")).toHaveValue("Weekly sync");
    click(/Run workflow/);
    await waitFor(() => expect(onStarted).toHaveBeenCalledWith("run-1"));
  });
});
