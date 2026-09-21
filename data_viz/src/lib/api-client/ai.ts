/**
 * API client for the DataViz AI page (text-schema LLM Q&A endpoints
 * under /api/ai — text.llm_qa / text.llm_qa_refs / text.news_digestions)
 * and the persisted ask history (text.llm_qa_by_ask family — the
 * "QA by Ask" feed).
 */
import { fetchJson } from "./_cache";
import type {
  AiAskHistoryDetail,
  AiAskHistoryItemsResponse,
  AiCalendarResponse,
  AiQaDetail,
  AiQaItemsResponse,
} from "@shared/types";

/** Params shared by the calendar + items endpoints. Industry scope, most
 *  specific first: pass `industry_id` for a single resolved industry (L2
 *  chip / picked security), `industry_ids` for an EXPLICIT set (strategy/
 *  theme scope = union of member securities' industries) — present-but-empty
 *  means the pick has no industries and matches NOTHING (vs. omitting the
 *  key entirely = unscoped), else `sector_id` for the whole L1. `search`
 *  ANDs the whitespace terms over question OR answer. Inactive rows never
 *  surface (the API hard-filters is_active). */
export interface AiScopeParams {
  sector_id?: string | null;
  industry_id?: string | null;
  industry_ids?: string[] | null;
  search?: string | null;
}

function qs<T extends object>(params: T): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v == null) continue;
    if (Array.isArray(v)) {
      sp.set(k, v.join(",")); // [] → "k=" — an explicit empty set, kept
    } else if (v !== "" && v !== false) {
      sp.set(k, String(v));
    }
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

/** Per-day Q&A counts (co-filtered by industry scope + keyword + status). */
export function fetchAiCalendar(params: AiScopeParams): Promise<AiCalendarResponse> {
  return fetchJson<AiCalendarResponse>(`/api/ai/calendar${qs(params)}`);
}

/** One page of Q&A rows for the scope. `date` = single-day pick from the
 *  strip; `date_from`/`date_to` = inclusive range window (used together,
 *  instead of `date`) around a picked day. */
export function fetchAiItems(
  params: AiScopeParams & {
    date?: string | null;
    date_from?: string | null;
    date_to?: string | null;
    limit?: number;
    offset?: number;
  },
): Promise<AiQaItemsResponse> {
  return fetchJson<AiQaItemsResponse>(`/api/ai/items${qs(params)}`);
}

/** Full Q&A row + refs — the feed card's expand fetch. Null when the qa_id
 *  is unknown. Cached like the other GET lookups. */
export function fetchAiQaDetail(qaId: number): Promise<AiQaDetail | null> {
  return fetchJson<AiQaDetail | null>(
    `/api/ai/qa?qa_id=${encodeURIComponent(String(qaId))}`,
  );
}

/** Ask-history params: the knowledge-base scope plus `code` — a picked
 *  security filters the ask's primary instrument code directly (more
 *  precise than the industry mapping). `search` ANDs the whitespace terms
 *  over question OR answer OR the derived keyword rows. */
export interface AiAskHistoryParams extends AiScopeParams {
  code?: string | null;
}

/** Per-day persisted-ask counts (the "QA by Ask" strip's dots). */
export function fetchAiAskCalendar(
  params: AiAskHistoryParams,
): Promise<AiCalendarResponse> {
  return fetchJson<AiCalendarResponse>(`/api/ai/ask-calendar${qs(params)}`);
}

/** One page of persisted asks for the scope (same date params as the
 *  knowledge-base items). */
export function fetchAiAskItems(
  params: AiAskHistoryParams & {
    date?: string | null;
    date_from?: string | null;
    date_to?: string | null;
    limit?: number;
    offset?: number;
  },
): Promise<AiAskHistoryItemsResponse> {
  return fetchJson<AiAskHistoryItemsResponse>(`/api/ai/ask-items${qs(params)}`);
}

/** Full ask row + keywords + image list — the by-ask card's expand fetch.
 *  Null when the ask_id is unknown. Cached like the other GET lookups. */
export function fetchAiAskDetail(askId: number): Promise<AiAskHistoryDetail | null> {
  return fetchJson<AiAskHistoryDetail | null>(
    `/api/ai/ask-qa?ask_id=${encodeURIComponent(String(askId))}`,
  );
}

/** The URL of one persisted screenshot's bytes (an <img> src — the browser
 *  fetches and caches it; not a fetchJson call). */
export function aiAskImageUrl(imageId: number): string {
  return `/api/ai/ask-image?image_id=${encodeURIComponent(String(imageId))}`;
}
