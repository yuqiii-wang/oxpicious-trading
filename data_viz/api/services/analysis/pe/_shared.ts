/**
 * PE & Dividend Yield (valuation) shared config — the per-sec_type close
 * source tables, sec_type normalization, and the cached classification
 * meta query shared by the codes list, chart, streaks and both themes
 * trees.
 * Split out of the former monolithic pe.ts.
 */
import { queryRows } from "@api/lib/db";
import type { QueryResultRow } from "pg";
import { cachedRows } from "@api/services/classification-cache";
import { LABEL_CODE } from "@api/services/label-codes";
import type { PeSecType } from "@shared/types";

// ----------------------------------------------------------------------------
//  Per-sec_type source-table config
//  Mirrors mov-ave-spreads SEC_SOURCES but only needs close (raw pe and
//  dividend_yield come from analysis.pe / analysis.dividends).
// ----------------------------------------------------------------------------
export interface SecSource {
  /** Schema-qualified identity table for the asset name lookup. */
  identityTable: string;
  /** FROM clause for the chart query — the code's close source (the
   *  analysis.pe / analysis.dividends LEFT JOINs are appended). */
  chartFromClause: string;
  /** SQL expression for the per-row close column. */
  closeExpr: string;
}

export const SEC_SOURCES: Record<PeSecType, SecSource> = {
  etf: {
    identityTable: "stats.etf_identity",
    // a.code = ANY($1) pushes the code predicate into the adjustment join
    // (see buildChartSql's note — unpinned LEFT JOIN sides full-scan).
    chartFromClause:
      "FROM stats.etf_basic_stats b\n" +
      "  LEFT JOIN stats.etf_adjustment a ON a.date = b.date AND a.code = b.code AND a.code = ANY($1::text[])",
    closeExpr: "COALESCE(a.adj_close, b.close)",
  },
  index: {
    identityTable: "stats.index_identity",
    chartFromClause:
      "FROM stats.index_basic_stats b",
    closeExpr: "b.close",
  },
  stock: {
    identityTable: "stats.stock_identity",
    chartFromClause:
      "FROM stats.stock_basic_stats b",
    closeExpr: "b.close",
  },
  // Industry (code = industry_id): the composite pool-'all' slice — a
  // rename subquery (industry_id -> code, mean_close -> close) so the
  // generic chart SQL's b.code / b.close bindings hold.
  industry: {
    identityTable: "stats.industry_identity",
    chartFromClause:
      "FROM (SELECT industry_id AS code, date, mean_close AS close\n" +
      "      FROM stats.industry_basic_stats\n" +
      "      WHERE pool_size = 3 AND mean_close IS NOT NULL) b",
    closeExpr: "b.close",
  },
};

const VALID_SEC_TYPES: ReadonlySet<PeSecType> = new Set(["etf", "index", "stock", "industry"]);

export function normalizeSecType(raw: string | undefined | null): PeSecType {
  const v = (raw ?? "").trim().toLowerCase();
  if (!VALID_SEC_TYPES.has(v as PeSecType)) {
    throw new Error(`Invalid sec_type: ${raw!}. Expected 'etf', 'index', 'stock', or 'industry'.`);
  }
  return v as PeSecType;
}

/** Latest-name lookup for one code (all variants) from the sec_type's
 *  identity table. */
export function buildNameSql(secType: PeSecType): string {
  const src = SEC_SOURCES[secType];
  return `
    SELECT DISTINCT ON (code) code, name
    FROM ${src.identityTable}
    WHERE code = ANY($1::text[])
    ORDER BY code, date DESC
  `;
}

const META_TYPE: Record<PeSecType, string> = {
  etf: "etf",
  index: "index",
  stock: "stock",
  // Unused: industry codes have NO stats.sec_classification rows — the
  // themes/codes functions short-circuit before the meta query.
  industry: "industry",
};

export interface DbMetaRow extends QueryResultRow {
  code: string;
  name: string;
  sector_id: string;
  sector_label: string;
  industry_id: string;
  industry_label: string;
  industry_slug: string;
  is_industry_not_strategy: boolean;
  exchange: string;
}

/** Meta SQL shared by listValuationThemes() and listValuationStrategyThemes().
 *  Returns one row per classification row of each code present in
 *  analysis.pe / analysis.dividends (filtered by sec_type) with its
 *  precomputed L1/L2 classification from stats.sec_classification (a
 *  multi-parent stock yields one row per parent — the tree builders
 *  consolidate). */
const META_SQL = `
  SELECT
    c.code,
    COALESCE(c.name, '')             AS name,
    COALESCE(m.sector_id,       'OTHER')  AS sector_id,
    COALESCE(m.sector_label,    '其他')   AS sector_label,
    COALESCE(m.industry_id,     'OTHER')  AS industry_id,
    COALESCE(m.industry_label,  '未分类') AS industry_label,
    COALESCE(m.industry_slug,   'other')  AS industry_slug,
    COALESCE(m.is_industry_not_strategy, TRUE) AS is_industry_not_strategy,
    COALESCE(m.exchange, '')               AS exchange
  FROM analysis.pe_codes c
  LEFT JOIN stats.sec_classification m ON m.code = c.code AND m.type = $2::text
  WHERE c.sec_type = $1  -- static.label_dim 'sec_type' code
    AND COALESCE(m.is_active, TRUE) = TRUE
`;

/** Meta rows (the analysis.pe ∪ analysis.dividends code universe joined with
 *  classification), cached in-process for the shared 10-min TTL. Sourced from
 *  the precomputed analysis.pe_codes summary — the former live DISTINCT over
 *  both tables' partitions cost ~0.65s on the stock universe even with the
 *  (sec_type, code) index-only scans, and every nav fetch of the PE page
 *  paid it on each cold TTL window. Exchange + classification filters are
 *  applied client-side, so the rows are exchange-independent and safely
 *  cacheable per sec_type. */
export async function listPeMetaRows(secType: PeSecType): Promise<DbMetaRow[]> {
  return cachedRows(`pe_meta:${secType}`, () =>
    queryRows<DbMetaRow>(META_SQL,
      [LABEL_CODE.sec_type[secType], META_TYPE[secType]]));
}
