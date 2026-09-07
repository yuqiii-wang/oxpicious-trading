/**
 * API client for the News page (text schema endpoints under /api/news).
 */
import { fetchJson } from "./_cache";
import type {
  NewsCalendarResponse,
  NewsItemsResponse,
  NewsThemesResponse,
} from "@shared/types";

/** Params shared by the calendar + items endpoints. Industry scope: pass
 *  industry_id when an L2 chip is active, else sector_id for the whole L1
 *  (works for BOTH columns — strategy sectors resolve through the same
 *  catalog). `source` and `author` narrow everything to one origin. */
export interface NewsScopeParams {
  sector_id?: string | null;
  industry_id?: string | null;
  search?: string | null;
  source?: string | null;
  author?: string | null;
}

function qs<T extends object>(params: T): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v != null && v !== "") sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

/** LEFT column — sector → industry tree with news counts. */
export function fetchNewsThemes(
  source?: string | null,
  author?: string | null,
): Promise<NewsThemesResponse> {
  return fetchJson<NewsThemesResponse>(`/api/news/themes${qs({ source, author })}`);
}

/** RIGHT column — parallel strategy → theme tree with news counts. */
export function fetchNewsStrategyThemes(
  source?: string | null,
  author?: string | null,
): Promise<NewsThemesResponse> {
  return fetchJson<NewsThemesResponse>(`/api/news/strategy-themes${qs({ source, author })}`);
}

/** Top authors for the filter chips (scoped by everything except author). */
export function fetchNewsAuthors(
  params: Omit<NewsScopeParams, "author">,
): Promise<Array<{ author: string; count: number }>> {
  return fetchJson<Array<{ author: string; count: number }>>(
    `/api/news/authors${qs(params)}`,
  );
}

/** Per-day news counts (co-filtered by industry scope + keyword + source). */
export function fetchNewsCalendar(
  params: NewsScopeParams & { start?: string | null; end?: string | null },
): Promise<NewsCalendarResponse> {
  return fetchJson<NewsCalendarResponse>(`/api/news/calendar${qs(params)}`);
}

/** One page of articles for the scope (+ optional single-day pick). */
export function fetchNewsItems(
  params: NewsScopeParams & { date?: string | null; limit?: number; offset?: number },
): Promise<NewsItemsResponse> {
  return fetchJson<NewsItemsResponse>(`/api/news/items${qs(params)}`);
}
