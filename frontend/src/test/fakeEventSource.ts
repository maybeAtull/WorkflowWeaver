// A stand-in for the browser's EventSource that a test can drive by hand, plus a helper to build
// the events the server sends. Install it with vi.stubGlobal("EventSource", FakeEventSource).
import type { RunEvent } from "../api/types";

export class FakeEventSource {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSED = 2;
  static instances: FakeEventSource[] = [];

  readyState = FakeEventSource.CONNECTING;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  private listeners = new Map<string, ((message: MessageEvent<string>) => void)[]>();

  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: (message: MessageEvent<string>) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  close() {
    this.closed = true;
    this.readyState = FakeEventSource.CLOSED;
  }

  // Test controls:
  open() {
    this.readyState = FakeEventSource.OPEN;
    this.onopen?.();
  }

  send(event: RunEvent) {
    const message = { data: JSON.stringify(event) } as MessageEvent<string>;
    for (const listener of this.listeners.get(event.type) ?? []) listener(message);
  }

  // The browser is retrying by itself (readyState CONNECTING) unless `final` is set.
  fail(final = false) {
    this.readyState = final ? FakeEventSource.CLOSED : FakeEventSource.CONNECTING;
    this.onerror?.();
  }

  static reset() {
    FakeEventSource.instances = [];
  }

  static latest(): FakeEventSource {
    return FakeEventSource.instances[FakeEventSource.instances.length - 1];
  }
}

export function ev(seq: number, type: RunEvent["type"], data: object = {}): RunEvent {
  return { run_id: "r1", seq, type, data, timestamp: "2026-01-01T12:00:00Z" } as RunEvent;
}
