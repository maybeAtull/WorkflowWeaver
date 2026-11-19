import { useEffect, useRef, useState } from "react";

import { ApiError, SERVER_UNREACHABLE, getTemplates, startRun } from "../api/client";
import type { TemplateInfo } from "../api/types";
import { AlertIcon, ArrowRightIcon } from "../components/icons";
import { panel, primaryButton, secondaryButton } from "../components/ui";
import DemoOptions from "./DemoOptions";
import FreeTextForm from "./FreeTextForm";
import TemplateCards from "./TemplateCards";
import TemplateForm from "./TemplateForm";
import {
  DEFAULT_DEMO_STEP,
  type DemoMode,
  buildFreeTextBody,
  buildTemplateBody,
  simulateValue,
  validateFreeText,
  validateValues,
} from "./formModel";
import { FREE_CHOICE, FREE_TEXT_ID, fieldId } from "./ids";

type Load =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; templates: TemplateInfo[] };

interface Props {
  onStarted: (runId: string) => void;
}

// Pick a workflow, fill it in, press Run. Everything the user typed is kept until the page closes,
// including when they switch cards or when the server refuses the request.
export default function StartScreen({ onStarted }: Props) {
  const [load, setLoad] = useState<Load>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  const [selected, setSelected] = useState<string | null>(null); // null: the first template
  const [valuesByTemplate, setValuesByTemplate] = useState<Record<string, Record<string, string>>>({});
  const [freeText, setFreeText] = useState("");
  const [touched, setTouched] = useState<Record<string, boolean>>({});
  const [showAllProblems, setShowAllProblems] = useState(false);
  const [demoMode, setDemoMode] = useState<DemoMode>("off");
  const [demoStep, setDemoStep] = useState(DEFAULT_DEMO_STEP);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  // A second click can arrive before React has re-rendered the disabled button; this stops it.
  const inFlight = useRef(false);

  useEffect(() => {
    const controller = new AbortController();
    getTemplates(controller.signal)
      .then((templates) => setLoad({ status: "ready", templates }))
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setLoad({
          status: "error",
          message: error instanceof ApiError ? error.message : SERVER_UNREACHABLE,
        });
      });
    return () => controller.abort();
  }, [attempt]);

  const retryLoading = () => {
    setLoad({ status: "loading" });
    setAttempt((n) => n + 1);
  };

  if (load.status === "loading") {
    return (
      <div className={`${panel} p-8 text-slate-400`} role="status">
        Loading your workflows…
      </div>
    );
  }
  if (load.status === "error") {
    return (
      <div className={`${panel} flex flex-col items-start gap-4 p-8`}>
        <p role="alert" className="flex items-start gap-3 text-slate-200">
          <AlertIcon className="mt-0.5 h-5 w-5 shrink-0 text-rose-300" />
          {load.message}
        </p>
        <button type="button" className={secondaryButton} onClick={retryLoading}>
          Try again
        </button>
      </div>
    );
  }

  const { templates } = load;
  const choice = selected ?? templates[0]?.id ?? FREE_CHOICE;
  const template = templates.find((t) => t.id === choice) ?? null; // null means "write your own"
  const values = template ? (valuesByTemplate[template.id] ?? {}) : {};

  // What is wrong with the current form, and which of it to show right now.
  const allProblems = template ? validateValues(template, values) : {};
  const freeProblem = template ? null : validateFreeText(freeText);
  const visibleProblems: Record<string, string> = {};
  for (const [key, message] of Object.entries(allProblems)) {
    if (showAllProblems || touched[`${choice}.${key}`]) visibleProblems[key] = message;
  }
  const visibleFreeProblem = showAllProblems || touched[`${choice}.free`] ? freeProblem : null;

  const markTouched = (key: string) => setTouched((t) => ({ ...t, [`${choice}.${key}`]: true }));

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (inFlight.current) return;
    setSubmitError(null);

    const firstProblem = template ? Object.keys(allProblems)[0] : freeProblem ? "free" : undefined;
    if (firstProblem) {
      setShowAllProblems(true);
      const id = template ? fieldId(template, firstProblem) : FREE_TEXT_ID;
      document.getElementById(id)?.focus(); // take the user straight to what needs fixing
      return;
    }

    const simulate = simulateValue(demoMode, demoStep);
    const body = template
      ? buildTemplateBody(template, values, simulate)
      : buildFreeTextBody(freeText, simulate);
    inFlight.current = true;
    setSubmitting(true);
    let runId: string;
    try {
      runId = await startRun(body);
    } catch (error) {
      setSubmitError(error instanceof ApiError ? error.message : SERVER_UNREACHABLE);
      setSubmitting(false);
      return;
    } finally {
      inFlight.current = false;
    }
    onStarted(runId); // the screen is replaced from here, so the button stays disabled
  };

  const selectCard = (id: string) => {
    setSelected(id);
    setShowAllProblems(false);
    setSubmitError(null);
  };

  return (
    <div className="space-y-8">
      <TemplateCards templates={templates} selected={choice} disabled={submitting} onSelect={selectCard} />

      <form onSubmit={submit} noValidate className={`${panel} space-y-6 p-6 sm:p-8`}>
        {template ? (
          <TemplateForm
            template={template}
            values={values}
            errors={visibleProblems}
            disabled={submitting}
            onChange={(key, value) =>
              setValuesByTemplate((all) => ({
                ...all,
                [template.id]: { ...all[template.id], [key]: value },
              }))
            }
            onBlur={markTouched}
          />
        ) : (
          <FreeTextForm
            text={freeText}
            error={visibleFreeProblem}
            disabled={submitting}
            onChange={setFreeText}
            onBlur={() => markTouched("free")}
          />
        )}

        <DemoOptions
          mode={demoMode}
          step={demoStep}
          disabled={submitting}
          onModeChange={setDemoMode}
          onStepChange={setDemoStep}
        />

        {submitError && (
          <p role="alert" className="flex items-start gap-3 rounded-2xl border border-rose-400/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-100">
            <AlertIcon className="mt-0.5 h-5 w-5 shrink-0 text-rose-300" />
            {submitError}
          </p>
        )}

        <div className="flex flex-wrap items-center justify-between gap-3">
          {template ? (
            <button
              type="button"
              className={secondaryButton}
              disabled={submitting}
              onClick={() =>
                setValuesByTemplate((all) => ({ ...all, [template.id]: { ...template.example } }))
              }
            >
              Try with an example
            </button>
          ) : (
            <span />
          )}
          <button type="submit" className={primaryButton} disabled={submitting}>
            {submitting ? "Starting…" : "Run workflow"}
            {!submitting && <ArrowRightIcon className="h-4 w-4" />}
          </button>
        </div>
      </form>
    </div>
  );
}
