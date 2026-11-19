import { type RefObject, useEffect, useRef } from "react";

// When the screen changes (start, run, history), move keyboard and screen-reader focus to the new
// screen's main heading, so the user is not left on a control that no longer exists.
//
// It acts only when `key` actually changes: not on first load, and never on live updates, so it
// cannot steal focus from someone who is typing. (Comparing with the previous key rather than
// "is this the first run" also keeps it correct when React replays effects in development.)
export function useFocusOnChange(key: string, container: RefObject<HTMLElement | null>): void {
  const previous = useRef(key);
  useEffect(() => {
    if (previous.current === key) return;
    previous.current = key;
    container.current?.querySelector<HTMLElement>("h1")?.focus();
  }, [key, container]);
}
