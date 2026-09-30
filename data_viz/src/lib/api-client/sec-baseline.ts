/**
 * Security baseline bundle — the ONE shared endpoint behind the shared
 * baseline-plot component (useSecBaselineBundle → IndexPanel /
 * EtfMarginPanel / StockPanel). Replaces the former three-way branch over
 * the page-scoped combined browse endpoints (/api/index/combined,
 * /api/etf-margin/combined, /api/stock/combined with page_size=1).
 */
import { fetchJson } from "./_cache";
import type { SecBaselineResponse } from "@shared/types";

/** Fetch one security's baseline bundle (rows + classification meta).
 *  `bundle` is null when the code has no rows in its sec_type. */
export function fetchSecBaseline(
  secType: "index" | "etf" | "stock",
  code: string,
): Promise<SecBaselineResponse> {
  const params = new URLSearchParams();
  params.set("sec_type", secType);
  params.set("code", code);
  return fetchJson<SecBaselineResponse>(`/api/sec-baseline?${params.toString()}`);
}
