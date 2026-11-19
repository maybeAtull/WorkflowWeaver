import { AlertIcon, RetryIcon } from "../components/icons";
import { secondaryButton } from "../components/ui";
import type { Connection } from "./useRunStream";

interface Props {
  connection: Connection;
  onTryAgain: () => void;
}

// Says nothing while the connection is fine. When it drops, the page keeps what it already shows
// and explains what is going on.
export default function ConnectionBanner({ connection, onTryAgain }: Props) {
  if (connection === "reconnecting") {
    return (
      <p
        role="status"
        className="flex items-center gap-3 rounded-2xl border border-amber-400/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-100"
      >
        <RetryIcon className="h-4 w-4 shrink-0 motion-safe:animate-spin" />
        Connection lost. Reconnecting… Your workflow keeps running in the meantime.
      </p>
    );
  }
  if (connection === "failed") {
    return (
      <div
        role="alert"
        className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-rose-400/30 bg-rose-500/10 px-4 py-3 text-sm text-rose-100"
      >
        <p className="flex items-start gap-3">
          <AlertIcon className="mt-0.5 h-5 w-5 shrink-0 text-rose-300" />
          We couldn't load this run's progress. The server may be unreachable, or the run may not exist.
          A run that was already started keeps going.
        </p>
        <button type="button" className={secondaryButton} onClick={onTryAgain}>
          Try again
        </button>
      </div>
    );
  }
  return null;
}
