import { fetchJson } from "./_cache";
import type {
  ForecastIdentityResponse,
  ForecastKind,
  ForecastLaddersResponse,
  ForecastResponse,
  ForecastTriggerDatesResponse,
  MaSpreadSecType,
  OhlcvPopulation,
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
  population?: OhlcvPopulation | null,
  snapshot?: string | null,
): Promise<ForecastResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (secType) params.set("sec_type", secType);
  params.set("kind", kind);
  if (date) params.set("date", date);
  // Derived-population selector (mov_rsi): one of the four children
  // ('gap_selection' / 'gap_boost' / 'vol_selection' / 'vol_boost')
  // reads that child's rows; the default reads every population's rows
  // (each carrying its own ohlcv_type verdict).
  if (population) params.set("population", population);
  // Snapshot scope ("latest" | an exact stat_date): the FIRST load
  // reads only the snapshot the end-month filter starts on — the table
  // paints over ~1/7 of the rows; the full set loads on demand when
  // the filter moves off it (see fetchForecastLadders' sibling call).
  if (snapshot) params.set("snapshot", snapshot);
  return fetchJson<ForecastResponse>(
    `/api/analysis/mov-ave-spread/forecast?${params.toString()}`,
  );
}

/** The code's delay ladders for the SAME scope as the base forecast
 *  read — the second half of the split response (2026-09-30): the
 *  table renders from the base payload first, then merges these
 *  forecast_id-keyed ladders into its rows when they land. */
export function fetchForecastLadders(
  code: string,
  secType: MaSpreadSecType,
  kind: ForecastKind,
  date?: string | null,
  population?: OhlcvPopulation | null,
  snapshot?: string | null,
): Promise<ForecastLaddersResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (secType) params.set("sec_type", secType);
  params.set("kind", kind);
  if (date) params.set("date", date);
  if (population) params.set("population", population);
  if (snapshot) params.set("snapshot", snapshot);
  return fetchJson<ForecastLaddersResponse>(
    `/api/analysis/mov-ave-spread/forecast-ladders?${params.toString()}`,
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
  population: OhlcvPopulation | null = null,
): Promise<ForecastTriggerDatesResponse> {
  const delayQs = delay > 0 ? `&delay=${delay}` : "";
  const popQs = population ? `&population=${population}` : "";
  return fetchJson<ForecastTriggerDatesResponse>(
    `/api/analysis/mov-ave-spread/forecast-dates?forecast_id=${forecastId}${delayQs}${popQs}`,
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
