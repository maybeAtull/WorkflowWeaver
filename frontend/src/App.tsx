import { useEffect, useState } from "react";

import BackendStatus from "./components/BackendStatus";
import { HistoryIcon, LogoIcon } from "./components/icons";
import HistoryScreen from "./history/HistoryScreen";
import RunScreen from "./run/RunScreen";
import { HISTORY_HASH, type Route, parseRoute, runHash } from "./run/route";
import StartScreen from "./start/StartScreen";

// The page shows the start screen, one run, or the history. Which one is kept in the address
// (see run/route.ts), so refreshing stays put and the browser's Back button works.
const routeFromAddress = (): Route => parseRoute(window.location.hash);

export default function App() {
  const [route, setRoute] = useState<Route>(routeFromAddress);

  useEffect(() => {
    // Clicking a link such as #run=<id> changes the address by itself; follow it.
    const sync = () => setRoute(routeFromAddress());
    window.addEventListener("popstate", sync);
    window.addEventListener("hashchange", sync);
    return () => {
      window.removeEventListener("popstate", sync);
      window.removeEventListener("hashchange", sync);
    };
  }, []);

  const go = (next: Route) => {
    // pushState adds a history entry (so Back returns to the previous screen) without reloading.
    const address =
      next.kind === "run"
        ? runHash(next.runId)
        : next.kind === "history"
          ? HISTORY_HASH
          : window.location.pathname + window.location.search;
    window.history.pushState(null, "", address);
    setRoute(next);
  };
  const start: Route = { kind: "start" };

  return (
    <div className="min-h-screen">
      <header className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-6 py-6">
        <button
          type="button"
          onClick={() => go(start)}
          className="flex items-center gap-3 rounded-xl"
          aria-label="WorkflowWeaver home"
        >
          <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-white text-xl text-ink-950">
            <LogoIcon />
          </span>
          <span className="text-lg font-medium tracking-tight">WorkflowWeaver</span>
        </button>
        <nav aria-label="Main" className="flex items-center gap-3">
          <a
            href={HISTORY_HASH}
            aria-current={route.kind === "history" ? "page" : undefined}
            className={
              "inline-flex items-center gap-2 rounded-full border px-4 py-1.5 text-sm transition " +
              (route.kind === "history"
                ? "border-white/20 bg-white text-ink-950"
                : "border-white/10 bg-white/5 text-slate-200 hover:bg-white/10")
            }
          >
            <HistoryIcon className="h-4 w-4" />
            History
          </a>
          <BackendStatus />
        </nav>
      </header>

      <main className="mx-auto max-w-6xl px-6 pb-24">
        {route.kind === "run" ? (
          <RunScreen runId={route.runId} onStartAnother={() => go(start)} />
        ) : route.kind === "history" ? (
          <HistoryScreen />
        ) : (
          <>
            <section className="mx-auto max-w-3xl py-12 text-center sm:py-16">
              <p className="mx-auto mb-6 inline-flex rounded-full border border-violet-400/30 bg-violet-500/10 px-4 py-1.5 text-xs">
                <span className="bg-gradient-to-r from-violet-300 to-fuchsia-300 bg-clip-text text-transparent">
                  Notion · Google Drive · Slack or Teams
                </span>
              </p>
              <h1 className="text-balance text-4xl font-light tracking-tight sm:text-6xl">
                Turn a request into finished work
              </h1>
              <p className="mt-3 text-2xl font-light tracking-tight text-slate-500 sm:text-3xl">
                Describe it in plain English.
              </p>
              <p className="mx-auto mt-6 max-w-xl text-base leading-relaxed text-slate-400">
                The agent plans the steps, does the work across your tools, checks it, and tells your team.
                If something goes wrong, it undoes what it created.
              </p>
            </section>
            <StartScreen onStarted={(runId) => go({ kind: "run", runId })} />
          </>
        )}
      </main>
    </div>
  );
}
