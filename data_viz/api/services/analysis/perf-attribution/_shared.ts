/**
 * Performance Attribution shared config — the whitelisted name/meta tables,
 * the per-code source resolution (summary rollup vs live aggregate vs
 * empty), and the classification meta fetch shared by the codes list and
 * both themes trees.
 * Split out of the former monolithic perf-attribution.ts.
 */
import { querySqlView, sqlFrag } from "@api/lib/sql-views";
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
  const summary = await querySqlView(
    "data_viz/api/services/analysis/perf-attribution/_shared/has_summary_rows",
    { sec_type: secType },
  );
  if (summary.length) return "summary";
  const source = await querySqlView(
    "data_viz/api/services/analysis/perf-attribution/_shared/has_cross_stats_rows",
    { sec_type: secType },
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

/** Meta row fetch shared by listPerfAttrThemes(), listPerfAttrStrategyThemes()
 *  and the codes list's classification filter. Returns one row per code in
 *  stats.cross_stats (filtered by sec_type) with its precomputed L1/L2
 *  classification from stats.sec_classification.
 *  is_industry_not_strategy distinguishes industry-primary (TRUE) from
 *  strategy-primary (FALSE) rows. `codeSource` picks the membership source:
 *  the summary rollup's PK scan ('summary') or the historical DISTINCT
 *  full-table scan ('live') — rendered from the sql views
 *  data_viz/api/services/analysis/perf-attribution/_shared/meta_rows_summary
 *  and .../_shared/meta_rows_live. */
export function fetchPerfAttrMetaRows(
  metaTable: string,
  codeSource: PerfAttrCodeSource,
  secType: PerfAttrSecType,
  metaType: string,
): Promise<DbPerfAttrMetaRow[]> {
  const view = codeSource === "summary"
    ? "data_viz/api/services/analysis/perf-attribution/_shared/meta_rows_summary"
    : "data_viz/api/services/analysis/perf-attribution/_shared/meta_rows_live";
  return querySqlView<DbPerfAttrMetaRow>(view, {
    sec_type: secType,
    meta_type: metaType,
    meta_table: sqlFrag(metaTable),
  });
}
