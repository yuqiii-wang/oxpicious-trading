/**
 * Industry Sentiments classification meta — the cached per-index
 * classification rows (composition-only filter applied) behind both themes
 * trees and the chart's classification filtering.
 * Split out of the former monolithic industry-sentiments.ts.
 */
import { queryRows } from "@api/lib/db";
import type { QueryResultRow } from "pg";
import { cachedRows } from "@api/services/classification-cache";

export interface DbIndustrySentimentsMetaRow extends QueryResultRow {
  code: string;
  name: string;
  exchange: string | null;
  sector_id: string;
  sector_label: string;
  industry_id: string;
  industry_label: string;
  industry_slug: string;
  is_industry_not_strategy: boolean;
}

// ----------------------------------------------------------------------------
//  Per-code meta query — one row per classified index (type='index') that has
//  at least one stats.sec_composition snapshot. Used by
//  listIndustrySentimentsStrategyThemes() (RIGHT column strategy/theme tree)
//  and by getIndustrySentimentsChart() for classification filtering.
//  is_industry_not_strategy distinguishes industry-primary (TRUE) from
//  strategy-primary (FALSE) rows; for strategy-primary rows sector_id/
//  industry_id carry the STRATEGY/theme classification.
// ----------------------------------------------------------------------------
const INDUSTRY_SENTIMENTS_META_SQL = `
  SELECT sc.code,
         COALESCE(sc.name, '')             AS name,
         sc.exchange                       AS exchange,
         COALESCE(sc.sector_id,       'OTHER')  AS sector_id,
         COALESCE(sc.sector_label,    '其他')   AS sector_label,
         COALESCE(sc.industry_id,     'OTHER')  AS industry_id,
         COALESCE(sc.industry_label,  '未分类')  AS industry_label,
         COALESCE(sc.industry_slug,   'other')  AS industry_slug,
         COALESCE(sc.is_industry_not_strategy, TRUE) AS is_industry_not_strategy
    FROM stats.sec_classification sc
   WHERE sc.type = 'index'
     AND sc.is_active = TRUE
     AND sc.industry_id IS NOT NULL
     AND sc.industry_id <> ''
     AND COALESCE(sc.sector_id, '') <> 'DEBT'
     AND EXISTS (
         SELECT 1 FROM stats.sec_composition sc2
         WHERE sc2.code = sc.code AND sc2.source_type = 'index'
     )
`;

/** The meta rows behind both themes trees, cached in-process for the shared
 *  10-min TTL (the EXISTS-over-sec_composition join costs ~0.4s; both the
 *  themes and strategy-themes nav fetches of the Industry Sentiments page
 *  paid it on every load). Exchange filters are applied client-side, so the
 *  rows are safely cacheable under one key. */
export async function listIndustrySentimentsMetaRows(): Promise<DbIndustrySentimentsMetaRow[]> {
  return cachedRows("industry_sentiments:meta", () =>
    queryRows<DbIndustrySentimentsMetaRow>(INDUSTRY_SENTIMENTS_META_SQL, []));
}
