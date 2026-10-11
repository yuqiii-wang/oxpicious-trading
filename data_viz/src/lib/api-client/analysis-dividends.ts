/**
 * Analysis Commons — Dividend Deliveries (Dividends page's per-security
 * audit table): the rolling 5-year (1-year stride) delivery audit rows
 * (analysis.dividend_deliveries — per-event yield / eps /
 * ±20d-last-year stability aggregated per annual window). LRU TTL
 * cache only; the analysis schema is recomputed offline by the Python
 * build script (analysis.pe_and_dividends --metrics deliveries).
 */
import { fetchJson } from "./_cache";
import type {
  PeSecType,
  DividendDeliveriesResponse,
} from "@shared/types";

export function fetchDividendDeliveries(
  code: string,
  secType: PeSecType,
): Promise<DividendDeliveriesResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (secType) params.set("sec_type", secType);
  const qs = params.toString();
  return fetchJson<DividendDeliveriesResponse>(
    `/api/analysis/dividends/deliveries${qs ? `?${qs}` : ""}`,
  );
}
