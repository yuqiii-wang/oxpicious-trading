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
  PbocOmaResponse,
  OptionsCombinedResponse,
  OptionsUnderlying,
  EtfOhlcvResponse,
  SectorNode,
  StrategyNode,
  EtfMarginCombinedResponse,
  IndexInfo,
  IndexCombinedResponse,
  IndexIntraday5minResponse,
  SecCompositionResponse,
  LinkedEtfsResponse,
  SimilarIndicesResponse,
  StockBaselineResponse,
  StockCombinedResponse,
  MovAveSpreadCodesResponse,
  MovAveSpreadChartResponse,
  MaSpreadSecType,
  PerfAttrCodesResponse,
  PerfAttrChartResponse,
  PerfAttrAttributionResponse,
  PerfAttrSecType,
  IndustrySentimentsChartResponse,
  IndustryCorrelationsResponse,
  IndustryBenchmarkAttributionResponse,
  IndustryAttributionBenchmarksResponse,
  BenchmarkPriceChartResponse,
  IndustryAttributionPriceSeriesResponse,
  AllIndustriesAttributionResponse,
  MemberIndexAttributionResponse,
  IndustryEtfPriceSeriesResponse,
  IndustryEtfContributionBarsResponse,
  IndustryHypesAndDrainsResponse,
  LiveDataSecType,
  LiveDataDatesResponse,
  LiveDataCombinedResponse,
  StrategyBacktestResponse,
  StrategyRiskResponse,
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
 *
 * Three flavors:
 *   • clearApiCache()              — wipes everything (use sparingly).
 *   • invalidateCacheForUrl(url)   — removes one exact URL (use for plot-level
 *                                     refresh where one endpoint = one plot).
 *   • invalidateCacheForPrefix(p)  — removes all URLs starting with `p` (use
 *                                     for page-level refresh where one logical
 *                                     "page" maps to multiple endpoints that
 *                                     share a common route prefix, e.g.
 *                                     "/api/debt-baseline").
 *
 * All three also clear the version cache so the next fetch re-checks the
 * DB's latest date instead of trusting the stale 30s snapshot.
 */
export function clearApiCache(): void {
  apiCache.clear();
  _versionCache = null;
}

export function invalidateCacheForUrl(url: string): void {
  apiCache.delete(url);
  _versionCache = null;
}

export function invalidateCacheForPrefix(prefix: string): void {
  apiCache.deletePrefix(prefix);
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
  if (url.startsWith("/api/stock-baseline/combined"))     return "stock_baseline";
  // No date in response — TTL only:
  // /api/etf-margin/themes, /api/szse-options/underlyings,
  // /api/index-baseline/themes, /api/index-baseline/strategy-themes,
  // /api/index-baseline/intraday-5min, /api/stock-baseline/themes
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
    // Order matters: the linked-etfs sub-route must be checked BEFORE the
    // broader /api/sec-composition prefix (linked-etfs returns etfs[].latest_date,
    // not a top-level snapshot_date).
    if (url.startsWith("/api/sec-composition/linked-etfs")) {
      const etfs = (data as LinkedEtfsResponse)?.etfs ?? [];
      return etfs.reduce((max, e) => (e.latest_date > max ? e.latest_date : max), "");
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
    if (url.startsWith("/api/stock-baseline/combined")) {
      const dates = (data as StockCombinedResponse)?.dates ?? [];
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

/** PBoC Open Market Announcements — small dataset, no date filter (TTL-only cache). */
export function fetchPbocOmaAnnouncements(): Promise<PbocOmaResponse> {
  return fetchJson<PbocOmaResponse>(`/api/debt-baseline/oma`);
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

export function fetchThemes(exchange?: string | null): Promise<SectorNode[]> {
  const params = new URLSearchParams();
  if (exchange) params.set("exchange", exchange);
  const qs = params.toString();
  return fetchJson<SectorNode[]>(`/api/etf-margin/themes${qs ? `?${qs}` : ""}`);
}

export function fetchEtfStrategyThemes(exchange?: string | null): Promise<StrategyNode[]> {
  const params = new URLSearchParams();
  if (exchange) params.set("exchange", exchange);
  const qs = params.toString();
  return fetchJson<StrategyNode[]>(`/api/etf-margin/strategy-themes${qs ? `?${qs}` : ""}`);
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
  exchange?: string | null,
  strategy?: string | null,
  theme?: string | null,
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
  if (exchange) params.set("exchange", exchange);
  if (strategy) params.set("strategy", strategy);
  if (theme) params.set("theme", theme);
  const qs = params.toString();
  return fetchJson<EtfMarginCombinedResponse>(`/api/etf-margin/combined${qs ? `?${qs}` : ""}`);
}

export function fetchIndexList(): Promise<IndexInfo[]> {
  return fetchJson<IndexInfo[]>(`/api/index-baseline/list`);
}

export function fetchIndexThemes(exchange?: string | null): Promise<SectorNode[]> {
  const params = new URLSearchParams();
  if (exchange) params.set("exchange", exchange);
  const qs = params.toString();
  return fetchJson<SectorNode[]>(`/api/index-baseline/themes${qs ? `?${qs}` : ""}`);
}

/** Fetch the parallel strategy → theme tree (RIGHT column of the two-column
 *  selector).  Returns strategy-primary indices only. */
export function fetchIndexStrategyThemes(exchange?: string | null): Promise<StrategyNode[]> {
  const params = new URLSearchParams();
  if (exchange) params.set("exchange", exchange);
  const qs = params.toString();
  return fetchJson<StrategyNode[]>(`/api/index-baseline/strategy-themes${qs ? `?${qs}` : ""}`);
}

export function fetchIndicesCombined(
  sector?: string | null,
  industry?: string | null,
  startDate?: string | null,
  endDate?: string | null,
  page?: number,
  pageSize?: number,
  code?: string | null,
  exchange?: string | null,
  /** Strategy filter (RIGHT column). When set (and sector is not), filters by
   *  sector_id on rows where is_industry_not_strategy=FALSE (strategy-primary). */
  strategy?: string | null,
  /** Theme filter (RIGHT column). When set, filters by industry_id/industry_slug
   *  on strategy-primary rows. */
  theme?: string | null,
): Promise<IndexCombinedResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (sector) params.set("sector", sector);
  if (industry) params.set("industry", industry);
  if (strategy) params.set("strategy", strategy);
  if (theme) params.set("theme", theme);
  if (startDate) params.set("start_date", startDate);
  if (endDate) params.set("end_date", endDate);
  if (page) params.set("page", String(page));
  if (pageSize) params.set("page_size", String(pageSize));
  if (exchange) params.set("exchange", exchange);
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

export function fetchStockBaseline(
  code: string,
  startDate?: string | null,
  endDate?: string | null,
): Promise<StockBaselineResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (startDate) params.set("start_date", startDate);
  if (endDate) params.set("end_date", endDate);
  const qs = params.toString();
  return fetchJson<StockBaselineResponse>(`/api/stock-baseline${qs ? `?${qs}` : ""}`);
}

export function fetchStockThemes(exchange?: string | null): Promise<SectorNode[]> {
  const params = new URLSearchParams();
  if (exchange) params.set("exchange", exchange);
  const qs = params.toString();
  return fetchJson<SectorNode[]>(`/api/stock-baseline/themes${qs ? `?${qs}` : ""}`);
}

export function fetchStockStrategyThemes(exchange?: string | null): Promise<StrategyNode[]> {
  const params = new URLSearchParams();
  if (exchange) params.set("exchange", exchange);
  const qs = params.toString();
  return fetchJson<StrategyNode[]>(`/api/stock-baseline/strategy-themes${qs ? `?${qs}` : ""}`);
}

export function fetchStocksCombined(
  sector?: string | null,
  industry?: string | null,
  startDate?: string | null,
  endDate?: string | null,
  page?: number,
  pageSize?: number,
  code?: string | null,
  exchange?: string | null,
  strategy?: string | null,
  theme?: string | null,
): Promise<StockCombinedResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (sector) params.set("sector", sector);
  if (industry) params.set("industry", industry);
  if (startDate) params.set("start_date", startDate);
  if (endDate) params.set("end_date", endDate);
  if (page) params.set("page", String(page));
  if (pageSize) params.set("page_size", String(pageSize));
  if (exchange) params.set("exchange", exchange);
  if (strategy) params.set("strategy", strategy);
  if (theme) params.set("theme", theme);
  const qs = params.toString();
  return fetchJson<StockCombinedResponse>(`/api/stock-baseline/combined${qs ? `?${qs}` : ""}`);
}

// ---------------------------------------------------------------------------
//  Analysis Commons — MA-Spread (ETF + Index)
//  All three endpoints require an `sec_type` query param ('etf' | 'index')
//  and rely on the LRU TTL cache only (no version check; the analysis schema
//  is recomputed offline by analyze_mov_ave_spread.py).
// ---------------------------------------------------------------------------
export function fetchMovAveSpreadCodes(
  secType: MaSpreadSecType,
  exchange?: string | null,
): Promise<MovAveSpreadCodesResponse> {
  const params = new URLSearchParams();
  if (secType) params.set("sec_type", secType);
  if (exchange) params.set("exchange", exchange);
  const qs = params.toString();
  return fetchJson<MovAveSpreadCodesResponse>(
    `/api/analysis/mov-ave-spread/codes${qs ? `?${qs}` : ""}`,
  );
}

/** Themes tree (L1 sector → L2 industry → items) for the ThemeSelector.
 *  Only includes codes that have rows in analysis.mov_ave_spreads_detail.
 *  When `exchange` is set, the tree is filtered at the backend via
 *  matchesExchange() so cross-border securities (HK/Overseas) are excluded
 *  unless explicitly selected. */
export function fetchMovAveSpreadThemes(
  secType: MaSpreadSecType,
  exchange?: string | null,
): Promise<SectorNode[]> {
  const params = new URLSearchParams();
  if (secType) params.set("sec_type", secType);
  if (exchange) params.set("exchange", exchange);
  const qs = params.toString();
  return fetchJson<SectorNode[]>(
    `/api/analysis/mov-ave-spread/themes${qs ? `?${qs}` : ""}`,
  );
}

export function fetchMovAveSpreadStrategyThemes(
  secType: MaSpreadSecType,
  exchange?: string | null,
): Promise<StrategyNode[]> {
  const params = new URLSearchParams();
  if (secType) params.set("sec_type", secType);
  if (exchange) params.set("exchange", exchange);
  const qs = params.toString();
  return fetchJson<StrategyNode[]>(
    `/api/analysis/mov-ave-spread/strategy-themes${qs ? `?${qs}` : ""}`,
  );
}

export function fetchMovAveSpreadChart(
  code: string,
  secType: MaSpreadSecType,
): Promise<MovAveSpreadChartResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (secType) params.set("sec_type", secType);
  const qs = params.toString();
  return fetchJson<MovAveSpreadChartResponse>(
    `/api/analysis/mov-ave-spread/chart${qs ? `?${qs}` : ""}`,
  );
}

// ---------------------------------------------------------------------------
//  Analysis Commons — Perf Attribution (ETF/Index × Index)
//  TTL-only cache (analysis schema is recomputed offline).
// ---------------------------------------------------------------------------
export function fetchPerfAttrCodes(
  secType: PerfAttrSecType = "etf",
): Promise<PerfAttrCodesResponse> {
  return fetchJson<PerfAttrCodesResponse>(
    `/api/analysis/perf-attr/codes?sec_type=${secType}`,
  );
}

/** Themes tree (L1 sector → L2 industry → items) for the ThemeSelector.
 *  Only includes codes that have rows in analysis.sec_alloc_perf_attribution. */
export function fetchPerfAttrThemes(
  secType: PerfAttrSecType = "etf",
): Promise<SectorNode[]> {
  return fetchJson<SectorNode[]>(
    `/api/analysis/perf-attr/themes?sec_type=${secType}`,
  );
}

export function fetchPerfAttrStrategyThemes(
  secType: PerfAttrSecType = "etf",
): Promise<StrategyNode[]> {
  return fetchJson<StrategyNode[]>(
    `/api/analysis/perf-attr/strategy-themes?sec_type=${secType}`,
  );
}

export function fetchPerfAttrAttribution(
  code: string,
  secType: PerfAttrSecType = "etf",
  date?: string | null,
): Promise<PerfAttrAttributionResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  params.set("sec_type", secType);
  if (date) params.set("date", date);
  const qs = params.toString();
  return fetchJson<PerfAttrAttributionResponse>(
    `/api/analysis/perf-attr/attribution?${qs}`,
  );
}

export function fetchPerfAttrChart(
  code: string,
  benchmarkCode: string,
  secType: PerfAttrSecType = "etf",
): Promise<PerfAttrChartResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (benchmarkCode) params.set("benchmark_code", benchmarkCode);
  params.set("sec_type", secType);
  const qs = params.toString();
  return fetchJson<PerfAttrChartResponse>(
    `/api/analysis/perf-attr/chart?${qs}`,
  );
}

// ---------------------------------------------------------------------------
//  Analysis Commons — Industry Sentiments (member index values, rebased to 100)
//  TTL-only cache. NO analysis-table intermediary — the data is queried
//  directly from stats.index_basic_stats JOIN stats.sec_classification at
//  request time. The frontend rebases each member index to 100 at the start
//  of the displayed (zoom) window (scale-invariant comparison across indices
//  with different absolute price levels).
// ---------------------------------------------------------------------------
/** Themes tree (L1 sector → L2 industry) for the ThemeSelector.
 *  Built directly from stats.sec_classification (type='index'). Each
 *  industry's chip count = number of member indices in that industry.
 *  The optional `exchange` filter narrows the tree to the selected exchange
 *  group (PRIMARY/SS/SZ/BJ/HK/OVERSEAS) — mirroring the index-baseline themes
 *  endpoint. */
export function fetchIndustrySentimentsThemes(
  exchange?: string | null,
): Promise<SectorNode[]> {
  const params = new URLSearchParams();
  if (exchange) params.set("exchange", exchange);
  const qs = params.toString();
  return fetchJson<SectorNode[]>(
    `/api/analysis/industry-sentiments/themes${qs ? `?${qs}` : ""}`,
  );
}

export function fetchIndustrySentimentsStrategyThemes(
  exchange?: string | null,
): Promise<StrategyNode[]> {
  const params = new URLSearchParams();
  if (exchange) params.set("exchange", exchange);
  const qs = params.toString();
  return fetchJson<StrategyNode[]>(
    `/api/analysis/industry-sentiments/strategy-themes${qs ? `?${qs}` : ""}`,
  );
}

/**
 * Fetch per-index close time series for ONE industry. Returns one entry per
 * member index in the industry, each with its raw daily close series from
 * stats.index_basic_stats. The frontend rebases each to 100 at the start of
 * the visible (zoom) window.
 */
export function fetchIndustrySentimentsChart(
  industryId: string,
): Promise<IndustrySentimentsChartResponse> {
  const params = new URLSearchParams();
  if (industryId) params.set("industry_id", industryId);
  const qs = params.toString();
  return fetchJson<IndustrySentimentsChartResponse>(
    `/api/analysis/industry-sentiments/chart?${qs}`,
  );
}

/** Fetch chart data (close series) for a single index code. Used when an
 *  L3 index chip is clicked under a strategy/theme — strategy-primary
 *  indices may not have an industry_id classification. */
export function fetchIndustrySentimentsChartByCode(
  code: string,
): Promise<IndustrySentimentsChartResponse> {
  const params = new URLSearchParams();
  params.set("code", code);
  return fetchJson<IndustrySentimentsChartResponse>(
    `/api/analysis/industry-sentiments/chart-by-code?${params.toString()}`,
  );
}

/**
 * Fetch pairwise rolling correlation time series between selected
 * industries' mean_price series. Returns one row per (date, pair) for
 * every lexicographic (a<b) pair from `industryIds`, with corr_5d /
 * corr_20d / corr_60d / corr_255d. Drives the expandable Correlation chart
 * on the IndustrySentiments page — only enabled when ≥2 industries are
 * selected.
 *
 * `poolSize` selects the same-pool slice for both endpoints (cross-pool
 * comparisons are not materialized). Defaults to 'all'.
 */
export function fetchIndustryCorrelations(
  industryIds: string[],
  poolSize: "all" | "small" | "mid" | "large" = "all",
): Promise<IndustryCorrelationsResponse> {
  const params = new URLSearchParams();
  if (industryIds.length > 0) params.set("industry_ids", industryIds.join(","));
  params.set("pool_size", poolSize);
  const qs = params.toString();
  return fetchJson<IndustryCorrelationsResponse>(
    `/api/analysis/industry-correlations?${qs}`,
  );
}

/**
 * Fetch the industry-level benchmark attribution for ONE industry at a
 * specific (or latest) date. Reads pre-materialized rows from
 * analysis.industry_attributions (industry_shared_weight +
 * benchmark_shared_weight per benchmark_code). benchmark_return is computed
 * on-the-fly. Drives the per-industry attribution bar charts (2nd plot
 * onward) in "Benchmark Attribution" mode on the IndustrySentiments page.
 *
 * `date` is optional (defaults to latest available). Returns one row per
 * benchmark with the shared weights and benchmark_return for that
 * (industry, benchmark, date).
 */
export function fetchIndustryBenchmarkAttribution(
  industryId: string,
  date?: string | null,
): Promise<IndustryBenchmarkAttributionResponse> {
  const params = new URLSearchParams();
  if (industryId) params.set("industry_id", industryId);
  if (date) params.set("date", date);
  const qs = params.toString();
  return fetchJson<IndustryBenchmarkAttributionResponse>(
    `/api/analysis/industry-benchmark-attribution?${qs}`,
  );
}

/**
 * Fetch the list of benchmark codes that appear in
 * analysis.industry_attributions, enriched with display name and
 * is_broad_market flag. Broad-market benchmarks are sorted first. Drives
 * the benchmark dropdown in "Benchmark Attribution" mode.
 */
export function fetchIndustryAttributionBenchmarks(): Promise<IndustryAttributionBenchmarksResponse> {
  return fetchJson<IndustryAttributionBenchmarksResponse>(
    `/api/analysis/industry-attribution/benchmarks`,
  );
}

/**
 * Fetch the daily close + fractional daily return series for ONE benchmark
 * index. Drives the 1st plot (benchmark price chart, clickable to pick a
 * date) in "Benchmark Attribution" mode.
 */
export function fetchBenchmarkPriceChart(
  code: string,
): Promise<BenchmarkPriceChartResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  const qs = params.toString();
  return fetchJson<BenchmarkPriceChartResponse>(
    `/api/analysis/industry-attribution/benchmark-price?${qs}`,
  );
}

/**
 * Fetch the non-this-industry price series for ONE (industry, benchmark) pair.
 * Returns benchmark close + benchmark_rolling + non_this_industry_price +
 * 5 rolling_Xdays_price columns (5/20/60/255/500) per date. Drives the
 * green/red shade overlay on the BenchmarkPriceChart — the frontend dropdown
 * picks which rolling window drives the shade.
 */
export function fetchIndustryAttributionPriceSeries(
  industryId: string,
  benchmarkCode: string,
): Promise<IndustryAttributionPriceSeriesResponse> {
  const params = new URLSearchParams();
  params.set("industry_id", industryId);
  params.set("benchmark_code", benchmarkCode);
  return fetchJson<IndustryAttributionPriceSeriesResponse>(
    `/api/analysis/industry-attribution/non-this-industry-price?${params.toString()}`,
  );
}

/** Fetch all industries' benchmark_shared_weight for a given benchmark+date.
 *  Drives the industry-level bar chart in "Benchmark Attribution" mode. */
export function fetchAllIndustriesAttribution(
  benchmarkCode: string,
  date?: string | null,
): Promise<AllIndustriesAttributionResponse> {
  const params = new URLSearchParams();
  params.set("benchmark_code", benchmarkCode);
  if (date) params.set("date", date);
  return fetchJson<AllIndustriesAttributionResponse>(
    `/api/analysis/industry-attribution/all-industries?${params.toString()}`,
  );
}

/** Fetch pre-computed top-5 (HYPE) + bottom-5 (DRAIN) industries ranked by
 *  attribution contribution to a COMPOSITE broad-market benchmark (MAIN or
 *  INNOV). Returns the 10 ranked industries + composite benchmark price series
 *  + each industry's mean_price series. Drives the "Hypes & Drains" sub-toggle
 *  in "Market Trend" mode.
 *  weighting: 'equal' (raw attribution contribution) or 'amt'
 *  (contribution × shared_trading_amt). Default: 'equal'. */
export function fetchIndustryHypesAndDrains(
  benchmarkCode: string,
  periodDays: number,
  weighting: "equal" | "amt" = "equal",
): Promise<IndustryHypesAndDrainsResponse> {
  const params = new URLSearchParams();
  params.set("benchmark_code", benchmarkCode);
  params.set("period_days", String(periodDays));
  params.set("weighting", weighting);
  return fetchJson<IndustryHypesAndDrainsResponse>(
    `/api/analysis/industry-hypes-and-drains?${params.toString()}`,
  );
}

/** Fetch all member indices' code_sec_shared_weight for a given
 *  industry+benchmark+date. Drives the per-industry bar charts. */
export function fetchMemberIndexAttribution(
  industryId: string,
  benchmarkCode: string,
  date?: string | null,
): Promise<MemberIndexAttributionResponse> {
  const params = new URLSearchParams();
  params.set("industry_id", industryId);
  params.set("benchmark_code", benchmarkCode);
  if (date) params.set("date", date);
  return fetchJson<MemberIndexAttributionResponse>(
    `/api/analysis/industry-attribution/member-indices?${params.toString()}`,
  );
}

// ---------------------------------------------------------------------------
//  Industry ETF Contribution — drives the "ETF Contribution" view on the
//  Industry Sentiments page. Mirrors "Benchmark Attribution" but with ETFs
//  as the unit of analysis.
// ---------------------------------------------------------------------------

/**
 * Fetch the daily close series for ALL ETFs tracking member indices of the
 * selected industries. Drives the 1st plot in "ETF Contribution" mode:
 * a multi-line chart where each line is one ETF, rebased to 100 at its own
 * first available date (cascading rebasing handled client-side). The chart
 * is clickable to pick the as-of date for the per-industry bar charts below.
 */
export function fetchIndustryEtfPriceSeries(
  industryIds: string[],
): Promise<IndustryEtfPriceSeriesResponse> {
  const params = new URLSearchParams();
  if (industryIds.length > 0) params.set("industry_ids", industryIds.join(","));
  return fetchJson<IndustryEtfPriceSeriesResponse>(
    `/api/analysis/industry-etf-contribution/etf-price?${params.toString()}`,
  );
}

/**
 * Fetch per-ETF contribution bars for ONE industry at a specific (or latest)
 * date. Returns one row per ETF with trading_amount + etf_return, plus the
 * industry aggregate from analysis.industry_etf_contribution. Drives the
 * 2nd+ plots in "ETF Contribution" mode.
 */
export function fetchIndustryEtfContributionBars(
  industryId: string,
  date?: string | null,
): Promise<IndustryEtfContributionBarsResponse> {
  const params = new URLSearchParams();
  params.set("industry_id", industryId);
  if (date) params.set("date", date);
  return fetchJson<IndustryEtfContributionBarsResponse>(
    `/api/analysis/industry-etf-contribution/etf-bars?${params.toString()}`,
  );
}

// ---------------------------------------------------------------------------
//  Live Data — intraday 5-min bars (index + stock)
//  TTL-only cache (no version check; the intraday tables are populated by
//  the streaming price-download scripts and the latest date moves
//  continuously throughout the trading day).
// ---------------------------------------------------------------------------
/** Distinct trading days with at least one intraday bar, descending. */
export function fetchLiveDataDates(
  secType: LiveDataSecType,
): Promise<LiveDataDatesResponse> {
  const params = new URLSearchParams();
  params.set("type", secType);
  return fetchJson<LiveDataDatesResponse>(
    `/api/live-data/dates?${params.toString()}`,
  );
}

/** L1 sector → L2 industry tree restricted to codes with intraday data. */
export function fetchLiveDataThemes(
  secType: LiveDataSecType,
  exchange?: string | null,
): Promise<SectorNode[]> {
  const params = new URLSearchParams();
  params.set("type", secType);
  if (exchange) params.set("exchange", exchange);
  return fetchJson<SectorNode[]>(
    `/api/live-data/themes?${params.toString()}`,
  );
}

export function fetchLiveDataStrategyThemes(
  secType: LiveDataSecType,
  exchange?: string | null,
): Promise<StrategyNode[]> {
  const params = new URLSearchParams();
  params.set("type", secType);
  if (exchange) params.set("exchange", exchange);
  return fetchJson<StrategyNode[]>(
    `/api/live-data/strategy-themes?${params.toString()}`,
  );
}

/** Paginated list of codes with their intraday bars for one date. */
export function fetchLiveDataCombined(
  secType: LiveDataSecType,
  date?: string | null,
  sector?: string | null,
  industry?: string | null,
  exchange?: string | null,
  page?: number,
  pageSize?: number,
  code?: string | null,
  strategy?: string | null,
  theme?: string | null,
): Promise<LiveDataCombinedResponse> {
  const params = new URLSearchParams();
  params.set("type", secType);
  if (date) params.set("date", date);
  if (sector) params.set("sector", sector);
  if (industry) params.set("industry", industry);
  if (exchange) params.set("exchange", exchange);
  if (page) params.set("page", String(page));
  if (pageSize) params.set("page_size", String(pageSize));
  if (code) params.set("code", code);
  if (strategy) params.set("strategy", strategy);
  if (theme) params.set("theme", theme);
  return fetchJson<LiveDataCombinedResponse>(
    `/api/live-data/combined?${params.toString()}`,
  );
}

// ---------------------------------------------------------------------------
//  Strategy — MA-spread crossover backtest
//  TTL-only cache (ephemeral backtest, no DB write).
// ---------------------------------------------------------------------------
export function fetchMaSpreadBacktest(
  code: string,
  secType: MaSpreadSecType,
): Promise<StrategyBacktestResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (secType) params.set("sec_type", secType);
  const qs = params.toString();
  return fetchJson<StrategyBacktestResponse>(
    `/api/strategy/ma-spread/backtest${qs ? `?${qs}` : ""}`,
  );
}

export function fetchMaSpreadRisks(
  code: string,
  secType: MaSpreadSecType,
): Promise<StrategyRiskResponse> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (secType) params.set("sec_type", secType);
  const qs = params.toString();
  return fetchJson<StrategyRiskResponse>(
    `/api/strategy/ma-spread/risks${qs ? `?${qs}` : ""}`,
  );
}

/** Result of POST /api/strategy/ma-spread/run. */
export interface RunStrategyResult {
  success: boolean;
  stdout: string;
  stderr: string;
  exitCode: number;
}

/**
 * Run the MA-spread backtest + risk computation for one (code, secType) by
 * spawning the Python scripts via the backend. Returns when both processes
 * exit. NOT cached (always a fresh POST).
 */
export async function runMaSpreadStrategy(
  code: string,
  secType: MaSpreadSecType,
): Promise<RunStrategyResult> {
  const params = new URLSearchParams();
  if (code) params.set("code", code);
  if (secType) params.set("sec_type", secType);
  const qs = params.toString();
  const res = await fetch(
    `/api/strategy/ma-spread/run${qs ? `?${qs}` : ""}`,
    { method: "POST" },
  );
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`HTTP ${res.status}: ${text}`);
  }
  return (await res.json()) as RunStrategyResult;
}
