/**
 * Performance Attribution shared config — the whitelisted name/meta tables,
 * the per-code source resolution (summary rollup vs live aggregate vs
 * empty), and the classification meta SQL shared by the codes list and both
 * themes trees.
 * Split out of the former monolithic perf-attribution.ts.
 */
import { queryRows } from "@api/lib/db";
import type { QueryResultRow } from "pg";
import type { PerfAttrSecType } from "@shared/types";

/** Whitelisted sec_type → name table mapping (safe for string interpolation). */
export const PERF_ATTR_NAME_TABLE: Record<string, string> = {
  etf: "stats.etf_identity",
  index: "stats.index_identity",
};

/** Which source the per-code aggregates read for this sec_type:
 *  - 'summary': stats.cross_stats_code_summary (per-(sec_type, code)
 *    rollup maintained by builds.cross_stats — millisecond reads; a live
 *    GROUP BY over the 70M+ row main table costs ~30s per request).
 *  - 'live': on-the-fly aggregate over stats.cross_stats — only used while
 *    the rollup has no rows for the sec_type yet (fresh DB before the
 *    first build).
 *  - 'empty': stats.cross_stats has no rows for the sec_type at all
 *    (e.g. sec_type='etf' — the ETF pair grain is reserved/unused) → the
 *    caller returns an empty result without any table scan. */
export type PerfAttrCodeSource = "summary" | "live" | "empty";

export async function resolvePerfAttrCodeSource(
  secType: PerfAttrSecType,
): Promise<PerfAttrCodeSource> {
  const summary = await queryRows(
    `SELECT 1 AS ok FROM stats.cross_stats_code_summary WHERE sec_type = $1::text LIMIT 1`,
    [secType],
  );
  if (summary.length) return "summary";
  const source = await queryRows(
    `SELECT 1 AS ok FROM stats.cross_stats WHERE sec_type = $1::text LIMIT 1`,
    [secType],
  );
  return source.length ? "live" : "empty";
}

/** Whitelisted sec_type → meta-table mapping (safe for string interpolation).
 *  Both ETF and index classification now live in the unified stats.sec_classification
 *  table, discriminated by the `type` column. */
export const PERF_ATTR_META_TABLE: Record<string, string> = {
  etf: "stats.sec_classification",
  index: "stats.sec_classification",
};

/** Whitelisted sec_type → type discriminator for sec_classification WHERE clause. */
export const PERF_ATTR_META_TYPE: Record<string, string> = {
  etf: "etf",
  index: "index",
};

export interface DbPerfAttrMetaRow extends QueryResultRow {
  code: string;
  name: string;
  sector_id: string;
  sector_label: string;
  industry_id: string;
  industry_label: string;
  industry_slug: string;
  /** When TRUE, sector_id/industry_id hold INDUSTRY classification (industry-
   *  primary row). When FALSE, they hold STRATEGY classification (strategy-
   *  primary row). Used by the parallel strategy/theme selector. */
  is_industry_not_strategy: boolean;
}

/** Meta SQL shared by listPerfAttrThemes() and listPerfAttrStrategyThemes().
 *  Returns one row per code in stats.cross_stats (filtered
 *  by sec_type) with its precomputed L1/L2 classification from
 *  stats.sec_classification. is_industry_not_strategy distinguishes
 *  industry-primary (TRUE) from strategy-primary (FALSE) rows.
 *  `codeSource` picks the membership source: the summary rollup's PK
 *  scan ('summary') or the historical DISTINCT full-table scan ('live'). */
export function buildPerfAttrMetaSql(
  metaTable: string,
  codeSource: PerfAttrCodeSource,
): string {
  const perfCodes =
    codeSource === "summary"
      ? `
      SELECT code
      FROM stats.cross_stats_code_summary
      WHERE sec_type = $1::text`
      : `
      SELECT DISTINCT code
      FROM stats.cross_stats
      WHERE sec_type = $1::text`;
  return `
    WITH perf_codes AS (${perfCodes})
    SELECT
      pc.code,
      COALESCE(m.name, '')             AS name,
      COALESCE(m.sector_id,       'OTHER')  AS sector_id,
      COALESCE(m.sector_label,    '其他')   AS sector_label,
      COALESCE(m.industry_id,     'OTHER')  AS industry_id,
      COALESCE(m.industry_label,  '未分类') AS industry_label,
      COALESCE(m.industry_slug,   'other')  AS industry_slug,
      COALESCE(m.is_industry_not_strategy, TRUE) AS is_industry_not_strategy
    FROM perf_codes pc
    LEFT JOIN ${metaTable} m ON m.code = pc.code AND m.type = $2::text
  `;
}
