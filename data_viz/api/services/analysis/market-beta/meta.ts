/**
 * Market Beta classification meta — the cached per-index
 * classification rows (composition-only filter applied) behind both themes
 * trees and the chart's classification filtering.
 * Split out of the former monolithic industry-sentiments.ts.
 */
import type { QueryResultRow } from "pg";
import { cachedRows } from "@api/services/classification-cache";
import { querySqlView } from "@api/lib/sql-views";

export interface DbMarketBetaMetaRow extends QueryResultRow {
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
//  listMarketBetaStrategyThemes() (RIGHT column strategy/theme tree)
//  and by getMarketBetaChart() for classification filtering.
//  is_industry_not_strategy distinguishes industry-primary (TRUE) from
//  strategy-primary (FALSE) rows; for strategy-primary rows sector_id/
//  industry_id carry the STRATEGY/theme classification.
//  The SELECT lives in the sql view
//  data_viz/api/services/analysis/market-beta/meta/classified_index_rows.
// ----------------------------------------------------------------------------

/** The meta rows behind both themes trees, cached in-process for the shared
 *  10-min TTL (the EXISTS-over-sec_composition join costs ~0.4s; both the
 *  themes and strategy-themes nav fetches of the Market Beta page
 *  paid it on every load). Exchange filters are applied client-side, so the
 *  rows are safely cacheable under one key. */
export async function listMarketBetaMetaRows(): Promise<DbMarketBetaMetaRow[]> {
  return cachedRows("market_beta:meta", () =>
    querySqlView<DbMarketBetaMetaRow>(
      "data_viz/api/services/analysis/market-beta/meta/classified_index_rows",
    ));
}
