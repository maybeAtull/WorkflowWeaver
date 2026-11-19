import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { eventsUrl } from "../api/client";
import { FakeEventSource, ev } from "../test/fakeEventSource";
import { MAX_RECONNECT_FAILURES, useRunStream } from "./useRunStream";

const latest = () => FakeEventSource.latest();

beforeEach(() => {
  FakeEventSource.reset();
  vi.stubGlobal("EventSource", FakeEventSource);
});
afterEach(() => vi.unstubAllGlobals());

describe("useRunStream", () => {
  it("opens the run's event stream and starts out connecting", () => {
    const { result } = renderHook(() => useRunStream("r1"));
    expect(latest().url).toBe(eventsUrl("r1"));
    expect(result.current.connection).toBe("connecting");
    expect(result.current.view.phase).toBe("starting");
  });

  it("becomes live when the connection opens", () => {
    const { result } = renderHook(() => useRunStream("r1"));
    act(() => latest().open());
    expect(result.current.connection).toBe("live");
  });

  it("turns each event into the timeline", () => {
    const { result } = renderHook(() => useRunStream("r1"));
    act(() => {
      latest().open();
      latest().send(ev(1, "run_started", { request: "Do it" }));
      latest().send(
        ev(2, "plan_ready", {
          steps: [{ id: "s1", tool: "notion", action: "create_page", description: "Create" }],
        }),
      );
    });
    expect(result.current.view.request).toBe("Do it");
    expect(result.current.view.steps).toHaveLength(1);
    expect(result.current.view.phase).toBe("running");
  });

  it("closes the stream when the run completes, so it cannot reconnect forever", () => {
    const { result } = renderHook(() => useRunStream("r1"));
    act(() => {
      latest().open();
      latest().send(ev(1, "run_completed", { status: "completed", error: null, headline: "Done" }));
    });
    expect(latest().closed).toBe(true);
    expect(result.current.connection).toBe("closed");
    expect(result.current.view.outcome?.headline).toBe("Done");
  });

  it("ignores the error a closing stream can raise after the run finished", () => {
    const { result } = renderHook(() => useRunStream("r1"));
    act(() => {
      latest().send(ev(1, "run_completed", { status: "failed", error: "x", headline: "H" }));
      latest().fail(true);
    });
    expect(result.current.connection).toBe("closed");
  });

  it("shows reconnecting while the browser retries, then live again", () => {
    const { result } = renderHook(() => useRunStream("r1"));
    act(() => latest().open());
    act(() => latest().fail());
    expect(result.current.connection).toBe("reconnecting");
    act(() => latest().open());
    expect(result.current.connection).toBe("live");
  });

  it("gives up when the browser will not retry (for example, an unknown run)", () => {
    const { result } = renderHook(() => useRunStream("r1"));
    act(() => latest().fail(true));
    expect(result.current.connection).toBe("failed");
    expect(latest().closed).toBe(true);
  });

  it("gives up and stops retrying after too many failures in a row", () => {
    const { result } = renderHook(() => useRunStream("r1"));
    for (let i = 1; i < MAX_RECONNECT_FAILURES; i++) {
      act(() => latest().fail());
      expect(result.current.connection).toBe("reconnecting");
    }
    act(() => latest().fail());
    expect(result.current.connection).toBe("failed");
    expect(latest().closed).toBe(true);
  });

  it("starts counting again after a successful reconnect", () => {
    const { result } = renderHook(() => useRunStream("r1"));
    for (let i = 1; i < MAX_RECONNECT_FAILURES; i++) act(() => latest().fail());
    act(() => latest().open());
    for (let i = 1; i < MAX_RECONNECT_FAILURES; i++) act(() => latest().fail());
    expect(result.current.connection).toBe("reconnecting");
  });

  it("does not double-count an event repeated after a reconnect", () => {
    const { result } = renderHook(() => useRunStream("r1"));
    act(() => {
      latest().send(ev(1, "run_started"));
      latest().send(ev(1, "run_started"));
    });
    expect(result.current.view.lastSeq).toBe(1);
  });

  it("closes its connection when the screen is left", () => {
    const { unmount } = renderHook(() => useRunStream("r1"));
    const source = latest();
    unmount();
    expect(source.closed).toBe(true);
  });
});
