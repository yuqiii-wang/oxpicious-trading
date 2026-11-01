/**
 * Classification cache — shared in-process cache for the security
 * classification meta rows used to build the UI nav trees
 * (sector → industry / strategy → theme).
 *
 * WHY: the classification only changes when build_classification.py runs
 * (daily/nightly), but every nav fetch used to re-aggregate the raw daily
 * tables (stock_identity × stock_basic_stats, v_etf_margin, intraday 5-min
 * tables) with COUNT/MIN/MAX per code — millions of rows per call.  The
 * precomputed/denormalized columns on stats.sec_classification
 * (name, n_days, first_date, last_date, exchange, is_active, labels) already
 * carry everything the nav needs.
 *
 * WHAT:
 *   • cachedRows(key, loader)        — generic TTL cache + in-flight
 *     de-duplication (concurrent callers share one DB query).
 *   • listClassificationMetaRows()   — ONE row per code from
 *     stats.sec_classification via DISTINCT ON (picks the primary-parent row
 *     for stocks that have multiple parent-index rows).  Cheap: reads only
 *     the small classification table, no aggregation over daily data.
 *
 * Services that need live row counts from their own views (e.g. etf-margin
 * counts v_etf_margin rows) can still wrap THEIR loader with cachedRows() to
 * get the same single-query-per-TTL behavior.
 */
import { queryRows } from "../lib/db.js";
import type { QueryResultRow } from "pg";
import type { IndustryEntityClassRow } from "./_shared.js";

// ---------------------------------------------------------------------------
//  Generic TTL cache with in-flight de-duplication
// ---------------------------------------------------------------------------

/** Cache TTL. Classification changes only on the nightly build; 10 minutes
 *  is a conservative freshness bound that still collapses hundreds of nav
 *  fetches into one DB query. */
const CACHE_TTL_MS = 10 * 60_000;

const cache = new Map<string, { at: number; value: unknown }>();
const inflight = new Map<string, Promise<unknown>>();

/** Drop the named cache keys — a write path's freshness escape hatch (the
 *  next read reloads through its loader instead of waiting out the TTL). */
export function invalidateCachedRows(...keys: string[]): void {
  for (const key of keys) {
    cache.delete(key);
  }
}

/** Run `loader()` at most once per TTL window per key. Concurrent callers
 *  share the same in-flight promise (no stampede). */
export async function cachedRows<T>(
  key: string,
  loader: () => Promise<T[]>,
): Promise<T[]> {
  const hit = cache.get(key);
  if (hit && Date.now() - hit.at < CACHE_TTL_MS) {
    return hit.value as T[];
  }
  const existing = inflight.get(key);
  if (existing) {
    return existing as Promise<T[]>;
  }
  const p = loader()
    .then((rows) => {
      cache.set(key, { at: Date.now(), value: rows });
      return rows;
    })
    .finally(() => {
      inflight.delete(key);
    });
  inflight.set(key, p);
  return p as Promise<T[]>;
}

// ---------------------------------------------------------------------------
//  Classification meta rows — one row per code from stats.sec_classification
// ---------------------------------------------------------------------------

export type ClassificationSecType = "index" | "etf" | "stock";

export interface ClassificationMetaRow extends QueryResultRow {
  code: string;
  name: string;
  n_days: number;
  first_date: string | null;
  last_date: string | null;
  sector_id: string;
  sector_label: string;
  industry_id: string;
  industry_label: string;
  industry_slug: string;
  is_industry_not_strategy: boolean;
  exchange: string;
  is_dummy: boolean;
}

/**
 * Fetch one classification row per code, cached in-process.
 *
 * DISTINCT ON (code) picks the "best" row per code for stocks that have
 * MULTIPLE sec_classification rows (one per qualifying parent index, PK is
 * (code, parent_index_code)): the parent_index_is_primary row first, then
 * the highest parent_index_weight.  Indices (parent_index_code='') and ETFs
 * (one-to-one tracking index) already have a single row per code, so the
 * DISTINCT ON is a no-op for them.
 *
 * NOTE: n_days comes from the precomputed sec_classification column
 * (populated by build_classification.py from the identity tables) instead of
 * a live COUNT(*) over the daily tables — that aggregation was the dominant
 * cost of the old nav queries.
 *
 * Rows are returned ordered by n_days DESC, code ASC (consumers paginate by
 * liquidity, most data first).
 */
export async function listClassificationMetaRows(
  secType: ClassificationSecType,
): Promise<ClassificationMetaRow[]> {
  const rows = await cachedRows(`sec_classification:${secType}`, () =>
    queryRows<ClassificationMetaRow>(
      `
      SELECT DISTINCT ON (sc.code)
             sc.code,
             COALESCE(sc.name, '')                  AS name,
             COALESCE(sc.n_days, 0)                 AS n_days,
             sc.first_date::text                    AS first_date,
             sc.last_date::text                     AS last_date,
             COALESCE(sc.sector_id,       'OTHER')  AS sector_id,
             COALESCE(sc.sector_label,    '其他')   AS sector_label,
             COALESCE(sc.industry_id,     'OTHER')  AS industry_id,
             COALESCE(sc.industry_label,  '未分类')  AS industry_label,
             COALESCE(sc.industry_slug,   'other')  AS industry_slug,
             COALESCE(sc.is_industry_not_strategy, TRUE) AS is_industry_not_strategy,
             COALESCE(sc.exchange, '')               AS exchange,
             COALESCE(sc.is_dummy, false)            AS is_dummy
        FROM stats.sec_classification sc
       WHERE sc.type = $1
         AND sc.is_active = TRUE
       ORDER BY sc.code,
                sc.parent_index_is_primary DESC NULLS LAST,
                sc.parent_index_weight DESC NULLS LAST,
                sc.parent_index_code
      `,
      [secType],
    ),
  );
  // Stable liquidity ordering (DISTINCT ON requires ORDER BY code first, so
  // the n_days DESC ordering is applied in JS — a few thousand rows, ~free).
  return [...rows].sort((a, b) => b.n_days - a.n_days || (a.code < b.code ? -1 : a.code > b.code ? 1 : 0));
}

// ---------------------------------------------------------------------------
//  Industry-entity classification rows — one row per industry_id
// ---------------------------------------------------------------------------

/**
 * Classification for the sec_type='industry' ENTITY ids (code =
 * industry_id, e.g. BANKS / BROAD_CSI300). Industry entities have no
 * sec_classification rows of their own — their classification is read from
 * the securities classified under the id, grouped by industry_id:
 * sector/label columns MIN-aggregated (an id carries exactly one sector and
 * label across its carriers — asserted 2026-10-29) and
 * BOOL_AND(is_industry_not_strategy) picks the column (no id straddles the
 * two today; a hypothetical straddler lands on the strategy side).
 *
 * Cached per id-set via the shared TTL — the classification only moves on
 * the nightly build.
 */
export async function listIndustryEntityClassRows(
  ids: Array<string | null | undefined>,
): Promise<IndustryEntityClassRow[]> {
  const clean = [...new Set(ids.filter((id): id is string => !!id))];
  if (clean.length === 0) return [];
  const key = `sec_classification:industry-entities:${clean.join(",")}`;
  return cachedRows(key, () =>
    queryRows<IndustryEntityClassRow>(
      `
      SELECT industry_id,
             MIN(sector_id)      AS sector_id,
             MIN(sector_label)   AS sector_label,
             MIN(industry_label) AS industry_label,
             MIN(industry_slug)  AS industry_slug,
             BOOL_AND(is_industry_not_strategy) AS is_industry_not_strategy
        FROM stats.sec_classification
       WHERE industry_id = ANY($1::text[])
       GROUP BY industry_id
      `,
      [clean],
    ),
  );
}
