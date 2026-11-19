import { Component, type ErrorInfo, type ReactNode } from "react";

import { AlertIcon } from "./icons";
import { panel, primaryButton } from "./ui";

interface Props {
  children: ReactNode;
}

// If something in the page throws unexpectedly, show a calm message instead of a blank screen.
// A run that was already started is unaffected: it keeps going on the server.
export default class ErrorBoundary extends Component<Props, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Log the technical detail for whoever debugs it; the user sees only the plain message below.
    console.error("The page crashed:", error, info.componentStack);
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <main className="mx-auto max-w-xl px-4 py-24">
        <div className={`${panel} space-y-4 p-8`} role="alert">
          <p className="flex items-center gap-3 text-xl font-light">
            <AlertIcon className="h-6 w-6 text-rose-300" />
            Something went wrong
          </p>
          <p className="text-slate-300">
            The page ran into a problem. Any workflow that was already started keeps running. Reload the page
            to continue; your past workflows are kept under History.
          </p>
          <button type="button" className={primaryButton} onClick={() => window.location.reload()}>
            Reload the page
          </button>
        </div>
      </main>
    );
  }
}
