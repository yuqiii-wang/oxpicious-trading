import { fetchJson } from "./_cache";
import type {
  SectorNode,
  StrategyNode,
  RecurringCyclesSecType,
  RecurringCyclesCodesResponse,
  RecurringCyclesChartResponse,
  RecurringCyclesSpectrumResponse,
} from "@shared/types";

// ---------------------------------------------------------------------------
//  Analysis Commons — Recurring Cycles (Index only)
//  All endpoints require a `sec_type` query param ('index') and rely on the
//  LRU TTL cache only (no version check; the analysis schema is recomputed
//  offline by the Python build script).
// ---------------------------------------------------------------------------
export function fetchRecurringCyclesCodes(
  secType: RecurringCyclesSecType,
  exchange?: string | null,
): Promise<RecurringCyclesCodesResponse> {
  const params = new URLSearchParams();
  if (secType) params.set("sec_type", secType);
  if (exchange) params.set("exchange", exchange);
  const qs = params.toString();
  return fetchJson<RecurringCyclesCodesResponse>(
    `/api/analysis/recurring-cycles/codes${qs ? `?${qs}` : ""}`,
  );
}

/** Themes tree (L1 sector → L2 industry → items) for SecClassificationNav.
 *  Only includes codes that have rows in analysis.recurring_cycles. */
export function fetchRecurringCyclesThemes(
  secType: RecurringCyclesSecType,
  exchange?: string | null,
): Promise<SectorNode[]> {
  const params = new URLSearchParams();
  if (secType) params.set("sec_type", secType);
  if (exchange) params.set("exchange", exchange);
  const qs = params.toString();
  return fetchJson<SectorNode[]>(
    `/api/analysis/recurring-cycles/themes${qs ? `?${qs}` : ""}`,
  );
}

export function fetchRecurringCyclesStrategyThemes(
  secType: RecurringCyclesSecType,
  exchange?: string | null,
): Promise<StrategyNode[]> {
  const params = new URLSearchParams();
  if (secType) params.set("sec_type", secType);
  if (exchange) params.set("exchange", exchange);
  const qs = params.toString();
  return fetchJson<StrategyNode[]>(
    `/api/analysis/recurring-cycles/strategy-themes${qs ? `?${qs}` : ""}`,
  );
}

export function fetchRecurringCyclesChart(
  code: string,
  secType: RecurringCyclesSecType,
): Promise<RecurringCyclesChartResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (secType) params.set("sec_type", secType);
  const qs = params.toString();
  return fetchJson<RecurringCyclesChartResponse>(
    `/api/analysis/recurring-cycles/chart${qs ? `?${qs}` : ""}`,
  );
}

/** Fetch the per-day recurring periodicity factors (amplitude / count /
 *  strength spectra, day-aligned: element j = day j+2) for one
 *  (code, last_date) across all range_days windows. When `lastDate` is
 *  null/empty, the backend defaults to the latest available date for the
 *  code. Drives the per-date bar charts below the top index price plot. */
export function fetchRecurringCyclesSpectrum(
  code: string,
  secType: RecurringCyclesSecType,
  lastDate?: string | null,
): Promise<RecurringCyclesSpectrumResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (secType) params.set("sec_type", secType);
  if (lastDate) params.set("last_date", lastDate);
  const qs = params.toString();
  return fetchJson<RecurringCyclesSpectrumResponse>(
    `/api/analysis/recurring-cycles/spectrum${qs ? `?${qs}` : ""}`,
  );
}
