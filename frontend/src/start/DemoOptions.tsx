import { ChevronIcon } from "../components/icons";
import { DEFAULT_DEMO_STEP, DEMO_MODE_LABELS, MAX_DEMO_STEP, type DemoMode } from "./formModel";

interface Props {
  mode: DemoMode;
  step: number;
  disabled: boolean;
  onModeChange: (mode: DemoMode) => void;
  onStepChange: (step: number) => void;
}

const MODES: DemoMode[] = ["off", "retry", "rollback"];
const STEPS = Array.from({ length: MAX_DEMO_STEP }, (_, i) => i + 1);

// Collapsed by default: it exists so retry and rollback can be shown on demand, not for everyday use.
export default function DemoOptions({ mode, step, disabled, onModeChange, onStepChange }: Props) {
  return (
    <details className="group rounded-2xl border border-white/10 bg-white/[0.02] px-4 py-3">
      <summary className="flex cursor-pointer list-none items-center gap-2 text-sm text-slate-300 [&::-webkit-details-marker]:hidden">
        <ChevronIcon className="h-4 w-4 transition-transform group-open:rotate-90" />
        Demo options
        {mode !== "off" && (
          <span className="ml-1 rounded-full bg-violet-500/20 px-2 py-0.5 text-xs text-violet-200">
            on
          </span>
        )}
      </summary>
      <fieldset className="mt-4 space-y-3" disabled={disabled}>
        <legend className="mb-2 text-xs text-slate-500">
          For demonstrations only: break a step on purpose to see how the agent recovers.
        </legend>
        {MODES.map((value) => (
          <label key={value} className="flex cursor-pointer items-center gap-3 text-sm text-slate-200">
            <input
              type="radio"
              name="demo-mode"
              value={value}
              checked={mode === value}
              onChange={() => onModeChange(value)}
              className="h-4 w-4 accent-violet-400"
            />
            {DEMO_MODE_LABELS[value]}
          </label>
        ))}
        {mode !== "off" && (
          <div className="flex items-center gap-3 pt-1 text-sm text-slate-200">
            <label htmlFor="demo-step">Break step</label>
            <select
              id="demo-step"
              value={step}
              onChange={(e) => onStepChange(Number(e.target.value))}
              className="rounded-xl border border-white/10 bg-ink-900 px-3 py-1.5 text-sm"
            >
              {STEPS.map((n) => (
                <option key={n} value={n}>
                  {n}
                  {n === DEFAULT_DEMO_STEP ? " (recommended)" : ""}
                </option>
              ))}
            </select>
          </div>
        )}
      </fieldset>
    </details>
  );
}
