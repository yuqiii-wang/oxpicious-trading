import { fetchJson } from "./_cache";
import type {
  ForecastKind,
  ForecastResponse,
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
 *  mov_rsi / mov_std / mov_gap / px_vol / margin_ratio buckets joined 1:1
 *  with their analysis_forecasts.forecast_results columns. ALL stat_months
 *  of the code are returned (optionally narrowed to stat_months >= `month`).
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
