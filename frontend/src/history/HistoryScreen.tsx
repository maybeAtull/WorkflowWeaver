import { useEffect, useState } from "react";

import { ApiError, SERVER_UNREACHABLE, listRuns } from "../api/client";
import type { RunListItem } from "../api/types";
import { AlertIcon, ArrowRightIcon } from "../components/icons";
import { panel, secondaryButton } from "../components/ui";
import { CHIP_STYLES, exactTime, relativeTime, statusChip } from "../report/format";
import { formatElapsed } from "../run/labels";
import { runHash } from "../run/route";

export const HISTORY_LIMIT = 50;

type Load =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; runs: RunListItem[] };

function Row({ run }: { run: RunListItem }) {
  const chip = statusChip(run.status);
  const title = run.title ?? run.request_preview;
  const detail = run.status === "running" ? "Still running…" : run.headline;
  return (
    <li>
      {/* A plain link to the run's address: the app's address handling opens it. */}
      <a
        href={runHash(run.run_id)}
        className="group flex flex-col gap-3 rounded-2xl border border-white/10 bg-white/[0.03] px-5 py-4 transition hover:border-white/20 hover:bg-white/[0.06] sm:flex-row sm:items-center sm:justify-between"
      >
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-3">
            <span className={`rounded-full border px-2.5 py-0.5 text-xs ${CHIP_STYLES[chip.tone]}`}>
              {chip.label}
            </span>
            <span className="truncate text-[15px] text-slate-100">{title}</span>
          </div>
          {detail && <p className="mt-1.5 text-sm text-slate-400">{detail}</p>}
          {run.title && <p className="mt-1 truncate text-xs text-slate-400">{run.request_preview}</p>}
        </div>
        <div className="flex shrink-0 items-center gap-4 text-sm text-slate-400">
          <div className="text-right">
            <p title={exactTime(run.created_at)}>{relativeTime(run.created_at)}</p>
            {run.duration_seconds !== null && (
              <p className="text-xs text-slate-400">took {formatElapsed(run.duration_seconds)}</p>
            )}
          </div>
          <ArrowRightIcon className="h-4 w-4 text-slate-400 transition group-hover:translate-x-0.5 group-hover:text-slate-200" />
        </div>
      </a>
    </li>
  );
}

// The past runs, newest first. Opening one replays its timeline and shows its report.
export default function HistoryScreen() {
  const [load, setLoad] = useState<Load>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    listRuns(HISTORY_LIMIT, controller.signal)
      .then((runs) => setLoad({ status: "ready", runs }))
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setLoad({
          status: "error",
          message: error instanceof ApiError ? error.message : SERVER_UNREACHABLE,
        });
      });
    return () => controller.abort();
  }, [attempt]);

  const retry = () => {
    setLoad({ status: "loading" });
    setAttempt((n) => n + 1);
  };

  return (
    <div className="mx-auto max-w-3xl space-y-6 pt-4">
      <div>
        <h1 tabIndex={-1} className="text-2xl font-light tracking-tight outline-none sm:text-3xl">
          Past workflows
        </h1>
        <p className="mt-1 text-sm text-slate-400">The latest {HISTORY_LIMIT}, newest first.</p>
      </div>

      {load.status === "loading" && (
        <p role="status" className={`${panel} p-6 text-slate-400`}>
          Loading your workflows…
        </p>
      )}

      {load.status === "error" && (
        <div className={`${panel} flex flex-col items-start gap-4 p-6`}>
          <p role="alert" className="flex items-start gap-3 text-slate-200">
            <AlertIcon className="mt-0.5 h-5 w-5 shrink-0 text-rose-300" />
            {load.message}
          </p>
          <button type="button" className={secondaryButton} onClick={retry}>
            Try again
          </button>
        </div>
      )}

      {load.status === "ready" && load.runs.length === 0 && (
        <div className={`${panel} p-8 text-center`}>
          <p className="text-lg text-slate-200">No workflows yet</p>
          <p className="mt-1 text-sm text-slate-400">Run one from the start screen and it will appear here.</p>
        </div>
      )}

      {load.status === "ready" && load.runs.length > 0 && (
        <ul aria-label="Past workflows" className="space-y-3">
          {load.runs.map((run) => (
            <Row key={run.run_id} run={run} />
          ))}
        </ul>
      )}
    </div>
  );
}
