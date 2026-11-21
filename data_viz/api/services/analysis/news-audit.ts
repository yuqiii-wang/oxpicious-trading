/**
 * News Audit (composite analysis) — the 5-day window audits of the news
 * corpus: provider/author rows (text.news_provider_audit joined with the
 * text.news_providers registry) and content rows per industry
 * (text.news_content_audit with the keyword rollup).
 *
 * Source: built by `python -m analysis.news_audit`; the UI refresh runs it
 * via POST /api/analysis/news-audit/run.
 */
import { formatDate, toNum } from "../../lib/db.js";
import { querySqlView } from "@api/lib/sql-views";
import type { QueryResultRow } from "pg";
import type {
  NewsContentAuditResponse,
  NewsContentAuditRow,
  NewsProviderAuditResponse,
  NewsProviderAuditRow,
} from "../../../shared/types.js";
import { fetchIndustryLabels } from "./industry-correlations.js";

const DEFAULT_LIMIT_WINDOWS = 8;
const MAX_LIMIT_WINDOWS = 156; // ~26 months of 5-day windows

function clampLimit(raw: string | undefined): number {
  const n = Number.parseInt((raw ?? "").trim(), 10);
  if (!Number.isFinite(n)) return DEFAULT_LIMIT_WINDOWS;
  return Math.min(Math.max(n, 1), MAX_LIMIT_WINDOWS);
}

interface DbProviderAuditRow extends QueryResultRow {
  window_start: Date | string;
  window_end: Date | string;
  source: string;
  author: string | null;
  is_complete: boolean;
  num_items: number;
  num_digested: number;
  num_fake: number;
  fake_pct: string | number | null;
  avg_sentiment: string | number | null;
  avg_word_count: string | number | null;
  num_industries: number | null;
  computed_at: Date | string;
  is_blocked: boolean | null;
  first_seen_date: Date | string | null;
  last_seen_date: Date | string | null;
}

// ----------------------------------------------------------------------------
//  GET providers — one row per (5-day window, origin), the most recent
//  limit_windows windows, optionally filtered to one provider.
// ----------------------------------------------------------------------------
export async function getNewsProviderAudit(
  rawLimit: string | undefined,
  rawSource: string | undefined,
): Promise<NewsProviderAuditResponse> {
  const limitWindows = clampLimit(rawLimit);
  const source = (rawSource ?? "").trim();

  const rows = await querySqlView<DbProviderAuditRow>(
    "data_viz/api/services/analysis/news-audit/provider-audit",
    { limit_windows: limitWindows, source },
  );

  const providers: NewsProviderAuditRow[] = rows.map((r) => ({
    window_start: formatDate(r.window_start),
    window_end: formatDate(r.window_end),
    source: r.source,
    author: r.author ?? "",
    is_complete: r.is_complete === true,
    num_items: r.num_items,
    num_digested: r.num_digested,
    num_fake: r.num_fake,
    fake_pct: toNum(r.fake_pct),
    avg_sentiment: toNum(r.avg_sentiment),
    avg_word_count: toNum(r.avg_word_count),
    num_industries: r.num_industries,
    // LEFT JOIN miss = origin vanished from the registry (should not
    // happen — the sync runs first); read as "not blocked".
    is_blocked: r.is_blocked === true,
    first_seen_date: r.first_seen_date == null ? null : formatDate(r.first_seen_date),
    last_seen_date: r.last_seen_date == null ? null : formatDate(r.last_seen_date),
    computed_at: new Date(r.computed_at).toISOString(),
  }));

  return {
    limit_windows: limitWindows,
    source,
    // Explicit backend arg for the shared ExpandedTable — this endpoint
    // opts IN to per-column header filters.
    enable_filters: true,
    rows: providers,
  };
}

interface DbContentAuditRow extends QueryResultRow {
  window_start: Date | string;
  window_end: Date | string;
  industry_id: string;
  is_complete: boolean;
  num_items: number;
  num_digested: number;
  num_fake: number;
  fake_pct: string | number | null;
  avg_sentiment: string | number | null;
  distinct_keywords: number | null;
  keyword_mentions: number | null;
  articles_with_keywords: number | null;
  computed_at: Date | string;
}

// ----------------------------------------------------------------------------
//  GET content — one row per (5-day window, industry), the most recent
//  limit_windows windows, optionally filtered to one industry_id.
// ----------------------------------------------------------------------------
export async function getNewsContentAudit(
  rawLimit: string | undefined,
  rawIndustryId: string | undefined,
): Promise<NewsContentAuditResponse> {
  const limitWindows = clampLimit(rawLimit);
  const industryId = (rawIndustryId ?? "").trim();

  const rows = await querySqlView<DbContentAuditRow>(
    "data_viz/api/services/analysis/news-audit/content-audit",
    { limit_windows: limitWindows, industry_id: industryId },
  );
  // Label the non-empty taxonomy ids once (""/untagged stays raw).
  const labelMap = await fetchIndustryLabels(
    Array.from(
      new Set(rows.map((r) => r.industry_id).filter((id) => id.length > 0)),
    ),
  );

  const content: NewsContentAuditRow[] = rows.map((r) => ({
    window_start: formatDate(r.window_start),
    window_end: formatDate(r.window_end),
    industry_id: r.industry_id,
    // "" (untagged) gets an explicit label so the payload is
    // self-describing; the taxonomy ids fall back to the raw id.
    industry_label:
      r.industry_id.length > 0
        ? labelMap.get(r.industry_id) ?? r.industry_id
        : "(untagged)",
    is_complete: r.is_complete === true,
    num_items: r.num_items,
    num_digested: r.num_digested,
    num_fake: r.num_fake,
    fake_pct: toNum(r.fake_pct),
    avg_sentiment: toNum(r.avg_sentiment),
    distinct_keywords: r.distinct_keywords,
    keyword_mentions: r.keyword_mentions,
    articles_with_keywords: r.articles_with_keywords,
    computed_at: new Date(r.computed_at).toISOString(),
  }));

  return {
    limit_windows: limitWindows,
    industry_id: industryId,
    enable_filters: true,
    rows: content,
  };
}
