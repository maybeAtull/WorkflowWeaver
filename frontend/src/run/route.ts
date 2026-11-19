// Which run the page is showing is kept in the address (#run=<id>), so a refresh stays on the same
// run and a run can be opened from a link. No router library is needed for this.
const RUN_HASH = /^#run=([A-Za-z0-9_-]{1,64})$/;

// The run id in an address hash, or null when the hash is anything else.
export function parseRunHash(hash: string): string | null {
  const match = RUN_HASH.exec(hash);
  return match ? match[1] : null;
}

export function runHash(runId: string): string {
  return `#run=${runId}`;
}
