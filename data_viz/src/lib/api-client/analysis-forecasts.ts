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
 *  mov_rsi / mov_std / mov_pairs / mov_pairs_ema / px_vol /
 *  margin_ratio buckets joined 1:1 with their
 *  analysis_forecasts.forecast_results columns. ALL stat_months of the
 *  code are returned (optionally narrowed to stat_months >= `month`).
 *  The response lists all available months for the month tick-filter. */
export function fetchAnalysisForecast(
  code: string,
  secType: MaSpreadSecType,
  kind: ForecastKind,
  month?: string | null,
): Promise<ForecastResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (secType) params.set("sec_type", secType);
  params.set("kind", kind);
  if (month) params.set("month", month);
  return fetchJson<ForecastResponse>(
    `/api/analysis/mov-ave-spread/forecast?${params.toString()}`,
  );
}

/** One forecast bucket's per-period trigger dates (the calendar dates
 *  behind each horizon's occurrence_count — the exact days the clicked
 *  row's stats were computed over). Fetched on forecast-table row click
 *  so the trend chart can mark the dates; cached per forecast_id. */
export function fetchForecastTriggerDates(
  forecastId: number,
): Promise<ForecastTriggerDatesResponse> {
  return fetchJson<ForecastTriggerDatesResponse>(
    `/api/analysis/mov-ave-spread/forecast-dates?forecast_id=${forecastId}`,
  );
}

/** Search by forecast_id: resolve the id against the shared-PK registry
 *  (analysis_forecasts.forecast_identities) — which security / month /
 *  bucket family the forecast belongs to — so the UI can jump straight
 *  to the bucket. Throws (404) when the id is not registered. */
export function fetchForecastIdentity(
  forecastId: number,
): Promise<ForecastIdentityResponse> {
  return fetchJson<ForecastIdentityResponse>(
    `/api/analysis/mov-ave-spread/forecast-identity?forecast_id=${forecastId}`,
  );
}
