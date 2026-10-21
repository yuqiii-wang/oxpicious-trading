/**
 * API client for Pair Pct-Change Ratios — reads
 * analysis.pair_pct_change_ratios (stride-grid values of the trailing
 * mean daily pct-change DIFFERENCE, industry × industry pairs).
 */
import { fetchJson } from "./_cache";
import type {
  PairPctRatioGrain,
  PairPctRatioUniverseResponse,
  PairPctRatiosResponse,
} from "@shared/types";

/** Fixed grid stride the UI charts (the table's PK member "interval";
 *  the default 20 grid is the one the nightly run maintains). */
export const PAIR_PCT_RATIO_INTERVAL = 20;

// ---------------------------------------------------------------------------
//  GET universe — both sides of the pair selector, both INDUSTRIES
//  (the pair is industry × industry). Optional industryIds scopes both
//  sides to the chosen industries (the page passes its already-selected
//  industries).
// ---------------------------------------------------------------------------
export function fetchPairPctRatioUniverse(
  industryIds: ReadonlyArray<string> = [],
): Promise<PairPctRatioUniverseResponse> {
  const params = new URLSearchParams({ interval: String(PAIR_PCT_RATIO_INTERVAL) });
  if (industryIds.length > 0) {
    params.set("industry_ids", industryIds.join(","));
  }
  return fetchJson<PairPctRatioUniverseResponse>(
    `/api/analysis/pair-pct-ratios/universe?${params.toString()}`,
  );
}

// ---------------------------------------------------------------------------
//  GET rows — the four stride-grid ratio rolling means for ONE
//  industry × industry pair (grain is always "industry").
// ---------------------------------------------------------------------------
export function fetchPairPctRatios(
  code: string,
  benchmark: string,
  grain: PairPctRatioGrain = "industry",
  market = "000300",
): Promise<PairPctRatiosResponse> {
  const params = new URLSearchParams({ code, benchmark, grain, market });
  return fetchJson<PairPctRatiosResponse>(
    `/api/analysis/pair-pct-ratios?${params.toString()}&interval=${PAIR_PCT_RATIO_INTERVAL}`,
  );
}
