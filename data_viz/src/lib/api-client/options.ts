import { fetchJson } from "./_cache";
import type {
  OptionsCombinedResponse,
  OptionsUnderlying,
  EtfOhlcvResponse,
  ExpiryGapsResponse,
  SkewnessCorrResponse,
} from "@shared/types";

export type OptionsTargetType = "ETF" | "INDEX";

export function fetchUnderlyings(targetType?: OptionsTargetType): Promise<OptionsUnderlying[]> {
  const params = new URLSearchParams();
  if (targetType) params.set("target_type", targetType);
  const qs = params.toString();
  return fetchJson<OptionsUnderlying[]>(`/api/szse-options/underlyings${qs ? `?${qs}` : ""}`);
}

export function fetchOptionsCombined(
  underlying: string,
  startDate?: string | null,
  endDate?: string | null,
  targetType?: OptionsTargetType,
): Promise<OptionsCombinedResponse> {
  const params = new URLSearchParams();
  if (underlying) params.set("underlying", underlying);
  if (startDate) params.set("start_date", startDate);
  if (endDate) params.set("end_date", endDate);
  if (targetType) params.set("target_type", targetType);
  const qs = params.toString();
  return fetchJson<OptionsCombinedResponse>(`/api/szse-options/combined${qs ? `?${qs}` : ""}`);
}

export function fetchEtfOhlcv(
  code: string,
  startDate?: string | null,
  endDate?: string | null,
  targetType?: OptionsTargetType,
): Promise<EtfOhlcvResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (startDate) params.set("start_date", startDate);
  if (endDate) params.set("end_date", endDate);
  if (targetType) params.set("target_type", targetType);
  const qs = params.toString();
  return fetchJson<EtfOhlcvResponse>(`/api/szse-options/etf-ohlcv${qs ? `?${qs}` : ""}`);
}

export function fetchOptionsExpiryGaps(
  underlying: string,
  startDate?: string | null,
  endDate?: string | null,
): Promise<ExpiryGapsResponse> {
  const params = new URLSearchParams();
  if (underlying) params.set("underlying", underlying);
  if (startDate) params.set("start_date", startDate);
  if (endDate) params.set("end_date", endDate);
  const qs = params.toString();
  return fetchJson<ExpiryGapsResponse>(
    `/api/szse-options/stats-before-expiry${qs ? `?${qs}` : ""}`,
  );
}

export function fetchOptionsSkewnessCorr(
  underlying: string,
  startDate?: string | null,
  endDate?: string | null,
): Promise<SkewnessCorrResponse> {
  const params = new URLSearchParams();
  if (underlying) params.set("underlying", underlying);
  if (startDate) params.set("start_date", startDate);
  if (endDate) params.set("end_date", endDate);
  const qs = params.toString();
  return fetchJson<SkewnessCorrResponse>(
    `/api/szse-options/skewness-corr${qs ? `?${qs}` : ""}`,
  );
}
