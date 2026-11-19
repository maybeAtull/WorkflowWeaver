import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../api/client";
import type { RunListItem } from "../api/types";
import HistoryScreen, { HISTORY_LIMIT } from "./HistoryScreen";

const api = vi.hoisted(() => ({ listRuns: vi.fn() }));
vi.mock("../api/client", async (original) => ({
  ...(await original<typeof import("../api/client")>()),
  listRuns: api.listRuns,
}));

const minutesAgo = (m: number) => new Date(Date.now() - m * 60_000).toISOString();

const RUNS: RunListItem[] = [
  {
    run_id: "aaa111",
    status: "completed",
    created_at: minutesAgo(5),
    finished_at: minutesAgo(4),
    request_preview: "Please turn these meeting notes into a Notion page",
    headline: "Done: all 3 steps finished.",
    title: "Project Falcon weekly sync",
    duration_seconds: 20.3,
  },
  {
    run_id: "bbb222",
    status: "rolled_back",
    created_at: minutesAgo(90),
    finished_at: minutesAgo(89),
    request_preview: "Create a sprint page",
    headline: "Stopped. Everything that had been created was undone.",
    title: null,
    duration_seconds: 7.4,
  },
  {
    run_id: "ccc333",
    status: "running",
    created_at: minutesAgo(0),
    finished_at: null,
    request_preview: "A run in progress",
    headline: null,
    title: null,
    duration_seconds: null,
  },
];

beforeEach(() => api.listRuns.mockResolvedValue(RUNS));
afterEach(() => vi.clearAllMocks());

describe("the history page", () => {
  it("asks for the latest runs and shows a loading message first", async () => {
    render(<HistoryScreen />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading");
    await screen.findByRole("list", { name: "Past workflows" });
    expect(api.listRuns).toHaveBeenCalledWith(HISTORY_LIMIT, expect.anything());
  });

  it("lists each run with its status, title, headline, time and duration", async () => {
    render(<HistoryScreen />);
    const items = within(await screen.findByRole("list", { name: "Past workflows" })).getAllByRole("listitem");
    expect(items).toHaveLength(3);
    expect(items[0]).toHaveTextContent("Completed");
    expect(items[0]).toHaveTextContent("Project Falcon weekly sync");
    expect(items[0]).toHaveTextContent("Done: all 3 steps finished.");
    expect(items[0]).toHaveTextContent("5 min ago");
    expect(items[0]).toHaveTextContent("took 20s");
    expect(items[1]).toHaveTextContent("Undone");
    expect(items[1]).toHaveTextContent("Create a sprint page"); // no title: the request stands in
    expect(items[1]).toHaveTextContent("1 h ago");
  });

  it("shows a run that is still going without a result", async () => {
    render(<HistoryScreen />);
    const items = within(await screen.findByRole("list", { name: "Past workflows" })).getAllByRole("listitem");
    expect(items[2]).toHaveTextContent("Running");
    expect(items[2]).toHaveTextContent("Still running…");
    expect(items[2]).not.toHaveTextContent("took");
  });

  it("links every row to its run's address", async () => {
    render(<HistoryScreen />);
    const links = within(await screen.findByRole("list", { name: "Past workflows" })).getAllByRole("link");
    expect(links.map((l) => l.getAttribute("href"))).toEqual(["#run=aaa111", "#run=bbb222", "#run=ccc333"]);
  });

  it("has a friendly empty state", async () => {
    api.listRuns.mockResolvedValue([]);
    render(<HistoryScreen />);
    expect(await screen.findByText("No workflows yet")).toBeInTheDocument();
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
  });

  it("explains a failure plainly and loads again on 'Try again'", async () => {
    api.listRuns.mockRejectedValueOnce(new ApiError("We can't reach the WorkflowWeaver server.", 0));
    render(<HistoryScreen />);
    expect(await screen.findByRole("alert")).toHaveTextContent("We can't reach the WorkflowWeaver server.");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("list", { name: "Past workflows" })).toBeInTheDocument();
    expect(api.listRuns).toHaveBeenCalledTimes(2);
  });
});
