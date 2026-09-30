import { fetchJson } from "./_cache";
import type {
  OptionsCombinedResponse,
  OptionsUnderlying,
  OptionsWallsResponse,
  OptionsOiStatsResponse,
  OptionsDatesResponse,
  OptionsSnapshotResponse,
  OptionsOiDailyResponse,
  OptionsOiCellsResponse,
  OptionsExpiryMetaResponse,
  OptionsMoneynessSeriesResponse,
  EtfOhlcvResponse,
  SkewnessSeriesResponse,
  IvSkewResponse,
  VolIndexResponse,
  SkewType,
} from "@shared/types";

export type OptionsTargetType = "ETF" | "INDEX";

/** Venue selector for the options dashboards. SZSE/SSE are both ETF-target
 *  venues (separated by the options tables' exchange column); CFFEX is the
 *  index-target venue. */
export type OptionsVenue = "SZSE" | "SSE" | "CFFEX";

export function venueToTargetType(v: OptionsVenue): OptionsTargetType {
  return v === "CFFEX" ? "INDEX" : "ETF";
}

export function fetchUnderlyings(
  targetType?: OptionsTargetType,
  exchange?: OptionsVenue,
): Promise<OptionsUnderlying[]> {
  const params = new URLSearchParams();
  if (targetType) params.set("target_type", targetType);
  if (exchange) params.set("exchange", exchange);
  const qs = params.toString();
  return fetchJson<OptionsUnderlying[]>(`/api/szse-options/underlyings${qs ? `?${qs}` : ""}`);
}

export function fetchOptionsCombined(
  underlying: string,
  startDate?: string | null,
  endDate?: string | null,
  targetType?: OptionsTargetType,
  exchange?: OptionsVenue,
): Promise<OptionsCombinedResponse> {
  const params = new URLSearchParams();
  if (underlying) params.set("underlying", underlying);
  if (startDate) params.set("start_date", startDate);
  if (endDate) params.set("end_date", endDate);
  if (targetType) params.set("target_type", targetType);
  if (exchange) params.set("exchange", exchange);
  const qs = params.toString();
  return fetchJson<OptionsCombinedResponse>(`/api/szse-options/combined${qs ? `?${qs}` : ""}`);
}

// ----------------------------------------------------------------------------
//  Per-plot fetchers — one SQL per plot (see api/services/options.service.ts
//  "Per-plot queries"). Each Options Analysis panel loads its own aggregate
//  instead of the full-history /combined blob.
// ----------------------------------------------------------------------------

/** Shared query-string builder for the per-plot endpoints. */
function plotQs(
  underlying: string,
  targetType?: OptionsTargetType,
  exchange?: OptionsVenue,
  extra?: Record<string, string>,
): string {
  const params = new URLSearchParams();
  if (underlying) params.set("underlying", underlying);
  if (targetType) params.set("target_type", targetType);
  if (exchange) params.set("exchange", exchange);
  if (extra) {
    for (const [k, v] of Object.entries(extra)) params.set(k, v);
  }
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

/** Distinct trading dates + counters (date picker, page caption). */
export function fetchOptionsDates(
  underlying: string,
  targetType?: OptionsTargetType,
  exchange?: OptionsVenue,
): Promise<OptionsDatesResponse> {
  return fetchJson<OptionsDatesResponse>(
    `/api/szse-options/dates${plotQs(underlying, targetType, exchange)}`,
  );
}

/** One date's quote rows + history-wide strike/expiry-month meta. */
export function fetchOptionsSnapshot(
  underlying: string,
  date: string,
  targetType?: OptionsTargetType,
  exchange?: OptionsVenue,
): Promise<OptionsSnapshotResponse> {
  return fetchJson<OptionsSnapshotResponse>(
    `/api/szse-options/snapshot${plotQs(underlying, targetType, exchange, { start_date: date })}`,
  );
}

/** Per (date × expiry) call/put OI + per-date spot (trend charts). */
export function fetchOptionsOiDaily(
  underlying: string,
  targetType?: OptionsTargetType,
  exchange?: OptionsVenue,
): Promise<OptionsOiDailyResponse> {
  return fetchJson<OptionsOiDailyResponse>(
    `/api/szse-options/oi-daily${plotQs(underlying, targetType, exchange)}`,
  );
}

/** Per (date × expiry × strike) OI cells for the selected expiry cohorts. */
export function fetchOptionsOiCells(
  underlying: string,
  expiries: string[],
  targetType?: OptionsTargetType,
  exchange?: OptionsVenue,
): Promise<OptionsOiCellsResponse> {
  return fetchJson<OptionsOiCellsResponse>(
    `/api/szse-options/oi-cells${plotQs(underlying, targetType, exchange, {
      expiries: expiries.join(","),
    })}`,
  );
}

/** Per-contract final-OI expiry markers + cohort totals. */
export function fetchOptionsExpiryMeta(
  underlying: string,
  targetType?: OptionsTargetType,
  exchange?: OptionsVenue,
): Promise<OptionsExpiryMetaResponse> {
  return fetchJson<OptionsExpiryMetaResponse>(
    `/api/szse-options/expiry-meta${plotQs(underlying, targetType, exchange)}`,
  );
}

/** Server-computed oi_moneyness skew series (spot + per-group aggregates). */
export function fetchOptionsMoneynessSeries(
  underlying: string,
  targetType?: OptionsTargetType,
  exchange?: OptionsVenue,
): Promise<OptionsMoneynessSeriesResponse> {
  return fetchJson<OptionsMoneynessSeriesResponse>(
    `/api/szse-options/oi-moneyness-series${plotQs(underlying, targetType, exchange)}`,
  );
}

/** Zone walls from analysis.options_walls (wall_type='zone' — the backend
 *  removed the legacy 80pct / large_num wall types). */
export function fetchOptionsWalls(
  underlying: string,
  startDate?: string | null,
  endDate?: string | null,
): Promise<OptionsWallsResponse> {
  const params = new URLSearchParams();
  if (underlying) params.set("underlying", underlying);
  if (startDate) params.set("start_date", startDate);
  if (endDate) params.set("end_date", endDate);
  const qs = params.toString();
  return fetchJson<OptionsWallsResponse>(`/api/szse-options/walls${qs ? `?${qs}` : ""}`);
}

/** Per-expiry OI stats from the dedicated per-real-expiry table
 *  analysis.options_oi_stats (OI level, Δ5d/Δ20d, trailing-20d max —
 *  month-aggregated to match the frontend's expiry-month grouping). */
export function fetchOptionsOiStats(
  underlying: string,
  startDate?: string | null,
  endDate?: string | null,
): Promise<OptionsOiStatsResponse> {
  const params = new URLSearchParams();
  if (underlying) params.set("underlying", underlying);
  if (startDate) params.set("start_date", startDate);
  if (endDate) params.set("end_date", endDate);
  const qs = params.toString();
  return fetchJson<OptionsOiStatsResponse>(
    `/api/szse-options/oi-stats${qs ? `?${qs}` : ""}`,
  );
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

/** Skew data source in options_skewness_stats: 'oi_moneyness' (OI-weighted
 *  mean moneyness, positioning), 'iv_smile' (OI-wtd 3rd moment of IV),
 *  or 'greek_<name>' (OI-wtd mean greek / ATM greek, per greek). */
export type { SkewType } from "@shared/types";

export function fetchOptionsSkewnessSeries(
  underlying: string,
  startDate?: string | null,
  endDate?: string | null,
  skewType: SkewType = "oi_moneyness",
): Promise<SkewnessSeriesResponse> {
  const params = new URLSearchParams();
  if (underlying) params.set("underlying", underlying);
  if (startDate) params.set("start_date", startDate);
  if (endDate) params.set("end_date", endDate);
  params.set("skew_type", skewType);
  const qs = params.toString();
  return fetchJson<SkewnessSeriesResponse>(
    `/api/szse-options/skewness-series${qs ? `?${qs}` : ""}`,
  );
}

export function fetchOptionsIvSkew(
  underlying: string,
  startDate?: string | null,
  endDate?: string | null,
): Promise<IvSkewResponse> {
  const params = new URLSearchParams();
  if (underlying) params.set("underlying", underlying);
  if (startDate) params.set("start_date", startDate);
  if (endDate) params.set("end_date", endDate);
  const qs = params.toString();
  return fetchJson<IvSkewResponse>(
    `/api/szse-options/iv-skew${qs ? `?${qs}` : ""}`,
  );
}

/** Daily 30d model-free vol index (VIX-style) — omit `underlying` to
 *  fetch every underlying's series (cross-asset comparison). */
export function fetchOptionsVolIndex(
  underlying?: string | null,
  startDate?: string | null,
  endDate?: string | null,
): Promise<VolIndexResponse> {
  const params = new URLSearchParams();
  if (underlying) params.set("underlying", underlying);
  if (startDate) params.set("start_date", startDate);
  if (endDate) params.set("end_date", endDate);
  const qs = params.toString();
  return fetchJson<VolIndexResponse>(
    `/api/szse-options/vol-index${qs ? `?${qs}` : ""}`,
  );
}
