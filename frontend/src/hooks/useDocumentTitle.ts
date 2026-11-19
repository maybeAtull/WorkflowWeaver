import { useEffect } from "react";

export const APP_NAME = "WorkflowWeaver";

// Sets the browser tab's title, so tabs and the history list are tell-apart-able and a screen reader
// announces where the user is. `null` leaves the title to whoever else sets it.
export function useDocumentTitle(title: string | null): void {
  useEffect(() => {
    if (title === null) return;
    document.title = title === APP_NAME ? APP_NAME : `${title} · ${APP_NAME}`;
  }, [title]);
}
