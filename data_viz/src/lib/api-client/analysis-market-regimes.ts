import { fetchJson } from "./_cache";
import type {
  MarketRegimeSpansResponse,
  MaSpreadSecType,
} from "@shared/types";

/**
 * Market-regime SPANS (the stats.market_regime_spans table — contiguous
 * same-regime runs builds.market_regimes materializes over the daily
 * stats.market_regimes registry) for one (sec_type, code), keyed by
 * regime (calm/hot/panic/quiet).
 *
 * Replaces the retired fetchMarketHypes (mov_ave_market_hypes episodes):
 * served by the dedicated GET /api/analysis/market-regimes endpoint so
 * the shared CodeTrendChart's Regimes toggle and the MA-Spread panel's
 * regime chip shading can fetch spans on demand for ANY page's code
 * trend (the toggle is off by default and only fires this once per
 * code). Relies on the LRU TTL cache only (no version check; the stats
 * table is rebuilt offline by `python -m builds.market_regimes`).
 */
export function fetchMarketRegimeSpans(
  code: string,
  secType: MaSpreadSecType,
): Promise<MarketRegimeSpansResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (secType) params.set("sec_type", secType);
  const qs = params.toString();
  return fetchJson<MarketRegimeSpansResponse>(
    `/api/analysis/market-regimes${qs ? `?${qs}` : ""}`,
  );
}
