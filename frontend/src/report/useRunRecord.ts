import { useEffect, useState } from "react";

import { getRun } from "../api/client";
import type { RunRecord } from "../api/types";

export type RecordState =
  | { status: "waiting" } // not asked for yet, or still being saved
  | { status: "ready"; record: RunRecord }
  | { status: "missing" }; // never became available

// The server sends the run's last event a moment before it has finished saving the report (a real
// run showed this gap), so ask a few times, half a second apart, before giving up.
export const POLL_ATTEMPTS = 10;
export const POLL_DELAY_MS = 500;

// Fetches the stored run (with its report) once `enabled` turns true, i.e. the run has finished.
export function useRunRecord(runId: string, enabled: boolean): RecordState {
  const [state, setState] = useState<RecordState>({ status: "waiting" });

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();

    const attempt = async (count: number): Promise<void> => {
      try {
        const record = await getRun(runId, controller.signal);
        if (cancelled) return;
        if (record.status !== "running" && record.report) {
          setState({ status: "ready", record });
          return;
        }
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") return;
        // Any other failure counts as "not yet": the next attempt may succeed.
      }
      if (cancelled) return;
      if (count + 1 >= POLL_ATTEMPTS) {
        setState({ status: "missing" });
        return;
      }
      timer = setTimeout(() => void attempt(count + 1), POLL_DELAY_MS);
    };
    void attempt(0);

    return () => {
      cancelled = true;
      clearTimeout(timer);
      controller.abort(); // leaving the screen must not leave a request or a timer behind
    };
  }, [runId, enabled]);

  return state;
}
