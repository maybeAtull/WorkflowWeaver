import { reportDownloadUrl } from "../api/client";
import type { RunRecord, RunReport } from "../api/types";
import { DownloadIcon, ExternalIcon } from "../components/icons";
import { panel, secondaryButton } from "../components/ui";
import { formatElapsed, isSafeUrl, toolLabel } from "../run/labels";
import { reportStepStatus, stepResult, stepsSummary } from "./format";
import { useRunRecord } from "./useRunRecord";

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/[0.03] px-4 py-3">
      <dt className="text-xs text-slate-400">{label}</dt>
      <dd className="mt-1 text-lg text-slate-100">{value}</dd>
    </div>
  );
}

// On a phone each step is a small card with labelled lines; from the `sm` breakpoint up the same
// markup is laid out as table columns. ARIA table roles keep it a real table for screen readers, and
// the per-cell labels are hidden on wide screens, where the column headers say the same thing.
const COLUMNS = "sm:grid-cols-[minmax(0,2.4fr)_minmax(0,1fr)_minmax(0,1fr)_3rem_minmax(0,2fr)] sm:gap-x-4";

function CellLabel({ children }: { children: string }) {
  return <span className="mr-1.5 text-xs text-slate-400 sm:hidden">{children}</span>;
}

function StepsTable({ report }: { report: RunReport }) {
  const steps = report.steps ?? [];
  if (steps.length === 0) return null;
  return (
    <div role="table" aria-label="What each step did" className="text-sm">
      <div role="row" className={`hidden pb-2 text-xs text-slate-400 sm:grid ${COLUMNS}`}>
        <span role="columnheader">Step</span>
        <span role="columnheader">Tool</span>
        <span role="columnheader">Status</span>
        <span role="columnheader">Tries</span>
        <span role="columnheader">Result</span>
      </div>
      <div role="rowgroup" className="space-y-3 sm:space-y-0 sm:divide-y sm:divide-white/5">
        {steps.map((step) => (
          <div
            role="row"
            key={step.id}
            className={`grid gap-1 rounded-2xl border border-white/10 bg-white/[0.02] p-4 text-slate-300 sm:items-start sm:rounded-none sm:border-0 sm:bg-transparent sm:p-0 sm:py-2.5 ${COLUMNS}`}
          >
            <div role="cell" className="text-slate-100">
              {step.description}
            </div>
            <div role="cell">
              <CellLabel>Tool:</CellLabel>
              {toolLabel(step.tool)}
            </div>
            <div role="cell">
              <CellLabel>Status:</CellLabel>
              {reportStepStatus(step.status)}
            </div>
            <div role="cell">
              <CellLabel>Tries:</CellLabel>
              {step.attempts}
            </div>
            <div role="cell" className="min-w-0 break-words text-slate-400">
              {step.status === "succeeded" && isSafeUrl(step.url) ? (
                <a
                  href={step.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1.5 text-violet-200 underline-offset-4 hover:underline"
                >
                  {stepResult(step)}
                  <ExternalIcon className="h-3.5 w-3.5 shrink-0" />
                </a>
              ) : (
                stepResult(step)
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function Notes({ title, items }: { title: string; items: { key: string; label: string; detail: string }[] }) {
  if (items.length === 0) return null;
  return (
    <div>
      <h4 className="mb-1.5 text-sm font-medium text-slate-200">{title}</h4>
      <ul className="space-y-1 text-sm text-slate-400">
        {items.map((item) => (
          <li key={item.key}>
            <span className="text-slate-300">{item.label}</span> {item.detail}
          </li>
        ))}
      </ul>
    </div>
  );
}

function ReportBody({ record, report }: { record: RunRecord; report: RunReport }) {
  const summary = stepsSummary(report);
  const traceUrl = isSafeUrl(record.trace_url) ? record.trace_url : null;
  return (
    <div className="space-y-6">
      {(report.title || report.summary) && (
        <div>
          {report.title && <h3 className="text-lg font-medium text-slate-100">{report.title}</h3>}
          {report.summary && <p className="mt-1 text-sm leading-relaxed text-slate-400">{report.summary}</p>}
        </div>
      )}

      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Took" value={formatElapsed(report.duration_seconds)} />
        <Stat label="Steps" value={summary.replace(" done", "") || "—"} />
        <Stat label="Retries" value={String(report.retries)} />
        <Stat label="Items created" value={String(report.created.length)} />
      </dl>

      <StepsTable report={report} />

      <Notes
        title="Checks before telling the team"
        items={(report.checks ?? []).map((c) => ({
          key: c.step_id,
          label: `${toolLabel(c.tool)}:`,
          detail: c.detail,
        }))}
      />
      <Notes
        title="What was undone"
        items={(report.rollback ?? []).map((u) => ({
          key: u.step_id,
          label: `${toolLabel(u.tool)} (${u.status}):`,
          detail: u.detail,
        }))}
      />

      <div className="flex flex-wrap items-center gap-3">
        <a className={secondaryButton} href={reportDownloadUrl(record.run_id)}>
          <DownloadIcon className="h-4 w-4" />
          Download report (JSON)
        </a>
        {traceUrl && (
          <a className={secondaryButton} href={traceUrl} target="_blank" rel="noopener noreferrer">
            View trace in Langfuse
            <ExternalIcon className="h-3.5 w-3.5" />
          </a>
        )}
      </div>

      <details className="rounded-2xl border border-white/10 bg-white/[0.02] px-4 py-3">
        <summary className="cursor-pointer text-sm text-slate-300">Show raw JSON</summary>
        <pre className="mt-3 max-h-96 overflow-auto rounded-xl bg-ink-950/80 p-4 text-xs leading-relaxed text-slate-300">
          {JSON.stringify(report, null, 2)}
        </pre>
      </details>
    </div>
  );
}

interface Props {
  runId: string;
  finished: boolean; // the report is only asked for once the run has ended
}

// The stored report for a finished run: summary, what each step did, and ways to keep it.
export default function ReportPanel({ runId, finished }: Props) {
  const state = useRunRecord(runId, finished);
  if (!finished) return null;

  return (
    <section aria-label="Report" className={`${panel} space-y-5 p-6 sm:p-8`}>
      <h2 className="text-xl font-light tracking-tight">Report</h2>
      {state.status === "waiting" && (
        <p role="status" className="text-sm text-slate-400">
          Preparing your report…
        </p>
      )}
      {state.status === "missing" && (
        <p role="status" className="text-sm text-slate-400">
          The report isn't available yet. Reload this page in a moment. The result above is still correct.
        </p>
      )}
      {state.status === "ready" && state.record.report && (
        <ReportBody record={state.record} report={state.record.report} />
      )}
    </section>
  );
}
