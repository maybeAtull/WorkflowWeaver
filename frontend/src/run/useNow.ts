import { useEffect, useState } from "react";

// The current time in milliseconds, refreshed every second while `active`. It drives the running
// timer; once a run is finished nothing needs to tick any more.
export function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active]);
  return now;
}
