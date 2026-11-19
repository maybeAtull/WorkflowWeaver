import { useState } from "react";

import BackendStatus from "./components/BackendStatus";
import { LogoIcon } from "./components/icons";
import { panel, secondaryButton } from "./components/ui";
import StartScreen from "./start/StartScreen";

type View = { kind: "start" } | { kind: "run"; runId: string };

export default function App() {
  const [view, setView] = useState<View>({ kind: "start" });

  return (
    <div className="min-h-screen">
      <header className="mx-auto flex max-w-6xl items-center justify-between px-6 py-6">
        <div className="flex items-center gap-3">
          <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-white text-xl text-ink-950">
            <LogoIcon />
          </span>
          <span className="text-lg font-medium tracking-tight">WorkflowWeaver</span>
        </div>
        <BackendStatus />
      </header>

      <main className="mx-auto max-w-6xl px-6 pb-24">
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

        {view.kind === "start" ? (
          <StartScreen onStarted={(runId) => setView({ kind: "run", runId })} />
        ) : (
          // Placeholder: the live timeline replaces this in the next task.
          <div className={`${panel} mx-auto flex max-w-xl flex-col items-start gap-4 p-8`}>
            <p className="text-lg font-medium">Your workflow has started</p>
            <p className="text-sm text-slate-400">
              Run <span className="font-mono text-slate-200">{view.runId}</span> is on its way. The live progress
              view arrives in the next step of the build.
            </p>
            <button type="button" className={secondaryButton} onClick={() => setView({ kind: "start" })}>
              Start another
            </button>
          </div>
        )}
      </main>
    </div>
  );
}
