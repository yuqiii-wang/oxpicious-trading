import { fetchJson } from "./_cache";
import type { SecBoardMapResponse } from "@shared/types";

/**
 * Board tag data (stats.sec_board_map) for one security — the code-trend
 * header's board tag: a stock's single listing board, or an ETF/index's
 * composition-weighted board mix. Empty `boards` when no data exists
 * (endpoint reflects the nightly build; safe to ignore failures).
 */
export function fetchSecBoardMap(
  code: string,
  secType: "etf" | "index" | "stock",
): Promise<SecBoardMapResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (secType) params.set("sec_type", secType);
  const qs = params.toString();
  return fetchJson<SecBoardMapResponse>(`/api/sec-board${qs ? `?${qs}` : ""}`);
}
