/**
 * AI ask-history service — reads the persisted interactive AI-ask tables
 * (text.llm_qa_by_ask + text.llm_qa_ask_context +
 * text.llm_qa_ask_context_images + text.llm_qa_keywords_by_ask +
 * multi_media.src_images, written by llm_agents.llm_ask.storage) for the
 * AiPage "QA by Ask" feed. Same SQL design as ai.service.ts:
 *
 *   • scope — a picked code filters llm_qa_by_ask.code directly (the ask's
 *     primary instrument); industry/sector scopes filter the 1:1 context
 *     row's denormalized industry_id/sector_id (L1 sectors also surface
 *     industry-tagged rows whose sector column is NULL, like ai.service).
 *   • keyword search — whitespace-separated terms, ALL must appear (AND);
 *     each term hits question OR answer ILIKE OR one derived keyword row
 *     (text.llm_qa_keywords_by_ask — codes, product, in-plot items, series
 *     names), so a chart-term search finds asks whose prose never mentions
 *     the term.
 *   • status — inactive rows never surface; failed asks (status='failed')
 *     DO surface with their error tail as the snippet.
 *
 * The ask "date" is ask_date (when the modal submitted) bucketed to
 * Asia/Shanghai days and mapped to the latest TRADING day on or before it
 * (same MATERIALIZED td CTE as ai.service.ts — see the performance note
 * there).
 */
import { queryRows } from "../lib/db.js";
import type { QueryResultRow } from "pg";
import type {
  AiAskHistoryDetail,
  AiAskHistoryImage,
  AiAskHistoryItem,
  AiAskHistoryItemsResponse,
  AiCalendarResponse,
} from "@shared/types";
import { cachedRows } from "./classification-cache.js";

// ---------------------------------------------------------------------------
// Shared filter SQL fragments (ai.service.ts idioms, against the ask tables)
// ---------------------------------------------------------------------------

interface AskHistoryFilters {
  sectorId?: string | null;
  industryId?: string | null;
  /** Explicit industry-id SET (a strategy/theme pick). PRESENT but empty
   * means the pick resolved to no industries → NO rows; absent leaves the
   * scope to the other filters. */
  industryIds?: string[] | null;
  /** Picked security (code search / L3 chip) — filters the ask's primary
   * code directly. Present-but-empty is not a filter. */
  code?: string | null;
  search?: string | null;
}

async function buildAskFilterSql(
  filters: AskHistoryFilters,
  params: unknown[],
): Promise<string> {
  const clauses: string[] = [];
  if (filters.code) {
    params.push(filters.code);
    clauses.push(`q.code = $${params.length}`);
  } else if (filters.industryId) {
    params.push(filters.industryId);
    clauses.push(`c.industry_id = $${params.length}`);
  } else if (filters.industryIds != null) {
    if (filters.industryIds.length === 0) return "1=0"; // pick has no industries — no rows
    params.push(filters.industryIds);
    clauses.push(`c.industry_id = ANY($${params.length}::text[])`);
  } else if (filters.sectorId) {
    const rows = await cachedRows<{ industry_id: string } & QueryResultRow>(
      "ai:catalog",
      () =>
        queryRows<{ sector_id: string; industry_id: string } & QueryResultRow>(
          `SELECT DISTINCT COALESCE(sector_id, 'OTHER')   AS sector_id,
                  COALESCE(industry_id, 'OTHER')          AS industry_id
             FROM stats.sec_classification`,
        ),
    );
    const ids = rows
      .filter((r) => r.sector_id === filters.sectorId)
      .map((r) => r.industry_id);
    params.push(filters.sectorId);
    const sectorP = `$${params.length}`;
    if (ids.length === 0) {
      clauses.push(`c.sector_id = ${sectorP}`);
    } else {
      params.push(ids);
      clauses.push(
        `(c.sector_id = ${sectorP} OR (c.sector_id IS NULL AND c.industry_id = ANY($${params.length}::text[])))`,
      );
    }
  }
  // Inactive rows never surface (soft-delete parity with text.llm_qa).
  clauses.push(`q.is_active`);
  // Keyword search: every term must hit question OR answer OR a derived
  // keyword row (AND across terms, OR within one term).
  const terms = (filters.search ?? "").trim().split(/\s+/).filter(Boolean);
  const termClauses = terms.map((term) => {
    params.push(`%${term}%`);
    const p = `$${params.length}`;
    return (
      `(q.question ILIKE ${p} OR q.answer ILIKE ${p} OR ` +
      `EXISTS (SELECT 1 FROM text.llm_qa_keywords_by_ask k ` +
      `WHERE k.ask_id = q.ask_id AND k.keyword ILIKE ${p}))`
    );
  });
  if (termClauses.length > 0) {
    clauses.push(termClauses.join(" AND "));
  }
  // Bare conditions — callers add the WHERE keyword themselves.
  return clauses.join(" AND ");
}

// ---------------------------------------------------------------------------
// Trading-day mapping — same MATERIALIZED td CTE as ai.service.ts; `q` is
// the ask row LEFT JOIN its 1:1 context row (scope columns), `m` maps each
// ask to the latest trading day on or before its Asia/Shanghai day.
// ---------------------------------------------------------------------------

const baseCte = (where: string): string => `
  WITH td AS MATERIALIZED (
    SELECT DISTINCT date FROM stats.industry_basic_stats
  ), q AS (
    SELECT q.*, (q.ask_date AT TIME ZONE 'Asia/Shanghai')::date AS ask_day,
           c.industry_id AS ctx_industry_id, c.sector_id AS ctx_sector_id,
           c.n_images
      FROM text.llm_qa_by_ask q
      LEFT JOIN text.llm_qa_ask_context c ON c.ask_id = q.ask_id
     ${where ? "WHERE " + where : ""}
  ), m AS (
    SELECT q.*, COALESCE(
             (SELECT MAX(td.date) FROM td WHERE td.date <= q.ask_day),
             q.ask_day) AS trade_day
      FROM q
  )`;

// ---------------------------------------------------------------------------
// Calendar — per-day ask counts for the date-event strip's dots
// ---------------------------------------------------------------------------

export async function listAskHistoryCalendar(
  filters: AskHistoryFilters,
): Promise<AiCalendarResponse> {
  const params: unknown[] = [];
  const conditions = await buildAskFilterSql(filters, params);
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
// Items — one page of ask rows
// ---------------------------------------------------------------------------

export async function listAskHistoryItems(
  filters: AskHistoryFilters & {
    date?: string | null;
    dateFrom?: string | null;
    dateTo?: string | null;
    limit?: number | null;
    offset?: number | null;
  },
): Promise<AiAskHistoryItemsResponse> {
  const params: unknown[] = [];
  const conditions = await buildAskFilterSql(filters, params);
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
  const items = await queryRows<AiAskHistoryItem & QueryResultRow>(
    `${base}
     SELECT m.ask_id, m.question, m.code, m.product, m.status,
            m.online_search, m.llm_model, m.language,
            m.trade_day::text AS date,
            (m.updated_at AT TIME ZONE 'Asia/Shanghai')::text AS updated_at,
            LEFT(COALESCE(m.answer, m.error_tail), 240) AS answer_snippet,
            COALESCE(m.n_images, 0)::int AS n_images,
            (SELECT COUNT(*)::int FROM text.llm_qa_keywords_by_ask k
              WHERE k.ask_id = m.ask_id) AS n_keywords
      FROM m
      ${dateFilter}
      ORDER BY trade_day DESC, ask_id DESC
      LIMIT ${limit} OFFSET ${offset}`,
    params,
  );
  return { total, items };
}

// ---------------------------------------------------------------------------
// Ask detail — full row + context summary + keywords + image list (ids and
// metadata only — the bytes come from getAskHistoryImage on demand).
// ---------------------------------------------------------------------------

export async function getAskHistoryDetail(
  askId: number,
): Promise<AiAskHistoryDetail | null> {
  const rows = await queryRows<
    Omit<AiAskHistoryDetail, "keywords" | "images"> & QueryResultRow
  >(
    `WITH td AS MATERIALIZED (
       SELECT DISTINCT date FROM stats.industry_basic_stats
     ), q AS (
       SELECT a.*, (a.ask_date AT TIME ZONE 'Asia/Shanghai')::date AS ask_day,
              c.chart_kind, c.chart_title, c.page,
              c.industry_id AS ctx_industry_id, c.sector_id AS ctx_sector_id,
              c.window_start, c.window_end, c.n_images
         FROM text.llm_qa_by_ask a
         LEFT JOIN text.llm_qa_ask_context c ON c.ask_id = a.ask_id
        WHERE a.ask_id = $1
     ), m AS (
       SELECT q.*, COALESCE(
                (SELECT MAX(td.date) FROM td WHERE td.date <= q.ask_day),
                q.ask_day) AS trade_day
         FROM q
     )
    SELECT m.ask_id, m.question, m.answer, m.error_tail, m.status,
           m.online_search, m.search_query, m.provider, m.llm_model,
           m.language, m.code, m.product,
           m.trade_day::text AS date,
           (m.updated_at AT TIME ZONE 'Asia/Shanghai')::text AS updated_at,
           COALESCE(m.n_images, 0)::int AS n_images,
           (SELECT COUNT(*)::int FROM text.llm_qa_keywords_by_ask k
             WHERE k.ask_id = m.ask_id) AS n_keywords,
           m.chart_kind, m.chart_title, m.page,
           m.ctx_industry_id AS industry_id, m.ctx_sector_id AS sector_id,
           m.window_start, m.window_end
      FROM m`,
    [askId],
  );
  if (rows.length === 0) return null;
  const keywords = await queryRows<{ keyword: string; kind: string } & QueryResultRow>(
    `SELECT keyword, kind
       FROM text.llm_qa_keywords_by_ask
      WHERE ask_id = $1
      ORDER BY kind, keyword`,
    [askId],
  );
  const images = await queryRows<AiAskHistoryImage & QueryResultRow>(
    `SELECT i.image_id, ci.position, i.mime_type, i.byte_size,
           i.width, i.height, i.label
      FROM text.llm_qa_ask_context_images ci
      JOIN multi_media.src_images i ON i.image_id = ci.image_id
     WHERE ci.ask_id = $1
     ORDER BY ci.position`,
    [askId],
  );
  return { ...rows[0], keywords, images };
}

// ---------------------------------------------------------------------------
// Image bytes — the thumbnail/full-image source behind /api/ai/ask-image
// ---------------------------------------------------------------------------

export async function getAskHistoryImage(
  imageId: number,
): Promise<{ mime_type: string; data: Buffer } | null> {
  const rows = await queryRows<{ mime_type: string; data: Buffer } & QueryResultRow>(
    `SELECT mime_type, data FROM multi_media.src_images WHERE image_id = $1`,
    [imageId],
  );
  if (rows.length === 0) return null;
  return { mime_type: rows[0].mime_type, data: rows[0].data };
}
