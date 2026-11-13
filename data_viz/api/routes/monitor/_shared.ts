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
// SHOULD, so the expected set lives here. Catalog members are the RESIDENT
// processes — the always-on services (the first Service Monitor table);
// every other heartbeat row is a batch pipeline process (the second table).
// Supervision: members WITHOUT `supervisedBy` are the stream watchdog's
// (it restarts them; the pgrep boot-revive pass covers them); members WITH
// `supervisedBy` are their area cluster server's residents (two-level
// supervision — e.g. the stream pollers moved under the downloader server,
// whose resident supervisor honors manual stops; they stay listed here for
// display but are excluded from the watchdog-style revive passes).
export const WATCHDOG_SERVICE = "database.stream_watchdog";
export const STALE_AFTER_S = 240;
export const SERVICE_CATALOG: ReadonlyArray<{
  service: string;
  scope: string;
  subScope: string | null;
  supervisedBy?: string;
}> = [
  // The five stream pollers — the downloader cluster server's residents
  // since the server cluster (2026-11): it auto-starts them at ITS boot,
  // revives dead ones (crash-loop guarded) and honors manual stops; the
  // watchdog no longer revives them, and this catalog no longer feeds
  // them into the pgrep boot-revive pass.
  { service: "downloads.stream.sse.price", scope: "downloads", subScope: "stream", supervisedBy: "server_cluster.downloader" },
  { service: "downloads.stream.szse.price", scope: "downloads", subScope: "stream", supervisedBy: "server_cluster.downloader" },
  { service: "downloads.stream.bse.price", scope: "downloads", subScope: "stream", supervisedBy: "server_cluster.downloader" },
  { service: "downloads.stream.csindex.price", scope: "downloads", subScope: "stream", supervisedBy: "server_cluster.downloader" },
  { service: "downloads.stream.cnindex.price", scope: "downloads", subScope: "stream", supervisedBy: "server_cluster.downloader" },
  // The server-cluster FastAPI servers (server_cluster/) — resident API
  // entries over the four pipeline areas' cmds (ports 8769-8772): the
  // downloader (8769) additionally supervises the pollers above; the
  // watchdog supervises these four like the other daemons.
  { service: "server_cluster.downloader", scope: "server_cluster", subScope: "downloader" },
  { service: "server_cluster.builder", scope: "server_cluster", subScope: "builder" },
  { service: "server_cluster.analyzer", scope: "server_cluster", subScope: "analyzer" },
  { service: "server_cluster.strategist", scope: "server_cluster", subScope: "strategist" },
  // Local-model host (2026-11; migrated from llm_agents.local_model_host
  // into the cluster's llm area, port 8767): the resident FastAPI over
  // the repo's local AI models (OpenAI chat completions incl. the
  // migrated dummy-1 provider simulation, the StructBERT segmenter, the
  // OneJev decision model + the load/offload management API) — absorbed
  // the former standalone dummy-llm server (:8768, test/dummy_llm).
  { service: "server_cluster.llm", scope: "server_cluster", subScope: "llm" },
  // DB health sampler — always-on like the pollers (stream sub-scope).
  { service: "database.db_health", scope: "database", subScope: "stream" },
  // The llm_agents daemons — always-on resident services the watchdog
  // starts/restarts like the pollers (see the MONITORED_SERVICES entries
  // for each one's role); the service name IS the module name (spawn
  // contract). Grouped under the stream sub-scope (the always-on
  // services). Without these, the monitor page files the running daemons
  // as batch pipeline processes.
  { service: "llm_agents.mcp", scope: "database", subScope: "stream" },
  { service: "llm_agents.sandbox", scope: "database", subScope: "stream" },
  { service: "llm_agents.deep_agents.server", scope: "database", subScope: "stream" },
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
