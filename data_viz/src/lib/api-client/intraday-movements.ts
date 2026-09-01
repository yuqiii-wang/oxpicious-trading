import { fetchJson } from "./_cache";
import type {
  IntradayMovementsResponse,
  PrevDayOhlcResponse,
} from "@shared/types";

// ---------------------------------------------------------------------------
//  Live Data — Intraday Movements (per-5-min-tick % change vs prev day close
//  for benchmark + ALL industries (shaded areas) + member indices). Drives
//  the "Market Movements" tab on the Live Data page. TTL-only cache — use
//  the Refresh button to bypass.
// ---------------------------------------------------------------------------
/** List benchmark codes that appear in live.sec_alloc_live_attribution
 *  (broad-market benchmarks sorted first). Drives the benchmark dropdown. */
export function fetchIntradayMovementsBenchmarks(): Promise<{
  benchmarks: Array<{
    benchmark_code: string;
    benchmark_name: string;
    is_broad_market: boolean | null;
  }>;
}> {
  return fetchJson(`/api/live-data/intraday-movements/benchmarks`);
}

/** Distinct dates available for ONE benchmark (raw intraday bars UNION live
 *  tick rows), newest first. Drives the Market Movements date selector. */
export function fetchIntradayMovementsDates(
  benchmarkCode: string,
): Promise<{ benchmark_code: string; dates: string[] }> {
  const params = new URLSearchParams();
  if (benchmarkCode) params.set("benchmark_code", benchmarkCode);
  return fetchJson(
    `/api/live-data/intraday-movements/dates?${params.toString()}`,
  );
}

/** Full Intraday Movements payload for ONE (benchmark, date).
 *  Returns benchmark_price_pct per tick + all industries' industry_price_pct
 *  per tick (for shades + middle bar chart) + member indices' code_price_pct
 *  per (code, tick) (for bottom bar chart). */
export function fetchIntradayMovements(
  benchmarkCode: string,
  date?: string | null,
): Promise<IntradayMovementsResponse> {
  const params = new URLSearchParams();
  if (benchmarkCode) params.set("benchmark_code", benchmarkCode);
  if (date) params.set("date", date);
  return fetchJson<IntradayMovementsResponse>(
    `/api/live-data/intraday-movements?${params.toString()}`,
  );
}

/** Raw prev-trading-day OHLC of the benchmark + every member index (with
 *  industry_id). Drives the single prev-day OHLC bar on the Market
 *  Movements top plot (before the 09:30 tick). `date` optional → latest
 *  available for the benchmark. */
export function fetchIntradayMovementsPrevDayOhlc(
  benchmarkCode: string,
  date?: string | null,
): Promise<PrevDayOhlcResponse> {
  const params = new URLSearchParams();
  if (benchmarkCode) params.set("benchmark_code", benchmarkCode);
  if (date) params.set("date", date);
  return fetchJson<PrevDayOhlcResponse>(
    `/api/live-data/intraday-movements/prev-day-ohlc?${params.toString()}`,
  );
}
