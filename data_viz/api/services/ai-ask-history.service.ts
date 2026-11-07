/**
 * AI ask-history service — reads the persisted interactive AI-ask tables
 * (llm.llm_qa_by_ask + llm.llm_qa_ask_context +
 * llm.llm_qa_ask_context_images + llm.llm_qa_keywords_by_ask +
 * multi_media.src_images, written by llm_agents.llm_ask.storage) for the
 * AiPage "QA by Ask" feed. Same SQL design as ai.service.ts:
 *
 *   • scope — a picked code filters llm_qa_by_ask.code directly (the ask's
 *     primary instrument); industry/sector scopes filter the 1:1 context
 *     row's denormalized industry_id/sector_id (L1 sectors also surface
 *     industry-tagged rows whose sector column is NULL, like ai.service).
 *   • keyword search — whitespace-separated terms, ALL must appear (AND);
 *     each term hits question OR answer ILIKE OR one derived keyword row
 *     (llm.llm_qa_keywords_by_ask — codes, product, in-plot items, series
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
import { querySqlView, renderSqlView, sqlFrag } from "../lib/sql-views.js";
import type { QueryResultRow } from "pg";
import type {
  AiAskHistoryDetail,
  AiAskHistoryImage,
  AiAskHistoryItem,
  AiAskHistoryItemsResponse,
  AiCalendarResponse,
} from "@shared/types";
import { cachedRows } from "./classification-cache.js";
import { segmentSearchTerms, splitSearchTerms } from "./search-query.service.js";

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
        // The SELECT lives in the sql view
        // data_viz/api/services/ai-ask-history/catalog_pairs.
        querySqlView<{ sector_id: string; industry_id: string } & QueryResultRow>(
          "data_viz/api/services/ai-ask-history/catalog_pairs",
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
  // Inactive rows never surface (soft-delete parity with llm.llm_qa).
  clauses.push(`q.is_active`);
  // Keyword search: every term must hit the GIN-indexed search_tsv (the
  // StructBERT-segmented full-text vector — the recall path for Chinese)
  // OR question/answer OR a derived keyword row (AND across terms, OR
  // within one term). Same search-query.service segmentation as ai.service.
  const terms = splitSearchTerms(filters.search);
  if (terms.length > 0) {
    const segmented = await segmentSearchTerms(terms.join(" "));
    const termClauses = terms.map((term, i) => {
      params.push(`%${term}%`);
      const p = `$${params.length}`;
      const parts = [
        `q.question ILIKE ${p}`,
        `q.answer ILIKE ${p}`,
        `EXISTS (SELECT 1 FROM llm.llm_qa_keywords_by_ask k ` +
          `WHERE k.ask_id = q.ask_id AND k.keyword ILIKE ${p})`,
      ];
      const tokens = segmented?.[i];
      if (tokens) {
        params.push(tokens);
        const q = `$${params.length}`;
        parts.unshift(`q.search_tsv @@ websearch_to_tsquery('simple', ${q})`);
      }
      return `(${parts.join(" OR ")})`;
    });
    clauses.push(termClauses.join(" AND "));
  }
  // Bare conditions — callers add the WHERE keyword themselves.
  return clauses.join(" AND ");
}

// ---------------------------------------------------------------------------
// Trading-day mapping — same MATERIALIZED td CTE as ai.service.ts; `q` is
// the ask row LEFT JOIN its 1:1 context row (scope columns), `m` maps each
// ask to the latest trading day on or before its Asia/Shanghai day. Kept
// in the sql views data_viz/api/services/ai-ask-history/{calendar,
// date_bounds,items_count,items}.
// ---------------------------------------------------------------------------

export async function listAskHistoryCalendar(
  filters: AskHistoryFilters,
): Promise<AiCalendarResponse> {
  const params: unknown[] = [];
  const conditions = await buildAskFilterSql(filters, params);
  if (conditions === "1=0") {
    return { days: [], min_date: null, max_date: null };
  }
  // The SELECTs live in the sql views
  // data_viz/api/services/ai-ask-history/calendar and
  // data_viz/api/services/ai-ask-history/date_bounds (where_clause rides a
  // fragment; its params stay caller-assembled).
  const rows = await queryRows<{ date: string; count: string } & QueryResultRow>(
    renderSqlView("data_viz/api/services/ai-ask-history/calendar", {
      where_clause: sqlFrag(conditions),
    }).sql,
    params,
  );
  const bounds = await queryRows<
    { min_date: string | null; max_date: string | null } & QueryResultRow
  >(
    renderSqlView("data_viz/api/services/ai-ask-history/date_bounds", {
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
  // Trade-day filter fragment (caller-built — carries its params).
  const dateFilter = filters.date
    ? `WHERE trade_day = $${params.push(filters.date)}::date`
    : filters.dateFrom && filters.dateTo
      ? `WHERE trade_day BETWEEN $${params.push(filters.dateFrom)}::date AND $${params.push(filters.dateTo)}::date`
      : "";

  const totalRows = await queryRows<{ n: string } & QueryResultRow>(
    renderSqlView("data_viz/api/services/ai-ask-history/items_count", {
      where_clause: sqlFrag(conditions),
      date_clause: sqlFrag(dateFilter),
    }).sql,
    params,
  );
  const total = Number(totalRows[0]?.n ?? 0);

  const limit = Math.min(Math.max(filters.limit ?? 20, 1), 100);
  const offset = Math.max(filters.offset ?? 0, 0);
  const items = await queryRows<AiAskHistoryItem & QueryResultRow>(
    renderSqlView("data_viz/api/services/ai-ask-history/items", {
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
// Ask detail — full row + context summary + keywords + image list (ids and
// metadata only — the bytes come from getAskHistoryImage on demand).
// ---------------------------------------------------------------------------

export async function getAskHistoryDetail(
  askId: number,
): Promise<AiAskHistoryDetail | null> {
  // The SELECTs live in the sql views
  // data_viz/api/services/ai-ask-history/ask_detail,
  // .../ask_keywords and .../ask_images.
  const rows = await querySqlView<
    Omit<AiAskHistoryDetail, "keywords" | "images"> & QueryResultRow
  >(
    "data_viz/api/services/ai-ask-history/ask_detail",
    { ask_id: askId },
  );
  if (rows.length === 0) return null;
  const keywords = await querySqlView<{ keyword: string; kind: string } & QueryResultRow>(
    "data_viz/api/services/ai-ask-history/ask_keywords",
    { ask_id: askId },
  );
  const images = await querySqlView<AiAskHistoryImage & QueryResultRow>(
    "data_viz/api/services/ai-ask-history/ask_images",
    { ask_id: askId },
  );
  return { ...rows[0], keywords, images };
}

// ---------------------------------------------------------------------------
// Image bytes — the thumbnail/full-image source behind /api/ai/ask-image
// ---------------------------------------------------------------------------

export async function getAskHistoryImage(
  imageId: number,
): Promise<{ mime_type: string; data: Buffer } | null> {
  // The SELECT lives in the sql view
  // data_viz/api/services/ai-ask-history/image_bytes.
  const rows = await querySqlView<{ mime_type: string; data: Buffer } & QueryResultRow>(
    "data_viz/api/services/ai-ask-history/image_bytes",
    { image_id: imageId },
  );
  if (rows.length === 0) return null;
  return { mime_type: rows[0].mime_type, data: rows[0].data };
}
