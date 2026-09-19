/**
 * AI service — reads the LLM Q&A knowledge base in the text schema
 * (text.llm_qa + text.llm_qa_refs, written by llm_agents) with the SAME
 * SQL design as the news service:
 *
 *   • industry scope — industry_id when an L2 chip is active; else, for an
 *     L1 sector, rows whose sector_id matches directly (the denormalized
 *     parent sector written by llm_agents) OR — for rows stored before that
 *     column was backfilled — whose industry_id belongs to the sector
 *     (resolved through the stats.sec_classification catalog); an explicit
 *     industryIds set (strategy/theme pick) still filters by the set itself;
 *     else unscoped.
 *   • keyword search — whitespace-separated terms, ALL must appear (AND)
 *     in question OR answer (case-insensitive).
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
import type { QueryResultRow } from "pg";
import type {
  AiCalendarResponse,
  AiQaDetail,
  AiQaItem,
  AiQaItemsResponse,
  AiQaRefItem,
} from "@shared/types";
import { cachedRows } from "./classification-cache.js";

// ---------------------------------------------------------------------------
// Canonical industry catalog — DISTINCT (sector, industry) pairs
// (same catalog query as news.service; separate cache key, same TTL cache)
// ---------------------------------------------------------------------------

interface CatalogRow extends QueryResultRow {
  sector_id: string;
  industry_id: string;
}

async function listCatalogRows(): Promise<CatalogRow[]> {
  return cachedRows("ai:catalog", () =>
    queryRows<CatalogRow>(
      `
      SELECT DISTINCT COALESCE(sector_id, 'OTHER')   AS sector_id,
             COALESCE(industry_id, 'OTHER')          AS industry_id
        FROM stats.sec_classification
      `,
    ),
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
  // Keyword search: every term must hit question OR answer (AND).
  const terms = (filters.search ?? "").trim().split(/\s+/).filter(Boolean);
  const termClauses = terms.map((term) => {
    params.push(`%${term}%`);
    const p = `$${params.length}`;
    return `(q.question ILIKE ${p} OR q.answer ILIKE ${p})`;
  });
  if (termClauses.length > 0) {
    clauses.push(termClauses.join(" AND "));
  }
  // Bare conditions — callers add the WHERE keyword themselves.
  return clauses.join(" AND ");
}

// ---------------------------------------------------------------------------
// Trading-day mapping — computed once per query.
//
// `td` holds the DISTINCT trading dates of stats.industry_basic_stats
// (MATERIALIZED: computed once, not per correlated reference); `q` applies
// the caller's scope/search filters; `m` maps each row to the latest
// trading day on or before its Asia/Shanghai day (weekends/holidays roll
// back), so the calendar strip, the day filter and the per-card dates all
// sit on the same dates as the trend charts.
// ---------------------------------------------------------------------------

const baseCte = (where: string): string => `
  WITH td AS MATERIALIZED (
    SELECT DISTINCT date FROM stats.industry_basic_stats
  ), q AS (
    SELECT q.*, (q.qa_date AT TIME ZONE 'Asia/Shanghai')::date AS qa_day
      FROM text.llm_qa q
     ${where ? "WHERE " + where : ""}
  ), m AS (
    SELECT q.*, COALESCE(
             (SELECT MAX(td.date) FROM td WHERE td.date <= q.qa_day),
             q.qa_day) AS trade_day
      FROM q
  )`;

// ---------------------------------------------------------------------------
// Calendar — per-day QA counts for the date-event strip's dots
// ---------------------------------------------------------------------------

export async function listAiCalendar(
  filters: AiFilters,
): Promise<AiCalendarResponse> {
  const params: unknown[] = [];
  const conditions = await buildFilterSql(filters, params);
  if (conditions === "1=0") {
    return { days: [], min_date: null, max_date: null };
  }
  const base = baseCte(conditions);
  const rows = await queryRows<{ date: string; count: string } & QueryResultRow>(
    `${base}
     SELECT trade_day::text AS date, COUNT(*)::text AS count
       FROM m
      GROUP BY trade_day
      ORDER BY trade_day`,
    params,
  );
  const bounds = await queryRows<
    { min_date: string | null; max_date: string | null } & QueryResultRow
  >(
    `${base}
     SELECT MIN(trade_day)::text AS min_date, MAX(trade_day)::text AS max_date
       FROM m`,
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

const QA_LIST_SELECT = `
  SELECT m.qa_id, m.question, m.industry_id, m.sector_id, m.category,
         m.llm_model, m.language, m.trade_day::text AS date,
         (m.updated_at AT TIME ZONE 'Asia/Shanghai')::text AS updated_at,
         LEFT(m.answer, 240) AS answer_snippet,
         (SELECT COUNT(*)::int FROM text.news_group_items gi
           WHERE gi.news_group_id = m.news_group_id) AS n_sources`;

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
  const base = baseCte(conditions);
  const dateFilter = filters.date
    ? `WHERE trade_day = $${params.push(filters.date)}::date`
    : filters.dateFrom && filters.dateTo
      ? `WHERE trade_day BETWEEN $${params.push(filters.dateFrom)}::date AND $${params.push(filters.dateTo)}::date`
      : "";

  const totalRows = await queryRows<{ n: string } & QueryResultRow>(
    `${base}
     SELECT COUNT(*)::text AS n FROM m ${dateFilter}`,
    params,
  );
  const total = Number(totalRows[0]?.n ?? 0);

  const limit = Math.min(Math.max(filters.limit ?? 20, 1), 100);
  const offset = Math.max(filters.offset ?? 0, 0);
  const items = await queryRows<AiQaItem & QueryResultRow>(
    `${base}
     ${QA_LIST_SELECT}
      FROM m
      ${dateFilter}
      ORDER BY trade_day DESC, qa_id DESC
      LIMIT ${limit} OFFSET ${offset}`,
    params,
  );
  return { total, items };
}

// ---------------------------------------------------------------------------
// QA detail — full row + per-ref resolutions joined back to their articles
// (the feed card's click-to-expand fetch). Each ref carries a 4000-char
// content preview for the inline expansion — exact rows can hold ~50KB of
// fetched page text, too much for one detail payload times every ref.
// ---------------------------------------------------------------------------

export async function getAiQa(qaId: number): Promise<AiQaDetail | null> {
  const rows = await queryRows<Omit<AiQaDetail, "refs"> & QueryResultRow>(
    `WITH td AS MATERIALIZED (
       SELECT DISTINCT date FROM stats.industry_basic_stats
     ), q AS (
       SELECT q.*, (q.qa_date AT TIME ZONE 'Asia/Shanghai')::date AS qa_day
         FROM text.llm_qa q
        WHERE q.qa_id = $1
     ), m AS (
       SELECT q.*, COALESCE(
                (SELECT MAX(td.date) FROM td WHERE td.date <= q.qa_day),
                q.qa_day) AS trade_day
         FROM q
     )
    SELECT m.qa_id, m.question, m.answer, m.context, m.industry_id,
           m.sector_id, m.category, m.llm_model, m.language,
           m.trade_day::text AS date,
           (m.updated_at AT TIME ZONE 'Asia/Shanghai')::text AS updated_at,
           (SELECT COUNT(*)::int FROM text.news_group_items gi
             WHERE gi.news_group_id = m.news_group_id) AS n_sources,
           (SELECT ROUND(AVG(d.sentiment_level)::numeric, 2)::float
              FROM text.llm_qa_refs r
              JOIN text.news_digestions d ON d.news_id = r.news_id
             WHERE r.qa_id = m.qa_id AND d.sentiment_level IS NOT NULL)
             AS avg_ref_sentiment
      FROM m`,
    [qaId],
  );
  if (rows.length === 0) return null;
  const refs = await queryRows<AiQaRefItem & QueryResultRow>(
    `SELECT r.ref, r.ref_type, r.resolved_via, r.resolved_url, r.is_used,
           (r.ref_time AT TIME ZONE 'Asia/Shanghai')::text AS ref_time,
           n.news_id, n.title, n.source, n.date::text AS date, n.url,
           LEFT(n.content, 4000) AS content
      FROM text.llm_qa_refs r
      JOIN text.news n ON n.news_id = r.news_id
     WHERE r.qa_id = $1
     ORDER BY r.ref`,
    [qaId],
  );
  return { ...rows[0], refs };
}
