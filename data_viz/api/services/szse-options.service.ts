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
} from "../../shared/types.js";

export interface OptionsQuery {
  underlying?: string;
  start_date?: string;
  end_date?: string;
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
  volume_wan: number | null;
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
// ----------------------------------------------------------------------------
export async function listUnderlyings(): Promise<OptionsUnderlying[]> {
  const rows = await queryRows<DbUnderlyingRow>(`
    SELECT DISTINCT underlying_code, underlying_name
    FROM stats.v_options_quote
    WHERE underlying_code IS NOT NULL AND underlying_code != ''
    ORDER BY underlying_code
  `);
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

  const params: unknown[] = [];
  const where: string[] = [];
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
//  Get ETF OHLCV from v_etf_margin for the annual-sentiment panel.
//  Uses split-adjusted prices when available.
// ----------------------------------------------------------------------------
export async function getEtfOhlcv(
  code: string,
  startDate?: string,
  endDate?: string,
): Promise<EtfOhlcvResponse> {
  const targetCode = stripExchangeSuffix(code).trim();

  const params: unknown[] = [];
  const where: string[] = [
    `REGEXP_REPLACE(code, '\\.(SZ|SS|SH)$', '') = $1`,
  ];
  params.push(targetCode);
  let i = 2;

  const sd = toDateParam(startDate);
  const ed = toDateParam(endDate);
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
           open, high, low, close, volume_wan
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
      volume: toNum(r.volume_wan) ?? 0,
    };
  });

  return {
    dates: transformed.map((r) => r.date),
    code: targetCode,
    rows: transformed,
  };
}
