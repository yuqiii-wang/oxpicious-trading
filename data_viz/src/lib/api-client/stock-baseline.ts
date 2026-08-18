import { fetchJson } from "./_cache";
import type {
  SectorNode,
  StrategyNode,
  StockBaselineResponse,
  StockCombinedResponse,
} from "@shared/types";

export function fetchStockBaseline(
  code: string,
  startDate?: string | null,
  endDate?: string | null,
): Promise<StockBaselineResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (startDate) params.set("start_date", startDate);
  if (endDate) params.set("end_date", endDate);
  const qs = params.toString();
  return fetchJson<StockBaselineResponse>(`/api/stock-baseline${qs ? `?${qs}` : ""}`);
}

export function fetchStockThemes(exchange?: string | null): Promise<SectorNode[]> {
  const params = new URLSearchParams();
  if (exchange) params.set("exchange", exchange);
  const qs = params.toString();
  return fetchJson<SectorNode[]>(`/api/stock-baseline/themes${qs ? `?${qs}` : ""}`);
}

export function fetchStockStrategyThemes(exchange?: string | null): Promise<StrategyNode[]> {
  const params = new URLSearchParams();
  if (exchange) params.set("exchange", exchange);
  const qs = params.toString();
  return fetchJson<StrategyNode[]>(`/api/stock-baseline/strategy-themes${qs ? `?${qs}` : ""}`);
}

export function fetchStocksCombined(
  sector?: string | null,
  industry?: string | null,
  startDate?: string | null,
  endDate?: string | null,
  page?: number,
  pageSize?: number,
  code?: string | null,
  exchange?: string | null,
  strategy?: string | null,
  theme?: string | null,
): Promise<StockCombinedResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (sector) params.set("sector", sector);
  if (industry) params.set("industry", industry);
  if (startDate) params.set("start_date", startDate);
  if (endDate) params.set("end_date", endDate);
  if (page) params.set("page", String(page));
  if (pageSize) params.set("page_size", String(pageSize));
  if (exchange) params.set("exchange", exchange);
  if (strategy) params.set("strategy", strategy);
  if (theme) params.set("theme", theme);
  const qs = params.toString();
  return fetchJson<StockCombinedResponse>(`/api/stock-baseline/combined${qs ? `?${qs}` : ""}`);
}
