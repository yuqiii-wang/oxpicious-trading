import { fetchJson } from "./_cache";

export interface FuturesExtRow {
  date: string;
  code: string;
  gap_price_vs_underlying: number | null;
  corr_price_vs_underlying: number | null;
}

export interface FuturesExtResponse {
  product: string;
  gapByCodeDate: Record<string, Record<string, number | null>>;
  corrByCodeDate: Record<string, Record<string, number | null>>;
  rows: FuturesExtRow[];
}

export function fetchFuturesExt(
  product: string,
): Promise<FuturesExtResponse> {
  return fetchJson<FuturesExtResponse>(
    `/api/analysis/futures/ext?product=${encodeURIComponent(product)}`,
  );
}
