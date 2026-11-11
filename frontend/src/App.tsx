import { useEffect, useState } from "react";

import { getHealth } from "./api/client";

type BackendStatus = "checking" | "connected" | "unreachable";

const STATUS_STYLES: Record<BackendStatus, { label: string; dot: string }> = {
  checking: { label: "Checking backend…", dot: "bg-amber-400" },
  connected: { label: "Backend connected", dot: "bg-emerald-500" },
  unreachable: { label: "Backend unreachable", dot: "bg-red-500" },
};

export default function App() {
  const [status, setStatus] = useState<BackendStatus>("checking");

  useEffect(() => {
    // Abort the request if the component unmounts so we never set state afterwards.
    const controller = new AbortController();
    getHealth(controller.signal)
      .then(() => setStatus("connected"))
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setStatus("unreachable");
      });
    return () => controller.abort();
  }, []);

  const { label, dot } = STATUS_STYLES[status];

  return (
    <main className="min-h-screen bg-slate-950 text-slate-100">
      <div className="mx-auto max-w-3xl px-6 py-16">
        <h1 className="text-3xl font-bold tracking-tight">WorkflowWeaver</h1>
        <p className="mt-2 text-slate-400">
          Describe a business process in plain English and let the agent run it across your tools.
        </p>
        <div className="mt-8 inline-flex items-center gap-2 rounded-full border border-slate-800 bg-slate-900 px-4 py-2 text-sm">
          <span className={`h-2.5 w-2.5 rounded-full ${dot}`} aria-hidden="true" />
          <span role="status">{label}</span>
        </div>
      </div>
    </main>
  );
}
