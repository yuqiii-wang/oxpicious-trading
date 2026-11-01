/**
 * Analysis Commons — Hot Stock Momentum (indices + composite industries).
 * The nav/codes/chart endpoints behind the Commons page
 * /analysis/commons/hot-stock-momentum. analysis.hot_stock_momentum is
 * built by analysis.hot_stock_momentum (IMPLEMENTATION PLACEHOLDER — the
 * chart returns empty rows until the pipeline lands), while the nav/codes
 * universe is the FULL classification (see the API-side
 * classification-universe module). Endpoints require a `sec_type` query
 * param ('index' | 'industry') and rely on the LRU TTL cache only (no
 * version check; the analysis schema is recomputed offline by the Python
 * build script).
 */
import { fetchJson } from "./_cache";
import type {
  SectorNode,
  StrategyNode,
  HotStockMomentumSecType,
  HotStockMomentumCodesResponse,
  HotStockMomentumChartResponse,
} from "@shared/types";

/** Themes tree (L1 sector → L2 industry → items) for SecClassificationNav
 *  — the FULL classification universe, not data-restricted. */
export function fetchHotStockMomentumThemes(
  secType: HotStockMomentumSecType,
  exchange?: string | null,
): Promise<SectorNode[]> {
  const params = new URLSearchParams();
  if (secType) params.set("sec_type", secType);
  if (exchange) params.set("exchange", exchange);
  const qs = params.toString();
  return fetchJson<SectorNode[]>(
    `/api/analysis/hot-stock-momentum/themes${qs ? `?${qs}` : ""}`,
  );
}

/** Parallel L1 strategy → L2 theme tree (RIGHT column). */
export function fetchHotStockMomentumStrategyThemes(
  secType: HotStockMomentumSecType,
  exchange?: string | null,
): Promise<StrategyNode[]> {
  const params = new URLSearchParams();
  if (secType) params.set("sec_type", secType);
  if (exchange) params.set("exchange", exchange);
  const qs = params.toString();
  return fetchJson<StrategyNode[]>(
    `/api/analysis/hot-stock-momentum/strategy-themes${qs ? `?${qs}` : ""}`,
  );
}

/** Codes list (code, name) of the full universe — the SecCodesPageShell
 *  fetcher (the shell filters client-side by the nav selection). */
export function fetchHotStockMomentumCodes(
  secType: HotStockMomentumSecType,
  exchange?: string | null,
): Promise<HotStockMomentumCodesResponse> {
  const params = new URLSearchParams();
  if (secType) params.set("sec_type", secType);
  if (exchange) params.set("exchange", exchange);
  const qs = params.toString();
  return fetchJson<HotStockMomentumCodesResponse>(
    `/api/analysis/hot-stock-momentum/codes${qs ? `?${qs}` : ""}`,
  );
}

/** One security's daily hot-stock momentum curve (empty rows until the
 *  pipeline is implemented). */
export function fetchHotStockMomentumChart(
  code: string,
  secType: HotStockMomentumSecType,
): Promise<HotStockMomentumChartResponse> {
  const params = new URLSearchParams();
  params.set("code", code);
  params.set("sec_type", secType);
  return fetchJson<HotStockMomentumChartResponse>(
    `/api/analysis/hot-stock-momentum/chart?${params.toString()}`,
  );
}
