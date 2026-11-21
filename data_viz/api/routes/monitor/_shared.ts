/**
 * Symbols shared by two or more monitor route subfiles: the always-on
 * service catalog + staleness constant, the freq auto-skip windows, and
 * the repo-root module-existence check.
 * Full route docs: see index.ts.
 */

import { existsSync } from "fs";
import path from "path";

// Mirror of _common/service_heartbeat.py (MONITORED_SERVICES + the area
// servers' resident tables — plus STALE_AFTER_S); keep in sync. The
// heartbeat table only tells us which services HAVE beaten, not which
// SHOULD, so the expected set lives here. Catalog members are the RESIDENT
// processes — the always-on services (the first Service Monitor table);
// every other heartbeat row is a batch pipeline process (the second table).
// Supervision (the completed 2026-11 resident migration): members WITH
// `supervisedBy` are their area cluster server's residents — the monitor's
// supervisor owns their desired state (auto-start, crash-guarded revival,
// persisted manual stops, hung-runtime kills; restarts execute on the
// maintainer executor). The ONE member WITHOUT `supervisedBy` is the
// monitor server itself — the stream watchdog's scan charge (the mutual
// pair: the monitor holds the watchdog's life as a resident).
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
  // revives dead ones (crash-loop guarded) and honors manual stops.
  { service: "downloads.stream.sse.price", scope: "downloads", subScope: "stream", supervisedBy: "server_cluster.downloader" },
  { service: "downloads.stream.szse.price", scope: "downloads", subScope: "stream", supervisedBy: "server_cluster.downloader" },
  { service: "downloads.stream.bse.price", scope: "downloads", subScope: "stream", supervisedBy: "server_cluster.downloader" },
  { service: "downloads.stream.csindex.price", scope: "downloads", subScope: "stream", supervisedBy: "server_cluster.downloader" },
  { service: "downloads.stream.cnindex.price", scope: "downloads", subScope: "stream", supervisedBy: "server_cluster.downloader" },
  // The server-cluster FastAPI servers (server_cluster/, ports 8767-8775)
  // — ALL of them the monitor area's residents (the seven SERVER_RESIDENTS
  // since 2026-11): the downloader (8769) additionally supervises the
  // pollers above; the maintainer (8775) is the cluster's ONE
  // resident-process EXECUTOR.
  { service: "server_cluster.downloader", scope: "server_cluster", subScope: "downloader", supervisedBy: "server_cluster.monitor" },
  { service: "server_cluster.builder", scope: "server_cluster", subScope: "builder", supervisedBy: "server_cluster.monitor" },
  { service: "server_cluster.analyzer", scope: "server_cluster", subScope: "analyzer", supervisedBy: "server_cluster.monitor" },
  { service: "server_cluster.strategist", scope: "server_cluster", subScope: "strategist", supervisedBy: "server_cluster.monitor" },
  // Local-model host (2026-11; migrated from llm_agents.local_model_host
  // into the cluster's llm area, port 8767): the resident FastAPI over
  // the repo's local AI models (OpenAI chat completions incl. the
  // migrated dummy-1 provider simulation, the StructBERT segmenter, the
  // OneJev decision model + the load/offload management API) — absorbed
  // the former standalone dummy-llm server (:8768, test/dummy_llm).
  { service: "server_cluster.llm", scope: "server_cluster", subScope: "llm", supervisedBy: "server_cluster.monitor" },
  // Read-only configuration area (2026-11, port 8774) + maintenance area
  // (2026-11, port 8775): the config API and the cluster's scheduler
  // (03:30 maintenance, 07:00 fleet bring-up, 16:00 trading-day nightly
  // pipeline chain — the retired sh tier's successor) + resident-process
  // executor — monitor residents like the rest.
  { service: "server_cluster.config", scope: "server_cluster", subScope: "config", supervisedBy: "server_cluster.monitor" },
  { service: "server_cluster.maintainer", scope: "server_cluster", subScope: "maintainer", supervisedBy: "server_cluster.monitor" },
  // The monitor server itself (port 8773) — the fleet's ops area and the
  // stream watchdog's ONE scan charge (a server cannot supervise itself;
  // the mutual pair keeps both alive).
  { service: "server_cluster.monitor", scope: "server_cluster", subScope: "monitor" },
  // DB health sampler — a monitor-area resident (2026-11 migration;
  // stream sub-scope).
  { service: "database.db_health", scope: "database", subScope: "stream", supervisedBy: "server_cluster.monitor" },
  // Per-table DB workload audit sampler — the per-TABLE side of db_health
  // (pg_stat_user_tables deltas -> monitor.db_table_audit; write-heavy
  // flag transitions -> monitor.db_table_alert). Same stream contract.
  { service: "database.db_table_audit", scope: "database", subScope: "stream", supervisedBy: "server_cluster.monitor" },
  // SQL statement audit sampler — the statement-level side (pg_stat_
  // statements deltas -> monitor.sql_audit, keyed by sha256 of the
  // normalized statement text). Same stream contract.
  { service: "database.sql_audit", scope: "database", subScope: "stream", supervisedBy: "server_cluster.monitor" },
  // The llm_agents daemons — monitor-area residents (2026-11 migration;
  // see _common/service_heartbeat.py's resident-table comments for each
  // one's role); the service name IS the module name (spawn contract).
  // Grouped under the stream sub-scope (the always-on services). Without
  // these, the monitor page files the running daemons as batch pipeline
  // processes.
  { service: "llm_agents.mcp", scope: "database", subScope: "stream", supervisedBy: "server_cluster.monitor" },
  { service: "llm_agents.sandbox", scope: "database", subScope: "stream", supervisedBy: "server_cluster.monitor" },
  { service: "llm_agents.deep_agents.server", scope: "database", subScope: "stream", supervisedBy: "server_cluster.monitor" },
  // The stream watchdog itself — the boot-hook root AND a monitor-area
  // resident (the mutual pair; the monitor adopts the boot-started
  // instance and revives it when it dies).
  { service: WATCHDOG_SERVICE, scope: "database", subScope: "monitoring", supervisedBy: "server_cluster.monitor" },
];

/** Freq → auto-skip window in ms (NON-RESIDENT tasks — a resident
 *  carries freq null and never auto-skips). `null` = never auto-skip
 *  (ad-hoc / continuous); `Infinity`-marker SKIP_ANY_SUCCESS = one
 *  success ever satisfies the freq. Unknown / null freq values fall
 *  back to never-skip. The retired "always" value moved to the
 *  service_config tree's is_resident flag (2026-11-20). */
export const SKIP_ANY_SUCCESS = Number.POSITIVE_INFINITY;
export const FREQ_PERIOD_MS: Record<string, number | null> = {
  daily: 24 * 3600_000,
  weekly: 7 * 24 * 3600_000,
  monthly: 30 * 24 * 3600_000,
  quarterly: 92 * 24 * 3600_000,
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
