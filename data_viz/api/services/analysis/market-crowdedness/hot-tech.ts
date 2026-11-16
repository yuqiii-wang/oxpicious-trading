/**
 * Market Crowdedness hot-tech — getMarketCrowdednessHotTech: one
 * security's daily stats close with the hot-stock tech aggregates LEFT
 * JOINed from analysis.market_crowdedness_hot_tech — per (pool, date)
 * the equal-weight mean + median over the day's rolling hot set of
 * eight per-stock metrics (trend stretch ma5_ratio / px_vs_ma20 /
 * px_vs_ma60, momentum macd_hist / rsi14 / roc20, the per-stock volume
 * gauge amt_vs_ma20, and the day's return), each aggregate also read as
 * a percentile vs the pool's own trailing-250-day history. ROLLING only
 * — the table has no year-pinned variant (hot_year is not a param).
 * Rows are driven by the close source so out-of-scope codes plot close
 * only; metric fields are NULL on dates with no hot-tech row.
 */
import { formatDate, toNum } from "@api/lib/db";
import { querySqlView, sqlFrag } from "@api/lib/sql-views";
import type { QueryResultRow } from "pg";
import { codeVariants } from "@api/lib/classify-etf";
import { stripped } from "@api/services/analysis/_shared";
import { LABEL_CODE } from "@api/services/infra/label-codes";
import type {
  MarketCrowdednessHotTechResponse,
  MarketCrowdednessHotTechRow,
} from "@shared/types";
import { normalizeMcSecType } from "./_shared.js";
import { CHART_FROM_CLAUSE, IDENTITY_TABLE } from "./chart.js";

interface DbHotTechRow extends QueryResultRow {
  date: Date | string;
  close: number | string | null;
  hot_n: number | string | null;
  mean_ma5_ratio: number | string | null;
  mean_ma5_ratio_pct: number | string | null;
  median_ma5_ratio: number | string | null;
  median_ma5_ratio_pct: number | string | null;
  mean_px_vs_ma20: number | string | null;
  mean_px_vs_ma20_pct: number | string | null;
  median_px_vs_ma20: number | string | null;
  median_px_vs_ma20_pct: number | string | null;
  mean_px_vs_ma60: number | string | null;
  mean_px_vs_ma60_pct: number | string | null;
  median_px_vs_ma60: number | string | null;
  median_px_vs_ma60_pct: number | string | null;
  mean_macd_hist: number | string | null;
  mean_macd_hist_pct: number | string | null;
  median_macd_hist: number | string | null;
  median_macd_hist_pct: number | string | null;
  mean_rsi14: number | string | null;
  mean_rsi14_pct: number | string | null;
  median_rsi14: number | string | null;
  median_rsi14_pct: number | string | null;
  mean_roc20: number | string | null;
  mean_roc20_pct: number | string | null;
  median_roc20: number | string | null;
  median_roc20_pct: number | string | null;
  mean_amt_vs_ma20: number | string | null;
  mean_amt_vs_ma20_pct: number | string | null;
  median_amt_vs_ma20: number | string | null;
  median_amt_vs_ma20_pct: number | string | null;
  mean_ret: number | string | null;
  mean_ret_pct: number | string | null;
  median_ret: number | string | null;
  median_ret_pct: number | string | null;
}

export async function getMarketCrowdednessHotTech(
  rawCode: string,
  rawSecType: string | undefined | null,
): Promise<MarketCrowdednessHotTechResponse> {
  const secType = normalizeMcSecType(rawSecType);
  const target = stripped(rawCode);

  // The code predicate is repeated inside the LEFT JOIN ON clause on
  // purpose (the chart_series pattern — see the sql view's header).
  // The SELECT lives in the sql view
  // …/market-crowdedness/hot-tech/hot_tech_series; the name lookup
  // reuses the chart's security_name template.
  const codes = codeVariants(target);
  const [techRows, nameRows] = await Promise.all([
    querySqlView<DbHotTechRow>(
      "data_viz/api/services/analysis/market-crowdedness/hot-tech/hot_tech_series",
      {
        codes,
        sec_type: LABEL_CODE.sec_type[secType],
        from_clause: sqlFrag(CHART_FROM_CLAUSE[secType]),
      },
    ),
    querySqlView<{ code: string; name: string | null }>(
      "data_viz/api/services/analysis/market-crowdedness/chart/security_name",
      { codes, identity_table: sqlFrag(IDENTITY_TABLE[secType]) },
    ),
  ]);

  const name = nameRows[0]?.name ?? "";

  const rows: MarketCrowdednessHotTechRow[] = techRows.map((r) => ({
    date: formatDate(r.date),
    close: toNum(r.close),
    hot_n: toNum(r.hot_n),
    mean_ma5_ratio: toNum(r.mean_ma5_ratio),
    mean_ma5_ratio_pct: toNum(r.mean_ma5_ratio_pct),
    median_ma5_ratio: toNum(r.median_ma5_ratio),
    median_ma5_ratio_pct: toNum(r.median_ma5_ratio_pct),
    mean_px_vs_ma20: toNum(r.mean_px_vs_ma20),
    mean_px_vs_ma20_pct: toNum(r.mean_px_vs_ma20_pct),
    median_px_vs_ma20: toNum(r.median_px_vs_ma20),
    median_px_vs_ma20_pct: toNum(r.median_px_vs_ma20_pct),
    mean_px_vs_ma60: toNum(r.mean_px_vs_ma60),
    mean_px_vs_ma60_pct: toNum(r.mean_px_vs_ma60_pct),
    median_px_vs_ma60: toNum(r.median_px_vs_ma60),
    median_px_vs_ma60_pct: toNum(r.median_px_vs_ma60_pct),
    mean_macd_hist: toNum(r.mean_macd_hist),
    mean_macd_hist_pct: toNum(r.mean_macd_hist_pct),
    median_macd_hist: toNum(r.median_macd_hist),
    median_macd_hist_pct: toNum(r.median_macd_hist_pct),
    mean_rsi14: toNum(r.mean_rsi14),
    mean_rsi14_pct: toNum(r.mean_rsi14_pct),
    median_rsi14: toNum(r.median_rsi14),
    median_rsi14_pct: toNum(r.median_rsi14_pct),
    mean_roc20: toNum(r.mean_roc20),
    mean_roc20_pct: toNum(r.mean_roc20_pct),
    median_roc20: toNum(r.median_roc20),
    median_roc20_pct: toNum(r.median_roc20_pct),
    mean_amt_vs_ma20: toNum(r.mean_amt_vs_ma20),
    mean_amt_vs_ma20_pct: toNum(r.mean_amt_vs_ma20_pct),
    median_amt_vs_ma20: toNum(r.median_amt_vs_ma20),
    median_amt_vs_ma20_pct: toNum(r.median_amt_vs_ma20_pct),
    mean_ret: toNum(r.mean_ret),
    mean_ret_pct: toNum(r.mean_ret_pct),
    median_ret: toNum(r.median_ret),
    median_ret_pct: toNum(r.median_ret_pct),
  }));

  return { code: target, name, rows };
}
