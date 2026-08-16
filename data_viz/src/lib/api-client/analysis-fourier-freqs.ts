import { fetchJson } from "./_cache";
import type {
  SectorNode,
  StrategyNode,
  FourierFreqsSecType,
  FourierFreqsCodesResponse,
  FourierFreqsChartResponse,
  FourierFreqsSpectrumResponse,
} from "../../../shared/types";

// ---------------------------------------------------------------------------
//  Analysis Commons — Fourier Frequencies (Index only)
//  All endpoints require a `sec_type` query param ('index') and rely on the
//  LRU TTL cache only (no version check; the analysis schema is recomputed
//  offline by the Python build script).
// ---------------------------------------------------------------------------
export function fetchFourierFreqsCodes(
  secType: FourierFreqsSecType,
  exchange?: string | null,
): Promise<FourierFreqsCodesResponse> {
  const params = new URLSearchParams();
  if (secType) params.set("sec_type", secType);
  if (exchange) params.set("exchange", exchange);
  const qs = params.toString();
  return fetchJson<FourierFreqsCodesResponse>(
    `/api/analysis/fourier-freqs/codes${qs ? `?${qs}` : ""}`,
  );
}

/** Themes tree (L1 sector → L2 industry → items) for SecClassificationNav.
 *  Only includes codes that have rows in analysis.fourier_freqs. */
export function fetchFourierFreqsThemes(
  secType: FourierFreqsSecType,
  exchange?: string | null,
): Promise<SectorNode[]> {
  const params = new URLSearchParams();
  if (secType) params.set("sec_type", secType);
  if (exchange) params.set("exchange", exchange);
  const qs = params.toString();
  return fetchJson<SectorNode[]>(
    `/api/analysis/fourier-freqs/themes${qs ? `?${qs}` : ""}`,
  );
}

export function fetchFourierFreqsStrategyThemes(
  secType: FourierFreqsSecType,
  exchange?: string | null,
): Promise<StrategyNode[]> {
  const params = new URLSearchParams();
  if (secType) params.set("sec_type", secType);
  if (exchange) params.set("exchange", exchange);
  const qs = params.toString();
  return fetchJson<StrategyNode[]>(
    `/api/analysis/fourier-freqs/strategy-themes${qs ? `?${qs}` : ""}`,
  );
}

export function fetchFourierFreqsChart(
  code: string,
  secType: FourierFreqsSecType,
): Promise<FourierFreqsChartResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (secType) params.set("sec_type", secType);
  const qs = params.toString();
  return fetchJson<FourierFreqsChartResponse>(
    `/api/analysis/fourier-freqs/chart${qs ? `?${qs}` : ""}`,
  );
}

/** Fetch the FULL one-sided amplitude spectrum for one (code, last_date)
 *  across all 5 range_days windows. When `lastDate` is null/empty, the
 *  backend defaults to the latest available date for the code. Drives the
 *  per-date spectrum bar charts below the top index price plot. */
export function fetchFourierFreqsSpectrum(
  code: string,
  secType: FourierFreqsSecType,
  lastDate?: string | null,
): Promise<FourierFreqsSpectrumResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (secType) params.set("sec_type", secType);
  if (lastDate) params.set("last_date", lastDate);
  const qs = params.toString();
  return fetchJson<FourierFreqsSpectrumResponse>(
    `/api/analysis/fourier-freqs/spectrum${qs ? `?${qs}` : ""}`,
  );
}
