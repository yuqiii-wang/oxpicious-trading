/**
 * Symbols shared by two or more monitor route subfiles: the always-on
 * service catalog + staleness constant, the freq auto-skip windows, and
 * the repo-root module-existence check.
 * Full route docs: see index.ts.
 */

import { existsSync } from "fs";
import path from "path";

// Mirror of _common/service_heartbeat.py (MONITORED_SERVICES — unique
// service name → scope/sub_scope — plus STALE_AFTER_S); keep in sync. The
// heartbeat table only tells us which services HAVE beaten, not which
// SHOULD, so the expected set lives here.
export const WATCHDOG_SERVICE = "database.stream_watchdog";
export const STALE_AFTER_S = 240;
export const SERVICE_CATALOG: ReadonlyArray<{
  service: string;
  scope: string;
  subScope: string | null;
}> = [
  { service: "downloads.stream.sse.price", scope: "downloads", subScope: "stream" },
  { service: "downloads.stream.szse.price", scope: "downloads", subScope: "stream" },
  { service: "downloads.stream.bse.price", scope: "downloads", subScope: "stream" },
  { service: "downloads.stream.csindex.price", scope: "downloads", subScope: "stream" },
  { service: "downloads.stream.cnindex.price", scope: "downloads", subScope: "stream" },
  // DB health sampler — always-on like the pollers (stream sub-scope).
  { service: "database.db_health", scope: "database", subScope: "stream" },
  { service: WATCHDOG_SERVICE, scope: "database", subScope: "monitoring" },
];

/** Freq → auto-skip window in ms. `null` = never auto-skip (ad-hoc /
 *  continuous); `Infinity`-marker SKIP_ANY_SUCCESS = one success ever
 *  satisfies the freq. Unknown freq values fall back to never-skip. */
export const SKIP_ANY_SUCCESS = Number.POSITIVE_INFINITY;
export const FREQ_PERIOD_MS: Record<string, number | null> = {
  always: null,
  daily: 24 * 3600_000,
  monthly: 30 * 24 * 3600_000,
  semiannually: 183 * 24 * 3600_000,
  once: SKIP_ANY_SUCCESS,
  occasionally: null,
  on_demand: null,
};

/** The repo root as the OS sees it (api/routes/monitor → api/routes → api →
 *  data_viz → root) — module-existence check so a typo'd / non-module
 *  service name is a 400 instead of an instant argparse-style failure
 *  after the spawn. */
export const REPO_ROOT = path.resolve(import.meta.dirname ?? __dirname, "..", "..", "..", "..");

/** A service is runnable iff `python -m <service>` has an entry: the dotted
 *  name maps to a package with __main__.py or a single-file module. */
export function isRunnableModule(service: string): boolean {
  const rel = service.split(".").join(path.sep);
  return (
    existsSync(path.join(REPO_ROOT, rel, "__main__.py")) ||
    existsSync(path.join(REPO_ROOT, `${rel}.py`))
  );
}
