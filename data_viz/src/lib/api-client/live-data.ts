import { fetchJson } from "./_cache";
import type {
  SectorNode,
  StrategyNode,
  LiveDataSecType,
  LiveDataDatesResponse,
  LiveDataCombinedResponse,
} from "../../../shared/types";

// ---------------------------------------------------------------------------
//  Live Data — intraday 5-min bars (index + stock)
//  TTL-only cache (no version check; the intraday tables are populated by
//  the streaming price-download scripts and the latest date moves
//  continuously throughout the trading day).
// ---------------------------------------------------------------------------
/** Distinct trading days with at least one intraday bar, descending. */
export function fetchLiveDataDates(
  secType: LiveDataSecType,
): Promise<LiveDataDatesResponse> {
  const params = new URLSearchParams();
  params.set("type", secType);
  return fetchJson<LiveDataDatesResponse>(
    `/api/live-data/dates?${params.toString()}`,
  );
}

/** L1 sector → L2 industry tree restricted to codes with intraday data. */
export function fetchLiveDataThemes(
  secType: LiveDataSecType,
  exchange?: string | null,
): Promise<SectorNode[]> {
  const params = new URLSearchParams();
  params.set("type", secType);
  if (exchange) params.set("exchange", exchange);
  return fetchJson<SectorNode[]>(
    `/api/live-data/themes?${params.toString()}`,
  );
}

export function fetchLiveDataStrategyThemes(
  secType: LiveDataSecType,
  exchange?: string | null,
): Promise<StrategyNode[]> {
  const params = new URLSearchParams();
  params.set("type", secType);
  if (exchange) params.set("exchange", exchange);
  return fetchJson<StrategyNode[]>(
    `/api/live-data/strategy-themes?${params.toString()}`,
  );
}

/** Paginated list of codes with their intraday bars for one date. */
export function fetchLiveDataCombined(
  secType: LiveDataSecType,
  date?: string | null,
  sector?: string | null,
  industry?: string | null,
  exchange?: string | null,
  page?: number,
  pageSize?: number,
  code?: string | null,
  strategy?: string | null,
  theme?: string | null,
): Promise<LiveDataCombinedResponse> {
  const params = new URLSearchParams();
  params.set("type", secType);
  if (date) params.set("date", date);
  if (sector) params.set("sector", sector);
  if (industry) params.set("industry", industry);
  if (exchange) params.set("exchange", exchange);
  if (page) params.set("page", String(page));
  if (pageSize) params.set("page_size", String(pageSize));
  if (code) params.set("code", code);
  if (strategy) params.set("strategy", strategy);
  if (theme) params.set("theme", theme);
  return fetchJson<LiveDataCombinedResponse>(
    `/api/live-data/combined?${params.toString()}`,
  );
}
