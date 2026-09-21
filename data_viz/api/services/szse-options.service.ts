/**
 * SZSE Options service — queries stats.v_options_quote view with
 * underlying + date-range filtering pushed down to the database.
 *
 * Also provides getEtfOhlcv() which queries stats.v_etf_margin for
 * the annual-sentiment panel (split-adjusted OHLCV).
 */
import { queryRows, toDateParam, formatDate, toNum } from "../lib/db.js";
import type { QueryResultRow } from "pg";
import { stripExchangeSuffix, codeVariants } from "../lib/classify-etf.js";
import type {
  OptionsRow,
  OptionsUnderlying,
  OptionsCombinedResponse,
  OptionsWallRow,
  OptionsWallsResponse,
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
    moneyness_ratio: toNum(r.moneyness_ratio) ?? 0,
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

const OPTIONS_COLUMNS = `
  date, contract_code, contract_name,
  underlying_code, underlying_name, option_type,
  expiry_month, expiry_date, days_to_expiry,
  strike_price, settle, underlying_close, moneyness_ratio,
  open_interest, volume,
  implied_vol, delta, theta, gamma, vega, rho
`;

// ----------------------------------------------------------------------------
//  List underlyings — SELECT DISTINCT from v_options_quote
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
    FROM stats.v_options_quote
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
// ----------------------------------------------------------------------------
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
    where.push(`underlying_target_type = $${i++}`);
    params.push(targetType);
  }
  if (exchange === "SSE" || exchange === "SZSE" || exchange === "CFFEX") {
    where.push(`exchange = $${i++}`);
    params.push(exchange);
  }
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

  const whereClause = where.length > 0 ? `WHERE ${where.join(" AND ")}` : "";

  const sql = `
    SELECT ${OPTIONS_COLUMNS}
    FROM stats.v_options_quote
    ${whereClause}
    ORDER BY date ASC
  `;
  const rows = await queryRows<DbOptionsRow>(sql, params);
  const transformed = rows.map(transformOptionsRow);
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
//    • INDEX mode — stats.v_index_baseline directly (code = index code)
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

  // ---- INDEX mode: query v_index_baseline with the raw index code ----
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
      FROM stats.v_index_baseline
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
