/**
 * Options service (all venues: SZSE / SSE ETF options, CFFEX index
 * options) — queries the split options_* tables
 * (options_identity/terms/strike/settlement/greeks/volume_oi — the same
 * rows v_options_quote reconstructs) with underlying + date-range
 * filtering pushed down to the database.
 *
 * Also provides getEtfOhlcv() which queries stats.v_etf_margin for
 * the annual-sentiment panel (split-adjusted OHLCV).
 */
import { queryRows, queryReadWithSession, toDateParam, formatDate, toNum } from "../lib/db.js";
import type { QueryResultRow } from "pg";
import { stripExchangeSuffix, codeVariants } from "../lib/classify-etf.js";
import { decodeLabel, LABEL_CODE } from "./label-codes.js";
import type {
  OptionsRow,
  OptionsUnderlying,
  OptionsCombinedResponse,
  OptionsWallRow,
  OptionsWallsResponse,
  OptionsDatesResponse,
  OptionsSnapshotRowsResponse,
  OptionsSnapshotAxesResponse,
  OptionsSkewDailyResponse,
  OptionsOiDailyResponse,
  OptionsOiCellsResponse,
  OptionsExpiryMetaResponse,
  OptionsMoneynessSeriesResponse,
  EtfOhlcvResponse,
  SkewType,
  SkewnessSeriesRow,
  SkewnessSeriesResponse,
  OptionsOiStatsRow,
  OptionsOiStatsResponse,
  OptionsBandsRow,
  OptionsBandsResponse,
  IvSkewRow,
  IvSkewResponse,
  VolIndexRow,
  VolIndexResponse,
} from "../../shared/types.js";

export interface OptionsQuery {
  underlying?: string;
  start_date?: string;
  end_date?: string;
  /** 'ETF' (SZSE/SSE ETF options) or 'INDEX' (CFFEX index options). */
  target_type?: string;
  /** Venue filter on the options tables' exchange column ('SZSE'|'SSE'|'CFFEX'). */
  exchange?: string;
}

/**
 * Shared options_terms WHERE builder — the (target_type, exchange,
 * underlying, date-range) filters every per-plot query scopes by. Terms
 * carries the (underlying_code, date) index, so every query built on it
 * hits the same narrow access path.
 */
function termsWhere(
  q: OptionsQuery,
  params: unknown[],
  dateAlias = "t.date",
): string[] {
  const where: string[] = [];
  const t = (q.target_type ?? "").trim().toUpperCase();
  const ex = (q.exchange ?? "").trim().toUpperCase();
  const underlying = (q.underlying ?? "").trim();
  if (t === "ETF" || t === "INDEX") {
    params.push(t);
    where.push(`t.underlying_target_type = $${params.length}`);
  }
  if (ex === "SSE" || ex === "SZSE" || ex === "CFFEX") {
    params.push(ex);
    where.push(`t.exchange = $${params.length}`);
  }
  if (underlying) {
    params.push(underlying);
    where.push(`t.underlying_code = $${params.length}`);
  }
  const startDate = toDateParam(q.start_date);
  if (startDate) {
    params.push(startDate);
    where.push(`${dateAlias} >= $${params.length}::date`);
  }
  const endDate = toDateParam(q.end_date);
  if (endDate) {
    params.push(endDate);
    where.push(`${dateAlias} <= $${params.length}::date`);
  }
  return where;
}

// ----------------------------------------------------------------------------
//  DB row types (mirror v_options_quote view columns)
// ----------------------------------------------------------------------------
interface DbOptionsRow extends QueryResultRow {
  date: Date | string;
  contract_code: string;
  contract_name: string;
  underlying_code: string;
  underlying_name: string;
  /** stats.label_dim 'option_type' SMALLINT code (0 CALL / 1 PUT). */
  option_type: number;
  expiry_month: string;
  expiry_date: Date | string;
  days_to_expiry: number;
  strike_price: number;
  settle: number;
  underlying_close: number;
  moneyness_ratio: number;
  open_interest: number;
  volume: number;
  implied_vol: number | null;
  delta: number | null;
  theta: number | null;
  gamma: number | null;
  vega: number | null;
  rho: number | null;
}

interface DbUnderlyingRow extends QueryResultRow {
  underlying_code: string;
  underlying_name: string;
}

interface DbIndexOhlcvRow extends QueryResultRow {
  date: Date | string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  trading_shares: number | null;
}

interface DbEtfOhlcvRow extends QueryResultRow {
  date: Date | string;
  adj_open: number | null;
  adj_high: number | null;
  adj_low: number | null;
  adj_close: number | null;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  trading_shares: number | null;
}

// ----------------------------------------------------------------------------
//  Row transformer
// ----------------------------------------------------------------------------
function transformOptionsRow(r: DbOptionsRow): OptionsRow {
  return {
    date: formatDate(r.date),
    contract_code: r.contract_code,
    contract_name: r.contract_name,
    underlying_code: r.underlying_code,
    underlying_name: r.underlying_name,
    option_type: (decodeLabel("option_type", r.option_type) ?? "CALL") as "CALL" | "PUT",
    expiry_month: r.expiry_month ?? "",
    expiry_date: formatDate(r.expiry_date),
    days_to_expiry: toNum(r.days_to_expiry) ?? 0,
    strike_price: toNum(r.strike_price) ?? 0,
    settle: toNum(r.settle) ?? 0,
    underlying_close: toNum(r.underlying_close) ?? 0,
    moneyness_ratio: toNum(r.moneyness_ratio),
    open_interest: toNum(r.open_interest) ?? 0,
    volume: toNum(r.volume) ?? 0,
    implied_vol: toNum(r.implied_vol),
    delta: toNum(r.delta),
    theta: toNum(r.theta),
    gamma: toNum(r.gamma),
    vega: toNum(r.vega),
    rho: toNum(r.rho),
  };
}

// ----------------------------------------------------------------------------
//  List underlyings — SELECT DISTINCT from options_terms
//
//  Queried on the terms table directly, NOT the v_options_quote view: the
//  view is a 7-way join over the hash-partitioned options_* tables, and a
//  DISTINCT over it forces the full join for a two-column answer
//  (~1s per venue on the CFFEX data volume). terms alone carries
//  (underlying_code, underlying_name, exchange, underlying_target_type).
//
//  SZSE/SSE ETF options keep native ETF codes (1599xx / 510xxx/588xxx);
//  CFFEX index options use index codes (000xxx/399xxx). The venues load
//  via the underlying_target_type filter, and the two ETF venues are
//  separated by the exchange column.
// ----------------------------------------------------------------------------
export async function listUnderlyings(targetType?: string, exchange?: string): Promise<OptionsUnderlying[]> {
  const t = (targetType ?? "").trim().toUpperCase();
  const ex = (exchange ?? "").trim().toUpperCase();

  const params: unknown[] = [];
  const where: string[] = ["underlying_code IS NOT NULL", "underlying_code != ''"];
  if (t === "ETF" || t === "INDEX") {
    params.push(t);
    where.push(`underlying_target_type = $${params.length}`);
  }
  if (ex === "SSE" || ex === "SZSE" || ex === "CFFEX") {
    params.push(ex);
    where.push(`exchange = $${params.length}`);
  }

  const sql = `
    SELECT DISTINCT underlying_code, underlying_name
    FROM stats.options_terms
    WHERE ${where.join(" AND ")}
    ORDER BY underlying_code
  `;
  const rows = await queryRows<DbUnderlyingRow>(sql, params);
  return rows.map((r) => ({
    code: r.underlying_code,
    name: r.underlying_name,
  }));
}

// ----------------------------------------------------------------------------
//  Get options data filtered by underlying + date range
//
//  Join driven from the FILTERED options_terms side instead of the
//  v_options_quote view: underlying_code only exists on terms, so through
//  the view the planner cannot push the filter into any sub-table — it
//  scanned ~832k CFFEX rows out of EVERY options_* table and merge-joined
//  them with external-merge sorts (~400MB temp I/O per request, 15s+ on
//  the primary, over the replica's 10s statement_timeout so every request
//  paid the primary retry too). Driving from terms gives the planner a
//  ~400k-row outer side it can hash-join against each sub-table.
//
//  Two session settings pin the plan on both pools:
//    • work_mem=256MB — the remaining sorts/hashes fit in RAM (the
//      containers cap /dev/shm at 256MB, so spilling is the slow path).
//    • no parallel workers per gather — parallel hash joins need dynamic
//      shared memory, which that same /dev/shm cap makes fail with
//      "No space left on device" once other segments are resident.
//  Measured (CFFEX 000300, full history): 15-16s → ~5s replica / ~4s primary.
// ----------------------------------------------------------------------------
const COMBINED_SESSION_SETS = [
  "SET work_mem = '256MB'",
  "SET max_parallel_workers_per_gather = 0",
] as const;

// ----------------------------------------------------------------------------
//  Version-keyed aggregate cache — the heavy full-history per-plot queries
//  (dates / snapshot-axes / oi-daily / expiry-meta / oi-moneyness) hash-join
//  the ~1M-row hash-partitioned options_* sub-tables on EVERY request: the
//  underlying/venue scope lives only on options_terms, so the sub-table side
//  can never be index-pruned (measured ~0.9-4s per joined table per request
//  on the biggest CFFEX underlying). Their inputs change only when the
//  options data does (daily build), so responses are memoized in memory
//  keyed by query + MAX(options_identity.date) with a TTL — repeat views,
//  reloads and other sessions skip the multi-second scans. `fresh` (the
//  Refresh button) bypasses the read but still repopulates the cache.
// ----------------------------------------------------------------------------
const AGG_CACHE_MAX = 24;
const AGG_CACHE_TTL_MS = 10 * 60_000;
const AGG_VERSION_TTL_MS = 30_000;

interface AggCacheEntry {
  version: string;
  expiresAt: number;
  value: unknown;
}
const aggCache = new Map<string, AggCacheEntry>();
let aggVersionMemo: { value: string; expiresAt: number } | null = null;

/** Cache version — MAX(date) over options_identity (index-only, ~1ms;
 *  memoized 30s to keep it off the hot path). */
async function aggVersion(): Promise<string> {
  if (aggVersionMemo && Date.now() < aggVersionMemo.expiresAt) {
    return aggVersionMemo.value;
  }
  const rows = await queryRows<{ d: Date | string | null }>(
    "SELECT MAX(date) AS d FROM stats.options_identity",
  );
  const value = rows[0]?.d ? formatDate(rows[0].d) : "";
  aggVersionMemo = { value, expiresAt: Date.now() + AGG_VERSION_TTL_MS };
  return value;
}

/** Cache key for a scope query (the fields every per-plot SQL scopes by). */
function aggKey(prefix: string, q: OptionsQuery): string {
  return `${prefix}:${q.underlying ?? ""}|${q.target_type ?? ""}|${q.exchange ?? ""}|${q.start_date ?? ""}|${q.end_date ?? ""}`;
}

async function cachedAggregate<T>(
  key: string,
  compute: () => Promise<T>,
  fresh = false,
): Promise<T> {
  if (!fresh) {
    const hit = aggCache.get(key);
    if (hit && Date.now() < hit.expiresAt && (await aggVersion()) === hit.version) {
      aggCache.delete(key);
      aggCache.set(key, hit); // refresh LRU recency
      return hit.value as T;
    }
    if (hit) aggCache.delete(key);
  }
  const [value, version] = await Promise.all([compute(), aggVersion()]);
  aggCache.set(key, { version, expiresAt: Date.now() + AGG_CACHE_TTL_MS, value });
  while (aggCache.size > AGG_CACHE_MAX) {
    const oldest = aggCache.keys().next().value;
    if (oldest === undefined) break;
    aggCache.delete(oldest);
  }
  return value;
}

const COMBINED_SQL = `
    SELECT
        t.date, i.contract_code, i.contract_name, i.exchange,
        t.underlying_code, t.underlying_name, t.option_type,
        t.expiry_month, t.expiry_date, t.days_to_expiry,
        s.strike_price, st.settle, st.underlying_close, st.moneyness_ratio,
        vo.open_interest, vo.volume,
        g.implied_vol, g.delta, g.theta, g.gamma, g.vega, g.rho
    FROM stats.options_terms t
    JOIN stats.options_identity i
        ON i.contract_code = t.contract_code AND i.date = t.date
    LEFT JOIN stats.options_strike s
        ON s.contract_code = t.contract_code AND s.date = t.date
    LEFT JOIN stats.options_settlement st
        ON st.contract_code = t.contract_code AND st.date = t.date
    LEFT JOIN stats.options_greeks g
        ON g.contract_code = t.contract_code AND g.date = t.date
    LEFT JOIN stats.options_volume_oi vo
        ON vo.contract_code = t.contract_code AND vo.date = t.date
`;

export async function getOptionsCombined(
  q: OptionsQuery,
): Promise<OptionsCombinedResponse> {
  const underlying = (q.underlying ?? "").trim();
  const targetType = (q.target_type ?? "").trim().toUpperCase();
  const exchange = (q.exchange ?? "").trim().toUpperCase();

  const params: unknown[] = [];
  const where: string[] = [];
  let i = 1;

  if (targetType === "ETF" || targetType === "INDEX") {
    where.push(`t.underlying_target_type = $${i++}`);
    params.push(targetType);
  }
  if (exchange === "SSE" || exchange === "SZSE" || exchange === "CFFEX") {
    where.push(`t.exchange = $${i++}`);
    params.push(exchange);
  }
  if (underlying) {
    where.push(`t.underlying_code = $${i++}`);
    params.push(underlying);
  }
  const startDate = toDateParam(q.start_date);
  const endDate = toDateParam(q.end_date);
  if (startDate) {
    where.push(`t.date >= $${i++}::date`);
    params.push(startDate);
  }
  if (endDate) {
    where.push(`t.date <= $${i++}::date`);
    params.push(endDate);
  }

  const whereClause = where.length > 0 ? `WHERE ${where.join(" AND ")}` : "";

  const sql = `${COMBINED_SQL} ${whereClause} ORDER BY t.date ASC, t.contract_code ASC`;
  const result = await queryReadWithSession<DbOptionsRow>(
    COMBINED_SESSION_SETS,
    sql,
    params,
  );
  const transformed = result.rows.map(transformOptionsRow);
  const dates = Array.from(new Set(transformed.map((r) => r.date))).sort();

  return { dates, underlying_code: underlying, rows: transformed };
}

// ----------------------------------------------------------------------------
//  Options wall zones — analysis.options_walls (wall_type='zone' only; the
//  legacy 80pct / large_num wall types were removed from the backend).
//  wall_low/high/center are in RAW strike units (same scale as
//  v_options_quote.strike_price).
// ----------------------------------------------------------------------------

interface DbOptionsWallRow extends QueryResultRow {
  date: Date | string;
  /** stats.label_dim 'option_type' SMALLINT code (0 CALL / 1 PUT). */
  option_type: number;
  underlying_code: string;
  expiry_date: Date | string;
  wall_type: string;
  wall_strike: number | null;
  wall_oi: number | null;
  wall_low: number | null;
  wall_high: number | null;
  wall_center: number | null;
  mass_share: number | null;
  gap_pct: number | null;
  days_persisted: number | null;
  state: string | null;
  strength_score: number | null;
}

export async function getOptionsWalls(
  q: OptionsQuery,
): Promise<OptionsWallsResponse> {
  const underlying = stripExchangeSuffix((q.underlying ?? "")).trim();

  const params: unknown[] = [];
  const where: string[] = ["wall_type = 'zone'"];
  let i = 1;

  if (underlying) {
    where.push(`underlying_code = $${i++}`);
    params.push(underlying);
  }
  const startDate = toDateParam(q.start_date);
  const endDate = toDateParam(q.end_date);
  if (startDate) {
    where.push(`date >= $${i++}::date`);
    params.push(startDate);
  }
  if (endDate) {
    where.push(`date <= $${i++}::date`);
    params.push(endDate);
  }

  const sql = `
    SELECT date, option_type, underlying_code, expiry_date, wall_type,
           wall_strike, wall_oi,
           wall_low, wall_high, wall_center,
           mass_share, gap_pct, days_persisted, state, strength_score
    FROM analysis.options_walls
    WHERE ${where.join(" AND ")}
    ORDER BY date ASC, expiry_date ASC, option_type ASC
  `;
  const rows = await queryRows<DbOptionsWallRow>(sql, params);
  const transformed: OptionsWallRow[] = rows.map((r) => ({
    date: formatDate(r.date),
    option_type: (decodeLabel("option_type", r.option_type) ?? "CALL") as "PUT" | "CALL",
    underlying_code: r.underlying_code,
    expiry_date: formatDate(r.expiry_date),
    wall_type: "zone" as const,
    wall_strike: toNum(r.wall_strike),
    wall_oi: toNum(r.wall_oi),
    wall_low: toNum(r.wall_low),
    wall_high: toNum(r.wall_high),
    wall_center: toNum(r.wall_center),
    mass_share: toNum(r.mass_share),
    gap_pct: toNum(r.gap_pct),
    days_persisted: toNum(r.days_persisted),
    state: (r.state ?? null) as OptionsWallRow["state"],
    strength_score: toNum(r.strength_score),
  }));

  return { underlying_code: underlying, rows: transformed };
}

// ----------------------------------------------------------------------------
//  Get underlying OHLCV for the annual-sentiment panel.
//    • ETF mode   — stats.v_etf_margin directly (code = native ETF code,
//                   split-adjusted prices when available)
//    • INDEX mode — stats.v_index_daily directly (code = index code)
// ----------------------------------------------------------------------------
export async function getEtfOhlcv(
  code: string,
  startDate?: string,
  endDate?: string,
  targetType?: string,
): Promise<EtfOhlcvResponse> {
  const cleanedCode = stripExchangeSuffix(code).trim();
  const t = (targetType ?? "").trim().toUpperCase();
  const sd = toDateParam(startDate);
  const ed = toDateParam(endDate);

  // ---- INDEX mode: query v_index_daily with the raw index code ----
  if (t === "INDEX") {
    const params: unknown[] = [cleanedCode];
    const where: string[] = [`code = $1`];
    let i = 2;
    if (sd) {
      where.push(`date >= $${i++}::date`);
      params.push(sd);
    }
    if (ed) {
      where.push(`date <= $${i++}::date`);
      params.push(ed);
    }
    const rows = await queryRows<DbIndexOhlcvRow>(`
      SELECT date, open, high, low, close, trading_shares
      FROM stats.v_index_daily
      WHERE ${where.join(" AND ")}
      ORDER BY date ASC
    `, params);
    // null-preserving: CN-holiday rows of cross-border indices / estimated
    // rows carry NULL OHLC or volume — render as chart gaps, never 0
    const transformed = rows.map((r) => ({
      date: formatDate(r.date),
      open: toNum(r.open),
      high: toNum(r.high),
      low: toNum(r.low),
      close: toNum(r.close),
      volume: toNum(r.trading_shares),
    }));
    return {
      dates: transformed.map((r) => r.date),
      code: cleanedCode,
      rows: transformed,
    };
  }

  // ---- ETF mode (default): v_etf_margin with the native ETF code ----
  const targetCode = cleanedCode;

  const params: unknown[] = [codeVariants(targetCode)];
  const where: string[] = [
    `code = ANY($1::text[])`,
  ];
  let i = 2;

  if (sd) {
    where.push(`date >= $${i++}::date`);
    params.push(sd);
  }
  if (ed) {
    where.push(`date <= $${i++}::date`);
    params.push(ed);
  }

  const sql = `
    SELECT date, adj_open, adj_high, adj_low, adj_close,
           open, high, low, close, trading_shares
    FROM stats.v_etf_margin
    WHERE ${where.join(" AND ")}
    ORDER BY date ASC
  `;
  const rows = await queryRows<DbEtfOhlcvRow>(sql, params);

  const transformed = rows.map((r) => {
    const hasAdj = r.adj_close != null && toNum(r.adj_close) !== null && (toNum(r.adj_close) ?? 0) > 1e-9;
    const useVal = (adj: number | null | undefined, raw: number | null | undefined, fallback = 0): number => {
      if (hasAdj) {
        const v = toNum(adj);
        if (v !== null && v > 1e-9) return v;
      }
      return toNum(raw) ?? fallback;
    };
    return {
      date: formatDate(r.date),
      open: useVal(r.adj_open, r.open),
      high: useVal(r.adj_high, r.high),
      low: useVal(r.adj_low, r.low),
      close: useVal(r.adj_close, r.close),
      volume: toNum(r.trading_shares) ?? 0,
    };
  });

  return {
    dates: transformed.map((r) => r.date),
    code: cleanedCode,
    rows: transformed,
  };
}

// ----------------------------------------------------------------------------
//  Options Skewness Series — daily raw skewness per (date, expiry month)
// ----------------------------------------------------------------------------

interface DbSkewnessSeriesRow extends QueryResultRow {
  date: Date | string;
  underlying_code: string;
  expiry_month: Date | string;
  expiry_date: Date | string | null;
  skewness: number | null;
}

export async function getOptionsSkewnessSeries(
  underlying: string,
  startDate?: string,
  endDate?: string,
  skewType: SkewType = "oi_moneyness",
): Promise<SkewnessSeriesResponse> {
  const cleanedCode = stripExchangeSuffix(underlying).trim();
  const sd = toDateParam(startDate);
  const ed = toDateParam(endDate);

  const params: unknown[] = [cleanedCode, skewType];
  const where: string[] = ["underlying_code = $1", "skew_type = $2"];
  let i = 3;

  if (sd) {
    where.push(`date >= $${i++}::date`);
    params.push(sd);
  }
  if (ed) {
    where.push(`date <= $${i++}::date`);
    params.push(ed);
  }

  const sql = `
    SELECT
      date,
      underlying_code,
      DATE_TRUNC('month', expiry_date) AS expiry_month,
      MAX(expiry_date) AS expiry_date,
      AVG(skewness) AS skewness
    FROM analysis.options_skewness_stats
    WHERE ${where.join(" AND ")}
      AND skewness IS NOT NULL
    GROUP BY date, underlying_code, DATE_TRUNC('month', expiry_date)
    ORDER BY date ASC, DATE_TRUNC('month', expiry_date) ASC
  `;

  const rows = await queryRows<DbSkewnessSeriesRow>(sql, params);
  const transformed: SkewnessSeriesRow[] = rows.map((r) => ({
    date: formatDate(r.date),
    expiry_month: formatDate(r.expiry_month),
    expiry_date: r.expiry_date ? formatDate(r.expiry_date) : null,
    skewness: toNum(r.skewness),
  }));

  return { underlying_code: cleanedCode, rows: transformed };
}

// ----------------------------------------------------------------------------
//  Options Bands — options-implied bounded-fluctuation band events
//  (analysis.options_bands)
// ----------------------------------------------------------------------------

interface DbBandsRow extends QueryResultRow {
  date: Date | string;
  underlying_close: number | null;
  corridor_low: number | null;
  corridor_high: number | null;
  oi_total: number | null;
  spot_pct: number | null;
  gap_low_pct: number | null;
  gap_high_pct: number | null;
  pos_state: string | null;
  lean: string | null;
  event: string | null;
  /** Fresh strike-grid expansion (trailing 40 sessions) and the
   *  edge-significance amplifier over the engaged corridor edge. */
  new_hi_40d: number | null;
  new_lo_40d: number | null;
  edge_sig: string | null;
  n_expiries: number | null;
  skew_all_above: number | null;
  skew_all_below: number | null;
  min_em: number | null;
  mean_em: number | null;
  em5_pct: number | null;
  ret5_pct: number | null;
  exhausted_5d: number | null;
}

export async function getOptionsBands(
  underlying: string,
  startDate?: string,
  endDate?: string,
): Promise<OptionsBandsResponse> {
  const cleanedCode = stripExchangeSuffix(underlying).trim();
  const sd = toDateParam(startDate);
  const ed = toDateParam(endDate);

  const params: unknown[] = [cleanedCode];
  const where: string[] = ["underlying_code = $1"];
  let i = 2;

  if (sd) {
    where.push(`date >= $${i++}::date`);
    params.push(sd);
  }
  if (ed) {
    where.push(`date <= $${i++}::date`);
    params.push(ed);
  }

  const sql = `
    SELECT
      date,
      underlying_close::float8 AS underlying_close,
      corridor_low::float8 AS corridor_low,
      corridor_high::float8 AS corridor_high,
      oi_total::float8 AS oi_total,
      spot_pct::float8 AS spot_pct,
      gap_low_pct::float8 AS gap_low_pct,
      gap_high_pct::float8 AS gap_high_pct,
      pos_state,
      lean,
      event,
      new_hi_40d,
      new_lo_40d,
      edge_sig,
      n_expiries,
      skew_all_above,
      skew_all_below,
      min_em::float8 AS min_em,
      mean_em::float8 AS mean_em,
      em5_pct::float8 AS em5_pct,
      ret5_pct::float8 AS ret5_pct,
      exhausted_5d
    FROM analysis.options_bands
    WHERE ${where.join(" AND ")}
    ORDER BY date ASC
  `;

  const rows = await queryRows<DbBandsRow>(sql, params);
  const transformed: OptionsBandsRow[] = rows.map((r) => ({
    date: formatDate(r.date),
    underlying_close: toNum(r.underlying_close),
    corridor_low: toNum(r.corridor_low),
    corridor_high: toNum(r.corridor_high),
    oi_total: toNum(r.oi_total),
    spot_pct: toNum(r.spot_pct),
    gap_low_pct: toNum(r.gap_low_pct),
    gap_high_pct: toNum(r.gap_high_pct),
    pos_state: (r.pos_state as OptionsBandsRow["pos_state"]) ?? null,
    lean: (r.lean as OptionsBandsRow["lean"]) ?? null,
    event: (r.event as OptionsBandsRow["event"]) ?? null,
    new_hi_40d: r.new_hi_40d == null ? null : Number(r.new_hi_40d),
    new_lo_40d: r.new_lo_40d == null ? null : Number(r.new_lo_40d),
    edge_sig: (r.edge_sig as OptionsBandsRow["edge_sig"]) ?? null,
    n_expiries: r.n_expiries == null ? null : Number(r.n_expiries),
    skew_all_above: r.skew_all_above == null ? null : r.skew_all_above === 1 ? 1 : 0,
    skew_all_below: r.skew_all_below == null ? null : r.skew_all_below === 1 ? 1 : 0,
    min_em: toNum(r.min_em),
    mean_em: toNum(r.mean_em),
    em5_pct: toNum(r.em5_pct),
    ret5_pct: toNum(r.ret5_pct),
    exhausted_5d: (r.exhausted_5d == null ? null : r.exhausted_5d === 1 ? 1 : 0),
  }));

  return { underlying_code: cleanedCode, rows: transformed };
}

// ----------------------------------------------------------------------------
//  Options OI Stats — per-expiry OI level/changes (analysis.options_oi_stats)
// ----------------------------------------------------------------------------

interface DbOiStatsRow extends QueryResultRow {
  date: Date | string;
  underlying_code: string;
  expiry_month: Date | string;
  expiry_date: Date | string | null;
  oi_total: number | null;
  oi_delta_5d: number | null;
  oi_delta_20d: number | null;
  oi_max_20d: number | null;
}

export async function getOptionsOiStats(
  underlying: string,
  startDate?: string,
  endDate?: string,
): Promise<OptionsOiStatsResponse> {
  const cleanedCode = stripExchangeSuffix(underlying).trim();
  const sd = toDateParam(startDate);
  const ed = toDateParam(endDate);

  const params: unknown[] = [cleanedCode];
  const where: string[] = ["underlying_code = $1"];
  let i = 2;

  if (sd) {
    where.push(`date >= $${i++}::date`);
    params.push(sd);
  }
  if (ed) {
    where.push(`date <= $${i++}::date`);
    params.push(ed);
  }

  // options_oi_stats stores per-REAL-expiry rows duplicated per option_type
  // (CALL/PUT hold the same value). The inner DISTINCT dedupes the
  // option_type duplication; the outer GROUP BY aggregates the month group
  // the frontend keys on: SUM across the month's expiries (1 per month for
  // the monthly ETF convention — exact; CFFEX weeklies sum to the month
  // group's total), MAX of the per-expiry trailing maxes (approximate for
  // multi-expiry months).
  const sql = `
    SELECT
      date,
      underlying_code,
      expiry_month,
      MAX(expiry_date) AS expiry_date,
      SUM(oi_total) AS oi_total,
      SUM(oi_delta_5d) AS oi_delta_5d,
      SUM(oi_delta_20d) AS oi_delta_20d,
      MAX(oi_max_20d) AS oi_max_20d
    FROM (
      SELECT DISTINCT date, underlying_code,
             DATE_TRUNC('month', expiry_date) AS expiry_month,
             expiry_date,
             oi_total, oi_delta_5d, oi_delta_20d, oi_max_20d
      FROM analysis.options_oi_stats
      WHERE ${where.join(" AND ")}
    ) t
    GROUP BY date, underlying_code, expiry_month
    ORDER BY date ASC, expiry_month ASC
  `;

  const rows = await queryRows<DbOiStatsRow>(sql, params);
  const transformed: OptionsOiStatsRow[] = rows.map((r) => ({
    date: formatDate(r.date),
    expiry_month: formatDate(r.expiry_month),
    expiry_date: r.expiry_date != null ? formatDate(r.expiry_date) : null,
    oi_total: toNum(r.oi_total),
    oi_delta_5d: toNum(r.oi_delta_5d),
    oi_delta_20d: toNum(r.oi_delta_20d),
    oi_max_20d: toNum(r.oi_max_20d),
  }));

  return { underlying_code: cleanedCode, rows: transformed };
}

// ----------------------------------------------------------------------------
//  Options IV Skew Stats — per-expiry implied-volatility skew metrics
// ----------------------------------------------------------------------------

interface DbIvSkewRow extends QueryResultRow {
  date: Date | string;
  underlying_code: string;
  expiry_month: Date | string;
  expiry_date: Date | string | null;
  atm_iv: number | null;
  iv_call25: number | null;
  iv_put25: number | null;
  risk_reversal_25d: number | null;
  put_skew_25d: number | null;
  call_skew_25d: number | null;
  iv_call10: number | null;
  iv_put10: number | null;
  risk_reversal_10d: number | null;
  smile_skewness: number | null;
  rr25_ma5: number | null;
  rr25_ma20: number | null;
  rr25_ma60: number | null;
  corr_rr25_ma5_vs_spot_ma5: number | null;
  corr_rr25_ma20_vs_spot_ma20: number | null;
  corr_rr25_ma60_vs_spot_ma60: number | null;
}

export async function getOptionsIvSkew(
  underlying: string,
  startDate?: string,
  endDate?: string,
): Promise<IvSkewResponse> {
  const cleanedCode = stripExchangeSuffix(underlying).trim();
  const sd = toDateParam(startDate);
  const ed = toDateParam(endDate);

  const params: unknown[] = [cleanedCode];
  const where: string[] = ["underlying_code = $1"];
  let i = 2;

  if (sd) {
    where.push(`date >= $${i++}::date`);
    params.push(sd);
  }
  if (ed) {
    where.push(`date <= $${i++}::date`);
    params.push(ed);
  }

  // AVG across CALL/PUT rows (pair metrics are duplicated per option_type;
  // smile_skewness differs per type, so the mean is the group-level value).
  // MAX(expiry_date) gives the latest exact expiry in the month group (for
  // the frontend shade boundaries / expiry marks).
  const sql = `
    SELECT
      date,
      underlying_code,
      DATE_TRUNC('month', expiry_date) AS expiry_month,
      MAX(expiry_date) AS expiry_date,
      AVG(atm_iv) AS atm_iv,
      AVG(iv_call25) AS iv_call25,
      AVG(iv_put25) AS iv_put25,
      AVG(risk_reversal_25d) AS risk_reversal_25d,
      AVG(put_skew_25d) AS put_skew_25d,
      AVG(call_skew_25d) AS call_skew_25d,
      AVG(iv_call10) AS iv_call10,
      AVG(iv_put10) AS iv_put10,
      AVG(risk_reversal_10d) AS risk_reversal_10d,
      AVG(smile_skewness) AS smile_skewness,
      AVG(rr25_ma5) AS rr25_ma5,
      AVG(rr25_ma20) AS rr25_ma20,
      AVG(rr25_ma60) AS rr25_ma60,
      AVG(corr_rr25_ma5_vs_spot_ma5) AS corr_rr25_ma5_vs_spot_ma5,
      AVG(corr_rr25_ma20_vs_spot_ma20) AS corr_rr25_ma20_vs_spot_ma20,
      AVG(corr_rr25_ma60_vs_spot_ma60) AS corr_rr25_ma60_vs_spot_ma60
    FROM analysis.options_iv_skew_stats
    WHERE ${where.join(" AND ")}
    GROUP BY date, underlying_code, DATE_TRUNC('month', expiry_date)
    ORDER BY date ASC, DATE_TRUNC('month', expiry_date) ASC
  `;

  const rows = await queryRows<DbIvSkewRow>(sql, params);
  const transformed: IvSkewRow[] = rows.map((r) => ({
    date: formatDate(r.date),
    expiry_month: formatDate(r.expiry_month),
    expiry_date: r.expiry_date != null ? formatDate(r.expiry_date) : null,
    atm_iv: toNum(r.atm_iv),
    iv_call25: toNum(r.iv_call25),
    iv_put25: toNum(r.iv_put25),
    risk_reversal_25d: toNum(r.risk_reversal_25d),
    put_skew_25d: toNum(r.put_skew_25d),
    call_skew_25d: toNum(r.call_skew_25d),
    iv_call10: toNum(r.iv_call10),
    iv_put10: toNum(r.iv_put10),
    risk_reversal_10d: toNum(r.risk_reversal_10d),
    smile_skewness: toNum(r.smile_skewness),
    rr25_ma5: toNum(r.rr25_ma5),
    rr25_ma20: toNum(r.rr25_ma20),
    rr25_ma60: toNum(r.rr25_ma60),
    corr_rr25_ma5_vs_spot_ma5: toNum(r.corr_rr25_ma5_vs_spot_ma5),
    corr_rr25_ma20_vs_spot_ma20: toNum(r.corr_rr25_ma20_vs_spot_ma20),
    corr_rr25_ma60_vs_spot_ma60: toNum(r.corr_rr25_ma60_vs_spot_ma60),
  }));

  return { underlying_code: cleanedCode, rows: transformed };
}

// ----------------------------------------------------------------------------
//  Vol index (30d model-free, VIX-style) — analysis.options_vol_index
// ----------------------------------------------------------------------------
interface DbVolIndexRow extends QueryResultRow {
  date: Date | string;
  underlying_code: string;
  near_expiry_date: Date | string | null;
  far_expiry_date: Date | string | null;
  dte_near: number | null;
  dte_far: number | null;
  var_near: number | null;
  var_far: number | null;
  variance_30d: number | null;
  vol_index_30d: number | null;
}

/**
 * Daily 30-day model-free implied-vol index per underlying
 * (analysis.options_vol_index). `underlying` optional — omitting it
 * returns every underlying's series for the cross-asset chart.
 */
export async function getOptionsVolIndex(
  underlying?: string,
  startDate?: string,
  endDate?: string,
): Promise<VolIndexResponse> {
  const params: unknown[] = [];
  const where: string[] = [];
  let i = 1;

  if (underlying) {
    where.push(`underlying_code = $${i++}`);
    params.push(stripExchangeSuffix(underlying).trim());
  }
  const sd = toDateParam(startDate);
  if (sd) {
    where.push(`date >= $${i++}::date`);
    params.push(sd);
  }
  const ed = toDateParam(endDate);
  if (ed) {
    where.push(`date <= $${i++}::date`);
    params.push(ed);
  }

  const sql = `
    SELECT date, underlying_code,
           near_expiry_date, far_expiry_date,
           dte_near, dte_far,
           var_near, var_far, variance_30d, vol_index_30d
    FROM analysis.options_vol_index
    ${where.length > 0 ? `WHERE ${where.join(" AND ")}` : ""}
    ORDER BY date ASC, underlying_code ASC
  `;

  const rows = await queryRows<DbVolIndexRow>(sql, params);
  const transformed: VolIndexRow[] = rows.map((r) => ({
    date: formatDate(r.date),
    underlying_code: r.underlying_code,
    near_expiry_date:
      r.near_expiry_date != null ? formatDate(r.near_expiry_date) : null,
    far_expiry_date:
      r.far_expiry_date != null ? formatDate(r.far_expiry_date) : null,
    dte_near: toNum(r.dte_near),
    dte_far: toNum(r.dte_far),
    var_near: toNum(r.var_near),
    var_far: toNum(r.var_far),
    variance_30d: toNum(r.variance_30d),
    vol_index_30d: toNum(r.vol_index_30d),
  }));

  return { underlying_code: underlying ?? null, rows: transformed };
}

// ----------------------------------------------------------------------------
//  Per-plot queries — one SQL per plot. The Options Analysis page loads
//  each panel independently instead of pulling the full-history /combined
//  blob (CFFEX 000300 alone ships ~400k rows / ~200MB of JSON through it).
//  All queries scope by the terms WHERE builder (underlying + venue +
//  optional date window) and aggregate server-side, so payloads are
//  10-1000x smaller than the raw rows.
// ----------------------------------------------------------------------------

function monthSortKey(label: string): number {
  const m = /(\d+)/.exec(label);
  return m ? parseInt(m[1], 10) : 0;
}

/** DISTINCT trading dates + row/contract counters (date picker, caption). */
export async function getOptionsDates(
  q: OptionsQuery,
  fresh = false,
): Promise<OptionsDatesResponse> {
  return cachedAggregate(aggKey("dates", q), async () => {
    const params: unknown[] = [];
    const where = termsWhere(q, params);
    const whereClause = where.length > 0 ? `WHERE ${where.join(" AND ")}` : "";
    const rows = await queryRows<QueryResultRow & { date: Date | string; row_count: string | number; contract_count: string | number }>(`
      SELECT t.date, COUNT(*) AS row_count, COUNT(DISTINCT t.contract_code) AS contract_count
      FROM stats.options_terms t
      ${whereClause}
      GROUP BY t.date
      ORDER BY t.date ASC
    `, params);
    return {
      underlying_code: (q.underlying ?? "").trim(),
      dates: rows.map((r) => formatDate(r.date)),
      row_count: rows.reduce((s, r) => s + (toNum(r.row_count) ?? 0), 0),
      contract_count: rows.reduce((m, r) => Math.max(m, toNum(r.contract_count) ?? 0), 0),
    };
  }, fresh);
}

/** Quote rows for one or more specific trading dates — one small query.
 *  The (underlying_code, date) terms index narrows each date to a few
 *  hundred rows, then per-row index probes into the joined sub-tables
 *  (~90ms per date vs ~1-4s for any full-history aggregate). Feeds the
 *  wall / smile / greeks panels and, batched, the StatTable columns. */
export async function getOptionsSnapshotRows(
  q: OptionsQuery,
  dates: string[],
): Promise<OptionsSnapshotRowsResponse> {
  const cleaned = dates
    .map((d) => toDateParam(d))
    .filter((d): d is string => d != null && /^\d{4}-\d{2}-\d{2}$/.test(d));
  const underlying = (q.underlying ?? "").trim();
  if (cleaned.length === 0) {
    return { underlying_code: underlying, rows: [] };
  }
  const params: unknown[] = [];
  const where = termsWhere(q, params);
  params.push(cleaned);
  where.push(`t.date = ANY($${params.length}::date[])`);
  const result = await queryReadWithSession<DbOptionsRow>(
    COMBINED_SESSION_SETS,
    `${COMBINED_SQL} WHERE ${where.join(" AND ")} ORDER BY t.date ASC, t.contract_code ASC`,
    params,
  );
  return { underlying_code: underlying, rows: result.rows.map(transformOptionsRow) };
}

/** History-wide display meta (the wall panel's unified strike /
 *  expiry-month axes) — cached per underlying/venue: it never changes with
 *  the selected date, so date switches don't re-run the full-history
 *  DISTINCT joins. */
export async function getOptionsSnapshotAxes(
  q: OptionsQuery,
  fresh = false,
): Promise<OptionsSnapshotAxesResponse> {
  return cachedAggregate(aggKey("axes", q), async () => {
    const metaParams: unknown[] = [];
    const metaWhere = termsWhere(
      { target_type: q.target_type, exchange: q.exchange, underlying: q.underlying },
      metaParams,
    );
    const metaWhereClause = metaWhere.length > 0 ? `WHERE ${metaWhere.join(" AND ")}` : "";
    const meta = await queryReadWithSession<QueryResultRow>(
      COMBINED_SESSION_SETS,
      `SELECT DISTINCT s.strike_price
         FROM stats.options_terms t
         JOIN stats.options_strike s ON s.contract_code = t.contract_code AND s.date = t.date
         ${metaWhereClause}
         ORDER BY 1`,
      metaParams,
    );
    // MAX(date) OVER () rides the same scan — the client cache's version
    // anchor for this response (it carries no per-row dates).
    const months = await queryRows<QueryResultRow & { expiry_month: string; latest_date: Date | string | null }>(`
      SELECT DISTINCT t.expiry_month, MAX(t.date) OVER () AS latest_date
      FROM stats.options_terms t
      ${metaWhereClause}
    `, metaParams.slice());
    // month labels are Chinese ("10月") — order by their numeric month part
    const sortedMonths = months.map((r) => r.expiry_month);
    sortedMonths.sort((a, b) => monthSortKey(a) - monthSortKey(b));
    const latest = months[0]?.latest_date;
    return {
      underlying_code: (q.underlying ?? "").trim(),
      strikes: meta.rows.map((r) => toNum(r.strike_price) ?? 0),
      expiry_months: sortedMonths,
      latest_date: latest ? formatDate(latest) : "",
    };
  }, fresh);
}

/** Per (date × real expiry) smile anchors for the skew chronology panel
 *  (Spot vs Level vs Skew): the nearest-|x| selections the panel formerly
 *  recomputed in-browser from the full /combined blob — ATM by
 *  |moneyness_ratio − 1| (NULLS LAST), 25Δ/10Δ OTM wings by |delta| within
 *  (0, 0.5)/(−0.5, 0), tie-broken by contract_code (the blob's iteration
 *  order, so anchor picks are identical to the old client compute). Row
 *  validity mirrors the panel's old filter. */
export async function getOptionsSkewDaily(
  q: OptionsQuery,
  fresh = false,
): Promise<OptionsSkewDailyResponse> {
  return cachedAggregate(aggKey("skew-daily", q), async () => {
    const params: unknown[] = [];
    const where = termsWhere(q, params);
    where.push(
      "g.implied_vol > 0 AND g.implied_vol < 5",
      "g.delta IS NOT NULL",
      "s.strike_price > 0",
      "st.underlying_close > 0",
    );
    const rows = await queryReadWithSession<QueryResultRow & {
      date: Date | string;
      expiry_date: Date | string;
      dte: string | number;
      spot: string | number | null;
      atm_iv: string | number | null;
      call25_iv: string | number | null;
      put25_iv: string | number | null;
      call10_iv: string | number | null;
      put10_iv: string | number | null;
    }>(
      COMBINED_SESSION_SETS,
      `
      SELECT t.date, t.expiry_date,
             MIN(t.days_to_expiry) AS dte,
             MAX(st.underlying_close) AS spot,
             (ARRAY_AGG(g.implied_vol * 100.0 ORDER BY abs(st.moneyness_ratio - 1.0) NULLS LAST, t.contract_code))[1] AS atm_iv,
             (ARRAY_AGG(g.implied_vol * 100.0 ORDER BY abs(g.delta - 0.25), t.contract_code)
               FILTER (WHERE t.option_type = ${LABEL_CODE.option_type.CALL} AND g.delta > 0 AND g.delta < 0.5))[1] AS call25_iv,
             (ARRAY_AGG(g.implied_vol * 100.0 ORDER BY abs(g.delta + 0.25), t.contract_code)
               FILTER (WHERE t.option_type = ${LABEL_CODE.option_type.PUT} AND g.delta < 0 AND g.delta > -0.5))[1] AS put25_iv,
             (ARRAY_AGG(g.implied_vol * 100.0 ORDER BY abs(g.delta - 0.10), t.contract_code)
               FILTER (WHERE t.option_type = ${LABEL_CODE.option_type.CALL} AND g.delta > 0 AND g.delta < 0.5))[1] AS call10_iv,
             (ARRAY_AGG(g.implied_vol * 100.0 ORDER BY abs(g.delta + 0.10), t.contract_code)
               FILTER (WHERE t.option_type = ${LABEL_CODE.option_type.PUT} AND g.delta < 0 AND g.delta > -0.5))[1] AS put10_iv
      FROM stats.options_terms t
      JOIN stats.options_greeks g ON g.contract_code = t.contract_code AND g.date = t.date
      JOIN stats.options_settlement st ON st.contract_code = t.contract_code AND st.date = t.date
      JOIN stats.options_strike s ON s.contract_code = t.contract_code AND s.date = t.date
      WHERE ${where.join(" AND ")}
      GROUP BY t.date, t.expiry_date
      ORDER BY t.date ASC, t.expiry_date ASC
    `,
      params,
    );
    return {
      underlying_code: (q.underlying ?? "").trim(),
      rows: rows.rows.map((r) => ({
        date: formatDate(r.date),
        expiry_date: formatDate(r.expiry_date),
        dte: toNum(r.dte) ?? 0,
        spot: toNum(r.spot),
        atm_iv: toNum(r.atm_iv),
        call25_iv: toNum(r.call25_iv),
        put25_iv: toNum(r.put25_iv),
        call10_iv: toNum(r.call10_iv),
        put10_iv: toNum(r.put10_iv),
      })),
    };
  }, fresh);
}

/** Per (date × expiry) call/put OI + per-date spot — the trend charts'
 *  daily aggregates and shared x-axis/spot line. The spot close rides the
 *  same scan (MAX over the date partition of the grouped rows) instead of
 *  a second full-history terms ⋈ settlement aggregation per request. */
export async function getOptionsOiDaily(
  q: OptionsQuery,
  fresh = false,
): Promise<OptionsOiDailyResponse> {
  return cachedAggregate(aggKey("oi-daily", q), async () => {
    const params: unknown[] = [];
    const where = termsWhere(q, params);
    const whereClause = where.length > 0 ? `WHERE ${where.join(" AND ")}` : "";
    const rows = await queryRows<QueryResultRow & {
      date: Date | string; expiry_date: Date | string; call_oi: string | number; put_oi: string | number;
      spot_close: string | number | null;
    }>(`
      SELECT t.date, t.expiry_date,
             SUM(CASE WHEN t.option_type = ${LABEL_CODE.option_type.CALL} THEN vo.open_interest ELSE 0 END) AS call_oi,
             SUM(CASE WHEN t.option_type = ${LABEL_CODE.option_type.PUT}  THEN vo.open_interest ELSE 0 END) AS put_oi,
             MAX(MAX(st.underlying_close)) OVER (PARTITION BY t.date) AS spot_close
      FROM stats.options_terms t
      JOIN stats.options_volume_oi vo ON vo.contract_code = t.contract_code AND vo.date = t.date
      LEFT JOIN stats.options_settlement st ON st.contract_code = t.contract_code AND st.date = t.date
      ${whereClause}
      GROUP BY t.date, t.expiry_date
      ORDER BY t.date ASC, t.expiry_date ASC
    `, params);

    const daily: OptionsOiDailyResponse["daily"] = [];
    const spot: OptionsOiDailyResponse["spot"] = [];
    let lastDate = "";
    for (const r of rows) {
      const date = formatDate(r.date);
      daily.push({
        date,
        expiry_date: formatDate(r.expiry_date),
        call_oi: toNum(r.call_oi) ?? 0,
        put_oi: toNum(r.put_oi) ?? 0,
      });
      // one spot point per date (the window value repeats across expiries)
      if (date !== lastDate) {
        lastDate = date;
        const close = toNum(r.spot_close);
        spot.push({ date, close: close != null && close > 0 ? close : null });
      }
    }
    return { underlying_code: (q.underlying ?? "").trim(), daily, spot };
  }, fresh);
}

/** Per (date × expiry × strike) call/put OI cells for the bands chart,
 *  restricted to the requested expiry dates (the panel's cohort pick). */
export async function getOptionsOiCells(
  q: OptionsQuery,
  expiries: string[],
): Promise<OptionsOiCellsResponse> {
  if (expiries.length === 0) {
    return { underlying_code: (q.underlying ?? "").trim(), cells: [] };
  }
  const params: unknown[] = [];
  const where = termsWhere(q, params);
  params.push(expiries);
  where.push(`t.expiry_date = ANY($${params.length}::date[])`);
  const rows = await queryRows<QueryResultRow & {
    date: Date | string; expiry_date: Date | string; strike_price: string | number;
    call_oi: string | number; put_oi: string | number;
  }>(`
    SELECT t.date, t.expiry_date, s.strike_price,
           SUM(CASE WHEN t.option_type = ${LABEL_CODE.option_type.CALL} THEN vo.open_interest ELSE 0 END) AS call_oi,
           SUM(CASE WHEN t.option_type = ${LABEL_CODE.option_type.PUT}  THEN vo.open_interest ELSE 0 END) AS put_oi
    FROM stats.options_terms t
    JOIN stats.options_strike s ON s.contract_code = t.contract_code AND s.date = t.date
    JOIN stats.options_volume_oi vo ON vo.contract_code = t.contract_code AND vo.date = t.date
    WHERE ${where.join(" AND ")}
    GROUP BY t.date, t.expiry_date, s.strike_price
    ORDER BY t.date ASC, t.expiry_date ASC, s.strike_price ASC
  `, params);
  return {
    underlying_code: (q.underlying ?? "").trim(),
    cells: rows.map((r) => ({
      date: formatDate(r.date),
      expiry_date: formatDate(r.expiry_date),
      strike_price: toNum(r.strike_price) ?? 0,
      call_oi: toNum(r.call_oi) ?? 0,
      put_oi: toNum(r.put_oi) ?? 0,
    })),
  };
}

/** Per-contract final OI (last session ≤ its expiry) — the expiry markers.
 *  One row per contract ever quoted (~5k for the biggest CFFEX underlying).
 *  Per-expiry lifetime totals (the cohort selector's options) are derived
 *  client-side from /oi-daily's per (date × expiry) rows — same numbers,
 *  one less full-history aggregation per request. */
export async function getOptionsExpiryMeta(
  q: OptionsQuery,
  fresh = false,
): Promise<OptionsExpiryMetaResponse> {
  return cachedAggregate(aggKey("expiry-meta", q), async () => {
    const params: unknown[] = [];
    const where = termsWhere(q, params);
    where.push(`t.date <= t.expiry_date`);
    const markers = await queryRows<QueryResultRow & {
      contract_code: string; contract_name: string; /** label_dim 'option_type' code (0 CALL / 1 PUT). */
      option_type: number;
      expiry_date: Date | string; last_date: Date | string; open_interest: string | number;
    }>(`
      SELECT DISTINCT ON (t.contract_code)
             t.contract_code, i.contract_name, t.option_type, t.expiry_date,
             t.date AS last_date, vo.open_interest
      FROM stats.options_terms t
      JOIN stats.options_identity i ON i.contract_code = t.contract_code AND i.date = t.date
      JOIN stats.options_volume_oi vo ON vo.contract_code = t.contract_code AND vo.date = t.date
      WHERE ${where.join(" AND ")}
      ORDER BY t.contract_code, t.date DESC
    `, params);

    return {
      underlying_code: (q.underlying ?? "").trim(),
      markers: markers.map((r) => ({
        contract_code: r.contract_code,
        contract_name: r.contract_name,
        option_type: (decodeLabel("option_type", r.option_type) ?? "CALL") as "CALL" | "PUT",
        expiry_date: formatDate(r.expiry_date),
        last_date: formatDate(r.last_date),
        open_interest: toNum(r.open_interest) ?? 0,
      })),
    };
  }, fresh);
}

/**
 * Server-side oi_moneyness skew series — read from the materialized
 * analysis.options_moneyness_series / options_spot_daily tables (built by
 * ``python -m analysis.options`` PIPELINE 8), which replicate the former
 * LIVE compute's semantics exactly:
 *   • valid rows: expiry_date >= date AND 0 < implied_vol < 5
 *   • weights: GREATEST(open_interest, 1)
 *   • e_m = Σ(w·K)/Σ(w)/S  (raw units; K and S share the loader's scale,
 *     so the ratio equals the browser's (K/PRICE_SCALE)/(S/PRICE_SCALE))
 *   • per month group boundary = MIN(expiry_date) over the group's valid rows
 *   • OTM OI sums over the group's ALL active rows with OI > 0
 *   • per-date spot = MAX(underlying_close); 0 → null (loader "no spot")
 * The n_valid < 3 per-group rule nulls e_m in the table; the date-total < 3
 * rule and the price-space rebase (S/PRICE_SCALE × e_m) stay client-side.
 *
 * The former live query 5-way-joined the full options history per request
 * (~4.8s cold on the biggest CFFEX underlying); the materialized read is
 * an indexed per-underlying scan (single-digit ms). A/B-verified
 * 2026-10-04 (temp_scripts/ab_moneyness_sql.py): all 12 underlyings,
 * every group/spot row identical to the live SQL.
 */
export async function getOptionsMoneynessSeries(
  q: OptionsQuery,
  fresh = false,
): Promise<OptionsMoneynessSeriesResponse> {
  return cachedAggregate(aggKey("oi-moneyness", q), () => computeMoneynessSeries(q), fresh);
}

async function computeMoneynessSeries(q: OptionsQuery): Promise<OptionsMoneynessSeriesResponse> {
  const underlying = stripExchangeSuffix((q.underlying ?? "")).trim();
  const sd = toDateParam(q.start_date);
  const ed = toDateParam(q.end_date);

  // The two reads share one parameter list (underlying, optional range).
  const params: unknown[] = [underlying];
  const range: string[] = [];
  if (sd) {
    params.push(sd);
    range.push(`date >= $${params.length}::date`);
  }
  if (ed) {
    params.push(ed);
    range.push(`date <= $${params.length}::date`);
  }
  const rangeClause = range.length > 0 ? ` AND ${range.join(" AND ")}` : "";

  const [groupRows, spotRows] = await Promise.all([
    queryRows<QueryResultRow & {
      date: Date | string; expiry_month: Date | string | null; expiry_date: Date | string | null;
      n_valid: number | null; e_m: number | null;
      wsum_k: number | null; wsum: number | null;
      call_oi: number | null; put_oi: number | null; call_otm_oi: number | null; put_otm_oi: number | null;
    }>(`
      SELECT date, expiry_month, expiry_date, n_valid,
             e_m::float8 AS e_m,
             wsum_k::float8 AS wsum_k, wsum::float8 AS wsum,
             call_oi::float8 AS call_oi, put_oi::float8 AS put_oi,
             call_otm_oi::float8 AS call_otm_oi, put_otm_oi::float8 AS put_otm_oi
      FROM analysis.options_moneyness_series
      WHERE underlying_code = $1${rangeClause}
      ORDER BY date ASC, expiry_month ASC
    `, params),
    queryRows<QueryResultRow & { date: Date | string; close: number | null }>(`
      SELECT date, close::float8 AS close
      FROM analysis.options_spot_daily
      WHERE underlying_code = $1${rangeClause}
      ORDER BY date ASC
    `, params.slice()),
  ]);

  const groups: OptionsMoneynessSeriesResponse["groups"] = [];
  for (const r of groupRows) {
    if (r.expiry_month == null) continue;
    groups.push({
      date: formatDate(r.date),
      expiry_month: formatDate(r.expiry_month),
      expiry_date: r.expiry_date != null ? formatDate(r.expiry_date) : "",
      n_valid: toNum(r.n_valid) ?? 0,
      e_m: toNum(r.e_m),
      wsum_k: toNum(r.wsum_k) ?? 0,
      wsum: toNum(r.wsum) ?? 0,
      call_oi: toNum(r.call_oi) ?? 0,
      put_oi: toNum(r.put_oi) ?? 0,
      call_otm_oi: toNum(r.call_otm_oi) ?? 0,
      put_otm_oi: toNum(r.put_otm_oi) ?? 0,
    });
  }

  return {
    underlying_code: underlying,
    spot: spotRows.map((r) => ({
      date: formatDate(r.date),
      close: (toNum(r.close) ?? 0) > 0 ? toNum(r.close) : null,
    })),
    groups,
  };
}
