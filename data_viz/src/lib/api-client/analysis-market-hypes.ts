import { fetchJson } from "./_cache";
import type {
  MarketHypeEpisodesResponse,
  MaSpreadSecType,
} from "@shared/types";

/**
 * Market-hype EPISODES (stats.mov_ave_market_hypes) for one
 * (sec_type, code), keyed by check-in window (5/20/60/120/255).
 *
 * MIGRATED off the mov-ave-spread/chart payload: served by the dedicated
 * GET /api/analysis/market-hypes endpoint so the shared CodeTrendChart's
 * Hypes toggle can fetch episodes on demand for ANY page's code trend
 * (the toggle is off by default and only fires this once per code).
 * Relies on the LRU TTL cache only (no version check; the stats table is
 * rebuilt offline by `python -m builds.market_hypes`).
 */
export function fetchMarketHypes(
  code: string,
  secType: MaSpreadSecType,
): Promise<MarketHypeEpisodesResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (secType) params.set("sec_type", secType);
  const qs = params.toString();
  return fetchJson<MarketHypeEpisodesResponse>(
    `/api/analysis/market-hypes${qs ? `?${qs}` : ""}`,
  );
}
