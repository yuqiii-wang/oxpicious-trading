/**
 * Futures service — queries stats.v_futures_daily (futures_identity
 * JOIN futures_basic_stats) across the futures families: CFFEX
 * (IC/IF/IH/IM/T/TF/TL/TS) plus the offshore China-A50 index futures
 * (CN = SGX FTSE A50, MCA = HKEX MSCI China A 50 Connect).
 *
 * Endpoints:
 *   listProducts()   → list of distinct product_code values
 *   getCombined()    → full combined response for one product
 *                      (dates calendar + contract meta + all daily rows)
 *
 * Continuity rule: a contract is marked is_continuous=TRUE only when every
 * product trading day between the contract's first and last appearance has
 * turnover > 0 — no zero days and no gaps inside its active window. The
 * offshore families (CN/MCA) publish no turnover, so trading_shares is the
 * fallback measure there.
 */
import { toNum } from "../lib/db.js";
import { querySqlView, sqlFrag } from "../lib/sql-views.js";
import { labelDecodeSql } from "./label-codes.js";
import type { QueryResultRow } from "pg";
import type {
  FuturesProduct,
  FuturesContractMeta,
  FuturesRow,
  FuturesCombinedResponse,
} from "../../shared/types.js";

/** Raw v_futures_daily row as pg returns it (numeric columns arrive as
 *  strings — coerced to FuturesRow once, right after the fetch). */
interface DbFuturesDailyRow extends QueryResultRow {
  date: string | Date;
  code: string;
  days_to_expiry: string | number | null;
  settlement_price: string | number | null;
  close: string | number | null;
  trading_amount: string | number | null;
  trading_shares: string | number | null;
  open_interest: string | number | null;
}

// ----------------------------------------------------------------------------
//  Products list
// ----------------------------------------------------------------------------
export async function listProducts(): Promise<FuturesProduct[]> {
  const rows = await querySqlView(
    "data_viz/api/services/futures/products",
    { contract_type_decode: sqlFrag(labelDecodeSql("contract_type", "contract_type")) },
  );
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
  const productRow = await querySqlView(
    "data_viz/api/services/futures/product_row",
    {
      product_code: product,
      contract_type_decode: sqlFrag(labelDecodeSql("contract_type", "contract_type")),
    },
  );

  if (productRow.length === 0) {
    throw new Error(`Unknown futures product: ${product}`);
  }
  const p = productRow[0];

  // Year-month per contract (distributed column — cheap to fetch)
  const ymdMap = await getYearMonthMap(product);

  // Fetch all rows and coerce once into the FuturesRow payload shape
  // (numeric-ish columns → number | null). The SELECT lives in the sql view
  // data_viz/api/services/futures/daily_rows.
  const dbRows = await querySqlView<DbFuturesDailyRow>(
    "data_viz/api/services/futures/daily_rows",
    { product_code: product },
  );
  const rows: FuturesRow[] = dbRows.map((r) => ({
    date: String(r.date),
    code: String(r.code),
    settlement_price: toNum(r.settlement_price),
    close: toNum(r.close),
    trading_amount: toNum(r.trading_amount),
    open_interest: toNum(r.open_interest),
    days_to_expiry: toNum(r.days_to_expiry),
  }));

  // Build product-level calendar + per-contract maps (from the raw rows,
  // which additionally carry trading_shares for the continuity measure)
  const datesSet = new Set<string>();
  const codeDates: Map<string, Set<string>> = new Map();
  const codeTradingAmt: Map<string, Map<string, number>> = new Map();

  for (const r of dbRows) {
    const date = String(r.date);
    const code = String(r.code);
    datesSet.add(date);

    if (!codeDates.has(code)) {
      codeDates.set(code, new Set());
      codeTradingAmt.set(code, new Map());
    }
    codeDates.get(code)!.add(date);
    // Turnover measure for the continuity test: the offshore CN/MCA
    // families publish no trading_amount — trading_shares stands in.
    const amt = toNum(r.trading_amount) ?? toNum(r.trading_shares) ?? 0;
    codeTradingAmt.get(code)!.set(date, amt);
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
  const rows = await querySqlView(
    "data_viz/api/services/futures/year_month_map",
    { product_code: product },
  );
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

  const rows = await querySqlView(
    "data_viz/api/services/futures/underlying_spot",
    { code: underlyingCode, min_date: minDate, max_date: maxDate },
  );

  // Build date → close map
  const priceMap = new Map<string, number | null>();
  for (const r of rows) {
    priceMap.set(String(r.date), toNum(r.close));
  }

  // Map to dates array (null for missing dates)
  return dates.map((d) => priceMap.get(d) ?? null);
}