/**
 * API client for the News page (text schema endpoints under /api/news).
 */
import { fetchJson } from "./_cache";
import type {
  NewsCalendarResponse,
  NewsCommentsResponse,
  NewsItemDetail,
  NewsItemsResponse,
  NewsSearchResponse,
  NewsThemesResponse,
} from "@shared/types";

/** Params shared by the calendar + items endpoints. Industry scope, most
 *  specific first: pass `industry_id` for a single resolved industry (L2
 *  chip / picked security), `industry_ids` for an EXPLICIT set (multi-select
 *  / ranked-industries scope = union of the picks' industries) —
 *  present-but-empty means the pick has no industries and matches NOTHING
 *  (vs. omitting the key entirely = unscoped), else `sector_id` for the
 *  whole L1 (works for BOTH columns — strategy sectors resolve through the
 *  same catalog). `source` and `author` narrow everything to one origin.
 *  `search_mode: "any"` ORs the whitespace terms in `search` (used by the
 *  question bar's tokenized local search); default is AND. */
export interface NewsScopeParams {
  sector_id?: string | null;
  industry_id?: string | null;
  industry_ids?: string[] | null;
  search?: string | null;
  search_mode?: "any" | "all" | null;
  source?: string | null;
  author?: string | null;
}

function qs<T extends object>(params: T): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (Array.isArray(v)) {
      sp.set(k, v.join(",")); // [] → "k=" — an explicit empty set, kept
    } else if (v != null && v !== "") {
      sp.set(k, String(v));
    }
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

/** One page of articles for the scope. `date` = single-day pick from the
 *  date bar; `date_from`/`date_to` = inclusive range window (used together,
 *  instead of `date`) around a picked day. */
export function fetchNewsItems(
  params: NewsScopeParams & {
    date?: string | null;
    date_from?: string | null;
    date_to?: string | null;
    limit?: number;
    offset?: number;
  },
): Promise<NewsItemsResponse> {
  return fetchJson<NewsItemsResponse>(`/api/news/items${qs(params)}`);
}

/** Full article row (untruncated content) — the feed's expand-card fetch.
 *  Null when the news_id is unknown. Cached like the other GET lookups. */
export function fetchNewsItem(newsId: number): Promise<NewsItemDetail | null> {
  return fetchJson<NewsItemDetail | null>(
    `/api/news/item?news_id=${encodeURIComponent(String(newsId))}`,
  );
}

/** Threaded comments of one article (roots by votes, replies nested). */
export function fetchNewsComments(newsId: number): Promise<NewsCommentsResponse> {
  return fetchJson<NewsCommentsResponse>(
    `/api/news/comments?news_id=${encodeURIComponent(String(newsId))}`,
  );
}

/** Request of POST /api/news/search — live question search (zhihu only). */
export interface NewsSearchRequest {
  question: string;
  source: string;
  author?: string | null;
}

/** Trigger the interactive question search. Resolves when the Python run
 *  finishes (or is deduped — `already_running`). Never throws; failures
 *  surface as `{ success: false, stderr_tail }`. Plain fetch (bypasses the
 *  response cache) since every call is a fresh live lookup. */
export async function runNewsSearch(
  req: NewsSearchRequest,
): Promise<NewsSearchResponse> {
  const fallback = {
    question: req.question,
    source: req.source,
    author: req.author ?? null,
  };
  try {
    const res = await fetch("/api/news/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: req.question, source: req.source, author: req.author ?? null }),
    });
    if (!res.ok) {
      const body = (await res.json().catch(() => null)) as { stderr_tail?: string } | null;
      return {
        success: false,
        ...fallback,
        total: 0,
        total_returned: 0,
        out_file: null,
        items: [],
        stderr_tail: body?.stderr_tail ?? `HTTP ${res.status}`,
      };
    }
    return (await res.json()) as NewsSearchResponse;
  } catch (e) {
    return {
      success: false,
      ...fallback,
      total: 0,
      total_returned: 0,
      out_file: null,
      items: [],
      stderr_tail: String(e),
    };
  }
}

/** Response of GET /api/news/tokenize. */
export interface NewsTokenizeResponse {
  tokens: string[];
}

/** Naive-tokenize a question into news-taxonomy keywords (WSL python
 *  builds.text.tokenize). Plain fetch — always a fresh computation. Never
 *  throws; failures surface as `{ tokens: [], error }`. */
export async function fetchNewsTokenize(
  question: string,
): Promise<NewsTokenizeResponse & { error?: string }> {
  const textB64 = btoa(
    String.fromCharCode(...new TextEncoder().encode(question)),
  );
  try {
    const res = await fetch(`/api/news/tokenize?text_b64=${encodeURIComponent(textB64)}`);
    if (!res.ok) {
      const body = (await res.json().catch(() => null)) as { error?: string } | null;
      return { tokens: [], error: body?.error ?? `HTTP ${res.status}` };
    }
    return (await res.json()) as NewsTokenizeResponse;
  } catch (e) {
    return { tokens: [], error: String(e) };
  }
}
