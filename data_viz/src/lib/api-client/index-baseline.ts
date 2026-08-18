import { fetchJson } from "./_cache";
import type {
  SectorNode,
  StrategyNode,
  IndexInfo,
  IndexCombinedResponse,
  IndexIntraday5minResponse,
} from "@shared/types";

export function fetchIndexList(): Promise<IndexInfo[]> {
  return fetchJson<IndexInfo[]>(`/api/index-baseline/list`);
}

export function fetchIndexThemes(exchange?: string | null): Promise<SectorNode[]> {
  const params = new URLSearchParams();
  if (exchange) params.set("exchange", exchange);
  const qs = params.toString();
  return fetchJson<SectorNode[]>(`/api/index-baseline/themes${qs ? `?${qs}` : ""}`);
}

/** Fetch the parallel strategy → theme tree (RIGHT column of the two-column
 *  selector).  Returns strategy-primary indices only. */
export function fetchIndexStrategyThemes(exchange?: string | null): Promise<StrategyNode[]> {
  const params = new URLSearchParams();
  if (exchange) params.set("exchange", exchange);
  const qs = params.toString();
  return fetchJson<StrategyNode[]>(`/api/index-baseline/strategy-themes${qs ? `?${qs}` : ""}`);
}

export function fetchIndicesCombined(
  sector?: string | null,
  industry?: string | null,
  startDate?: string | null,
  endDate?: string | null,
  page?: number,
  pageSize?: number,
  code?: string | null,
  exchange?: string | null,
  /** Strategy filter (RIGHT column). When set (and sector is not), filters by
   *  sector_id on rows where is_industry_not_strategy=FALSE (strategy-primary). */
  strategy?: string | null,
  /** Theme filter (RIGHT column). When set, filters by industry_id/industry_slug
   *  on strategy-primary rows. */
  theme?: string | null,
): Promise<IndexCombinedResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (sector) params.set("sector", sector);
  if (industry) params.set("industry", industry);
  if (strategy) params.set("strategy", strategy);
  if (theme) params.set("theme", theme);
  if (startDate) params.set("start_date", startDate);
  if (endDate) params.set("end_date", endDate);
  if (page) params.set("page", String(page));
  if (pageSize) params.set("page_size", String(pageSize));
  if (exchange) params.set("exchange", exchange);
  const qs = params.toString();
  return fetchJson<IndexCombinedResponse>(`/api/index-baseline/combined${qs ? `?${qs}` : ""}`);
}

export function fetchIndexIntraday5min(
  code: string,
  date: string,
): Promise<IndexIntraday5minResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (date) params.set("date", date);
  const qs = params.toString();
  return fetchJson<IndexIntraday5minResponse>(`/api/index-baseline/intraday-5min${qs ? `?${qs}` : ""}`);
}
