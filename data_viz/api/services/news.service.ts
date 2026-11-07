/**
 * News service — reads the text schema populated by builds.text
 * (text.news / text.news_keywords).
 *
 * Three endpoints back the News page (dataviz):
 *   • listNewsThemes()  — L1 sector → L2 industry tree (SectorNode[]) where
 *     industry counts = number of news articles (not securities). Labels/
 *     slugs come from the canonical classification in stats.sec_classification
 *     (DISTINCT industry pairs — the unified (sector_id, industry_id) column
 *     model covers BOTH industry-primary and strategy-primary rows, and news
 *     industry_ids use the same UPPERCASE ids, incl. BROAD_* macro themes).
 *     items[] is always empty — news has no L3 security level.
 *   • listNewsCalendar() — per-day news counts for the date bar's dots,
 *     co-filtered by industry scope + keyword search so the bar reacts to
 *     the classification nav.
 *   • listNewsItems()   — one page of articles for the same filters (+ an
 *     optional single-day pick from the date bar).
 *
 * Filters:
 *   industry scope — industry_id when an L2 chip is active; else, for an L1
 *   sector, rows whose sector_id matches directly (the denormalized parent
 *   sector written by the tagging pipeline) OR — for rows stored before that
 *   column was backfilled — whose industry_id belongs to the sector; else
 *   unscoped.
 *   keyword search — per term: the GIN-indexed search_tsv (Chinese,
 *   StructBERT-segmented on both sides) OR a raw title/content substring;
 *   ALL terms must hit (AND) unless searchAny ORs them.
 */
import { queryRows } from "../lib/db.js";
import { querySqlView, renderSqlView, sqlFrag } from "../lib/sql-views.js";
import type { QueryResultRow } from "pg";
import type {
  NewsComment,
  NewsCommentsResponse,
  NewsDayCount,
  NewsItem,
  NewsItemDetail,
  SectorNode,
} from "@shared/types";
import { cachedRows } from "./classification-cache.js";
import { segmentSearchTerms, splitSearchTerms } from "./search-query.service.js";

// ---------------------------------------------------------------------------
// Canonical industry catalog — DISTINCT (sector, industry) pairs
// ---------------------------------------------------------------------------

interface CatalogRow extends QueryResultRow {
  sector_id: string;
  sector_label: string;
  industry_id: string;
  industry_label: string;
  industry_slug: string;
  is_industry_not_strategy: boolean;
}

/** Full unified-model catalog (industry + strategy rows alike). Cached — the
 *  classification only changes on the nightly build. The SELECT lives in
 *  the sql view data_viz/api/services/news/catalog_rows. */
async function listCatalogRows(): Promise<CatalogRow[]> {
  return cachedRows("news:catalog", () =>
    querySqlView<CatalogRow>("data_viz/api/services/news/catalog_rows"),
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

interface NewsFilters {
  sectorId?: string | null;
  industryId?: string | null;
  /** Explicit industry-id SET (a multi-select / ranked-industries scope).
   *  PRESENT but empty means the pick resolved to no industries → NO rows;
   *  absent (null/undefined) leaves the scope to the other filters. Same
   *  semantics as the AI endpoints' industry_ids. */
  industryIds?: string[] | null;
  search?: string | null;
  /** true = terms are OR-ed ("any token hits") instead of the default AND.
   *  Used by the question bar's local search, whose terms come from the
   *  naive tokenizer (taxonomy keywords extracted from the question). */
  searchAny?: boolean;
  source?: string | null;
  author?: string | null;
  /** true = only articles whose mapped industry had a big market move on the
   *  article's date (text.news.is_big_fluctuation — the builds.text
   *  big_market_mv pass: |day change| >= 3% AND RSI(3) pinned, 5-td
   *  cooldown per industry). */
  bigFluctuationOnly?: boolean;
}

async function buildFilterSql(
  filters: NewsFilters,
  params: unknown[],
): Promise<string> {
  const clauses: string[] = [];
  if (filters.industryId) {
    params.push(filters.industryId);
    clauses.push(`n.industry_id = $${params.length}`);
  } else if (filters.industryIds != null) {
    if (filters.industryIds.length === 0) return "1=0"; // pick has no industries — no rows, not all rows
    params.push(filters.industryIds);
    clauses.push(`n.industry_id = ANY($${params.length}::text[])`);
  } else if (filters.sectorId) {
    // Sector scope: rows tagged at sector granularity match directly; rows
    // stored before sector_id was backfilled (sector_id IS NULL) still
    // surface through their industry's membership in the sector.
    const ids = await getSectorIndustryIds(filters.sectorId);
    params.push(filters.sectorId);
    const sectorP = `$${params.length}`;
    if (ids.length === 0) {
      clauses.push(`n.sector_id = ${sectorP}`);
    } else {
      params.push(ids);
      clauses.push(
        `(n.sector_id = ${sectorP} OR (n.sector_id IS NULL AND n.industry_id = ANY($${params.length}::text[])))`,
      );
    }
  }
  if (filters.source) {
    params.push(filters.source);
    clauses.push(`n.source = $${params.length}`);
  }
  if (filters.author) {
    params.push(filters.author);
    clauses.push(`n.author = $${params.length}`);
  }
  if (filters.bigFluctuationOnly) {
    clauses.push(`n.is_big_fluctuation`);
  }
  // Keyword search: every term must hit the GIN-indexed search_tsv (the
  // StructBERT-segmented full-text vector — the recall path for Chinese,
  // which raw ILIKE tokens can't segment) OR title/content as a raw
  // substring (the pre-GIN path, kept for substrings inside segmented
  // words and for the segmentation-degraded fallback). AND across terms,
  // or — with searchAny — a single clause matching ANY term (OR across
  // terms). Query segmentation comes from the shared CLI via
  // search-query.service (cached per search string).
  const terms = splitSearchTerms(filters.search);
  if (terms.length > 0) {
    const segmented = await segmentSearchTerms(terms.join(" "));
    const termClauses = terms.map((term, i) => {
      params.push(`%${term}%`);
      const p = `$${params.length}`;
      const tokens = segmented?.[i];
      if (!tokens) {
        return `(n.title ILIKE ${p} OR n.content ILIKE ${p})`;
      }
      params.push(tokens);
      const q = `$${params.length}`;
      return (
        `(n.search_tsv @@ websearch_to_tsquery('simple', ${q})` +
        ` OR n.title ILIKE ${p} OR n.content ILIKE ${p})`
      );
    });
    clauses.push(
      filters.searchAny && termClauses.length > 1
        ? `(${termClauses.join(" OR ")})`
        : termClauses.join(" AND "),
    );
  }
  // Bare conditions — callers add the WHERE keyword themselves.
  return clauses.join(" AND ");
}

// ---------------------------------------------------------------------------
// Themes — sector → industry tree with news counts
// ---------------------------------------------------------------------------

/** Cached article count per industry_id, optionally scoped to one source /
 *  author (refreshed more often than the catalog: builds.text runs daily). */
async function getNewsCountByIndustry(
  source?: string | null,
  author?: string | null,
): Promise<Map<string, number>> {
  const key = `news:counts-by-industry:${source ?? "all"}:${author ?? "all"}`;
  const rows = await cachedRows<{ industry_id: string; n: string } & QueryResultRow>(
    key,
    () => {
      const params: unknown[] = [];
      const clauses: string[] = [];
      if (source) {
        params.push(source);
        clauses.push(`source = $${params.length}`);
      }
      if (author) {
        params.push(author);
        clauses.push(`author = $${params.length}`);
      }
      // The SELECT lives in the sql view
      // data_viz/api/services/news/count_by_industry (where_clause rides a
      // fragment; its params stay caller-assembled).
      return queryRows(
        renderSqlView("data_viz/api/services/news/count_by_industry", {
          where_clause: sqlFrag(
            clauses.length ? `WHERE ${clauses.join(" AND ")}` : "",
          ),
        }).sql,
        params,
      );
    },
  );
  return new Map(rows.map((r) => [r.industry_id, Number(r.n)]));
}

/**
 * Build one nav tree (SectorNode[]) from the catalog rows whose
 * is_industry_not_strategy flag matches *industryPrimary*, with chip counts =
 * news counts (scoped to *source* when given). LEFT column (sector →
 * industry) uses industry-primary rows; the RIGHT column (strategy → theme)
 * reuses the SAME builder with industryPrimary=false.
 *
 * Every catalog industry of the column stays visible even with zero matching
 * articles — a source/author filter empties a chip to (0) instead of
 * hiding it. Items tagged outside the catalog surface in the Unclassified
 * bucket (industry column only).
 */
async function buildNewsTree(
  industryPrimary: boolean,
  source?: string | null,
  author?: string | null,
): Promise<SectorNode[]> {
  const [rows, counts] = await Promise.all([
    listCatalogRows(),
    getNewsCountByIndustry(source, author),
  ]);
  const catalogByIndustry = new Map(
    rows.filter((r) => r.is_industry_not_strategy === industryPrimary)
        .map((r) => [r.industry_id, r]),
  );

  const sectorMap = new Map<string, {
    sector_label: string;
    industries: Map<string, { label: string; slug: string; count: number }>;
  }>();
  let unclassified = 0;

  // All catalog industries of this column, zero-count included — catalog
  // order is preserved inside each sector (final sort below is count-desc).
  for (const cat of catalogByIndustry.values()) {
    const count = counts.get(cat.industry_id) ?? 0;
    if (!sectorMap.has(cat.sector_id)) {
      sectorMap.set(cat.sector_id, {
        sector_label: cat.sector_label,
        industries: new Map(),
      });
    }
    const sector = sectorMap.get(cat.sector_id)!;
    const ind = sector.industries.get(cat.industry_id) ?? {
      label: cat.industry_label,
      slug: cat.industry_slug,
      count: 0,
    };
    ind.count += count;
    sector.industries.set(cat.industry_id, ind);
  }

  // Articles tagged with an industry_id outside this column's catalog (or
  // outside the catalog entirely) — keep the count visible under an
  // Unclassified bucket so nothing silently disappears from the nav.
  for (const [industryId, count] of counts) {
    if (count <= 0) continue;
    if (!catalogByIndustry.has(industryId)) {
      unclassified += count;
    }
  }

  const sectors: SectorNode[] = Array.from(sectorMap.entries()).map(
    ([sector_id, sector]) => ({
      sector_id,
      sector_label: sector.sector_label,
      count: Array.from(sector.industries.values()).reduce(
        (sum, i) => sum + i.count,
        0,
      ),
      industries: Array.from(sector.industries.entries())
        .map(([industry_id, i]) => ({
          industry_id,
          industry_label: i.label,
          industry_slug: i.slug,
          count: i.count,
          items: [], // news has no L3 security level
        }))
        .sort((a, b) => b.count - a.count),
    }),
  );
  sectors.sort((a, b) => b.count - a.count);

  // The Unclassified bucket is only meaningful in the LEFT (industry) column:
  // an industry-tagged article has no strategy classification, and counting
  // it as "unclassified strategy" would swamp the strategy tree.
  if (unclassified > 0 && industryPrimary) {
    sectors.push({
      sector_id: "OTHER",
      sector_label: "未分类",
      count: unclassified,
      industries: [
        {
          industry_id: "OTHER",
          industry_label: "未分类",
          industry_slug: "other",
          count: unclassified,
          items: [],
        },
      ],
    });
  }
  return sectors;
}

/** LEFT column — sector → industry tree (is_industry_not_strategy=TRUE). */
export function listNewsThemes(
  source?: string | null,
  author?: string | null,
): Promise<SectorNode[]> {
  return buildNewsTree(true, source, author);
}

/** RIGHT column — parallel strategy → theme tree (is_industry_not_strategy=FALSE). */
export function listNewsStrategyThemes(
  source?: string | null,
  author?: string | null,
): Promise<SectorNode[]> {
  return buildNewsTree(false, source, author);
}

/**
 * Top authors for the author filter chips — scoped by everything EXCEPT the
 * author itself (selecting an author narrows the other chips, not this list).
 */
export async function listNewsAuthors(
  filters: Omit<NewsFilters, "author">,
): Promise<Array<{ author: string; count: number }>> {
  const params: unknown[] = [];
  const conditions = await buildFilterSql(filters, params);
  if (conditions === "1=0") return [];
  // The SELECT lives in the sql view data_viz/api/services/news/authors
  // (where_clause rides a fragment; its params stay caller-assembled).
  const whereClause = conditions
    ? `${conditions} AND n.author IS NOT NULL`
    : "n.author IS NOT NULL";
  const rows = await queryRows<{ author: string; n: string } & QueryResultRow>(
    renderSqlView("data_viz/api/services/news/authors", {
      where_clause: sqlFrag(whereClause),
    }).sql,
    params,
  );
  return rows.map((r) => ({ author: r.author, count: Number(r.n) }));
}

// ---------------------------------------------------------------------------
// Calendar — per-day counts for the date-bar dots
// ---------------------------------------------------------------------------

export async function listNewsCalendar(
  filters: NewsFilters & { start?: string | null; end?: string | null },
): Promise<{ days: NewsDayCount[]; min_date: string | null; max_date: string | null }> {
  const params: unknown[] = [];
  const conditions = await buildFilterSql(filters, params);
  if (conditions === "1=0") {
    return { days: [], min_date: null, max_date: null };
  }
  const extra: string[] = [];
  if (conditions) extra.push(conditions);
  if (filters.start) {
    params.push(filters.start, filters.end);
    extra.push(`n.date BETWEEN $${params.length - 1} AND $${params.length}`);
  }
  // The SELECT lives in the sql view data_viz/api/services/news/calendar
  // (where_clause rides a fragment; its params stay caller-assembled).
  const rows = await queryRows<{ date: string; count: string } & QueryResultRow>(
    renderSqlView("data_viz/api/services/news/calendar", {
      where_clause: sqlFrag(extra.length ? `WHERE ${extra.join(" AND ")}` : ""),
    }).sql,
    params,
  );
  const days = rows.map((r) => ({ date: r.date, count: Number(r.count) }));
  // The SELECT lives in the sql view
  // data_viz/api/services/news/date_bounds.
  const bounds = await querySqlView<{ min_date: string | null; max_date: string | null } & QueryResultRow>(
    "data_viz/api/services/news/date_bounds",
  );
  return {
    days,
    min_date: bounds[0]?.min_date ?? null,
    max_date: bounds[0]?.max_date ?? null,
  };
}

// ---------------------------------------------------------------------------
// Items — one page of articles
// ---------------------------------------------------------------------------

export async function listNewsItems(
  filters: NewsFilters & {
    date?: string | null;
    /** Inclusive date RANGE (date_from ≤ date ≤ date_to) — used instead of
     *  the single `date` pick when the caller wants a window around the
     *  picked day. */
    dateFrom?: string | null;
    dateTo?: string | null;
    limit?: number | null;
    offset?: number | null;
  },
): Promise<{ total: number; items: NewsItem[] }> {
  const params: unknown[] = [];
  const conditions = await buildFilterSql(filters, params);
  if (conditions === "1=0") return { total: 0, items: [] };

  if (filters.date) {
    params.push(filters.date);
    const dateClause = `WHERE ${conditions ? `${conditions} AND ` : ""}n.date = $${params.length}`;
    return finishItems(dateClause, params, filters);
  }
  if (filters.dateFrom && filters.dateTo) {
    params.push(filters.dateFrom, filters.dateTo);
    const rangeClause = `WHERE ${conditions ? `${conditions} AND ` : ""}n.date BETWEEN $${params.length - 1} AND $${params.length}`;
    return finishItems(rangeClause, params, filters);
  }
  return finishItems(conditions ? `WHERE ${conditions}` : "", params, filters);
}

async function finishItems(
  whereClause: string,
  params: unknown[],
  filters: { limit?: number | null; offset?: number | null },
): Promise<{ total: number; items: NewsItem[] }> {
  const limit = Math.min(Math.max(filters.limit ?? 10, 1), 200);
  const offset = Math.max(filters.offset ?? 0, 0);

  // The SELECTs live in the sql views
  // data_viz/api/services/news/items_count and news/items (where_clause
  // rides a fragment; its params stay caller-assembled; LIMIT/OFFSET ride
  // caller-built clamped-literal fragments).
  const totalRows = await queryRows<{ n: string } & QueryResultRow>(
    renderSqlView("data_viz/api/services/news/items_count", {
      where_clause: sqlFrag(whereClause),
    }).sql,
    params,
  );
  const items = await queryRows<NewsItem & QueryResultRow>(
    renderSqlView("data_viz/api/services/news/items", {
      where_clause: sqlFrag(whereClause),
      limit: sqlFrag(`${limit}`),
      offset: sqlFrag(`${offset}`),
    }).sql,
    params,
  );
  return { total: Number(totalRows[0]?.n ?? 0), items };
}

// ---------------------------------------------------------------------------
// Item detail (full content) + threaded comments — the social-style feed
// expands a post on click and lazily fetches these.
// ---------------------------------------------------------------------------

export async function getNewsItem(newsId: number): Promise<NewsItemDetail | null> {
  // The SELECT lives in the sql view data_viz/api/services/news/news_item.
  const rows = await querySqlView<NewsItemDetail & QueryResultRow>(
    "data_viz/api/services/news/news_item",
    { news_id: newsId },
  );
  return rows[0] ?? null;
}

export async function listNewsComments(newsId: number): Promise<NewsCommentsResponse> {
  // The SELECT lives in the sql view
  // data_viz/api/services/news/news_comments.
  const rows = await querySqlView<NewsComment & QueryResultRow>(
    "data_viz/api/services/news/news_comments",
    { news_id: newsId },
  );
  // node-pg serves BIGINT (int8) as strings — normalize the id columns to
  // numbers so the NewsComment contract holds and the votes/`comment_id`
  // sort below stays numeric.
  const comments: NewsComment[] = rows.map((r) => ({
    ...r,
    comment_id: Number(r.comment_id),
    parent_comment_id: r.parent_comment_id == null ? null : Number(r.parent_comment_id),
  }));
  // Rebuild the thread: replies nest under their root (conversation order);
  // roots surface by votes so the best comments lead.
  const byId = new Map<number, NewsComment>();
  const roots: NewsComment[] = [];
  for (const r of comments) {
    byId.set(r.comment_id, { ...r, replies: [] });
  }
  for (const c of byId.values()) {
    if (c.parent_comment_id != null) {
      byId.get(c.parent_comment_id)?.replies.push(c);
    } else {
      roots.push(c);
    }
  }
  const byVotes = (a: NewsComment, b: NewsComment) =>
    (b.votes ?? -1) - (a.votes ?? -1) || a.comment_id - b.comment_id;
  roots.sort(byVotes);
  for (const c of byId.values()) c.replies.sort(byVotes);
  return { total: rows.length, comments: roots };
}
