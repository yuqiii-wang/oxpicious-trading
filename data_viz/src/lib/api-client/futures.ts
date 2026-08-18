import { fetchJson } from "./_cache";
import type {
  FuturesProduct,
  FuturesCombinedResponse,
} from "@shared/types";

export function fetchFuturesProducts(): Promise<{ products: FuturesProduct[] }> {
  return fetchJson<{ products: FuturesProduct[] }>(`/api/futures/products`);
}

export function fetchFuturesCombined(
  product: string,
): Promise<FuturesCombinedResponse> {
  return fetchJson<FuturesCombinedResponse>(
    `/api/futures/combined?product=${encodeURIComponent(product)}`,
  );
}