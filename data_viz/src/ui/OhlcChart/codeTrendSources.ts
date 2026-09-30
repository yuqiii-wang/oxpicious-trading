/**
 * codeTrendSources — the default per-sec_type data sources of
 * CodeTrendChart (fetch + map to the shared CodeTrendRow shape).
 *
 * ALL sec_types load through the ONE shared per-code baseline endpoint
 * (2026-09-29 consolidation — shared component ⇒ shared data path):
 *
 *   GET /api/sec-baseline?sec_type=…&code=…   (fetchSecBaseline)
 *
 * which resolves (sec_type, code) → exactly one bundle: rows + display
 * name + (stock only) dividend events, already carrying margin overlays.
 * One cached URL per (sec_type, code) shared with every other baseline
 * consumer (useSecBaselineBundle on the PE / Dividends panels, the live
 * market-movements member history, IndustrySentiments' market trend, …).
 *
 * Rows are mapped onto the StockDailyRow shape the shared chart consumes
 * (see mapEtfRows / mapIndexRows); fields a source doesn't carry (EPS,
 * dividends, …) are null and their overlays simply don't render. The
 * former per-leg endpoints (the etf-margin/index combined browse endpoints
 * with a code filter, and the stock core/margin/dividends granular block
 * fan-out) are retired from this path.
 */
import { fetchSecBaseline } from "@/lib/api-client";
import type {
  EtfBundle,
  EtfMarginRow,
  IndexBundle,
  IndexDailyRow,
  StockBundle,
} from "@shared/types";
import type { CodeTrendRow, CodeTrendSource, CodeTrendSecType } from "./codeTrendTypes";

/** Strip the exchange suffix — the baseline endpoint matches bare codes. */
export function stripCodeSuffix(code: string): string {
  return code.toUpperCase().replace(/\.(SS|SZ|SH|BJ|HK)$/i, "");
}

/** ETF margin rows → the shared chart's row shape (adjusted OHLC preferred). */
export function mapEtfRows(rows: EtfMarginRow[]): CodeTrendRow[] {
  return rows.map((r) => ({
    date: r.date,
    // Prefer adjusted OHLC — corp actions (dividend/split) otherwise draw
    // artificial cliffs into the trend.
    open: r.adj_open ?? r.open,
    high: r.adj_high ?? r.high,
    low: r.adj_low ?? r.low,
    close: r.adj_close ?? r.close,
    prev_close: r.prev_close,
    pct_change: null,
    pe: null,
    eps: null,
    is_pe_estimated: false,
    has_intraday_5mins: false,
    trading_shares: 0,
    trading_amount: r.trading_amount,
    rz_balance: r.rz_balance,
    rz_buy: null,
    rq_balance_qty: r.rq_balance_qty,
    rq_balance_amt: r.rq_balance_amt,
    total_balance: r.total_balance,
  }));
}

/** Index baseline rows → the shared chart's row shape (PE carried over). */
export function mapIndexRows(rows: IndexDailyRow[]): CodeTrendRow[] {
  return rows.map((r) => ({
    date: r.date,
    open: r.open,
    high: r.high,
    low: r.low,
    close: r.close,
    prev_close: null,
    pct_change: r.change_pct,
    pe: r.pe,
    eps: null,
    is_pe_estimated: false,
    has_intraday_5mins: r.has_intraday_5mins,
    trading_shares: r.trading_shares ?? 0,
    trading_amount: r.trading_amount ?? 0,
    rz_balance: null,
    rz_buy: null,
    rq_balance_qty: null,
    rq_balance_amt: null,
    total_balance: null,
  }));
}

/** Fetch one code's baseline bundle through the shared endpoint and map it
 *  to CodeTrendData: rows in the chart's StockDailyRow shape, display
 *  name, and (stock only) the dividend events for ex-div markers. */
async function fetchBaselineData(
  secType: CodeTrendSecType,
  code: string,
): Promise<{ rows: CodeTrendRow[]; name: string | null; dividends: StockBundle["dividends"] }> {
  const r = await fetchSecBaseline(secType, stripCodeSuffix(code));
  const bundle = r.bundle;
  if (!bundle) return { rows: [], name: null, dividends: [] };
  switch (secType) {
    case "index": {
      const b = bundle as IndexBundle;
      return { rows: mapIndexRows(b.rows), name: b.name || null, dividends: [] };
    }
    case "etf": {
      const b = bundle as EtfBundle;
      return { rows: mapEtfRows(b.rows), name: b.name || null, dividends: [] };
    }
    default: {
      const b = bundle as StockBundle;
      // Stock bundle rows are ALREADY StockDailyRow (the chart's row shape)
      // with margin overlays joined server-side.
      return { rows: b.rows, name: b.name || null, dividends: b.dividends };
    }
  }
}

/** Default per-sec_type sources — override via the `sources` prop. */
export const CODE_TREND_SOURCES: Record<CodeTrendSecType, CodeTrendSource> = {
  stock: {
    fetch: (code) => fetchBaselineData("stock", code),
  },
  etf: {
    fetch: (code) => fetchBaselineData("etf", code),
  },
  index: {
    fetch: (code) => fetchBaselineData("index", code),
  },
};
