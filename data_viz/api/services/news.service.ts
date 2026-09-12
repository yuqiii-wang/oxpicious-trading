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
 *   industry scope — industry_id when an L2 chip is active, else ALL
 *   industry_ids of the active sector (L1), else unscoped.
 *   keyword search — whitespace-separated terms, ALL must appear (AND) in
 *   title OR content (case-insensitive).
 */
import { queryRows } from "../lib/db.js";
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
 *  classification only changes on the nightly build. */
async function listCatalogRows(): Promise<CatalogRow[]> {
  return cachedRows("news:catalog", () =>
    queryRows<CatalogRow>(
      `
      SELECT DISTINCT ON (sector_id, industry_id, is_industry_not_strategy)
             COALESCE(sector_id,      'OTHER') AS sector_id,
             COALESCE(sector_label,   '其他')  AS sector_label,
             COALESCE(industry_id,    'OTHER') AS industry_id,
             COALESCE(industry_label, '未分类') AS industry_label,
             COALESCE(industry_slug,  'other') AS industry_slug,
             COALESCE(is_industry_not_strategy, TRUE) AS is_industry_not_strategy
        FROM stats.sec_classification
       ORDER BY sector_id, industry_id, is_industry_not_strategy
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

interface NewsFilters {
  sectorId?: string | null;
  industryId?: string | null;
  search?: string | null;
  /** true = terms are OR-ed ("any token hits") instead of the default AND.
   *  Used by the question bar's local search, whose terms come from the
   *  naive tokenizer (taxonomy keywords extracted from the question). */
  searchAny?: boolean;
  source?: string | null;
  author?: string | null;
}

async function buildFilterSql(
  filters: NewsFilters,
  params: unknown[],
): Promise<string> {
  const clauses: string[] = [];
  if (filters.industryId) {
    params.push(filters.industryId);
    clauses.push(`n.industry_id = $${params.length}`);
  } else if (filters.sectorId) {
    const ids = await getSectorIndustryIds(filters.sectorId);
    if (ids.length === 0) return "1=0"; // unknown sector — no rows, not all rows
    params.push(ids);
    clauses.push(`n.industry_id = ANY($${params.length}::text[])`);
  }
  if (filters.source) {
    params.push(filters.source);
    clauses.push(`n.source = $${params.length}`);
  }
  if (filters.author) {
    params.push(filters.author);
    clauses.push(`n.author = $${params.length}`);
  }
  // Keyword search: every term must hit title OR content (AND), or — with
  // searchAny — a single clause matching ANY term (OR across terms).
  const terms = (filters.search ?? "").trim().split(/\s+/).filter(Boolean);
  const termClauses = terms.map((term) => {
    params.push(`%${term}%`);
    const p = `$${params.length}`;
    return `(n.title ILIKE ${p} OR n.content ILIKE ${p})`;
  });
  if (termClauses.length > 0) {
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
      const where = clauses.length ? `WHERE ${clauses.join(" AND ")}` : "";
      return queryRows(
        `SELECT COALESCE(industry_id, '') AS industry_id, COUNT(*)::text AS n
           FROM text.news ${where} GROUP BY 1`,
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
  const rows = await queryRows<{ author: string; n: string } & QueryResultRow>(
    `SELECT n.author, COUNT(*)::text AS n
       FROM text.news n ${conditions ? `WHERE ${conditions} AND ` : "WHERE "}n.author IS NOT NULL
      GROUP BY n.author ORDER BY COUNT(*) DESC, n.author ASC
      LIMIT 30`,
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
  const where = extra.length ? `WHERE ${extra.join(" AND ")}` : "";
  const rows = await queryRows<{ date: string; count: string } & QueryResultRow>(
    `SELECT n.date::text AS date, COUNT(*)::text AS count
       FROM text.news n ${where}
      GROUP BY n.date ORDER BY n.date`,
    params,
  );
  const days = rows.map((r) => ({ date: r.date, count: Number(r.count) }));
  const bounds = await queryRows<{ min_date: string | null; max_date: string | null } & QueryResultRow>(
    `SELECT MIN(date)::text AS min_date, MAX(date)::text AS max_date FROM text.news`,
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
  return finishItems(conditions ? `WHERE ${conditions}` : "", params, filters);
}

async function finishItems(
  whereClause: string,
  params: unknown[],
  filters: { limit?: number | null; offset?: number | null },
): Promise<{ total: number; items: NewsItem[] }> {
  const limit = Math.min(Math.max(filters.limit ?? 50, 1), 200);
  const offset = Math.max(filters.offset ?? 0, 0);

  const totalRows = await queryRows<{ n: string } & QueryResultRow>(
    `SELECT COUNT(*)::text AS n FROM text.news n ${whereClause}`,
    params,
  );
  params.push(limit);
  params.push(offset);
  const items = await queryRows<NewsItem & QueryResultRow>(
    `SELECT n.news_id, n.title, n.source, n.date::text AS date, n.url,
            n.author, n.industry_id, n.votes,
            (SELECT COUNT(*)::int FROM text.news_comments c
              WHERE c.news_id = n.news_id) AS comment_count,
            CASE WHEN n.content IS NULL THEN NULL
                 ELSE LEFT(n.content, 200) END AS snippet
       FROM text.news n ${whereClause}
      ORDER BY n.date DESC, n.votes DESC NULLS LAST, n.source ASC, n.news_id ASC
      LIMIT $${params.length - 1} OFFSET $${params.length}`,
    params,
  );
  return { total: Number(totalRows[0]?.n ?? 0), items };
}

// ---------------------------------------------------------------------------
// Item detail (full content) + threaded comments — the social-style feed
// expands a post on click and lazily fetches these.
// ---------------------------------------------------------------------------

export async function getNewsItem(newsId: number): Promise<NewsItemDetail | null> {
  const rows = await queryRows<NewsItemDetail & QueryResultRow>(
    `SELECT n.news_id, n.title, n.source, n.date::text AS date, n.url,
            n.author, n.industry_id, n.votes, n.content,
            (SELECT COUNT(*)::int FROM text.news_comments c
              WHERE c.news_id = n.news_id) AS comment_count
       FROM text.news n
      WHERE n.news_id = $1`,
    [newsId],
  );
  return rows[0] ?? null;
}

export async function listNewsComments(newsId: number): Promise<NewsCommentsResponse> {
  const rows = await queryRows<NewsComment & QueryResultRow>(
    `SELECT c.comment_id, c.parent_comment_id, c.author, c.content,
            c.date::text AS date, c.votes, c.is_reply
       FROM text.news_comments c
      WHERE c.news_id = $1
      ORDER BY c.date ASC, c.comment_id ASC`,
    [newsId],
  );
  // Rebuild the thread: replies nest under their root (conversation order);
  // roots surface by votes so the best comments lead.
  const byId = new Map<number, NewsComment>();
  const roots: NewsComment[] = [];
  for (const r of rows) {
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
