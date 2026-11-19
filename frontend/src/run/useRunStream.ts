// Follows one run live: opens the event stream, feeds each event to the reducer, and reports
// whether the connection is healthy.
//
// Use it as `useRunStream(runId)` inside a component that is given `key={runId}`, so a different
// run always starts from a clean state (this keeps the hook free of reset logic).
//
// The browser's EventSource does the hard part: when the connection drops it reconnects by
// itself and sends the id of the last event it saw, so the server resumes without repeats.
// What this hook adds is the ending (close at `run_completed`, or it would reconnect forever)
// and giving up when the server stays unreachable.
import { useEffect, useReducer } from "react";

import { eventsUrl } from "../api/client";
import { RUN_EVENT_TYPES, type RunEvent } from "../api/types";
import { type RunView, initialRunView, reduceRun } from "./model";

export type Connection =
  | "connecting" // opening the stream for the first time
  | "live" // connected and receiving
  | "reconnecting" // dropped; the browser is retrying
  | "closed" // the run finished and the stream ended normally
  | "failed"; // gave up: the server stayed unreachable, or the run does not exist

// Consecutive failed attempts before giving up (the browser waits a few seconds between tries).
export const MAX_RECONNECT_FAILURES = 8;

interface State {
  view: RunView;
  connection: Connection;
}

type Action =
  | { kind: "event"; event: RunEvent }
  | { kind: "open" }
  | { kind: "error"; giveUp: boolean };

function reducer(state: State, action: Action): State {
  switch (action.kind) {
    case "event": {
      const view = reduceRun(state.view, action.event);
      return {
        view,
        // A completed run closes the stream; after that, errors no longer matter.
        connection: view.phase === "finished" ? "closed" : state.connection,
      };
    }
    case "open":
      // A finished run's stream closing is not a new connection.
      return state.connection === "closed" ? state : { ...state, connection: "live" };
    case "error":
      if (state.connection === "closed") return state;
      return { ...state, connection: action.giveUp ? "failed" : "reconnecting" };
  }
}

export interface RunStream {
  view: RunView;
  connection: Connection;
}

export function useRunStream(runId: string): RunStream {
  const [state, dispatch] = useReducer(reducer, undefined, () => ({
    view: initialRunView(runId),
    connection: "connecting" as Connection,
  }));

  useEffect(() => {
    const source = new EventSource(eventsUrl(runId));
    let failures = 0; // consecutive; a successful connection resets it

    const onEvent = (message: MessageEvent<string>) => {
      const event = JSON.parse(message.data) as RunEvent;
      dispatch({ kind: "event", event });
      if (event.type === "run_completed") source.close();
    };
    // The stream names each message by its type, so every type needs its own listener.
    for (const type of RUN_EVENT_TYPES) {
      source.addEventListener(type, onEvent as EventListener);
    }
    source.onopen = () => {
      failures = 0;
      dispatch({ kind: "open" });
    };
    source.onerror = () => {
      failures += 1;
      // readyState CLOSED means the browser will not retry (for example, an unknown run);
      // otherwise it is already trying again, and we only give up after repeated failures.
      const giveUp =
        source.readyState === EventSource.CLOSED || failures >= MAX_RECONNECT_FAILURES;
      dispatch({ kind: "error", giveUp });
      if (giveUp) source.close(); // stops the browser's own retrying too
    };

    return () => source.close(); // leaving the screen must not leave a connection open
  }, [runId]);

  return state;
}
