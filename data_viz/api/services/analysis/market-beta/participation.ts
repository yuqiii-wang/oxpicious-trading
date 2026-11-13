/**
 * Market Beta page — "Market Participation" panel: the latest N
 * trading days of analysis.market_participation (whole-market turnover,
 * SSE share, advance/decline breadth). Built by the
 * analysis.market_participation daily pipeline.
 */
import { querySqlView } from "../../../lib/sql-views.js";

/** One analysis.market_participation row as served to the chart. */
export interface MarketParticipationRow {
  date: string;
  total_trading_amount: string | null;
  ss_trading_amount: string | null;
  sz_trading_amount: string | null;
  total_amt_vs_ma5: string | null;
  total_amt_vs_ma20: string | null;
  total_amt_vs_ma60: string | null;
  total_amt_pct_rank_252: string | null;
  ss_share: string | null;
  ss_share_ma5: string | null;
  ss_share_ma20: string | null;
  ss_share_ma60: string | null;
  ss_share_vs_ma20_pp: string | null;
  ss_share_slope20_pp: string | null;
  adv_count: number | null;
  dec_count: number | null;
  flat_count: number | null;
  ad_ratio: string | null;
  median_pct_change: string | null;
  mean_pct_change: string | null;
  ad_ratio_ma5: string | null;
  ad_ratio_ma20: string | null;
  ad_ratio_pct_rank_252: string | null;
}

/**
 * Latest `days` trading days of market participation, oldest first.
 * NUMERIC columns arrive as strings (pg wire format) — the chart option
 * builder maps to numbers.
 */
export async function getMarketParticipation(
  days: number,
): Promise<MarketParticipationRow[]> {
  return querySqlView<MarketParticipationRow>(
    "data_viz/api/services/analysis/market-beta/participation/series",
    { days },
  );
}
