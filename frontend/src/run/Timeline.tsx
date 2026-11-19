import { AlertIcon, CheckIcon, CircleIcon, ExternalIcon, RetryIcon, SpinnerIcon, UndoIcon } from "../components/icons";
import { isSafeUrl, stepStatusText, toolLabel } from "./labels";
import type { CheckView, RunView, StepView } from "./model";

// The icon and colours that say where a step is at a glance.
function StepIcon({ step }: { step: StepView }) {
  const base = "h-5 w-5";
  if (step.undo === "undone") return <UndoIcon className={`${base} text-slate-300`} />;
  if (step.undo === "failed") return <AlertIcon className={`${base} text-amber-300`} />;
  switch (step.status) {
    case "queued":
      return <CircleIcon className={`${base} text-slate-600`} />;
    case "running":
      return <SpinnerIcon className={`${base} text-violet-300 motion-safe:animate-spin`} />;
    case "retrying":
      return <RetryIcon className={`${base} text-amber-300`} />;
    case "done":
      return <CheckIcon className={`${base} text-emerald-300`} />;
    case "failed":
      return <AlertIcon className={`${base} text-rose-300`} />;
  }
}

function statusColour(step: StepView): string {
  if (step.undo === "failed") return "text-amber-300";
  if (step.undo === "undone") return "text-slate-400";
  switch (step.status) {
    case "queued":
      return "text-slate-500";
    case "running":
      return "text-violet-300";
    case "retrying":
      return "text-amber-300";
    case "done":
      return "text-emerald-300";
    case "failed":
      return "text-rose-300";
  }
}

function StepRow({ step, undoDetail }: { step: StepView; undoDetail: string | null }) {
  const active = step.status === "running" || step.status === "retrying";
  // A link only for something that exists: not for a step that failed or was undone.
  const link = step.status === "done" && step.undo === null && isSafeUrl(step.url) ? step.url : null;
  return (
    <li
      className={
        "flex gap-4 rounded-2xl border px-4 py-4 transition-colors " +
        (active ? "border-violet-400/30 bg-violet-500/[0.07]" : "border-white/10 bg-white/[0.03]")
      }
    >
      <span className="mt-0.5 shrink-0">
        <StepIcon step={step} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <p className={`text-[15px] ${step.status === "queued" ? "text-slate-400" : "text-slate-100"}`}>
            {step.description}
          </p>
          <span className="rounded-full bg-white/5 px-2.5 py-0.5 text-xs text-slate-400">
            {toolLabel(step.tool)}
          </span>
        </div>
        <p className={`mt-1 text-sm ${statusColour(step)}`}>
          {stepStatusText(step)}
          {step.status === "done" && step.attempts > 1 && step.undo === null && ` after ${step.attempts} attempts`}
        </p>
        {step.status === "retrying" && step.retry && (
          <p className="mt-1 text-xs text-slate-400">Reason: {step.retry.reason}</p>
        )}
        {step.status === "done" && step.undo === null && step.summary && (
          <p className="mt-1 text-sm text-slate-400">
            {link ? (
              <a
                href={link}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 text-violet-200 underline-offset-4 hover:underline"
              >
                {step.summary}
                <ExternalIcon className="h-3.5 w-3.5" />
              </a>
            ) : (
              step.summary
            )}
          </p>
        )}
        {step.status === "failed" && step.error && (
          <p className="mt-1 text-xs text-slate-400">Details: {step.error}</p>
        )}
        {step.undo && undoDetail && <p className="mt-1 text-xs text-slate-400">{undoDetail}</p>}
      </div>
    </li>
  );
}

const CHECK_TEXT: Record<CheckView["status"], { label: string; colour: string }> = {
  verified: { label: "Confirmed", colour: "text-emerald-300" },
  unconfirmed: { label: "Couldn't confirm", colour: "text-amber-300" },
  failed: { label: "Not as expected", colour: "text-rose-300" },
};

// The read-back of what was created, shown before anything is announced to the team.
function Checks({ checks }: { checks: CheckView[] }) {
  return (
    <section aria-label="Checks" className="rounded-2xl border border-white/10 bg-white/[0.02] px-4 py-4">
      <h3 className="mb-2 text-sm font-medium text-slate-200">Checking what was created</h3>
      <ul className="space-y-1.5 text-sm">
        {checks.map((check) => (
          <li key={check.stepId} className="flex flex-wrap gap-x-3 text-slate-300">
            <span className={CHECK_TEXT[check.status].colour}>{CHECK_TEXT[check.status].label}</span>
            <span className="text-slate-400">
              {toolLabel(check.tool)}: {check.detail}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

export default function Timeline({ view }: { view: RunView }) {
  const undoDetail = (id: string) => view.undone.find((u) => u.stepId === id)?.detail ?? null;
  return (
    <div className="space-y-4">
      {view.steps.length > 0 && (
        <ol aria-label="Workflow steps" className="space-y-3">
          {view.steps.map((step) => (
            <StepRow key={step.id} step={step} undoDetail={undoDetail(step.id)} />
          ))}
        </ol>
      )}
      {view.checks.length > 0 && <Checks checks={view.checks} />}
    </div>
  );
}
