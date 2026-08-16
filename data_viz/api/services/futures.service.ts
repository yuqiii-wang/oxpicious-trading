/**
 * Futures service — queries stats.v_futures_baseline (futures_identity
 * JOIN futures_basic_stats) for CFFEX futures products.
 *
 * Endpoints:
 *   listProducts()   → list of 8 products (IC/IF/IH/IM/T/TF/TL/TS)
 *   getCombined()    → full combined response for one product
 *                      (dates calendar + contract meta + all daily rows)
 *
 * Continuity rule: a contract is marked is_continuous=TRUE only when every
 * product trading day between the contract's first and last appearance has
 * trading_amount > 0 — no zero-amount days and no gaps inside its active
 * window.
 */
import { queryRows, toNum } from "../lib/db.js";
import type {
  FuturesProduct,
  FuturesContractMeta,
  FuturesRow,
  FuturesCombinedResponse,
} from "../../shared/types.js";

// ----------------------------------------------------------------------------
//  Products list
// ----------------------------------------------------------------------------
export async function listProducts(): Promise<FuturesProduct[]> {
  const rows = await queryRows(`
    SELECT DISTINCT
      product_code,
      name,
      contract_type,
      underlying_code,
      underlying_name
    FROM stats.futures_identity
    ORDER BY product_code
  `);
  return rows.map((r) => ({
    product_code: r.product_code as string,
    name: r.name as string,
    contract_type: r.contract_type as "index" | "bond",
    underlying_code: r.underlying_code as string,
    underlying_name: r.underlying_name as string,
  }));
}

// ----------------------------------------------------------------------------
//  Combined response for one product
// ----------------------------------------------------------------------------
export async function getCombined(
  product: string,
): Promise<FuturesCombinedResponse> {
  // Validate product + fetch header info
  const productRow = await queryRows(`
    SELECT product_code, name, contract_type, underlying_code, underlying_name
    FROM stats.futures_identity
    WHERE product_code = $1
    LIMIT 1
  `, [product]);

  if (productRow.length === 0) {
    throw new Error(`Unknown futures product: ${product}`);
  }
  const p = productRow[0];

  // Year-month per contract (distributed column — cheap to fetch)
  const ymdMap = await getYearMonthMap(product);

  // Fetch all rows
  const rows = await queryRows(`
    SELECT
      i.date,
      i.code,
      i.days_to_expiry,
      b.settlement_price,
      b.close,
      b.trading_amount,
      b.open_interest
    FROM stats.futures_identity i
    LEFT JOIN stats.futures_basic_stats b
      ON i.date = b.date AND i.code = b.code
    WHERE i.product_code = $1
    ORDER BY i.date, i.code
  `, [product]);

  // Build product-level calendar + per-contract maps
  const datesSet = new Set<string>();
  const codeDates: Map<string, Set<string>> = new Map();
  const codeTradingAmt: Map<string, Map<string, number>> = new Map();
  const codeRows: Map<string, FuturesRow[]> = new Map();

  for (const r of rows) {
    const date = String(r.date);
    const code = String(r.code);
    datesSet.add(date);

    if (!codeDates.has(code)) {
      codeDates.set(code, new Set());
      codeTradingAmt.set(code, new Map());
      codeRows.set(code, []);
    }
    codeDates.get(code)!.add(date);
    const amt = toNum(r.trading_amount) ?? 0;
    codeTradingAmt.get(code)!.set(date, amt);
    codeRows.get(code)!.push({
      date,
      code,
      settlement_price: toNum(r.settlement_price),
      close: toNum(r.close),
      trading_amount: amt,
      open_interest: toNum(r.open_interest),
      days_to_expiry: toNum(r.days_to_expiry),
    });
  }

  const dates = Array.from(datesSet).sort();
  const productLatestDate = dates[dates.length - 1];

  // Per-contract meta + continuity check
  const contracts: FuturesContractMeta[] = [];
  const contractCodes = Array.from(codeDates.keys()).sort();

  for (const code of contractCodes) {
    const myDates = Array.from(codeDates.get(code)!).sort();
    const firstDate = myDates[0];
    const lastDate = myDates[myDates.length - 1];
    const isAlive = lastDate === productLatestDate;

    // Continuity: every product trading day between firstDate and lastDate
    // must have the contract present AND trading_amount > 0.
    let isContinuous = true;
    const amtMap = codeTradingAmt.get(code)!;
    for (const d of dates) {
      if (d < firstDate || d > lastDate) continue;
      const amt = amtMap.get(d);
      if (amt === undefined || amt <= 0) {
        isContinuous = false;
        break;
      }
    }

    contracts.push({
      code,
      contract_year_month: ymdMap.get(code) ?? "",
      first_date: firstDate,
      last_date: lastDate,
      is_alive: isAlive,
      is_continuous: isContinuous,
    });
  }

  // Fetch underlying spot price (only for index futures)
  const spotPrice =
    p.contract_type === "index"
      ? await getUnderlyingSpotPrice(p.underlying_code as string, dates)
      : null;

  return {
    product: p.product_code as string,
    product_name: p.name as string,
    contract_type: p.contract_type as "index" | "bond",
    underlying_code: p.underlying_code as string,
    underlying_name: p.underlying_name as string,
    dates,
    contracts,
    rows,
    spot_price: spotPrice,
  };
}

// ----------------------------------------------------------------------------
//  Helpers
// ----------------------------------------------------------------------------
async function getYearMonthMap(product: string): Promise<Map<string, string>> {
  const rows = await queryRows(`
    SELECT DISTINCT code, contract_year_month
    FROM stats.futures_identity
    WHERE product_code = $1
  `, [product]);
  const map = new Map<string, string>();
  for (const r of rows) {
    map.set(String(r.code), String(r.contract_year_month));
  }
  return map;
}

// ----------------------------------------------------------------------------
//  Underlying spot price — fetch index daily close for index futures
// ----------------------------------------------------------------------------
async function getUnderlyingSpotPrice(
  underlyingCode: string,
  dates: string[],
): Promise<(number | null)[]> {
  if (dates.length === 0) return [];

  const minDate = dates[0];
  const maxDate = dates[dates.length - 1];

  const rows = await queryRows(`
    SELECT date, close
    FROM stats.index_basic_stats
    WHERE code = $1
      AND date >= $2
      AND date <= $3
    ORDER BY date
  `, [underlyingCode, minDate, maxDate]);

  // Build date → close map
  const priceMap = new Map<string, number | null>();
  for (const r of rows) {
    priceMap.set(String(r.date), toNum(r.close));
  }

  // Map to dates array (null for missing dates)
  return dates.map((d) => priceMap.get(d) ?? null);
}