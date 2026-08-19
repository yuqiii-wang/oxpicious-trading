/**
 * SZSE Options service — queries stats.v_options_quote view with
 * underlying + date-range filtering pushed down to the database.
 *
 * Also provides getEtfOhlcv() which queries stats.v_etf_margin for
 * the annual-sentiment panel (split-adjusted OHLCV).
 */
import { queryRows, toDateParam, formatDate, toNum } from "../lib/db.js";
import type { QueryResultRow } from "pg";
import { stripExchangeSuffix } from "../lib/classify-etf.js";
import type {
  OptionsRow,
  OptionsUnderlying,
  OptionsCombinedResponse,
  EtfOhlcvResponse,
  SkewnessCorrRow,
  SkewnessCorrResponse,
  SkewnessCrossCountRow,
  SkewnessCrossCountResponse,
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

  const params: unknown[] = [];
  const where: string[] = [
    `REGEXP_REPLACE(code, '\\.(SZ|SS|SH)$', '') = $1`,
  ];
  params.push(targetCode);
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
): Promise<SkewnessCorrResponse> {
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
): Promise<SkewnessCrossCountResponse> {
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
