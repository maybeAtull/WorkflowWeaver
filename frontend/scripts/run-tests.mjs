// Runs the unit tests (vitest) from the canonical spelling of this folder.
//
// Why: on Windows the same folder can be reached as "d:\..." or "D:\..." (VS Code terminals often
// use the lowercase one). Vitest then ends up loaded under both spellings, and every test fails
// with "Cannot read properties of undefined (reading 'config')". Starting it through the real
// path avoids that. On other systems this is just `vitest run`.
import { spawnSync } from "node:child_process";
import { realpathSync } from "node:fs";
import { join } from "node:path";

const root = realpathSync.native(process.cwd());
const vitest = join(root, "node_modules", "vitest", "vitest.mjs");
const result = spawnSync(process.execPath, [vitest, "run", ...process.argv.slice(2)], {
  cwd: root,
  stdio: "inherit",
});
process.exit(result.status ?? 1);
