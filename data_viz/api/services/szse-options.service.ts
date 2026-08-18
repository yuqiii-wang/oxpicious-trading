/**
 * SZSE Options service — queries stats.v_options_quote view with
 * underlying + date-range filtering pushed down to the database.
 *
 * Also provides getEtfOhlcv() which queries stats.v_etf_margin for
 * the annual-sentiment panel (split-adjusted OHLCV).
 */
import { query, queryRows, toDateParam, formatDate, toNum } from "../lib/db.js";
import type { QueryResultRow } from "pg";
import { stripExchangeSuffix } from "../lib/classify-etf.js";
import type {
  OptionsRow,
  OptionsUnderlying,
  OptionsCombinedResponse,
  EtfOhlcvResponse,
  SkewnessCorrRow,
  SkewnessCorrResponse,
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

// Per-expiry-group gap row from analysis.options_stats_before_expiry
// (expiry-level PK: date, option_type, underlying_code, expiry_date).
// CALL and PUT rows are aggregated (AVG) and grouped by expiry month
// (DATE_TRUNC('month', expiry_date)) to match the frontend's
// month-level key structure: Map<`date|yyyymm`, ExpiryGapRow>.
interface DbExpiryGapRow extends QueryResultRow {
  date: Date | string;
  underlying_code: string;
  expiry_date: Date | string;
  today_gap_from_today_spot: number | null;
  today_gap_from_max_before_expiry: number | null;
  today_gap_from_min_before_expiry: number | null;
}

export interface ExpiryGapRow {
  date: string;
  underlying_code: string;
  expiry_date: string;
  today_gap_from_today_spot: number | null;
  today_gap_from_max_before_expiry: number | null;
  today_gap_from_min_before_expiry: number | null;
}

export interface ExpiryGapsResponse {
  underlying_code: string;
  rows: ExpiryGapRow[];
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
//  ETF code ↔ index code mapping (kept in sync with builds/options/szse/__main__.py)
// ----------------------------------------------------------------------------

const INDEX_TO_ETF: Record<string, { etfCode: string; etfName: string; indexName: string }> = {
  "000300": { etfCode: "159919", etfName: "沪深300ETF", indexName: "沪深300" },
  "000905": { etfCode: "159922", etfName: "中证500ETF", indexName: "中证500" },
  "399330": { etfCode: "159901", etfName: "深证100ETF", indexName: "深证100" },
  "399006": { etfCode: "159915", etfName: "创业板ETF", indexName: "创业板" },
};

const ETF_TO_INDEX: Record<string, string> = {};
for (const [idxCode, { etfCode }] of Object.entries(INDEX_TO_ETF)) {
  ETF_TO_INDEX[etfCode] = idxCode;
}

// ----------------------------------------------------------------------------
//  List underlyings — SELECT DISTINCT from v_options_quote
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
  return rows.map((r) => {
    const code = r.underlying_code;
    const info = INDEX_TO_ETF[code];
    return {
      code,
      name: info ? info.indexName : r.underlying_name,
    };
  });
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
//    • ETF mode   — stats.v_etf_margin via INDEX_TO_ETF mapping
//                   (split-adjusted prices when available)
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

  // ---- ETF mode (default): v_etf_margin via ETF mapping ----
  const etfCode = INDEX_TO_ETF[cleanedCode]?.etfCode ?? cleanedCode;
  const targetCode = etfCode;

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
//  Get precomputed expiry-set gap stats from analysis.options_stats_before_expiry.
//  Aggregates back from the per-contract store to (date, underlying_code,
//  expiry_date) level. Gaps are identical across contracts of the same
//  expiry set, so we use MIN() / MAX() / ANY_VALUE() aggregations.
// ----------------------------------------------------------------------------
export async function getOptionsExpiryGaps(
  underlying: string,
  startDate?: string,
  endDate?: string,
): Promise<ExpiryGapsResponse> {
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
      DATE_TRUNC('month', expiry_date) AS expiry_date,
      AVG(today_gap_from_today_spot)        AS today_gap_from_today_spot,
      AVG(today_gap_from_max_before_expiry) AS today_gap_from_max_before_expiry,
      AVG(today_gap_from_min_before_expiry) AS today_gap_from_min_before_expiry
    FROM analysis.options_stats_before_expiry
    WHERE ${where.join(" AND ")}
    GROUP BY date, underlying_code, DATE_TRUNC('month', expiry_date)
    ORDER BY date ASC, DATE_TRUNC('month', expiry_date) ASC
  `;

  const rows = await queryRows<DbExpiryGapRow>(sql, params);
  const transformed: ExpiryGapRow[] = rows.map((r) => ({
    date: formatDate(r.date),
    underlying_code: r.underlying_code,
    expiry_date: formatDate(r.expiry_date),
    today_gap_from_today_spot: toNum(r.today_gap_from_today_spot),
    today_gap_from_max_before_expiry: toNum(r.today_gap_from_max_before_expiry),
    today_gap_from_min_before_expiry: toNum(r.today_gap_from_min_before_expiry),
  }));

  return { underlying_code: cleanedCode, rows: transformed };
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
