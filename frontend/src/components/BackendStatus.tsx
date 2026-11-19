import { useEffect, useState } from "react";

import { getHealth } from "../api/client";

type Status = "checking" | "connected" | "unreachable";

const STYLES: Record<Status, { label: string; dot: string }> = {
  checking: { label: "Checking…", dot: "bg-amber-400" },
  connected: { label: "Backend connected", dot: "bg-emerald-400" },
  unreachable: { label: "Backend unreachable", dot: "bg-rose-400" },
};

// A small pill in the header that says whether the server is up.
export default function BackendStatus() {
  const [status, setStatus] = useState<Status>("checking");

  useEffect(() => {
    // Abort on unmount so no state is set afterwards.
    const controller = new AbortController();
    getHealth(controller.signal)
      .then(() => setStatus("connected"))
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setStatus("unreachable");
      });
    return () => controller.abort();
  }, []);

  const { label, dot } = STYLES[status];
  return (
    <div className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-3 py-2 text-xs text-slate-300 sm:px-3.5 sm:py-1.5">
      <span className={`h-2 w-2 rounded-full ${dot}`} aria-hidden="true" />
      {/* On a phone only the dot shows; the words stay for screen readers. */}
      <span role="status" className="sr-only sm:not-sr-only">
        {label}
      </span>
    </div>
  );
}
