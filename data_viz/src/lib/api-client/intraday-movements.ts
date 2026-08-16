import { fetchJson } from "./_cache";
import type {
  IntradayMovementsResponse,
} from "../../../shared/types";

// ---------------------------------------------------------------------------
//  Live Data — Intraday Movements (per-5-min-tick % change vs prev day close
//  for benchmark + ALL industries (shaded areas) + member indices). Drives
//  the "Market Movements" tab on the Live Data page. TTL-only cache — use
//  the Refresh button to bypass.
// ---------------------------------------------------------------------------
/** List benchmark codes that appear in analysis.intraday_industry_market_movements
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
