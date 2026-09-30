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
import type {
  OptionsRow,
  OptionsUnderlying,
  OptionsCombinedResponse,
  OptionsWallRow,
  OptionsWallsResponse,
  OptionsDatesResponse,
  OptionsSnapshotResponse,
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
  option_type: string;
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
    option_type: (String(r.option_type).toUpperCase() === "PUT" ? "PUT" : "CALL") as "CALL" | "PUT",
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
  option_type: string;
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
    option_type: (String(r.option_type).toUpperCase() === "PUT" ? "PUT" : "CALL") as "PUT" | "CALL",
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
export async function getOptionsDates(q: OptionsQuery): Promise<OptionsDatesResponse> {
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
}

/** One trading date's quote rows + history-wide strike/expiry-month meta
 *  (the wall panel's unified axes). Small: a few hundred rows per date. */
export async function getOptionsSnapshot(q: OptionsQuery): Promise<OptionsSnapshotResponse> {
  const date = toDateParam(q.start_date) ?? toDateParam(q.end_date);
  if (date == null) {
    throw new Error("snapshot requires start_date (the snapshot date)");
  }
  const combined = await getOptionsCombined({ ...q, start_date: date, end_date: date });

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
  const months = await queryRows<QueryResultRow & { expiry_month: string }>(`
    SELECT DISTINCT t.expiry_month
    FROM stats.options_terms t
    ${metaWhereClause}
  `, metaParams.slice());
  // month labels are Chinese ("10月") — order by their numeric month part
  const sortedMonths = months.map((r) => r.expiry_month);
  sortedMonths.sort((a, b) => monthSortKey(a) - monthSortKey(b));

  return {
    underlying_code: combined.underlying_code,
    date,
    rows: combined.rows,
    strikes: meta.rows.map((r) => toNum(r.strike_price) ?? 0),
    expiry_months: sortedMonths,
  };
}

/** Per (date × expiry) call/put OI + per-date spot — the trend charts'
 *  daily aggregates and shared x-axis/spot line. */
export async function getOptionsOiDaily(q: OptionsQuery): Promise<OptionsOiDailyResponse> {
  const params: unknown[] = [];
  const where = termsWhere(q, params);
  const whereClause = where.length > 0 ? `WHERE ${where.join(" AND ")}` : "";
  const rows = await queryRows<QueryResultRow & {
    date: Date | string; expiry_date: Date | string; call_oi: string | number; put_oi: string | number;
  }>(`
    SELECT t.date, t.expiry_date,
           SUM(CASE WHEN t.option_type = 'CALL' THEN vo.open_interest ELSE 0 END) AS call_oi,
           SUM(CASE WHEN t.option_type = 'PUT'  THEN vo.open_interest ELSE 0 END) AS put_oi
    FROM stats.options_terms t
    JOIN stats.options_volume_oi vo ON vo.contract_code = t.contract_code AND vo.date = t.date
    ${whereClause}
    GROUP BY t.date, t.expiry_date
    ORDER BY t.date ASC, t.expiry_date ASC
  `, params);

  const spot = await getOptionsSpotSeries(q);

  return {
    underlying_code: (q.underlying ?? "").trim(),
    daily: rows.map((r) => ({
      date: formatDate(r.date),
      expiry_date: formatDate(r.expiry_date),
      call_oi: toNum(r.call_oi) ?? 0,
      put_oi: toNum(r.put_oi) ?? 0,
    })),
    spot,
  };
}

/** Per-date underlying close (null when the loader stored 0 = "no spot"). */
async function getOptionsSpotSeries(
  q: OptionsQuery,
): Promise<Array<{ date: string; close: number | null }>> {
  const spotParams: unknown[] = [];
  const spotWhere = termsWhere(q, spotParams);
  const spotWhereClause = spotWhere.length > 0 ? `WHERE ${spotWhere.join(" AND ")}` : "";
  const spot = await queryRows<QueryResultRow & { date: Date | string; close: string | number | null }>(`
    SELECT t.date, MAX(st.underlying_close) AS close
    FROM stats.options_terms t
    JOIN stats.options_settlement st ON st.contract_code = t.contract_code AND st.date = t.date
    ${spotWhereClause}
    GROUP BY t.date
    ORDER BY t.date ASC
  `, spotParams);
  return spot.map((r) => ({
    date: formatDate(r.date),
    close: (toNum(r.close) ?? 0) > 0 ? toNum(r.close) : null,
  }));
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
           SUM(CASE WHEN t.option_type = 'CALL' THEN vo.open_interest ELSE 0 END) AS call_oi,
           SUM(CASE WHEN t.option_type = 'PUT'  THEN vo.open_interest ELSE 0 END) AS put_oi
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

/** Per-contract final OI (last session ≤ its expiry) + per-expiry lifetime
 *  OI totals — the expiry markers and cohort list. One row per contract
 *  ever quoted (~5k for the biggest CFFEX underlying). */
export async function getOptionsExpiryMeta(q: OptionsQuery): Promise<OptionsExpiryMetaResponse> {
  const params: unknown[] = [];
  const where = termsWhere(q, params);
  where.push(`t.date <= t.expiry_date`);
  const markers = await queryRows<QueryResultRow & {
    contract_code: string; contract_name: string; option_type: string;
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

  const cohortParams: unknown[] = [];
  const cohortWhere = termsWhere(q, cohortParams);
  const cohortWhereClause = cohortWhere.length > 0 ? `WHERE ${cohortWhere.join(" AND ")}` : "";
  const cohorts = await queryRows<QueryResultRow & { expiry_date: Date | string; total_oi: string | number }>(`
    SELECT t.expiry_date, SUM(vo.open_interest) AS total_oi
    FROM stats.options_terms t
    JOIN stats.options_volume_oi vo ON vo.contract_code = t.contract_code AND vo.date = t.date
    ${cohortWhereClause}
    GROUP BY t.expiry_date
    ORDER BY t.expiry_date ASC
  `, cohortParams);

  return {
    underlying_code: (q.underlying ?? "").trim(),
    markers: markers.map((r) => ({
      contract_code: r.contract_code,
      contract_name: r.contract_name,
      option_type: (String(r.option_type).toUpperCase() === "PUT" ? "PUT" : "CALL") as "CALL" | "PUT",
      expiry_date: formatDate(r.expiry_date),
      last_date: formatDate(r.last_date),
      open_interest: toNum(r.open_interest) ?? 0,
    })),
    cohorts: cohorts.map((r) => ({
      expiry_date: formatDate(r.expiry_date),
      total_oi: toNum(r.total_oi) ?? 0,
    })),
  };
}

/**
 * Server-side oi_moneyness skew series — replicates the former in-browser
 * computeDailySkewSeries() semantics exactly:
 *   • valid rows: expiry_date >= date AND 0 < implied_vol < 5
 *   • weights: GREATEST(open_interest, 1)
 *   • e_m = Σ(w·K)/Σ(w)/S  (raw units; K and S share the loader's scale,
 *     so the ratio equals the browser's (K/PRICE_SCALE)/(S/PRICE_SCALE))
 *   • per month group boundary = MIN(expiry_date) over the group's valid rows
 *   • OTM OI sums over the group's ALL active rows with OI > 0
 *   • per-date spot = MAX(underlying_close); 0 → null (loader "no spot")
 * The n_valid < 3 per-group rule nulls e_m here; the date-total < 3 rule
 * and the price-space rebase (S/PRICE_SCALE × e_m) stay client-side.
 */
export async function getOptionsMoneynessSeries(q: OptionsQuery): Promise<OptionsMoneynessSeriesResponse> {
  const params: unknown[] = [];
  const where = termsWhere(q, params);
  const whereClause = where.length > 0 ? `WHERE ${where.join(" AND ")}` : "";

  const sql = `
    WITH scoped AS (
      SELECT t.date, t.expiry_date, DATE_TRUNC('month', t.expiry_date) AS expiry_month,
             t.option_type, s.strike_price, st.underlying_close,
             vo.open_interest, g.implied_vol
      FROM stats.options_terms t
      LEFT JOIN stats.options_strike s
        ON s.contract_code = t.contract_code AND s.date = t.date
      LEFT JOIN stats.options_settlement st
        ON st.contract_code = t.contract_code AND st.date = t.date
      LEFT JOIN stats.options_greeks g
        ON g.contract_code = t.contract_code AND g.date = t.date
      LEFT JOIN stats.options_volume_oi vo
        ON vo.contract_code = t.contract_code AND vo.date = t.date
      ${whereClause}
    ),
    day_spot AS (
      SELECT date, MAX(underlying_close) AS close FROM scoped GROUP BY date
    ),
    per_group AS (
      SELECT v.date, v.expiry_month,
             MIN(v.expiry_date) AS expiry_date,
             COUNT(*) AS n_valid,
             SUM(GREATEST(v.open_interest, 1) * v.strike_price)::float8 AS wsum_k,
             SUM(GREATEST(v.open_interest, 1))::float8 AS wsum
      FROM scoped v
      WHERE v.expiry_date >= v.date
        AND v.implied_vol > 0 AND v.implied_vol < 5
      GROUP BY v.date, v.expiry_month
    ),
    active_agg AS (
      SELECT a.date, a.expiry_month,
             SUM(CASE WHEN a.option_type = 'CALL' THEN a.open_interest ELSE 0 END)::float8 AS call_oi,
             SUM(CASE WHEN a.option_type = 'PUT'  THEN a.open_interest ELSE 0 END)::float8 AS put_oi,
             SUM(CASE WHEN a.option_type = 'CALL' AND a.strike_price >= a.underlying_close
                      THEN a.open_interest ELSE 0 END)::float8 AS call_otm_oi,
             SUM(CASE WHEN a.option_type = 'PUT'  AND a.strike_price <= a.underlying_close
                      THEN a.open_interest ELSE 0 END)::float8 AS put_otm_oi
      FROM scoped a
      WHERE a.expiry_date >= a.date AND a.open_interest > 0
      GROUP BY a.date, a.expiry_month
    )
    SELECT ds.date, pg.expiry_month, pg.expiry_date, pg.n_valid,
           CASE WHEN pg.n_valid >= 3 AND ds.close > 0
                THEN (pg.wsum_k / pg.wsum) / ds.close
                ELSE NULL END::float8 AS e_m,
           pg.wsum_k, pg.wsum,
           aa.call_oi, aa.put_oi, aa.call_otm_oi, aa.put_otm_oi
    FROM day_spot ds
    LEFT JOIN per_group pg ON pg.date = ds.date
    LEFT JOIN active_agg aa ON aa.date = ds.date AND aa.expiry_month = pg.expiry_month
    ORDER BY ds.date ASC, pg.expiry_month ASC
  `;
  const rows = (await queryReadWithSession<QueryResultRow & {
    date: Date | string; expiry_month: Date | string | null; expiry_date: Date | string | null;
    n_valid: string | number | null; e_m: number | null;
    wsum_k: number | null; wsum: number | null;
    call_oi: number | null; put_oi: number | null; call_otm_oi: number | null; put_otm_oi: number | null;
  }>(COMBINED_SESSION_SETS, sql, params)).rows;

  const groups: OptionsMoneynessSeriesResponse["groups"] = [];
  for (const r of rows) {
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
    underlying_code: (q.underlying ?? "").trim(),
    spot: await getOptionsSpotSeries(q),
    groups,
  };
}
