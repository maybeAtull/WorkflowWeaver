import { useState } from "react";

import { panel } from "../components/ui";
import ReportPanel from "../report/ReportPanel";
import ConnectionBanner from "./ConnectionBanner";
import OutcomeCard from "./OutcomeCard";
import Timeline from "./Timeline";
import { announcement, formatElapsed, phaseMessage } from "./labels";
import { elapsedSeconds } from "./model";
import { useNow } from "./useNow";
import { useRunStream } from "./useRunStream";

interface Props {
  runId: string;
  onStartAnother: () => void;
}

function LiveRun({ runId, onStartAnother, onTryAgain }: Props & { onTryAgain: () => void }) {
  const { view, connection } = useRunStream(runId);
  const finished = view.phase === "finished";
  const now = useNow(!finished);

  // While running, count up from the first event; afterwards show how long the run really took.
  const seconds = finished
    ? elapsedSeconds(view)
    : view.startedAt
      ? Math.max(0, (now - Date.parse(view.startedAt)) / 1000)
      : 0;
  // After giving up on the connection we no longer know the run's state, so don't claim one.
  const lost = connection === "failed" && !finished;
  const banner = lost ? null : phaseMessage(view.phase);
  const heading = finished
    ? "Your workflow has finished"
    : lost
      ? "We can't show this run right now"
      : "Your workflow is running";

  return (
    <div className="mx-auto max-w-3xl space-y-6 pt-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-light tracking-tight sm:text-3xl">{heading}</h1>
          <p className="mt-1 text-sm text-slate-500">
            Run <span className="font-mono text-slate-400">{runId}</span>
          </p>
        </div>
        <div className="flex items-center gap-3 text-sm text-slate-300">
          {view.startedAt && <span aria-label="Elapsed time">{formatElapsed(seconds)}</span>}
          {!finished && connection === "live" && (
            <span className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs">
              <span className="h-2 w-2 rounded-full bg-emerald-400" aria-hidden="true" />
              Live
            </span>
          )}
        </div>
      </div>

      {/* Read out by screen readers when something changes; the timer is deliberately not in here. */}
      <p className="sr-only" aria-live="polite">
        {lost ? "" : announcement(view)}
      </p>

      <ConnectionBanner connection={connection} onTryAgain={onTryAgain} />

      {banner && !finished && (
        <p className={`${panel} px-5 py-4 text-slate-200`} role="status">
          {banner}
        </p>
      )}

      <Timeline view={view} />
      <OutcomeCard view={view} onStartAnother={onStartAnother} />
      <ReportPanel runId={runId} finished={finished} />
    </div>
  );
}

// Follows one run. "Try again" reopens the stream from the start; the server replays everything
// that already happened, so no progress is lost.
export default function RunScreen({ runId, onStartAnother }: Props) {
  const [attempt, setAttempt] = useState(0);
  return (
    <LiveRun
      key={`${runId}:${attempt}`}
      runId={runId}
      onStartAnother={onStartAnother}
      onTryAgain={() => setAttempt((n) => n + 1)}
    />
  );
}
