import { useEffect, useState } from "react";

import BackendStatus from "./components/BackendStatus";
import { LogoIcon } from "./components/icons";
import RunScreen from "./run/RunScreen";
import { parseRunHash, runHash } from "./run/route";
import StartScreen from "./start/StartScreen";

// The page shows either the start screen or one run. Which one is kept in the address (#run=<id>),
// so refreshing stays on the run and the back button works.
const runFromAddress = () => parseRunHash(window.location.hash);

export default function App() {
  const [runId, setRunId] = useState<string | null>(runFromAddress);

  useEffect(() => {
    const sync = () => setRunId(runFromAddress());
    window.addEventListener("popstate", sync);
    window.addEventListener("hashchange", sync);
    return () => {
      window.removeEventListener("popstate", sync);
      window.removeEventListener("hashchange", sync);
    };
  }, []);

  const show = (id: string | null) => {
    // pushState adds a history entry (so Back returns to the previous screen) without reloading.
    const address = id ? runHash(id) : window.location.pathname + window.location.search;
    window.history.pushState(null, "", address);
    setRunId(id);
  };

  return (
    <div className="min-h-screen">
      <header className="mx-auto flex max-w-6xl items-center justify-between px-6 py-6">
        <button
          type="button"
          onClick={() => show(null)}
          className="flex items-center gap-3 rounded-xl"
          aria-label="WorkflowWeaver home"
        >
          <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-white text-xl text-ink-950">
            <LogoIcon />
          </span>
          <span className="text-lg font-medium tracking-tight">WorkflowWeaver</span>
        </button>
        <BackendStatus />
      </header>

      <main className="mx-auto max-w-6xl px-6 pb-24">
        {runId ? (
          <RunScreen runId={runId} onStartAnother={() => show(null)} />
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
            <StartScreen onStarted={show} />
          </>
        )}
      </main>
    </div>
  );
}
