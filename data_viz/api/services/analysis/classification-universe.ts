/**
 * Full classification universe — the shared row sources behind the
 * full-universe navs of the placeholder-pipeline analysis services
 * (market-crowdedness, hot-stock-momentum).
 *
 * DELIBERATE DEVIATION from the pe / mov-ave-spread pattern: those navs
 * are restricted to codes that already have analysis rows. These
 * pipelines are placeholders (their analysis tables are empty — see
 * database/sql/analysis/29_market_crowdedness.sql /
 * 30_hot_stock_momentum.sql), so a data-restricted nav would render the
 * pages dead. The nav/codes universe here is the FULL classification
 * universe instead (stats.sec_classification type='index' + the
 * stats.industry_identity entities) — the chart endpoints read the
 * (empty) analysis tables and the UI shows its "no data" state per code
 * until the pipelines fill them.
 */
import { queryRows } from "@api/lib/db";
import type { QueryResultRow } from "pg";
import { cachedRows, listClassificationMetaRows } from "@api/services/classification-cache";
import type { ClassificationMetaRow } from "@api/services/classification-cache";

/** The sec_types these pages expose (the toggle) and their pipelines are
 *  scoped to. */
export type IndexIndustrySecType = "index" | "industry";

const VALID_SEC_TYPES: ReadonlySet<string> = new Set(["index", "industry"]);

export function normalizeIndexIndustrySecType(
  raw: string | undefined | null,
): IndexIndustrySecType {
  const v = (raw ?? "").trim().toLowerCase();
  if (!VALID_SEC_TYPES.has(v)) {
    throw new Error(`Invalid sec_type: ${raw!}. Expected 'index' or 'industry'.`);
  }
  return v as IndexIndustrySecType;
}

export interface DbIndexUniverseRow extends QueryResultRow {
  code: string;
  name: string;
  exchange: string;
  sector_id: string;
  sector_label: string;
  industry_id: string;
  industry_label: string;
  industry_slug: string;
  is_industry_not_strategy: boolean;
}

/** The FULL active index universe with its classification — one row per
 *  code from stats.sec_classification (type='index', is_active), already
 *  cached in-process by the shared classification cache. */
export async function listFullIndexUniverseRows(): Promise<
  DbIndexUniverseRow[]
> {
  const rows: ClassificationMetaRow[] = await listClassificationMetaRows("index");
  return rows as unknown as DbIndexUniverseRow[];
}

export interface DbIndustryUniverseRow extends QueryResultRow {
  code: string;
  name: string;
}

/** The FULL industry-entity universe — one row per entity from
 *  stats.industry_identity (the pool-'all' slice of
 *  stats.industry_basic_stats; code = industry_id, name =
 *  industry_label), latest name per code. The industry entities' sec_type
 *  classification (sector/strategy side) is joined per-request by
 *  listIndustryEntityClassRows in the themes modules. Cached under the
 *  shared 10-min TTL (the view only moves on the nightly builds.industry). */
export async function listFullIndustryUniverseRows(): Promise<
  DbIndustryUniverseRow[]
> {
  return cachedRows("classification_universe:industry", () =>
    queryRows<DbIndustryUniverseRow>(
      `
      SELECT DISTINCT ON (code) code, name
      FROM stats.industry_identity
      ORDER BY code, date DESC
      `,
      [],
    ),
  );
}
