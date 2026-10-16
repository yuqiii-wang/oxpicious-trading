/**
 * Static JSON config service — shared read/write access to the repo's
 * static config files under database/sql/monitoring/static/.
 *
 * These files replaced the monitor.service_config and
 * monitor.external_request_config DB tables (10_static_json_config.sql
 * dropped them 2026-10-10): the CONFIG of record lives in git, so config
 * changes are code-reviewed edits instead of out-of-band UPDATEs. The
 * Python side reads the same files — _common/service_heartbeat.py's
 * max_parallel_for() is the runtime claim guard's cap source and must
 * stay shape-compatible with the service_config/ tree.
 *
 * Files (snake_case keys mirroring the retired tables' columns):
 *
 *   service_config/ — a DIRECTORY TREE, one file per service.
 *     The service's dotted name IS the path:
 *     downloads/stream/szse/price.json ← "downloads.stream.szse.price".
 *     Each file holds that service's ONE config object:
 *     { service, max_parallel, included_parallel_services,
 *     excluded_parallel_services, freq, schedule, notes, source_ref (the
 *     config's code/script location(s) as RELATIVE FILE PATHS only —
 *     never line numbers; ';'-separated when several), updated_at }.
 *     max_parallel is the concurrency CAP (read at registration by
 *     _common/service_heartbeat.py — keep the shape compatible); freq is
 *     the SUPPOSED-RUN cadence mirrored from scripts/*.sh (always |
 *     daily | monthly | semiannually | once | occasionally), shown on
 *     the Service Monitor page's Pipeline processes table. The two
 *     parallel-services lists gate CROSS-service parallelism (empty
 *     included = unrestricted; non-empty = whitelist; excluded = denylist;
 *     entries are rooted dotted prefixes — a scope, a sub-scope path, or
 *     the full service name; more specific wins on conflict, name >
 *     sub-scope > scope, ties to excluded) — enforced at registration by
 *     _common/service_heartbeat.py and previewed by the UI run guard.
 *
 *     default.json (tree ROOT, not part of the inventory) is the
 *     FALLBACK for services without their own file: max_parallel cap 1
 *     + freq "on_demand" (lookups substitute the requesting service's
 *     name).
 *
 *   external_request_config/ — a DIRECTORY TREE, one file per service.
 *     The service's dotted name IS the path (same layout):
 *     downloads/stream/szse/price.json ← "downloads.stream.szse.price".
 *     Each file holds ONLY that service's rows (an array sorted by
 *     request_type; the service field stays on every row so reads can
 *     concat blindly): { service, request_type, resource_type (0 http |
 *     1 llm label code — decode at the boundary via
 *     decodeLabel("ext_resource")), sleep_sec, sleep_jitter,
 *     poll_interval_sec, max_retries, retry_backoff_sec,
 *     timeout_connect_sec, timeout_read_sec, rotate_browser_profile,
 *     add_random_param, enable_host_tracking, host_block_cooldown_sec,
 *     notes, source_ref (the config's code location(s) as RELATIVE FILE
 *     PATHS only — never line numbers; ';'-separated when several),
 *     updated_at } — the per-(service, request_type)
 *     pacing / anti-bot inventory.
 *
 *     default.json (tree ROOT, not part of the inventory) is the
 *     FALLBACK for services without their own file: extRequestConfigFor
 *     substitutes it (repo-wide 60s anti-bot cooldown) whenever a
 *     service file is missing.
 *
 * Reads are cached per file mtime (the files are tiny; the cache only
 * exists so a burst of requests in one poll tick doesn't re-read the
 * disk). Writes stamp updated_at, keep the files' canonical shape
 * (sorted keys / rows, 2-space indent + trailing newline — the same
 * format the migration generator wrote) and land atomically: temp file
 * in the same dir, then rename, so a concurrent reader never sees a
 * torn file.
 */
import fs from "fs";
import path from "path";

// ---------------------------------------------------------------------------
//  Paths — database/sql/monitoring/static/ (three levels above this
//  file's services/ dir → repo root, same resolution as db.service.ts).
// ---------------------------------------------------------------------------
const STATIC_CONFIG_DIR = path.resolve(
  import.meta.dirname ?? __dirname,
  "..",
  "..",
  "..",
  "database",
  "sql",
  "monitoring",
  "static",
);
const SERVICE_CONFIG_DIR = path.join(STATIC_CONFIG_DIR, "service_config");
/** The fallback file at the tree root — NOT part of the per-service
 *  inventory (excluded from the walk and protected on the write path). */
const SERVICE_CONFIG_DEFAULT_PATH = path.join(SERVICE_CONFIG_DIR, "default.json");
const EXT_REQUEST_CONFIG_DIR = path.join(
  STATIC_CONFIG_DIR,
  "external_request_config",
);
/** The fallback file at the tree root — NOT part of the per-service
 *  inventory (excluded from the walk and protected on the write path). */
const EXT_REQUEST_DEFAULT_PATH = path.join(
  EXT_REQUEST_CONFIG_DIR,
  "default.json",
);

/** The repo-wide default when a service has no override (mirror of
 *  _common/service_heartbeat.py DEFAULT_MAX_PARALLEL). */
export const DEFAULT_MAX_PARALLEL = 1;
/** The fallback freq when a service has no file of its own (the
 *  default.json entry's value). */
export const DEFAULT_SERVICE_FREQ = "on_demand";

// ---------------------------------------------------------------------------
//  Shapes
// ---------------------------------------------------------------------------

/** One service_config/ tree entry — a service's concurrency cap + its
 *  supposed-run cadence mirrored from scripts/*.sh + its cross-service
 *  parallel-services lists (included = whitelist, empty = unrestricted;
 *  excluded = denylist; entries are rooted dotted prefixes — scope,
 *  sub-scope path, or full service name; more specific wins on conflict,
 *  ties to excluded; same contract as _common/service_heartbeat.py). */
export interface ServiceConfigEntry {
  service: string;
  max_parallel: number;
  included_parallel_services: string[];
  excluded_parallel_services: string[];
  /** always | daily | monthly | semiannually | once | occasionally
   *  (default.json fallback: on_demand). */
  freq: string;
  /** Human-readable timing detail (null on the fallback). */
  schedule: string | null;
  notes: string | null;
  source_ref: string;
  updated_at: string;
}

/** One external-request config row (snake_case = the retired
 *  table's columns; resource_type is still the static.label_dim
 *  'ext_resource' SMALLINT code — 0 http | 1 llm). */
export interface ExtRequestConfigRow {
  service: string;
  request_type: string;
  resource_type: number;
  sleep_sec: number | null;
  sleep_jitter: number | null;
  poll_interval_sec: number | null;
  max_retries: number | null;
  retry_backoff_sec: number | null;
  timeout_connect_sec: number | null;
  timeout_read_sec: number | null;
  rotate_browser_profile: boolean;
  add_random_param: boolean;
  enable_host_tracking: boolean;
  host_block_cooldown_sec: number | null;
  notes: string | null;
  source_ref: string;
  updated_at: string;
}

// ---------------------------------------------------------------------------
//  mtime-cached read
// ---------------------------------------------------------------------------

interface CacheEntry<T> {
  mtimeMs: number;
  value: T;
}

const cache = new Map<string, CacheEntry<unknown>>();

/** mtime-cached JSON read — shared with orchestration.service (the
 *  scripts/orchestration/ step files use the same read discipline). */
export function readJsonCached<T>(file: string, fallback: T): T {
  let stat: fs.Stats;
  try {
    stat = fs.statSync(file);
  } catch {
    cache.delete(file);
    return fallback;
  }
  const hit = cache.get(file);
  if (hit && hit.mtimeMs === stat.mtimeMs) {
    return hit.value as T;
  }
  try {
    const value = JSON.parse(fs.readFileSync(file, "utf-8")) as T;
    cache.set(file, { mtimeMs: stat.mtimeMs, value });
    return value;
  } catch (err) {
    // Unparseable config must never take the API down — serve the
    // fallback; the write path or a git edit fixes the file.
    console.error(`[static-config] could not parse ${file}:`, err);
    cache.delete(file);
    return fallback;
  }
}

// ---------------------------------------------------------------------------
//  service_config/ — the per-service tree (cap + supposed-run freq)
// ---------------------------------------------------------------------------

const SERVICE_ENTRY_FALLBACK: ServiceConfigEntry = {
  service: "",
  max_parallel: DEFAULT_MAX_PARALLEL,
  included_parallel_services: [],
  excluded_parallel_services: [],
  freq: DEFAULT_SERVICE_FREQ,
  schedule: null,
  notes: null,
  source_ref: "",
  updated_at: "",
};

/** A valid parallel-services entry: a rooted dotted prefix of a service
 *  name (scope / sub-scope path / full name — same segment charset). */
const DOTTED_PREFIX_RE = /^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*$/;

/** JSON value → sanitized parallel-services list: string entries matching
 *  the dotted-prefix pattern are kept (deduped, first occurrence wins),
 *  anything else dropped — a malformed entry must never block a read. */
export function normalizeParallelServices(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  const out: string[] = [];
  for (const v of value) {
    if (typeof v === "string" && DOTTED_PREFIX_RE.test(v) && !out.includes(v)) {
      out.push(v);
    }
  }
  return out;
}

/** Every valid parallel-services entry, derived from the EXISTING
 *  service_config/ inventory: split each service name on "." and hash
 *  every dotted prefix — one segment = a scope ("downloads"), two or more
 *  = a sub-scope path ("downloads.stock"), the full name = a service.
 *  The write path (PUT /api/config/service) rejects entries outside this
 *  set, so a list can only ever name real scopes / services. */
export function validParallelServiceEntries(): Set<string> {
  const entries = new Set<string>();
  for (const { service } of readServiceConfig()) {
    const segments = service.split(".");
    for (let i = 1; i <= segments.length; i++) {
      entries.add(segments.slice(0, i).join("."));
    }
  }
  return entries;
}

function normalizeServiceEntry(doc: unknown, service: string): ServiceConfigEntry {
  const d = (
    typeof doc === "object" && doc != null ? doc : {}
  ) as Partial<ServiceConfigEntry>;
  return {
    service,
    max_parallel:
      typeof d.max_parallel === "number" &&
      Number.isInteger(d.max_parallel) &&
      d.max_parallel >= 1
        ? d.max_parallel
        : DEFAULT_MAX_PARALLEL,
    included_parallel_services: normalizeParallelServices(
      d.included_parallel_services,
    ),
    excluded_parallel_services: normalizeParallelServices(
      d.excluded_parallel_services,
    ),
    freq: typeof d.freq === "string" && d.freq !== "" ? d.freq : DEFAULT_SERVICE_FREQ,
    schedule: typeof d.schedule === "string" ? d.schedule : null,
    notes: typeof d.notes === "string" ? d.notes : null,
    source_ref: typeof d.source_ref === "string" ? d.source_ref : "",
    updated_at: typeof d.updated_at === "string" ? d.updated_at : "",
  };
}

/** The default.json fallback entry, verbatim (service stays "default" —
 *  a pseudo-name; substitute per service on lookup). */
export function readDefaultServiceConfig(): ServiceConfigEntry {
  return normalizeServiceEntry(
    readJsonCached<unknown>(SERVICE_CONFIG_DEFAULT_PATH, {}),
    "default",
  );
}

/** The whole service-config inventory, one entry per tree file, sorted
 *  by service. Does NOT include the default.json fallback (read it via
 *  readDefaultServiceConfig / serviceConfigFor). */
export function readServiceConfig(): ServiceConfigEntry[] {
  return listServiceConfigFiles(SERVICE_CONFIG_DIR, SERVICE_CONFIG_DEFAULT_PATH)
    .map((file) =>
      normalizeServiceEntry(
        readJsonCached<unknown>(
          file,
          SERVICE_ENTRY_FALLBACK,
        ),
        serviceFromTreePath(SERVICE_CONFIG_DIR, file),
      ),
    )
    .sort((a, b) => a.service.localeCompare(b.service));
}

/** One service's config entry: its own tree file when it has one, else
 *  the default.json fallback (cap 1, freq on_demand) under the service's
 *  name. */
export function serviceConfigFor(service: string): ServiceConfigEntry {
  const fallback = { ...readDefaultServiceConfig(), service };
  return normalizeServiceEntry(
    readJsonCached<unknown>(dottedServicePath(SERVICE_CONFIG_DIR, service), fallback),
    service,
  );
}

/** One service's concurrency cap: its tree entry's max_parallel
 *  (fallback entry included). */
export function maxParallelFor(service: string): number {
  return serviceConfigFor(service).max_parallel;
}

// ---------------------------------------------------------------------------
//  external_request_config/ — the per-service tree
// ---------------------------------------------------------------------------

/** Service dotted name ↔ tree path within ``dir``: "downloads.stream.szse.price"
 *  ↔ downloads/stream/szse/price.json. Segments are validated on every use
 *  so a bad name throws instead of escaping the tree. */
function dottedServicePath(dir: string, service: string): string {
  const segments = service.split(".");
  if (
    !service ||
    segments.some((s) => !/^[A-Za-z0-9_-]+$/.test(s))
  ) {
    throw new Error(`[static-config] unusable service name: ${service}`);
  }
  return `${path.join(dir, ...segments)}.json`;
}

function serviceFromTreePath(dir: string, file: string): string {
  return path
    .relative(dir, file)
    .replace(/\.json$/, "")
    .split(path.sep)
    .join(".");
}

/** Recursively list the per-service *.json files under a tree (the
 *  root-level default.json fallback is NOT a service — excluded). */
function listServiceConfigFiles(
  dir: string,
  defaultPath: string,
): string[] {
  let entries: fs.Dirent[];
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return []; // no tree (yet) or unreadable → empty inventory
  }
  const files: string[] = [];
  for (const entry of entries) {
    const p = path.join(dir, entry.name);
    if (entry.isDirectory()) files.push(...listServiceConfigFiles(p, defaultPath));
    else if (entry.isFile() && entry.name.endsWith(".json") && p !== defaultPath) {
      files.push(p);
    }
  }
  return files;
}

function byServiceThenType(a: ExtRequestConfigRow, b: ExtRequestConfigRow): number {
  return (
    a.service.localeCompare(b.service) ||
    a.request_type.localeCompare(b.request_type)
  );
}

/** The whole external-request pacing inventory, concatenated from the
 *  per-service files and sorted by (service, request_type) — the retired
 *  table's ORDER BY. Does NOT include the default.json fallback (read it
 *  via readDefaultExtRequestConfig / extRequestConfigFor). */
export function readExternalRequestConfig(): ExtRequestConfigRow[] {
  const rows: ExtRequestConfigRow[] = [];
  for (const file of listServiceConfigFiles(
    EXT_REQUEST_CONFIG_DIR,
    EXT_REQUEST_DEFAULT_PATH,
  )) {
    rows.push(...readJsonCached<ExtRequestConfigRow[]>(file, []));
  }
  return rows.sort(byServiceThenType);
}

/** The default.json fallback rows, verbatim (service stays "default" —
 *  a pseudo-name; substitute per service on lookup). */
export function readDefaultExtRequestConfig(): ExtRequestConfigRow[] {
  return readJsonCached<ExtRequestConfigRow[]>(EXT_REQUEST_DEFAULT_PATH, []);
}

/** One service's request-config rows: its own file when it has one, else
 *  the default.json fallback cloned under the service's name (the
 *  repo-wide 60s anti-bot cooldown). The per-service lookup the UI (and,
 *  eventually, runtime consumers) ask. */
export function extRequestConfigFor(service: string): ExtRequestConfigRow[] {
  const own = dottedServicePath(EXT_REQUEST_CONFIG_DIR, service);
  try {
    if (fs.statSync(own).isFile()) {
      return readJsonCached<ExtRequestConfigRow[]>(own, []);
    }
  } catch {
    // fall through to the default
  }
  return readDefaultExtRequestConfig().map((r) => ({ ...r, service }));
}

/** Write external_request_config/ from a whole-inventory row list:
 *  rows are regrouped per service (each file re-sorted by request_type,
 *  written atomically), files of services no longer present are removed,
 *  and dirs their removal emptied are pruned. Callers editing one row:
 *  read, map, write — the write stamps nothing itself, so set updated_at
 *  on the rows you change. */
export function writeExternalRequestConfig(rows: ExtRequestConfigRow[]): void {
  const byService = new Map<string, ExtRequestConfigRow[]>();
  for (const row of rows) {
    if (row.service === "default") {
      throw new Error(
        "[static-config] service 'default' is the reserved fallback " +
          "pseudo-name — edit default.json directly instead",
      );
    }
    const list = byService.get(row.service);
    if (list) list.push(row);
    else byService.set(row.service, [row]);
  }
  for (const [service, svcRows] of byService) {
    writeJsonFile(
      dottedServicePath(EXT_REQUEST_CONFIG_DIR, service),
      svcRows.sort(byServiceThenType),
    );
  }
  const keep = new Set(byService.keys());
  for (const file of listServiceConfigFiles(EXT_REQUEST_CONFIG_DIR, EXT_REQUEST_DEFAULT_PATH)) {
    if (!keep.has(serviceFromTreePath(EXT_REQUEST_CONFIG_DIR, file))) fs.unlinkSync(file);
  }
  pruneEmptyDirs(EXT_REQUEST_CONFIG_DIR, EXT_REQUEST_CONFIG_DIR);
}

/** Write ONE service's rows into the external_request_config/ tree
 *  (read-modify-write of the whole inventory so ONLY that service's file
 *  changes): service "default" rewrites the root default.json fallback;
 *  any other service rewrites its own tree file, and an EMPTY row list
 *  REMOVES the service's file (so the default fallback applies again).
 *  Rows are written verbatim except for the (service, request_type)
 *  regrouping sort — callers stamp updated_at on what they change. */
export function writeExtRequestRowsFor(
  service: string,
  rows: ExtRequestConfigRow[],
): void {
  if (service === "default") {
    writeJsonFile(
      EXT_REQUEST_DEFAULT_PATH,
      [...rows].sort(byServiceThenType),
    );
    return;
  }
  const others = readExternalRequestConfig().filter((r) => r.service !== service);
  writeExternalRequestConfig([...others, ...rows]);
}

/** Write ONE service_config/ entry — its own tree file, or the root
 *  default.json fallback when service is "default". Stamps updated_at
 *  and writes the canonical shape atomically; returns the entry as
 *  written. */
export function writeServiceConfigEntry(entry: ServiceConfigEntry): ServiceConfigEntry {
  const stamped: ServiceConfigEntry = {
    ...normalizeServiceEntry(entry, entry.service),
    updated_at: new Date().toISOString(),
  };
  const file =
    entry.service === "default"
      ? SERVICE_CONFIG_DEFAULT_PATH
      : dottedServicePath(SERVICE_CONFIG_DIR, entry.service);
  writeJsonFile(file, stamped);
  return stamped;
}

/** Remove ONE service's service_config/ tree file (the default.json
 *  fallback then applies to it). Returns false when the service had no
 *  file of its own. */
export function deleteServiceConfigEntry(service: string): boolean {
  if (service === "default") {
    throw new Error(
      "[static-config] the default.json fallback cannot be deleted — edit it instead",
    );
  }
  const file = dottedServicePath(SERVICE_CONFIG_DIR, service);
  try {
    fs.unlinkSync(file);
  } catch (err) {
    if ((err as { code?: string }).code === "ENOENT") return false;
    throw err;
  }
  pruneEmptyDirs(path.dirname(file), SERVICE_CONFIG_DIR);
  return true;
}

/** Remove now-empty dirs: everything below ``dir`` (deepest first), then
 *  ``dir`` itself and its empty ancestors up to (never including) the
 *  tree ``root``. */
function pruneEmptyDirs(dir: string, root: string): void {
  let entries: fs.Dirent[];
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return;
  }
  for (const entry of entries) {
    if (entry.isDirectory()) pruneEmptyDirs(path.join(dir, entry.name), root);
  }
  let cur = dir;
  while (path.relative(root, cur) !== "") {
    try {
      if (fs.readdirSync(cur).length > 0) return;
      fs.rmdirSync(cur);
    } catch {
      return;
    }
    cur = path.dirname(cur);
  }
}

// ---------------------------------------------------------------------------
//  Atomic write
// ---------------------------------------------------------------------------

function writeJsonFile(file: string, value: unknown): void {
  const body = JSON.stringify(value, null, 2) + "\n";
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const tmp = `${file}.tmp-${process.pid}`;
  fs.writeFileSync(tmp, body, "utf-8");
  renameOver(tmp, file);
}

/** rename over an existing file, retrying the Windows transient: a
 *  concurrent reader / AV scan briefly holding the destination turns
 *  renameSync into EPERM (2026-10-10 round-trip test). Shared with
 *  sec-statics.service (same atomic-write mechanics). */
export function renameOver(tmp: string, file: string): void {
  for (let attempt = 0; ; attempt++) {
    try {
      fs.renameSync(tmp, file);
      return;
    } catch (err) {
      if ((err as { code?: string }).code !== "EPERM" || attempt >= 4) {
        throw err;
      }
      Atomics.wait(
        new Int32Array(new SharedArrayBuffer(4)), 0, 0, 50 * (attempt + 1),
      );
    }
  }
}
