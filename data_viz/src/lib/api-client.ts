/**
 * Lightweight fetch client for the backend API.
 *
 * Caching strategy (version-aware):
 *   1. Responses are cached by URL in an LRU cache (TTL = 10 min safety net).
 *   2. Before returning a cached entry, the client asks the backend for the
 *      latest (MAX) date of the corresponding data source via
 *      /api/cache/latest-dates (itself cached for 30s to avoid hammering DB).
 *   3. If the DB's latest date is newer than what the cached response holds,
 *      the cache is bypassed and fresh data is fetched.
 *   4. If the dates match (or the request is a historical filter whose
 *      end_date predates the DB max), the cached payload is reused.
 *
 * Endpoints without a date in their response (themes, underlyings list,
 * intraday-5min) skip the version check and rely on the TTL only.
 */
import { LruCache } from "@/lib/lru-cache";
import type {
  LatestDatesResponse,
  DebtBaselineResponse,
  OptionsCombinedResponse,
  OptionsUnderlying,
  EtfOhlcvResponse,
  SectorNode,
  EtfMarginCombinedResponse,
  IndexInfo,
  IndexCombinedResponse,
  IndexIntraday5minResponse,
  SecCompositionResponse,
} from "../../shared/types";

// Module-level cache singleton: 100 entries, 10-minute TTL (safety net).
const apiCache = new LruCache<unknown>(100, 10 * 60 * 1000);

// ---------------------------------------------------------------------------
//  Version cache — /api/cache/latest-dates is fetched at most once per 30s.
// ---------------------------------------------------------------------------
const VERSION_TTL_MS = 30_000;
let _versionCache: { value: LatestDatesResponse; expiresAt: number } | null = null;

/**
 * Manual cache invalidation — call after destructive operations or when the
 * user explicitly wants fresh data (e.g. a "Refresh" button).
 */
export function clearApiCache(): void {
  apiCache.clear();
  _versionCache = null;
}

async function fetchJsonUncached<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`HTTP ${res.status}: ${text}`);
  }
  return (await res.json()) as T;
}

// ---------------------------------------------------------------------------
//  URL → data source mapping for version checks.
//  Returns null for endpoints that have no date in their response (themes,
//  underlyings list, intraday-5min) — these rely on the TTL cache only.
// ---------------------------------------------------------------------------
function mapUrlToSource(url: string): keyof LatestDatesResponse | null {
  if (url.startsWith("/api/debt-baseline"))               return "debt";
  if (url.startsWith("/api/sec-composition"))             return "sec_composition";
  if (url.startsWith("/api/index-baseline/list"))         return "index_baseline";
  if (url.startsWith("/api/index-baseline/combined"))     return "index_baseline";
  if (url.startsWith("/api/etf-margin/combined"))         return "etf_margin";
  if (url.startsWith("/api/szse-options/combined"))       return "options";
  if (url.startsWith("/api/szse-options/etf-ohlcv"))      return "etf_margin";
  // No date in response — TTL only:
  // /api/etf-margin/themes, /api/szse-options/underlyings,
  // /api/index-baseline/intraday-5min
  return null;
}

/**
 * Extract the latest date from a cached response (the max date the UI
 * currently holds for this data).  Returns "" if the response has no
 * date field or is empty.
 */
function extractLatestDate(url: string, data: unknown): string {
  try {
    if (url.startsWith("/api/debt-baseline")) {
      return (data as DebtBaselineResponse)?.maxDate ?? "";
    }
    if (url.startsWith("/api/sec-composition")) {
      return (data as SecCompositionResponse)?.snapshot_date ?? "";
    }
    if (url.startsWith("/api/index-baseline/list")) {
      const indices = (data as IndexInfo[]) ?? [];
      return indices.reduce((max, i) => (i.last_date > max ? i.last_date : max), "");
    }
    if (url.startsWith("/api/index-baseline/combined")) {
      const dates = (data as IndexCombinedResponse)?.dates ?? [];
      return dates.length ? dates[dates.length - 1] : "";
    }
    if (url.startsWith("/api/etf-margin/combined")) {
      const dates = (data as EtfMarginCombinedResponse)?.dates ?? [];
      return dates.length ? dates[dates.length - 1] : "";
    }
    if (url.startsWith("/api/szse-options/combined")) {
      const dates = (data as OptionsCombinedResponse)?.dates ?? [];
      return dates.length ? dates[dates.length - 1] : "";
    }
    if (url.startsWith("/api/szse-options/etf-ohlcv")) {
      const dates = (data as EtfOhlcvResponse)?.dates ?? [];
      return dates.length ? dates[dates.length - 1] : "";
    }
  } catch {
    // ignore — treat as no date
  }
  return "";
}

/** Extract the end_date query param from a URL (returns null if absent). */
function extractEndDateParam(url: string): string | null {
  const idx = url.indexOf("end_date=");
  if (idx < 0) return null;
  const start = idx + "end_date=".length;
  const end = url.indexOf("&", start);
  const val = end < 0 ? url.slice(start) : url.slice(start, end);
  return val || null;
}

/**
 * Fetch the latest-dates payload (cached for 30s).  Falls back gracefully —
 * if the request fails, callers treat the version as unknown and use cache.
 */
async function fetchLatestDates(): Promise<LatestDatesResponse | null> {
  if (_versionCache && Date.now() < _versionCache.expiresAt) {
    return _versionCache.value;
  }
  try {
    const value = await fetchJsonUncached<LatestDatesResponse>("/api/cache/latest-dates");
    _versionCache = { value, expiresAt: Date.now() + VERSION_TTL_MS };
    return value;
  } catch {
    return null; // version check unavailable → use cache
  }
}

/**
 * Decide whether a cached entry should be refreshed.
 *
 * Returns true (refresh) when the DB has a newer latest date than what the
 * cached response holds.  Returns false (use cache) when:
 *   - the endpoint has no version tracking (null source), or
 *   - the version check is unavailable (backend error), or
 *   - the request is a historical filter (end_date < DB max → frozen), or
 *   - the cached latest date >= DB latest date (UI is up-to-date).
 */
async function shouldRefreshCache(url: string, cachedData: unknown): Promise<boolean> {
  const source = mapUrlToSource(url);
  if (!source) return false;

  const latest = await fetchLatestDates();
  if (!latest) return false; // version check unavailable → use cache

  const dbMax = latest[source] ?? "";
  if (!dbMax) return false; // DB empty → use cache

  // Historical filter: if the request explicitly asked for data up to a
  // past end_date, the filtered range is frozen — no new rows can appear.
  const endDate = extractEndDateParam(url);
  if (endDate && endDate < dbMax) return false;

  const cachedMax = extractLatestDate(url, cachedData);
  if (cachedMax && cachedMax >= dbMax) return false; // UI is up-to-date

  return true; // DB has newer data → refresh
}

/**
 * Cached fetch — returns the cached payload if it is still fresh (version
 * check passes), otherwise performs the network request and stores the
 * result.
 * In-flight requests are de-duplicated so concurrent callers share a single
 * fetch (avoids stampedes when multiple components mount at once).
 */
const inflight = new Map<string, Promise<unknown>>();

async function fetchJson<T>(url: string): Promise<T> {
  const cached = apiCache.get(url);
  if (cached !== undefined) {
    const refresh = await shouldRefreshCache(url, cached);
    if (!refresh) {
      return cached as T;
    }
    // DB has newer data → fall through to fetch fresh
  }
  const existing = inflight.get(url);
  if (existing) {
    return existing as Promise<T>;
  }
  const p = fetchJsonUncached<T>(url)
    .then((value) => {
      apiCache.set(url, value);
      return value;
    })
    .finally(() => {
      inflight.delete(url);
    });
  inflight.set(url, p);
  return p;
}

export function fetchDebtBaseline(
  startDate?: string | null,
  endDate?: string | null,
): Promise<DebtBaselineResponse> {
  const params = new URLSearchParams();
  if (startDate) params.set("start_date", startDate);
  if (endDate) params.set("end_date", endDate);
  const qs = params.toString();
  return fetchJson<DebtBaselineResponse>(`/api/debt-baseline${qs ? `?${qs}` : ""}`);
}

export function fetchUnderlyings(): Promise<OptionsUnderlying[]> {
  return fetchJson<OptionsUnderlying[]>(`/api/szse-options/underlyings`);
}

export function fetchOptionsCombined(
  underlying: string,
  startDate?: string | null,
  endDate?: string | null,
): Promise<OptionsCombinedResponse> {
  const params = new URLSearchParams();
  if (underlying) params.set("underlying", underlying);
  if (startDate) params.set("start_date", startDate);
  if (endDate) params.set("end_date", endDate);
  const qs = params.toString();
  return fetchJson<OptionsCombinedResponse>(`/api/szse-options/combined${qs ? `?${qs}` : ""}`);
}

export function fetchEtfOhlcv(
  code: string,
  startDate?: string | null,
  endDate?: string | null,
): Promise<EtfOhlcvResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (startDate) params.set("start_date", startDate);
  if (endDate) params.set("end_date", endDate);
  const qs = params.toString();
  return fetchJson<EtfOhlcvResponse>(`/api/szse-options/etf-ohlcv${qs ? `?${qs}` : ""}`);
}

export function fetchThemes(): Promise<SectorNode[]> {
  return fetchJson<SectorNode[]>(`/api/etf-margin/themes`);
}

export function fetchEtfMarginCombined(
  sector?: string | null,
  industry?: string | null,
  startDate?: string | null,
  endDate?: string | null,
  limitPerTheme?: number,
  page?: number,
  pageSize?: number,
  code?: string | null,
): Promise<EtfMarginCombinedResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (sector) params.set("sector", sector);
  if (industry) params.set("industry", industry);
  if (startDate) params.set("start_date", startDate);
  if (endDate) params.set("end_date", endDate);
  if (limitPerTheme) params.set("limit_per_theme", String(limitPerTheme));
  if (page) params.set("page", String(page));
  if (pageSize) params.set("page_size", String(pageSize));
  const qs = params.toString();
  return fetchJson<EtfMarginCombinedResponse>(`/api/etf-margin/combined${qs ? `?${qs}` : ""}`);
}

export function fetchIndexList(): Promise<IndexInfo[]> {
  return fetchJson<IndexInfo[]>(`/api/index-baseline/list`);
}

export function fetchIndexThemes(): Promise<SectorNode[]> {
  return fetchJson<SectorNode[]>(`/api/index-baseline/themes`);
}

export function fetchIndicesCombined(
  sector?: string | null,
  industry?: string | null,
  startDate?: string | null,
  endDate?: string | null,
  page?: number,
  pageSize?: number,
  code?: string | null,
): Promise<IndexCombinedResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (sector) params.set("sector", sector);
  if (industry) params.set("industry", industry);
  if (startDate) params.set("start_date", startDate);
  if (endDate) params.set("end_date", endDate);
  if (page) params.set("page", String(page));
  if (pageSize) params.set("page_size", String(pageSize));
  const qs = params.toString();
  return fetchJson<IndexCombinedResponse>(`/api/index-baseline/combined${qs ? `?${qs}` : ""}`);
}

export function fetchIndexIntraday5min(
  code: string,
  date: string,
): Promise<IndexIntraday5minResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (date) params.set("date", date);
  const qs = params.toString();
  return fetchJson<IndexIntraday5minResponse>(`/api/index-baseline/intraday-5min${qs ? `?${qs}` : ""}`);
}

export function fetchSecComposition(code: string): Promise<SecCompositionResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  const qs = params.toString();
  return fetchJson<SecCompositionResponse>(`/api/sec-composition${qs ? `?${qs}` : ""}`);
}
