import { fetchJson } from "./_cache";
import type {
  SecCompositionResponse,
  LinkedEtfsResponse,
  SimilarIndicesResponse,
} from "../../../shared/types";

export function fetchSecComposition(code: string): Promise<SecCompositionResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  const qs = params.toString();
  return fetchJson<SecCompositionResponse>(`/api/sec-composition${qs ? `?${qs}` : ""}`);
}

/** Fetch ETFs tracking the given index (parent_index_code = code).
 *  Used by the Index Baseline page's "Linked ETFs" expansion. */
export function fetchLinkedEtfs(code: string): Promise<LinkedEtfsResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  const qs = params.toString();
  return fetchJson<LinkedEtfsResponse>(`/api/sec-composition/linked-etfs${qs ? `?${qs}` : ""}`);
}

/** Fetch the top-3 similar indices by mutual shared composition weight for
 *  the given index (from stats.sec_similars, sec_type='index', latest snapshot <= today).
 *  TTL-only cache (composition snapshot dates move infrequently). */
export function fetchSimilarIndices(code: string): Promise<SimilarIndicesResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  const qs = params.toString();
  return fetchJson<SimilarIndicesResponse>(`/api/sec-composition/similar-indices${qs ? `?${qs}` : ""}`);
}
