/**
 * Analysis Signals — Oracle (analysis.oracle_signals; the hindsight
 * ORACLE tier, DECOUPLED from the rule tier — run scope = index only
 * for now, the 10 study indices): v3.1 grammar — local extremes of the
 * 5-bar window scanned daily, buy at a local minimum / sell at a
 * local maximum, one action PER SIDE per 5-day window (a buy and a
 * sell may sit within the same 5 rows). The codes +
 * series endpoints behind the Signals → Oracle page
 * (/analysis/signals/oracle). Endpoints rely on the LRU TTL cache
 * only (no version check; the analysis schema is recomputed offline
 * by the Python pipeline).
 */
import { fetchJson } from "./_cache";
import type {
  OracleCodesResponse,
  OracleSeriesResponse,
} from "@shared/types";

/** The sec_types the endpoints accept (the pipeline covers the
 *  security sec_types; run scope = index only for now). */
export type OracleSecType = "etf" | "index" | "stock";

/** Codes list (code, name) of the oracle rows' universe for the
 *  sec_type — the SecCodesPageShell fetcher (the shell filters
 *  client-side by the nav selection; the nav trees come from the
 *  shared classification endpoints the market-crowdedness page already
 *  exposes). */
export function fetchOracleCodes(
  secType: OracleSecType = "index",
): Promise<OracleCodesResponse> {
  const params = new URLSearchParams();
  params.set("sec_type", secType);
  return fetchJson<OracleCodesResponse>(
    `/api/analysis/oracle/codes?${params.toString()}`,
  );
}

/** One security's FULL hindsight action history (the UI slices via
 *  dataZoom): one row per placed 5-bar-window local-extreme action —
 *  buy at a local minimum, sell at a local maximum, one action per
 *  5-day window PER SIDE (same-side actions sit >= 5 rows apart),
 *  each carrying the next opposite action as its swing
 *  target — plus the full daily close series for the price overlay. */
export function fetchOracleSeries(
  code: string,
  secType: OracleSecType = "index",
): Promise<OracleSeriesResponse> {
  const params = new URLSearchParams();
  params.set("code", code);
  params.set("sec_type", secType);
  return fetchJson<OracleSeriesResponse>(
    `/api/analysis/oracle/series?${params.toString()}`,
  );
}
