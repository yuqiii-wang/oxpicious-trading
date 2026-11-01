/**
 * AI research service — reads the research-logic Q&A knowledge base
 * (text.llm_qa_by_research, written by llm_agents.llm_qa_by_research's
 * five-request generation flow) with the same SQL design as ai.service
 * (the llm_qa knowledge base):
 *
 *   • same industry-scope + keyword-search + active-only filters;
 *   • the row's "date" is qa_date mapped to the latest trading day on or
 *     before it (the same td CTE contract);
 *   • differences from llm_qa: no is_big_fluctuation (research rows are not
 *     anchored to a price move); answer is the 6th request's SUMMARY (the
 *     display content) with the five evaluation dimensions — intro /
 *     optimistic_forecast / pessimistic_forecast / risks / capitalization —
 *     as the expandable detail; logic_label is a
 *     static.label_dim SMALLINT code (domain research_logic) decoded here at
 *     the response boundary (labelDecodeSql); there is no per-ref table —
 *     the row's news group (news_group_id -> text.news_group_items) IS the
 *     refs mapping, so the detail's sources come from that join.
 */
import { queryRows } from "../lib/db.js";
import type { QueryResultRow } from "pg";
import type {
  AiCalendarResponse,
  AiQaRefItem,
  AiQaResearchDetail,
  AiQaResearchItem,
  AiQaResearchItemsResponse,
  AiQaResearchSource,
} from "@shared/types";
import { cachedRows } from "./classification-cache.js";
import { segmentSearchTerms, splitSearchTerms } from "./search-query.service.js";
import { labelDecodeSql } from "./label-codes.js";

// ---------------------------------------------------------------------------
// Canonical industry catalog (same query/cache contract as ai.service —
// separate cache key).
// ---------------------------------------------------------------------------

interface CatalogRow extends QueryResultRow {
  sector_id: string;
  industry_id: string;
}

async function listCatalogRows(): Promise<CatalogRow[]> {
  return cachedRows("ai-research:catalog", () =>
    queryRows<CatalogRow>(
      `
      SELECT DISTINCT COALESCE(sector_id, 'OTHER')   AS sector_id,
             COALESCE(industry_id, 'OTHER')          AS industry_id
        FROM stats.sec_classification
      `,
    ),
  );
}

async function getSectorIndustryIds(sectorId: string): Promise<string[]> {
  const rows = await listCatalogRows();
  return rows.filter((r) => r.sector_id === sectorId).map((r) => r.industry_id);
}

// ---------------------------------------------------------------------------
// Shared filter SQL fragments (ai.service's shape, minus the
// big-fluctuation chip, plus the logic_label code filter).
// ---------------------------------------------------------------------------

interface AiResearchFilters {
  sectorId?: string | null;
  industryId?: string | null;
  /** Explicit industry-id SET — present but empty means no rows. */
  industryIds?: string[] | null;
  search?: string | null;
  /** research_logic label CODE (static.label_dim; the route encodes the
   *  request's label name). null = all labels. */
  logicLabel?: number | null;
}

async function buildFilterSql(
  filters: AiResearchFilters,
  params: unknown[],
): Promise<string> {
  const clauses: string[] = [];
  if (filters.industryId) {
    params.push(filters.industryId);
    clauses.push(`q.industry_id = $${params.length}`);
  } else if (filters.industryIds != null) {
    if (filters.industryIds.length === 0) return "1=0";
    params.push(filters.industryIds);
    clauses.push(`q.industry_id = ANY($${params.length}::text[])`);
  } else if (filters.sectorId) {
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
  clauses.push(`q.is_active`);
  if (filters.logicLabel != null) {
    params.push(filters.logicLabel);
    clauses.push(`q.logic_label = $${params.length}::smallint`);
  }
  // Keyword search: every term must hit the GIN-indexed search_tsv (the
  // StructBERT-segmented full-text vector — the recall path for Chinese)
  // OR question/answer (the summary) as a raw substring. AND across terms.
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
  return clauses.join(" AND ");
}

// Same trading-day mapping CTE as ai.service (MATERIALIZED td, computed
// once per query — see the performance note there).
const baseCte = (where: string): string => `
  WITH td AS MATERIALIZED (
    SELECT DISTINCT date FROM stats.industry_basic_stats
  ), q AS (
    SELECT q.*, (q.qa_date AT TIME ZONE 'Asia/Shanghai')::date AS qa_day
      FROM text.llm_qa_by_research q
     ${where ? "WHERE " + where : ""}
  ), m AS (
    SELECT q.*, COALESCE(
             (SELECT MAX(td.date) FROM td WHERE td.date <= q.qa_day),
             q.qa_day) AS trade_day
      FROM q
  )`;

// ---------------------------------------------------------------------------
// Calendar — per-day research Q&A counts for the date-event strip's dots
// ---------------------------------------------------------------------------

export async function listAiResearchCalendar(
  filters: AiResearchFilters,
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
// Items — one page of research Q&A rows (the summary is the snippet)
// ---------------------------------------------------------------------------

const RESEARCH_LIST_SELECT = `
  SELECT m.qa_id, m.question, m.industry_id, m.sector_id, m.category,
         ${labelDecodeSql("research_logic", "m.logic_label")} AS logic_label,
         m.llm_model, m.language, m.trade_day::text AS date,
         (m.updated_at AT TIME ZONE 'Asia/Shanghai')::text AS updated_at,
         LEFT(m.answer, 240) AS answer_snippet,
         (SELECT COUNT(*)::int FROM text.news_group_items gi
           WHERE gi.news_group_id = m.news_group_id) AS n_sources`;

export async function listAiResearchItems(
  filters: AiResearchFilters & {
    date?: string | null;
    dateFrom?: string | null;
    dateTo?: string | null;
    limit?: number | null;
    offset?: number | null;
  },
): Promise<AiQaResearchItemsResponse> {
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
  const items = await queryRows<AiQaResearchItem & QueryResultRow>(
    `${base}
     ${RESEARCH_LIST_SELECT}
      FROM m
      ${dateFilter}
      ORDER BY trade_day DESC, qa_id DESC
      LIMIT ${limit} OFFSET ${offset}`,
    params,
  );
  return { total, items };
}

// ---------------------------------------------------------------------------
// Research QA detail — the full row (summary + the five evaluation
// dimensions + the per-dimension reference blocks). The dimension texts'
// inline [来源：ref_N] citations expand per tag (the AiQaRefBox pattern):
// the row's dim_refs column — persisted at store time by the generation
// flow, the by-research analog of text.llm_qa_refs (which FKs text.llm_qa
// only) — is joined back to text.news into per-dimension AiQaRefItem
// arrays. Rows stored before dim_refs existed (null) get null refs and the
// UI renders the texts plain. The merged news group stays as the
// 全部来源 list (sources).
// ---------------------------------------------------------------------------

/** One dim_refs entry as persisted by llm_agents.llm_qa_by_research. */
interface DimRefEntry {
  ref: string;
  news_id: number;
  is_used: boolean;
  ref_time: string | null;
}

const DIMENSION_KEYS = [
  "intro",
  "optimistic_forecast",
  "pessimistic_forecast",
  "risks",
  "capitalization",
] as const;

export async function getAiResearchQa(
  qaId: number,
): Promise<AiQaResearchDetail | null> {
  const rows = await queryRows<Omit<AiQaResearchDetail, "sources" | "dimension_refs"> & QueryResultRow>(
    `WITH td AS MATERIALIZED (
       SELECT DISTINCT date FROM stats.industry_basic_stats
     ), q AS (
       SELECT q.*, (q.qa_date AT TIME ZONE 'Asia/Shanghai')::date AS qa_day
         FROM text.llm_qa_by_research q
        WHERE q.qa_id = $1
     ), m AS (
       SELECT q.*, COALESCE(
                (SELECT MAX(td.date) FROM td WHERE td.date <= q.qa_day),
                q.qa_day) AS trade_day
         FROM q
     )
    SELECT m.qa_id, m.question, m.answer, m.context, m.intro,
           m.optimistic_forecast, m.pessimistic_forecast,
           m.risks, m.capitalization, m.dim_refs,
           m.industry_id, m.sector_id, m.category,
           ${labelDecodeSql("research_logic", "m.logic_label")} AS logic_label,
           m.llm_model, m.language, m.trade_day::text AS date,
           (m.updated_at AT TIME ZONE 'Asia/Shanghai')::text AS updated_at,
           (SELECT COUNT(*)::int FROM text.news_group_items gi
             WHERE gi.news_group_id = m.news_group_id) AS n_sources,
           (SELECT ROUND(AVG(d.sentiment_level)::numeric, 2)::float
              FROM text.news_group_items gi
              JOIN text.news_digestions d ON d.news_id = gi.news_id
             WHERE gi.news_group_id = m.news_group_id
               AND d.sentiment_level IS NOT NULL)
             AS avg_ref_sentiment
      FROM m`,
    [qaId],
  );
  if (rows.length === 0) return null;
  const { dim_refs, ...row } = rows[0];
  const sources = await queryRows<AiQaResearchSource & QueryResultRow>(
    `SELECT n.news_id, n.title, n.source, n.url,
           LEFT(n.content, 4000) AS content,
           (n.date)::text AS date
      FROM text.news_group_items gi
      JOIN text.news n ON n.news_id = gi.news_id
     WHERE gi.news_group_id = (
             SELECT news_group_id FROM text.llm_qa_by_research
              WHERE qa_id = $1)
     ORDER BY gi.news_id`,
    [qaId],
  );
  // Expand dim_refs into per-dimension AiQaRefItem arrays (one join over
  // every referenced article).
  let dimension_refs = null;
  const dimRefs = dim_refs as Record<string, DimRefEntry[]> | null;
  if (dimRefs) {
    const ids = [
      ...new Set(DIMENSION_KEYS.flatMap((k) => (dimRefs[k] ?? []).map((d) => d.news_id))),
    ];
    const news = ids.length
      ? await queryRows<
          { news_id: number; title: string; source: string | null; url: string | null; content: string | null; date: string | null } & QueryResultRow
        >(
          `SELECT news_id, title, source, url, LEFT(content, 4000) AS content,
                  (date)::text AS date
             FROM text.news WHERE news_id = ANY($1::bigint[])`,
          [ids],
        )
      : [];
    const byId = new Map(news.map((n) => [Number(n.news_id), n]));
    // One entry per persisted dim_refs row, joined to its article.
    // Research refs are all search echoes of the dimension's own request:
    // exact + summary provenance by construction. Display date: the ref's
    // actual publish time per the search response (the llm_qa_refs date
    // contract), else the article's stored date.
    const build = (entries: DimRefEntry[]): AiQaRefItem[] =>
      entries.map((d) => {
        const n = byId.get(d.news_id);
        return {
          ref: d.ref,
          ref_type: "exact",
          resolved_via: "summary",
          resolved_url: null,
          is_used: d.is_used,
          ref_time: d.ref_time,
          news_id: d.news_id,
          title: n?.title ?? "",
          source: n?.source ?? null,
          date: d.ref_time ? d.ref_time.slice(0, 10) : (n?.date ?? null),
          url: n?.url ?? null,
          content: n?.content ?? null,
        };
      });
    dimension_refs = {
      intro: build(dimRefs.intro ?? []),
      optimistic_forecast: build(dimRefs.optimistic_forecast ?? []),
      pessimistic_forecast: build(dimRefs.pessimistic_forecast ?? []),
      risks: build(dimRefs.risks ?? []),
      capitalization: build(dimRefs.capitalization ?? []),
    };
  }
  return { ...row, dimension_refs, sources };
}
