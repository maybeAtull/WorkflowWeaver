import { AlertIcon, CheckIcon, ExternalIcon, UndoIcon } from "../components/icons";
import { primaryButton } from "../components/ui";
import { createdLinks, leftBehind, outcomeTitle, outcomeTone, toolLabel, type Tone } from "./labels";
import type { RunView } from "./model";

const TONE_STYLES: Record<Tone, { card: string; icon: string }> = {
  success: { card: "border-emerald-400/30 bg-emerald-500/[0.08]", icon: "text-emerald-300" },
  warning: { card: "border-amber-400/30 bg-amber-500/[0.08]", icon: "text-amber-300" },
  neutral: { card: "border-white/15 bg-white/[0.05]", icon: "text-slate-300" },
  danger: { card: "border-rose-400/30 bg-rose-500/[0.08]", icon: "text-rose-300" },
};

function ToneIcon({ tone, className }: { tone: Tone; className: string }) {
  if (tone === "success") return <CheckIcon className={className} />;
  if (tone === "neutral") return <UndoIcon className={className} />;
  return <AlertIcon className={className} />;
}

interface Props {
  view: RunView;
  onStartAnother: () => void;
}

// The result, in plain words, once the run has ended. Shown only when there is an outcome.
export default function OutcomeCard({ view, onStartAnother }: Props) {
  const outcome = view.outcome;
  if (!outcome) return null;
  const tone = outcomeTone(outcome.status);
  const styles = TONE_STYLES[tone];
  const links = createdLinks(view);
  const remaining = leftBehind(view);

  return (
    <section aria-label="Result" className={`rounded-3xl border p-6 sm:p-8 ${styles.card}`}>
      <div className="flex items-start gap-4">
        <ToneIcon tone={tone} className={`mt-1 h-6 w-6 shrink-0 ${styles.icon}`} />
        <div className="min-w-0 flex-1">
          <h2 className="text-xl font-medium">{outcomeTitle(outcome.status)}</h2>
          <p className="mt-1 text-slate-300">{outcome.headline}</p>

          {links.length > 0 && (
            <ul className="mt-5 space-y-2" aria-label="Created items">
              {links.map((item) => (
                <li key={item.id}>
                  <a
                    href={item.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex max-w-full flex-wrap items-center gap-x-2 rounded-2xl border border-white/10 bg-white/5 px-4 py-2 text-sm text-slate-100 transition hover:bg-white/10 sm:rounded-full"
                  >
                    <span className="text-slate-400">{toolLabel(item.tool)}</span>
                    {item.label}
                    <ExternalIcon className="h-3.5 w-3.5 text-slate-400" />
                  </a>
                </li>
              ))}
            </ul>
          )}

          {remaining.length > 0 && (
            <div className="mt-5 rounded-2xl border border-amber-400/30 bg-amber-500/10 p-4 text-sm text-amber-100">
              <p className="font-medium">These items could not be removed. Please delete them by hand:</p>
              <ul className="mt-2 space-y-1">
                {remaining.map((item) => (
                  <li key={item.id}>
                    {toolLabel(item.tool)}: {item.label}
                    {item.url && (
                      <>
                        {" "}
                        <a
                          href={item.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="underline underline-offset-4"
                        >
                          open
                        </a>
                      </>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {outcome.error && outcome.status !== "completed" && (
            <details className="mt-4 text-sm text-slate-400">
              <summary className="cursor-pointer">Technical details</summary>
              <p className="mt-2 break-words">{outcome.error}</p>
            </details>
          )}

          <div className="mt-6">
            <button type="button" className={primaryButton} onClick={onStartAnother}>
              Start another workflow
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}
