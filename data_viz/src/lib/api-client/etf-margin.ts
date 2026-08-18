import { fetchJson } from "./_cache";
import type {
  SectorNode,
  StrategyNode,
  EtfMarginCombinedResponse,
} from "@shared/types";

export function fetchThemes(exchange?: string | null): Promise<SectorNode[]> {
  const params = new URLSearchParams();
  if (exchange) params.set("exchange", exchange);
  const qs = params.toString();
  return fetchJson<SectorNode[]>(`/api/etf-margin/themes${qs ? `?${qs}` : ""}`);
}

export function fetchEtfStrategyThemes(exchange?: string | null): Promise<StrategyNode[]> {
  const params = new URLSearchParams();
  if (exchange) params.set("exchange", exchange);
  const qs = params.toString();
  return fetchJson<StrategyNode[]>(`/api/etf-margin/strategy-themes${qs ? `?${qs}` : ""}`);
}

export function fetchEtfMarginCombined(
  sector?: string | null,
  industry?: string | null,
  startDate?: string | null,
  endDate?: string | null,
  limitPerTheme?: number,
  page?: number,
  pageSize?: number,
  code?: string | null,
  exchange?: string | null,
  strategy?: string | null,
  theme?: string | null,
): Promise<EtfMarginCombinedResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (sector) params.set("sector", sector);
  if (industry) params.set("industry", industry);
  if (startDate) params.set("start_date", startDate);
  if (endDate) params.set("end_date", endDate);
  if (limitPerTheme) params.set("limit_per_theme", String(limitPerTheme));
  if (page) params.set("page", String(page));
  if (pageSize) params.set("page_size", String(pageSize));
  if (exchange) params.set("exchange", exchange);
  if (strategy) params.set("strategy", strategy);
  if (theme) params.set("theme", theme);
  const qs = params.toString();
  return fetchJson<EtfMarginCombinedResponse>(`/api/etf-margin/combined${qs ? `?${qs}` : ""}`);
}
