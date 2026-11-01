/**
 * Analysis Commons — Market Crowdedness (indices + composite industries).
 * The nav/codes/chart endpoints behind the Commons page
 * /analysis/commons/market-crowdedness. analysis.market_crowdedness is
 * built by analysis.market_crowdedness (IMPLEMENTATION PLACEHOLDER — the
 * chart returns empty rows until the pipeline lands), while the nav/codes
 * universe is the FULL classification (see the service's _shared.ts).
 * Endpoints require a `sec_type` query param ('index' | 'industry') and
 * rely on the LRU TTL cache only (no version check; the analysis schema
 * is recomputed offline by the Python build script).
 */
import { fetchJson } from "./_cache";
import type {
  SectorNode,
  StrategyNode,
  MarketCrowdednessSecType,
  MarketCrowdednessCodesResponse,
  MarketCrowdednessChartResponse,
} from "@shared/types";

/** Themes tree (L1 sector → L2 industry → items) for SecClassificationNav
 *  — the FULL classification universe, not data-restricted. */
export function fetchMarketCrowdednessThemes(
  secType: MarketCrowdednessSecType,
  exchange?: string | null,
): Promise<SectorNode[]> {
  const params = new URLSearchParams();
  if (secType) params.set("sec_type", secType);
  if (exchange) params.set("exchange", exchange);
  const qs = params.toString();
  return fetchJson<SectorNode[]>(
    `/api/analysis/market-crowdedness/themes${qs ? `?${qs}` : ""}`,
  );
}

/** Parallel L1 strategy → L2 theme tree (RIGHT column). */
export function fetchMarketCrowdednessStrategyThemes(
  secType: MarketCrowdednessSecType,
  exchange?: string | null,
): Promise<StrategyNode[]> {
  const params = new URLSearchParams();
  if (secType) params.set("sec_type", secType);
  if (exchange) params.set("exchange", exchange);
  const qs = params.toString();
  return fetchJson<StrategyNode[]>(
    `/api/analysis/market-crowdedness/strategy-themes${qs ? `?${qs}` : ""}`,
  );
}

/** Codes list (code, name) of the full universe — the SecCodesPageShell
 *  fetcher (the shell filters client-side by the nav selection). */
export function fetchMarketCrowdednessCodes(
  secType: MarketCrowdednessSecType,
  exchange?: string | null,
): Promise<MarketCrowdednessCodesResponse> {
  const params = new URLSearchParams();
  if (secType) params.set("sec_type", secType);
  if (exchange) params.set("exchange", exchange);
  const qs = params.toString();
  return fetchJson<MarketCrowdednessCodesResponse>(
    `/api/analysis/market-crowdedness/codes${qs ? `?${qs}` : ""}`,
  );
}

/** One security's daily crowdedness curve (empty rows until the pipeline
 *  is implemented). */
export function fetchMarketCrowdednessChart(
  code: string,
  secType: MarketCrowdednessSecType,
): Promise<MarketCrowdednessChartResponse> {
  const params = new URLSearchParams();
  params.set("code", code);
  params.set("sec_type", secType);
  return fetchJson<MarketCrowdednessChartResponse>(
    `/api/analysis/market-crowdedness/chart?${params.toString()}`,
  );
}
