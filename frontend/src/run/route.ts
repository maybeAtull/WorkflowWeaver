// Which screen the page shows is kept in the address, so a refresh stays where it was, links can
// be shared, and the browser's Back button works. No router library is needed for three screens:
//   (no hash)      start screen
//   #run=<id>      one run
//   #history       the list of past runs
export type Route =
  | { kind: "start" }
  | { kind: "run"; runId: string }
  | { kind: "history" };

const RUN_HASH = /^#run=([A-Za-z0-9_-]{1,64})$/;
export const HISTORY_HASH = "#history";

// The run id in an address hash, or null when the hash is anything else.
export function parseRunHash(hash: string): string | null {
  const match = RUN_HASH.exec(hash);
  return match ? match[1] : null;
}

export function runHash(runId: string): string {
  return `#run=${runId}`;
}

// Anything that is not a recognised hash is simply the start screen.
export function parseRoute(hash: string): Route {
  const runId = parseRunHash(hash);
  if (runId) return { kind: "run", runId };
  if (hash === HISTORY_HASH) return { kind: "history" };
  return { kind: "start" };
}
