/**
 * AI service — reads the LLM Q&A knowledge base in the text schema
 * (llm.llm_qa + llm.llm_qa_refs, written by llm_agents) with the SAME
 * SQL design as the news service:
 *
 *   • industry scope — industry_id when an L2 chip is active; else, for an
 *     L1 sector, rows whose sector_id matches directly (the denormalized
 *     parent sector written by llm_agents) OR — for rows stored before that
 *     column was backfilled — whose industry_id belongs to the sector
 *     (resolved through the stats.sec_classification catalog); an explicit
 *     industryIds set (strategy/theme pick) still filters by the set itself;
 *     else unscoped.
 *   • keyword search — per term: the GIN-indexed search_tsv (Chinese,
 *     StructBERT-segmented on both sides) OR a raw question/answer
 *     substring; ALL terms must hit (AND).
 *   • status — inactive rows (is_active = false) never surface; every
 *     list/calendar query is hard-filtered to active rows.
 *
 * The QA "date" is qa_date (the question's own data date — the weekly
 * industry Q&A's （截至…） anchor; now() for manual rows) bucketed to
 * Asia/Shanghai days and mapped to the latest TRADING day on or before it
 * (weekends/holidays roll back), so the calendar strip, the day filter and
 * the per-card dates all sit on the same dates as the trend charts.
 *
 * PERFORMANCE — the trading-day mapping is hoisted into a `td` CTE
 * (SELECT DISTINCT date FROM stats.industry_basic_stats) computed ONCE per
 * query (AS MATERIALIZED — without it Postgres re-runs the DISTINCT for
 * every correlated reference, which cost seconds per call); each row's
 * mapping is then a MAX lookup over that small in-memory set.
 */
import { queryRows } from "../lib/db.js";
import { querySqlView, renderSqlView, sqlFrag } from "../lib/sql-views.js";
import type { QueryResultRow } from "pg";
import type {
  AiCalendarResponse,
  AiQaDetail,
  AiQaItem,
  AiQaItemsResponse,
  AiQaRefItem,
} from "@shared/types";
import { cachedRows } from "./classification-cache.js";
import { segmentSearchTerms, splitSearchTerms } from "./search-query.service.js";

// ---------------------------------------------------------------------------
// Canonical industry catalog — DISTINCT (sector, industry) pairs
// (same catalog query as news.service; separate cache key, same TTL cache)
// ---------------------------------------------------------------------------

interface CatalogRow extends QueryResultRow {
  sector_id: string;
  industry_id: string;
}

async function listCatalogRows(): Promise<CatalogRow[]> {
  // The SELECT lives in the sql view
  // data_viz/api/services/ai/catalog_pairs.
  return cachedRows("ai:catalog", () =>
    querySqlView<CatalogRow>("data_viz/api/services/ai/catalog_pairs"),
  );
}

/** All industry_ids belonging to one sector (for L1-only scoping). */
async function getSectorIndustryIds(sectorId: string): Promise<string[]> {
  const rows = await listCatalogRows();
  return rows.filter((r) => r.sector_id === sectorId).map((r) => r.industry_id);
}

// ---------------------------------------------------------------------------
// Shared filter SQL fragments
// ---------------------------------------------------------------------------

interface AiFilters {
  sectorId?: string | null;
  industryId?: string | null;
  /** Explicit industry-id SET (a strategy/theme pick). PRESENT but empty
   *  means the pick resolved to no industries (e.g. a broad-market index
   *  family) → NO rows; absent (null/undefined) leaves the scope to the
   *  other filters. */
  industryIds?: string[] | null;
  search?: string | null;
  /** true = only Q&A rows whose mapped industry had a big market move on the
   *  row's Shanghai qa_date day (llm.llm_qa.is_big_fluctuation — the
   *  builds.text big_market_mv pass: |day change| >= 3% AND RSI(3) pinned,
   *  5-td cooldown per industry). */
  bigFluctuationOnly?: boolean;
}

async function buildFilterSql(
  filters: AiFilters,
  params: unknown[],
): Promise<string> {
  const clauses: string[] = [];
  if (filters.industryId) {
    params.push(filters.industryId);
    clauses.push(`q.industry_id = $${params.length}`);
  } else if (filters.industryIds != null) {
    if (filters.industryIds.length === 0) return "1=0"; // pick has no industries — no rows, not all rows
    params.push(filters.industryIds);
    clauses.push(`q.industry_id = ANY($${params.length}::text[])`);
  } else if (filters.sectorId) {
    // Sector scope: rows tagged at sector granularity match directly; rows
    // stored before sector_id was backfilled (sector_id IS NULL) still
    // surface through their industry's membership in the sector.
    const ids = await getSectorIndustryIds(filters.sectorId);
    params.push(filters.sectorId);
    const sectorP = `$${params.length}`;
    if (ids.length === 0) {
      clauses.push(`q.sector_id = ${sectorP}`);
    } else {
      params.push(ids);
      clauses.push(
        `(q.sector_id = ${sectorP} OR (q.sector_id IS NULL AND q.industry_id = ANY($${params.length}::text[])))`,
      );
    }
  }
  // Inactive rows never surface (no opt-out — the feed is active-only).
  clauses.push(`q.is_active`);
  if (filters.bigFluctuationOnly) {
    clauses.push(`q.is_big_fluctuation`);
  }
  // Keyword search: every term must hit the GIN-indexed search_tsv (the
  // StructBERT-segmented full-text vector — the recall path for Chinese)
  // OR question/answer as a raw substring (substrings inside segmented
  // words + the segmentation-degraded fallback). AND across terms.
  const terms = splitSearchTerms(filters.search);
  if (terms.length > 0) {
    const segmented = await segmentSearchTerms(terms.join(" "));
    const termClauses = terms.map((term, i) => {
      params.push(`%${term}%`);
      const p = `$${params.length}`;
      const tokens = segmented?.[i];
      if (!tokens) {
        return `(q.question ILIKE ${p} OR q.answer ILIKE ${p})`;
      }
      params.push(tokens);
      const q = `$${params.length}`;
      return (
        `(q.search_tsv @@ websearch_to_tsquery('simple', ${q})` +
        ` OR q.question ILIKE ${p} OR q.answer ILIKE ${p})`
      );
    });
    clauses.push(termClauses.join(" AND "));
  }
  // Bare conditions — callers add the WHERE keyword themselves.
  return clauses.join(" AND ");
}

// ---------------------------------------------------------------------------
// Trading-day mapping — computed once per query.
//
// The scoped queries (calendar / items) share one CTE shape, kept in the
// sql views data_viz/api/services/ai/{calendar,date_bounds,items_count,
// items}: `td` holds the DISTINCT trading dates of
// stats.industry_basic_stats (MATERIALIZED: computed once, not per
// correlated reference); `q` applies the caller's scope/search filters;
// `m` maps each row to the latest trading day on or before its
// Asia/Shanghai day (weekends/holidays roll back), so the calendar strip,
// the day filter and the per-card dates all sit on the same dates as the
// trend charts.
// ---------------------------------------------------------------------------

export async function listAiCalendar(
  filters: AiFilters,
): Promise<AiCalendarResponse> {
  const params: unknown[] = [];
  const conditions = await buildFilterSql(filters, params);
  if (conditions === "1=0") {
    return { days: [], min_date: null, max_date: null };
  }
  // The SELECTs live in the sql views
  // data_viz/api/services/ai/calendar and
  // data_viz/api/services/ai/date_bounds (where_clause rides a fragment;
  // its params stay caller-assembled).
  const rows = await queryRows<{ date: string; count: string } & QueryResultRow>(
    renderSqlView("data_viz/api/services/ai/calendar", {
      where_clause: sqlFrag(conditions),
    }).sql,
    params,
  );
  const bounds = await queryRows<
    { min_date: string | null; max_date: string | null } & QueryResultRow
  >(
    renderSqlView("data_viz/api/services/ai/date_bounds", {
      where_clause: sqlFrag(conditions),
    }).sql,
    params,
  );
  return {
    days: rows.map((r) => ({ date: r.date, count: Number(r.count) })),
    min_date: bounds[0]?.min_date ?? null,
    max_date: bounds[0]?.max_date ?? null,
  };
}

// ---------------------------------------------------------------------------
// Items — one page of Q&A rows
// ---------------------------------------------------------------------------

export async function listAiItems(
  filters: AiFilters & {
    date?: string | null;
    /** Inclusive trade-day RANGE (date_from ≤ trade_day ≤ date_to) — used
     *  instead of the single `date` pick when the caller wants a window
     *  around the picked day. */
    dateFrom?: string | null;
    dateTo?: string | null;
    limit?: number | null;
    offset?: number | null;
  },
): Promise<AiQaItemsResponse> {
  const params: unknown[] = [];
  const conditions = await buildFilterSql(filters, params);
  if (conditions === "1=0") {
    return { total: 0, items: [] };
  }
  // Trade-day filter fragment (caller-built — carries its params).
  const dateFilter = filters.date
    ? `WHERE trade_day = $${params.push(filters.date)}::date`
    : filters.dateFrom && filters.dateTo
      ? `WHERE trade_day BETWEEN $${params.push(filters.dateFrom)}::date AND $${params.push(filters.dateTo)}::date`
      : "";

  const totalRows = await queryRows<{ n: string } & QueryResultRow>(
    renderSqlView("data_viz/api/services/ai/items_count", {
      where_clause: sqlFrag(conditions),
      date_clause: sqlFrag(dateFilter),
    }).sql,
    params,
  );
  const total = Number(totalRows[0]?.n ?? 0);

  const limit = Math.min(Math.max(filters.limit ?? 20, 1), 100);
  const offset = Math.max(filters.offset ?? 0, 0);
  const items = await queryRows<AiQaItem & QueryResultRow>(
    renderSqlView("data_viz/api/services/ai/items", {
      where_clause: sqlFrag(conditions),
      date_clause: sqlFrag(dateFilter),
      limit: sqlFrag(`${limit}`),
      offset: sqlFrag(`${offset}`),
    }).sql,
    params,
  );
  return { total, items };
}

// ---------------------------------------------------------------------------
// QA detail — full row + per-ref resolutions joined back to their articles
// (the feed card's click-to-expand fetch). Each ref carries a 4000-char
// content preview for the inline expansion — exact rows can hold ~50KB of
// fetched page text, too much for one detail payload times every ref.
//
// REF DATES — the DB carries the truth, populated by the Python writers
// (llm_agents / builds.text): llm.llm_qa_refs.ref_time is the ref's
// ACTUAL publish time (the search response's date, else the URL-embedded
// date) and NULL means unknown — a now()/qa-date fallback used to be
// written there and displayed as the article's date. `date` below is
// ref_time's Asia/Shanghai day; null renders nothing on the card. Refs
// whose known date falls outside the QA's interest window
// [interest day − 20d, interest day] (qa_date's Shanghai day — the date
// the question is about, not the ask time) are not stored at all.
// ---------------------------------------------------------------------------

export async function getAiQa(qaId: number): Promise<AiQaDetail | null> {
  // The SELECTs live in the sql views
  // data_viz/api/services/ai/qa_detail and
  // data_viz/api/services/ai/qa_refs.
  const rows = await querySqlView<Omit<AiQaDetail, "refs"> & QueryResultRow>(
    "data_viz/api/services/ai/qa_detail",
    { qa_id: qaId },
  );
  if (rows.length === 0) return null;
  const refs = await querySqlView<AiQaRefItem & QueryResultRow>(
    "data_viz/api/services/ai/qa_refs",
    { qa_id: qaId },
  );
  return { ...rows[0], refs };
}
