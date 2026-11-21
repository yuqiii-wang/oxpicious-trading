/**
 * Analysis Commons — Volatility (analysis.volatility; structured for
 * all security sec_types, run scope = index only for now — the 10
 * study indices). The codes + series endpoints behind the Commons page
 * /analysis/commons/volatility. Endpoints rely on the LRU TTL cache
 * only (no version check; the analysis schema is recomputed offline by
 * the Python pipeline).
 */
import { fetchJson } from "./_cache";
import type {
  VolatilityCodesResponse,
  VolatilitySeriesResponse,
} from "@shared/types";

/** The sec_types the endpoints accept (the pipeline covers the
 *  security sec_types; industry stays out — pool grain). */
export type VolatilitySecType = "etf" | "index" | "stock";

/** Codes list (code, name) of the vol rows' universe for the sec_type —
 *  the SecCodesPageShell fetcher (the shell filters client-side by the
 *  nav selection; the nav trees come from the shared classification
 *  endpoints the market-crowdedness page already exposes). */
export function fetchVolatilityCodes(
  secType: VolatilitySecType = "index",
): Promise<VolatilityCodesResponse> {
  const params = new URLSearchParams();
  params.set("sec_type", secType);
  return fetchJson<VolatilityCodesResponse>(
    `/api/analysis/volatility/codes?${params.toString()}`,
  );
}

/** One security's FULL daily volatility series (the UI slices via
 *  dataZoom): close-to-close HV across six windows + EWMA, the
 *  Parkinson/GK/RS/YZ 20-day estimators, the overnight/intraday split,
 *  ATR/NATR, band width, vol-of-vol, short/long ratio, trailing-year
 *  percentiles + z-scores, and the 5-min realized vol where intraday
 *  bars exist. All *vol* fields percent annualized. */
export function fetchVolatilitySeries(
  code: string,
  secType: VolatilitySecType = "index",
): Promise<VolatilitySeriesResponse> {
  const params = new URLSearchParams();
  params.set("code", code);
  params.set("sec_type", secType);
  return fetchJson<VolatilitySeriesResponse>(
    `/api/analysis/volatility/series?${params.toString()}`,
  );
}
