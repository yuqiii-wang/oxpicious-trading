/**
 * MA-Spread classification meta query — the sec_classification JOIN shared
 * by listMovAveSpreadCodes (filter maps), listMovAveSpreadThemes and
 * listMovAveSpreadStrategyThemes.
 * Split out of the former monolithic mov-ave-spreads.ts.
 */
import type { QueryResultRow } from "pg";
import type { MaSpreadSecType } from "@shared/types";

/** Whitelisted sec_type → sec_classification type discriminator (safe for
 *  string interpolation in the SQL). */
export const MA_SPREAD_META_TYPE: Record<MaSpreadSecType, string> = {
  etf: "etf",
  index: "index",
  stock: "stock",
  // Unused: industry codes have NO stats.sec_classification rows (the
  // industry IS the classification top) — the themes/codes functions
  // short-circuit before the meta query for 'industry'.
  industry: "industry",
};

export interface DbMaSpreadMetaRow extends QueryResultRow {
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
  /** Exchange code from stats.sec_classification (SS/STAR/SZ/GEM/BJ/HK/OVERSEAS).
   *  Used by matchesExchange() to filter the tree by the UI exchange filter. */
  exchange: string;
}

/** Meta SQL shared by listMovAveSpreadThemes() and
 *  listMovAveSpreadStrategyThemes(). Returns one row per code WITH analysis
 *  rows (from the precomputed per-code summary table — the former
 *  `SELECT DISTINCT code` over the detail table scanned all ~6.8M stock
 *  rows per request) with its precomputed L1/L2 classification from
 *  stats.sec_classification. is_industry_not_strategy distinguishes
 *  industry-primary (TRUE) from strategy-primary (FALSE) rows. */
export const MA_SPREAD_META_SQL = `
  WITH spread_codes AS (
    SELECT code
    FROM analysis.mov_ave_spreads_codes
    WHERE sec_type = $1
  )
  SELECT
    sc.code,
    COALESCE(m.name, '')             AS name,
    COALESCE(m.sector_id,       'OTHER')  AS sector_id,
    COALESCE(m.sector_label,    '其他')   AS sector_label,
    COALESCE(m.industry_id,     'OTHER')  AS industry_id,
    COALESCE(m.industry_label,  '未分类') AS industry_label,
    COALESCE(m.industry_slug,   'other')  AS industry_slug,
    COALESCE(m.is_industry_not_strategy, TRUE) AS is_industry_not_strategy,
    COALESCE(m.exchange, '')               AS exchange
  FROM spread_codes sc
  LEFT JOIN stats.sec_classification m ON m.code = sc.code AND m.type = $2::text
  WHERE COALESCE(m.is_active, TRUE) = TRUE
`;
