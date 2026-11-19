import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import { FakeEventSource } from "./test/fakeEventSource";

// The app only fetches the health check and the template list; answer both with an empty server.
function stubFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) =>
      Promise.resolve(
        new Response(JSON.stringify(url.endsWith("/templates") ? [] : { status: "ok" }), { status: 200 }),
      ),
    ),
  );
}

beforeEach(() => {
  FakeEventSource.reset();
  vi.stubGlobal("EventSource", FakeEventSource);
  stubFetch();
  window.history.replaceState(null, "", "/");
});
afterEach(() => vi.unstubAllGlobals());

describe("which screen the address selects", () => {
  it("shows the start screen at the plain address", async () => {
    render(<App />);
    expect(await screen.findByRole("heading", { name: /Turn a request into finished work/ })).toBeInTheDocument();
  });

  it("opens a run straight from #run=<id> (so a refresh stays on the run)", () => {
    window.history.replaceState(null, "", "/#run=abc123");
    render(<App />);
    expect(screen.getByRole("heading", { name: "Your workflow is running" })).toBeInTheDocument();
    expect(FakeEventSource.latest().url).toContain("/api/runs/abc123/events");
  });

  it("returns to the start screen and clears the run from the address", async () => {
    window.history.replaceState(null, "", "/#run=abc123");
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "WorkflowWeaver home" }));
    expect(await screen.findByRole("heading", { name: /Turn a request into finished work/ })).toBeInTheDocument();
    expect(window.location.hash).toBe("");
    expect(FakeEventSource.latest().closed).toBe(true); // the run's stream is closed on the way out
  });

  it("follows the browser's Back and Forward buttons", async () => {
    render(<App />);
    await screen.findByRole("heading", { name: /Turn a request/ });
    act(() => {
      window.history.pushState(null, "", "/#run=abc123");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(screen.getByRole("heading", { name: "Your workflow is running" })).toBeInTheDocument();
    act(() => {
      window.history.pushState(null, "", "/");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(await screen.findByRole("heading", { name: /Turn a request/ })).toBeInTheDocument();
  });

  it("ignores a hash that is not a run", async () => {
    window.history.replaceState(null, "", "/#run=<script>");
    render(<App />);
    expect(await screen.findByRole("heading", { name: /Turn a request/ })).toBeInTheDocument();
  });
});
