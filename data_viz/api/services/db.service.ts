/**
 * Database service — read/write split across two pg.Pools (primary + replica).
 *
 * This is the canonical DB access layer for the API. All services import the
 * pools, query helpers, and value formatters from here.
 *
 * Routing mirrors the Python layer (_common/db_commons/_router.py):
 * read-only statements (SELECT / WITH / TABLE / SHOW / EXPLAIN / VALUES,
 * scanned for hidden writes in WITH bodies) go to the read-only replica
 * pool (SUPABASE_REPLICA_HOST:SUPABASE_REPLICA_PORT, falling back to the
 * primary when unset); writes and unclassifiable statements go to the
 * primary pool (SUPABASE_HOST:SUPABASE_PORT). getClient() always hands
 * out a primary client (multi-statement transactions).
 *
 * REPLICA FAILOVER (2026-09-21): the replica can fall behind or cancel
 * queries while it replays a WAL burst (a --force rebuild streams GBs of
 * WAL; queries needing row versions the primary already vacuumed are
 * cancelled with SQLSTATE 40001 after max_standby_streaming_delay, and
 * during catch-up the whole replica can refuse connections). Read-routed
 * queries therefore (1) consult a 10s-cached replica-lag probe and go
 * straight to the primary while the replica is > REPLICA_MAX_LAG_S
 * behind, and (2) always run with a 10s statement_timeout on the
 * replica — on a lag/timeout/connection error the query retries ONCE on
 * the primary. hot_standby_feedback=on on the primary (set via ALTER
 * SYSTEM 2026-09-21) removes most 40001s at the source; this failover
 * is the belt-and-braces layer for rebuild windows.
 *
 * Reads connection parameters from `database/.env` (SUPABASE_* variables).
 * The pools are lazily created on first use and reused for all subsequent
 * queries.  Call `closePool()` on process shutdown to release connections.
 *
 * Best practices applied:
 *  - Connection pooling (pg.Pool with sensible defaults)
 *  - Parameterized queries ($1, $2, …) to prevent SQL injection and enable
 *    prepared-statement caching on the server side
 *  - Index-driven WHERE clauses (date ranges, code lookups) instead of
 *    loading entire tables into memory
 *  - Schema-qualified table/view names (stats.*) to avoid search_path
 *    ambiguity
 */
import fs from "fs";
import path from "path";
import dotenv from "dotenv";
import { Pool, types, type PoolClient, type QueryResult, type QueryResultRow } from "pg";

// ----------------------------------------------------------------------------
//  Type parsers — return DATE columns as raw "YYYY-MM-DD" strings.
//
//  By default pg parses DATE (OID 1082) into a JS Date object at midnight LOCAL
//  time.  When callers later call `toISOString()` to format the date, the UTC
//  conversion shifts the day by one in non-UTC timezones (e.g. UTC+8 displays
//  "2025-07-01" as "2025-06-30").  Returning the raw string avoids this entire
//  class of bugs: a DATE has no time component, so no timezone math is needed.
// ----------------------------------------------------------------------------
types.setTypeParser(1082, (val: string) => val); // DATE
types.setTypeParser(1114, (val: string) => val); // TIMESTAMP WITHOUT TIME ZONE

// ----------------------------------------------------------------------------
//  Config — load database/.env once
// ----------------------------------------------------------------------------
let _envLoaded = false;
function loadDbEnv(): void {
  if (_envLoaded) return;
  // The .env file lives at <repo>/database/.env — three levels above this
  // file's services/ directory (data_viz/api/services → repo root).
  const envPath = path.resolve(
    import.meta.dirname ?? __dirname,
    "..",
    "..",
    "..",
    "database",
    ".env",
  );
  if (fs.existsSync(envPath)) {
    dotenv.config({ path: envPath });
  }
  // No root .env fallback — database/.env is the single source of truth.
  // If it's absent, rely on already-set environment variables.
  _envLoaded = true;
}

export interface DbConfig {
  host: string;
  port: number;
  database: string;
  user: string;
  password: string;
  max: number;          // max pool size
  idleTimeoutMillis: number;
  connectionTimeoutMillis: number;
}

export function getDbConfig(): DbConfig {
  loadDbEnv();
  return {
    host:     process.env.SUPABASE_HOST ?? "127.0.0.1",
    port:     parseInt(process.env.SUPABASE_PORT ?? "9876", 10),
    database: process.env.SUPABASE_DB ?? "oxpicious-stats",
    user:     process.env.SUPABASE_USER ?? "postgres",
    password: process.env.SUPABASE_PASSWORD ?? "postgres",
    max:      10,
    idleTimeoutMillis: 30_000,
    connectionTimeoutMillis: 5_000,
  };
}

/**
 * Connection parameters for the read-only replica (SUPABASE_REPLICA_*).
 * Falls back to the primary host/port when no replica is configured, so
 * environments without a replica (CI, remote deployments) still work —
 * same fallback contract as Python's _get_replica_conn_params().
 */
export function getReplicaDbConfig(): DbConfig {
  const cfg = getDbConfig();
  return {
    ...cfg,
    host: process.env.SUPABASE_REPLICA_HOST ?? cfg.host,
    port: parseInt(process.env.SUPABASE_REPLICA_PORT ?? String(cfg.port), 10),
  };
}

// ----------------------------------------------------------------------------
//  Replica failover knobs (see the module docstring)
// ----------------------------------------------------------------------------
/** statement_timeout on every replica-pool connection — a busy replaying
 *  replica fails fast into the primary retry instead of hanging the API. */
const REPLICA_STATEMENT_TIMEOUT_MS = 10_000;
/** A replica whose last replayed commit is older than this is "busy" —
 *  reads skip it for the next probe window. Generous: an idle primary
 *  with no writes also shows a growing gap, so this must only trip on
 *  genuine backlog (rebuilds stream WAL continuously). */
const REPLICA_MAX_LAG_S = 300;
/** How long a replica-lag probe result is cached. */
const REPLICA_PROBE_TTL_MS = 10_000;
/** The probe's own budget (connection + query). */
const REPLICA_PROBE_TIMEOUT_MS = 3_000;

/** Cached replica health: lag seconds (null = probe failed = unhealthy). */
let _replicaLag: { value: number | null; checkedAt: number } | null = null;

/**
 * SQLSTATEs that mean "the replica can't serve this right now": recovery
 * conflict (40001 — cancelled query during WAL replay), query canceled /
 * statement_timeout (57014), server shutdown states (57P01–57P03), and
 * connection-class failures (08xxx).
 */
function isReplicaUnavailableError(err: unknown): boolean {
  const code = (err as { code?: string }).code;
  if (code === "40001" || code === "57014") return true;
  if (typeof code === "string" && code.startsWith("57")) return true; // 57P0x shutdowns
  if (typeof code === "string" && code.startsWith("08")) return true; // 08xxx connection
  return false;
}

/**
 * Probe the replica's replay lag once per TTL. Cheap on purpose: one
 * scalar SELECT against the replica's own WAL state with a 3s budget —
 * a replica stuck in recovery refuses/fails it fast. Never throws.
 */
async function replicaLagSeconds(): Promise<number | null> {
  const now = Date.now();
  if (_replicaLag && now - _replicaLag.checkedAt < REPLICA_PROBE_TTL_MS) {
    return _replicaLag.value;
  }
  let value: number | null = null;
  try {
    const probe = getReadPool().query<{ lag_s: number | null }>(
      "SELECT EXTRACT(EPOCH FROM (clock_timestamp() - pg_last_wal_replay_timestamp()))::float8 AS lag_s",
    );
    const raced = await Promise.race([
      probe,
      new Promise<null>((resolve) =>
        setTimeout(() => resolve(null), REPLICA_PROBE_TIMEOUT_MS)),
    ]);
    value = raced ? (raced.rows[0]?.lag_s ?? null) : null;
  } catch {
    value = null;
  }
  _replicaLag = { value, checkedAt: now };
  return value;
}

// ----------------------------------------------------------------------------
//  Pool — lazily created singleton
// ----------------------------------------------------------------------------
let _pool: Pool | null = null;

export function getPool(): Pool {
  if (_pool) return _pool;
  const cfg = getDbConfig();
  _pool = new Pool({
    host: cfg.host,
    port: cfg.port,
    database: cfg.database,
    user: cfg.user,
    password: cfg.password,
    max: cfg.max,
    idleTimeoutMillis: cfg.idleTimeoutMillis,
    connectionTimeoutMillis: cfg.connectionTimeoutMillis,
  });
  // Log unexpected pool errors (e.g. idle client disconnect)
  _pool.on("error", (err) => {
    console.error("[db] pool error:", err.message);
  });
  console.log(
    `[db] pool created → ${cfg.user}@${cfg.host}:${cfg.port}/${cfg.database} (max=${cfg.max})`,
  );
  return _pool;
}

// ----------------------------------------------------------------------------
//  Read pool — lazily created singleton for the read-only replica
// ----------------------------------------------------------------------------
let _readPool: Pool | null = null;

export function getReadPool(): Pool {
  if (_readPool) return _readPool;
  const cfg = getReplicaDbConfig();
  _readPool = new Pool({
    host: cfg.host,
    port: cfg.port,
    database: cfg.database,
    user: cfg.user,
    password: cfg.password,
    max: cfg.max,
    idleTimeoutMillis: cfg.idleTimeoutMillis,
    connectionTimeoutMillis: cfg.connectionTimeoutMillis,
    // Per-connection statement timeout — the replica failover budget: a
    // replaying replica cancels the query at 10s and query() retries on
    // the primary instead of hanging the request for the full
    // max_standby_streaming_delay (30s+).
    options: `-c statement_timeout=${REPLICA_STATEMENT_TIMEOUT_MS}`,
  });
  // Session-level read-only guard on every replica connection — mirrors the
  // Python router's _configure_replica(). The SET is queued on the client
  // before any query handed out by the pool, so socket write order makes it
  // effective even for the first query on a fresh connection.
  _readPool.on("connect", (client) => {
    void client.query("SET default_transaction_read_only = on");
  });
  _readPool.on("error", (err) => {
    console.error("[db] read pool error:", err.message);
  });
  console.log(
    `[db] read pool created → ${cfg.user}@${cfg.host}:${cfg.port}/${cfg.database} (max=${cfg.max})`,
  );
  return _readPool;
}

// ----------------------------------------------------------------------------
//  Read/write classification — port of _common/db_commons/_router.py
// ----------------------------------------------------------------------------

// Statements that mutate state — always routed to the primary.
const WRITE_STMTS =
  /^(INSERT|UPDATE|DELETE|MERGE|UPSERT|TRUNCATE|CREATE|DROP|ALTER|GRANT|REVOKE|COPY|VACUUM|ANALYZE|REINDEX|CLUSTER|COMMENT|LOCK|SET|RESET|CALL|DO|LISTEN|NOTIFY|REFRESH)\b/i;

// Explicitly read-only statements — routed to the replica.
const READ_STMTS = /^(SELECT|WITH|TABLE|SHOW|EXPLAIN|VALUES)\b/i;

const WITH_BODY_WRITE =
  /\b(INSERT|UPDATE|DELETE|MERGE|TRUNCATE|FOR\s+UPDATE|FOR\s+SHARE)\b/i;

/**
 * Return true if the SQL statement is read-only (safe for the replica).
 *
 * A CTE (WITH) containing INSERT/UPDATE/DELETE is a write, so WITH
 * statements are conservatively scanned for write keywords. Unknown
 * statement types fall back to the primary (safe default), matching
 * is_read_query() in the Python router.
 */
export function isReadQuery(text: string): boolean {
  // Strip leading comments (-- and /* */) that may precede the statement.
  const stripped = text.replace(/^(--[^\n]*\n|\/\*[\s\S]*?\*\/|\s)+/, "");
  if (!stripped) return false;
  if (WRITE_STMTS.test(stripped)) return false;
  if (READ_STMTS.test(stripped)) {
    // WITH x AS (INSERT ...) SELECT ... is a write — check the body.
    return !WITH_BODY_WRITE.test(stripped.replace(/^WITH\b/i, ""));
  }
  return false;
}

/**
 * Run a parameterized query and return the full QueryResult.
 * Uses a client from the pool for the duration of the call.
 *
 * Read-only queries go to the replica pool; everything else (and any
 * unclassifiable statement) goes to the primary pool — the same split
 * as execute() in _common/db_commons/_router.py. A read whose replica
 * is BUSY (replay lag over REPLICA_MAX_LAG_S, probe failure, recovery
 * conflict, statement timeout, connection loss) retries once on the
 * PRIMARY — writes never touch the replica path at all.
 */
export async function query<T extends QueryResultRow = QueryResultRow>(
  text: string,
  params?: ReadonlyArray<unknown>,
): Promise<QueryResult<T>> {
  if (!isReadQuery(text)) {
    return getPool().query<T>(text, params as unknown[]);
  }
  const lag = await replicaLagSeconds();
  const replicaBusy = lag == null || lag > REPLICA_MAX_LAG_S;
  if (replicaBusy) {
    if (lag != null) {
      console.warn(
        `[db] replica lag ${Math.round(lag)}s > ${REPLICA_MAX_LAG_S}s — reading from primary for ${REPLICA_PROBE_TTL_MS / 1000}s`,
      );
    }
    return getPool().query<T>(text, params as unknown[]);
  }
  try {
    return await getReadPool().query<T>(text, params as unknown[]);
  } catch (err) {
    if (!isReplicaUnavailableError(err)) throw err;
    const code = (err as { code?: string }).code ?? "?";
    console.warn(
      `[db] replica read failed (SQLSTATE ${code}) — retrying on primary`,
    );
    // Bust the probe cache so the next read re-probes.
    _replicaLag = null;
    return getPool().query<T>(text, params as unknown[]);
  }
}

/**
 * Run a query and return just the rows (typed).
 */
export async function queryRows<T extends QueryResultRow = QueryResultRow>(
  text: string,
  params?: ReadonlyArray<unknown>,
): Promise<T[]> {
  const result = await query<T>(text, params);
  return result.rows;
}

/**
 * Acquire a dedicated client from the pool (for multi-statement transactions).
 * Caller is responsible for calling `client.release()`.
 *
 * Example:
 *   const client = await getClient();
 *   try {
 *     await client.query("BEGIN");
 *     // … multiple queries …
 *     await client.query("COMMIT");
 *   } catch (e) {
 *     await client.query("ROLLBACK");
 *     throw e;
 *   } finally {
 *     client.release();
 *   }
 */
export async function getClient(): Promise<PoolClient> {
  return getPool().connect();
}

/**
 * Gracefully close both pools — call on process shutdown.
 */
export async function closePool(): Promise<void> {
  if (_pool) {
    await _pool.end();
    _pool = null;
    console.log("[db] pool closed");
  }
  if (_readPool) {
    await _readPool.end();
    _readPool = null;
    console.log("[db] read pool closed");
  }
}

// ----------------------------------------------------------------------------
//  Helpers
// ----------------------------------------------------------------------------

/**
 * Normalize a date input to a "YYYY-MM-DD" string for PostgreSQL DATE
 * comparison.  Passing a plain string (instead of a JS Date) avoids timezone
 * conversion issues: pg sends Date objects as full timestamps, and PostgreSQL
 * casts them to DATE using the session timezone, which can shift the day by
 * one in either direction.  A plain "YYYY-MM-DD" string is parsed by
 * PostgreSQL as a DATE literal, independent of timezone.
 *
 * Accepts: "2025-07-01" | "2025-07-01T00:00:00Z" | Date | null
 */
export function toDateParam(v: string | Date | null | undefined): string | null {
  if (v == null || v === "") return null;
  if (v instanceof Date) {
    // Use local getters so the date is preserved in the caller's timezone
    // (toISOString() would shift the day in non-UTC timezones).
    const yyyy = v.getFullYear();
    const mm = String(v.getMonth() + 1).padStart(2, "0");
    const dd = String(v.getDate()).padStart(2, "0");
    return `${yyyy}-${mm}-${dd}`;
  }
  const s = String(v);
  return s.length >= 10 ? s.slice(0, 10) : s;
}

/**
 * Format a DB date value as "YYYY-MM-DD".  After setting the DATE/TIMESTAMP
 * type parsers above, most date columns arrive as strings and pass through
 * unchanged.  For any remaining Date objects (e.g. TIMESTAMPTZ), use local
 * getters to avoid the UTC shift of toISOString().
 */
export function formatDate(v: unknown): string {
  if (v == null) return "";
  if (v instanceof Date) {
    const yyyy = v.getFullYear();
    const mm = String(v.getMonth() + 1).padStart(2, "0");
    const dd = String(v.getDate()).padStart(2, "0");
    return `${yyyy}-${mm}-${dd}`;
  }
  const s = String(v);
  return s.length >= 10 ? s.slice(0, 10) : s;
}

/**
 * Coerce a DB value to number | null.
 */
export function toNum(v: unknown): number | null {
  if (v == null) return null;
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}
