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
  SkewnessCorrRow,
  SkewnessCorrResponse,
  SkewnessCrossCountRow,
  SkewnessCrossCountResponse,
  SkewnessSeriesRow,
  SkewnessSeriesResponse,
  IvSkewRow,
  IvSkewResponse,
} from "../../shared/types.js";

export interface OptionsQuery {
  underlying?: string;
  start_date?: string;
  end_date?: string;
  /** 'ETF' (SZSE ETF options) or 'INDEX' (CFFEX index options). */
  target_type?: string;
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
//  SZSE ETF options keep native ETF codes (1599xx); CFFEX index options
//  use index codes (000xxx/399xxx). The two venues load different code
//  sets via the underlying_target_type filter — no code mapping needed.
// ----------------------------------------------------------------------------
export async function listUnderlyings(targetType?: string): Promise<OptionsUnderlying[]> {
  const t = (targetType ?? "").trim().toUpperCase();
  const sql = `
    SELECT DISTINCT underlying_code, underlying_name
    FROM stats.v_options_quote
    WHERE underlying_code IS NOT NULL AND underlying_code != ''
      ${t === "ETF" || t === "INDEX" ? "AND underlying_target_type = $1" : ""}
    ORDER BY underlying_code
  `;
  const rows = await queryRows<DbUnderlyingRow>(sql, t === "ETF" || t === "INDEX" ? [t] : []);
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

  const params: unknown[] = [];
  const where: string[] = [];
  let i = 1;

  if (targetType === "ETF" || targetType === "INDEX") {
    where.push(`underlying_target_type = $${i++}`);
    params.push(targetType);
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
    const transformed = rows.map((r) => ({
      date: formatDate(r.date),
      open: toNum(r.open) ?? 0,
      high: toNum(r.high) ?? 0,
      low: toNum(r.low) ?? 0,
      close: toNum(r.close) ?? 0,
      volume: toNum(r.trading_shares) ?? 0,
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
//  Options Skewness Stats — per-expiry whole-period correlation
// ----------------------------------------------------------------------------

interface DbSkewnessCorrRow extends QueryResultRow {
  date: Date | string;
  underlying_code: string;
  expiry_month: Date | string;
  corr_skewness_ma5_vs_spot_ma5: number | null;
  corr_skewness_ma20_vs_spot_ma20: number | null;
  corr_skewness_ma60_vs_spot_ma60: number | null;
}

export async function getOptionsSkewnessCorr(
  underlying: string,
  startDate?: string,
  endDate?: string,
  skewType: SkewType = "oi_moneyness",
): Promise<SkewnessCorrResponse> {
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
      AVG(corr_skewness_ma5_vs_spot_ma5) AS corr_skewness_ma5_vs_spot_ma5,
      AVG(corr_skewness_ma20_vs_spot_ma20) AS corr_skewness_ma20_vs_spot_ma20,
      AVG(corr_skewness_ma60_vs_spot_ma60) AS corr_skewness_ma60_vs_spot_ma60
    FROM analysis.options_skewness_stats
    WHERE ${where.join(" AND ")}
    GROUP BY date, underlying_code, DATE_TRUNC('month', expiry_date)
    ORDER BY date ASC, DATE_TRUNC('month', expiry_date) ASC
  `;

  const rows = await queryRows<DbSkewnessCorrRow>(sql, params);
  const transformed: SkewnessCorrRow[] = rows.map((r) => ({
    date: formatDate(r.date),
    expiry_month: formatDate(r.expiry_month),
    corr_skewness_ma5_vs_spot_ma5: toNum(r.corr_skewness_ma5_vs_spot_ma5),
    corr_skewness_ma20_vs_spot_ma20: toNum(r.corr_skewness_ma20_vs_spot_ma20),
    corr_skewness_ma60_vs_spot_ma60: toNum(r.corr_skewness_ma60_vs_spot_ma60),
  }));

  return { underlying_code: cleanedCode, rows: transformed };
}

// ----------------------------------------------------------------------------
//  Options Skewness Cross Counts — per-expiry cross count of skewness curve
// ----------------------------------------------------------------------------

interface DbSkewnessCrossCountRow extends QueryResultRow {
  date: Date | string;
  underlying_code: string;
  expiry_month: Date | string;
  count_skewness_curve_crossed_spot: number;
}

export async function getOptionsSkewnessCrossCounts(
  underlying: string,
  startDate?: string,
  endDate?: string,
  skewType: SkewType = "oi_moneyness",
): Promise<SkewnessCrossCountResponse> {
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
      MAX(count_skewness_curve_crossed_spot) AS count_skewness_curve_crossed_spot
    FROM analysis.options_skewness_stats
    WHERE ${where.join(" AND ")}
    GROUP BY date, underlying_code, DATE_TRUNC('month', expiry_date)
    ORDER BY date ASC, DATE_TRUNC('month', expiry_date) ASC
  `;

  const rows = await queryRows<DbSkewnessCrossCountRow>(sql, params);
  const transformed: SkewnessCrossCountRow[] = rows.map((r) => ({
    date: formatDate(r.date),
    expiry_month: formatDate(r.expiry_month),
    count_skewness_curve_crossed_spot: toNum(r.count_skewness_curve_crossed_spot) ?? 0,
  }));

  return { underlying_code: cleanedCode, rows: transformed };
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
  smile_skewness: number | null;
  rr25_ma5: number | null;
  rr25_ma20: number | null;
  rr25_ma60: number | null;
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
      AVG(smile_skewness) AS smile_skewness,
      AVG(rr25_ma5) AS rr25_ma5,
      AVG(rr25_ma20) AS rr25_ma20,
      AVG(rr25_ma60) AS rr25_ma60
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
    smile_skewness: toNum(r.smile_skewness),
    rr25_ma5: toNum(r.rr25_ma5),
    rr25_ma20: toNum(r.rr25_ma20),
    rr25_ma60: toNum(r.rr25_ma60),
  }));

  return { underlying_code: cleanedCode, rows: transformed };
}
