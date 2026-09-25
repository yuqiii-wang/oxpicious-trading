import { fetchJson } from "./_cache";
import type {
  ForecastIdentityResponse,
  ForecastKind,
  ForecastResponse,
  ForecastTriggerDatesResponse,
  MaSpreadSecType,
} from "@shared/types";

// ---------------------------------------------------------------------------
//  Analysis Commons — Forecast buckets (analysis_forecasts schema)
//  Migrated off the MA-Spread panel (served from the same backend endpoint,
//  /api/analysis/mov-ave-spread/forecast, which the shared
//  getForecastTable service backs); the Recent Movements page's 2nd plot
//  uses it now. `sec_type` accepts 'etf' | 'index' | 'stock'.
// ---------------------------------------------------------------------------

/** One code's forecast bucket table (analysis_forecasts) for `kind`:
 *  mov_rsi / mov_std / mov_pairs / mov_pairs_ema /
 *  margin_ratio buckets joined 1:1 with their
 *  analysis_forecasts.forecast_results columns. ALL stat_dates of the
 *  code are returned (annual grid — completed year-ends plus the
 *  running year; optionally narrowed to stat_dates >= `date`). The
 *  response lists all available stat_dates for the snapshot
 *  tick-filter. */
export function fetchAnalysisForecast(
  code: string,
  secType: MaSpreadSecType,
  kind: ForecastKind,
  date?: string | null,
): Promise<ForecastResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (secType) params.set("sec_type", secType);
  params.set("kind", kind);
  if (date) params.set("date", date);
  return fetchJson<ForecastResponse>(
    `/api/analysis/mov-ave-spread/forecast?${params.toString()}`,
  );
}

/** One forecast bucket's per-period trigger dates (the calendar dates
 *  behind each horizon's occurrence_count — the exact days the clicked
 *  row's stats were computed over). Fetched on forecast-table row click
 *  so the trend chart can mark the dates; cached per forecast_id.
 *  `delay` selects the anchor-delay rung (0..5, default 0) — the
 *  table's inline expansion rows pass their rung's delay so the chart
 *  shades that rung's own trigger days. */
export function fetchForecastTriggerDates(
  forecastId: number,
  delay = 0,
): Promise<ForecastTriggerDatesResponse> {
  const delayQs = delay > 0 ? `&delay=${delay}` : "";
  return fetchJson<ForecastTriggerDatesResponse>(
    `/api/analysis/mov-ave-spread/forecast-dates?forecast_id=${forecastId}${delayQs}`,
  );
}

/** Search by forecast_id: resolve the id against the shared-PK registry
 *  (analysis_forecasts.forecast_identities) — which security / snapshot
 *  date / bucket family the forecast belongs to — so the UI can jump straight
 *  to the bucket. Throws (404) when the id is not registered. */
export function fetchForecastIdentity(
  forecastId: number,
): Promise<ForecastIdentityResponse> {
  return fetchJson<ForecastIdentityResponse>(
    `/api/analysis/mov-ave-spread/forecast-identity?forecast_id=${forecastId}`,
  );
}
