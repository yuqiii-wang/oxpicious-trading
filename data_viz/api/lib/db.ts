/**
 * Shared database service — single pg.Pool for the entire API process.
 *
 * Reads connection parameters from `database/.env` (SUPABASE_* variables).
 * The pool is lazily created on first use and reused for all subsequent
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
  // The .env file lives at <repo>/database/.env (one level above data_viz/)
  const envPath = path.resolve(
    import.meta.dirname ?? __dirname,
    "..",
    "..",
    "database",
    ".env",
  );
  if (fs.existsSync(envPath)) {
    dotenv.config({ path: envPath });
  } else {
    // Fallback: rely on already-set environment variables
    dotenv.config();
  }
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
    host:     process.env.SUPABASE_HOST ?? "localhost",
    port:     parseInt(process.env.SUPABASE_PORT ?? "9876", 10),
    database: process.env.SUPABASE_DB ?? "oxpicious-stats",
    user:     process.env.SUPABASE_USER ?? "postgres",
    password: process.env.SUPABASE_PASSWORD ?? "postgres",
    max:      10,
    idleTimeoutMillis: 30_000,
    connectionTimeoutMillis: 5_000,
  };
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

/**
 * Run a parameterized query and return the full QueryResult.
 * Uses a client from the pool for the duration of the call.
 */
export async function query<T extends QueryResultRow = QueryResultRow>(
  text: string,
  params?: ReadonlyArray<unknown>,
): Promise<QueryResult<T>> {
  const pool = getPool();
  return pool.query<T>(text, params as unknown[]);
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
 * Gracefully close the pool — call on process shutdown.
 */
export async function closePool(): Promise<void> {
  if (_pool) {
    await _pool.end();
    _pool = null;
    console.log("[db] pool closed");
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
