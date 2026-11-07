/**
 * Market Beta charts — getMarketBetaChart (per-index close
 * series for ONE industry + the precomputed mean/var aggregation rows per
 * pool_size slice) and getMarketBetaChartByCode (a single index,
 * for strategy-primary indices without an industry_id). Both overlay the
 * broad-market benchmark close series. Member values are rebased to 100
 * client-side.
 * Split out of the former monolithic industry-sentiments.ts.
 */
import { formatDate, toNum } from "@api/lib/db";
import { querySqlView, sqlFrag } from "@api/lib/sql-views";
import type { QueryResultRow } from "pg";
import { stripExchangeSuffix } from "@api/lib/classify-etf";
import { matchesClassification } from "@api/services/_shared";
import { labelDecodeSql, POOL_SIZE_SORT_SQL } from "@api/services/label-codes";
import type {
  MarketBetaIndexRow,
  MarketBetaIndex,
  MarketBetaAggRow,
  MarketBetaChartResponse,
} from "@shared/types";
import {
  listMarketBetaMetaRows,
  type DbMarketBetaMetaRow,
} from "./meta.js";

interface DbMarketBetaChartRow extends QueryResultRow {
  code: string;
  name: string;
  exchange: string | null;
  date: Date | string;
  close: number | null;
  stock_num: number | null;
}

interface DbMarketBetaAggRow extends QueryResultRow {
  date: Date | string;
  pool_size: "small" | "mid" | "large" | "all";
  index_count: number | null;
  mean_close: number | null;
  var_price: number | null;
  mean_pe: number | null;
  total_trading_amount: number | null;
}

/** Broad-market benchmark indices offered in the UI benchmark dropdown.
 *  Each is fetched as a close series and rebased to 100 client-side (same as
 *  member indices). The frontend renders a multi-select dropdown so the user
 *  can tick any subset to overlay on the chart. */
const MARKET_BETA_BENCHMARKS = [
  { code: "000300", name: "沪深300", stockNum: 300 },
  { code: "000016", name: "上证50", stockNum: 50 },
  { code: "000852", name: "中证1000", stockNum: 1000 },
  { code: "932000", name: "中证2000", stockNum: 2000 },
  { code: "000688", name: "科创50", stockNum: 50 },
];

/** Fetch + shape the broad-market benchmark close series shared by both
 *  chart endpoints. One entry per benchmark with close rows, in the
 *  canonical MARKET_BETA_BENCHMARKS order; benchmarks with no close
 *  data are omitted. Rebased to 100 client-side, same as member indices. */
async function fetchBenchmarks(): Promise<MarketBetaIndex[]> {
  const benchmarkCodes = MARKET_BETA_BENCHMARKS.map((b) => b.code);
  const benchmarkRows = await querySqlView<{ code: string; date: Date | string; close: number | null }>(
    "data_viz/api/services/analysis/market-beta/chart/benchmark_closes",
    { benchmark_codes: benchmarkCodes },
  );
  const benchRowsByCode = new Map<string, { date: Date | string; close: number | null }[]>();
  for (const r of benchmarkRows) {
    let arr = benchRowsByCode.get(r.code);
    if (!arr) {
      arr = [];
      benchRowsByCode.set(r.code, arr);
    }
    arr.push({ date: r.date, close: r.close });
  }
  const benchmarks: MarketBetaIndex[] = [];
  for (const b of MARKET_BETA_BENCHMARKS) {
    const rows = benchRowsByCode.get(b.code);
    if (!rows || rows.length === 0) continue;
    benchmarks.push({
      code: b.code,
      name: `${b.name} (benchmark)`,
      exchange: null,
      rows: rows.map((r) => ({
        date: formatDate(r.date),
        close: toNum(r.close),
        stock_num: b.stockNum,
      })),
    });
  }
  return benchmarks;
}

/** Group flat chart rows by index code, preserving first-seen order. */
function groupChartRowsByCode(
  chartRows: DbMarketBetaChartRow[],
): Map<string, MarketBetaIndex> {
  const byCode = new Map<string, MarketBetaIndex>();
  for (const r of chartRows) {
    let idx = byCode.get(r.code);
    if (!idx) {
      idx = { code: r.code, name: r.name ?? "", exchange: r.exchange ?? null, rows: [] };
      byCode.set(r.code, idx);
    }
    const row: MarketBetaIndexRow = {
      date: formatDate(r.date),
      close: toNum(r.close),
      stock_num: r.stock_num == null ? null : Number(r.stock_num),
    };
    idx.rows.push(row);
  }
  return byCode;
}

/**
 * Fetch per-index close time series for ONE industry + precomputed mean/var
 * aggregation rows per pool_size slice. Returns one entry per member index
 * in the industry (stats.sec_classification type='index' AND industry_id =
 * $1 AND index has composition data in stats.sec_composition), each with
 * its raw daily close series AND stock_num (looked up from the latest
 * sec_composition snapshot). The frontend rebases each index to 100 at the
 * start of the visible (zoom) window and overlays the precomputed mean/var
 * for the user-selected pool_size slice.
 *
 * COMPOSITION-ONLY FILTER: indices WITHOUT any sec_composition snapshot are
 * excluded entirely — they are never returned to the frontend.
 *
 * ALSO returns the close series for each broad-market benchmark
 * (MARKET_BETA_BENCHMARKS) so the frontend can overlay any subset
 * via the benchmark dropdown.
 *
 * Indices classified into the industry but lacking any index_basic_stats
 * rows are omitted (nothing to plot). The shared date axis is the union of
 * all member indices' trading days — handled client-side by the chart.
 */
export async function getMarketBetaChart(
  rawIndustryId: string,
  sector?: string | null,
  industry?: string | null,
  strategy?: string | null,
  theme?: string | null,
): Promise<MarketBetaChartResponse> {
  const industryId = (rawIndustryId ?? "").trim();
  if (!industryId) throw new Error("Missing 'industry_id' parameter");
  const sectorFilter = (sector ?? "").trim();
  const industryFilter = (industry ?? "").trim();
  const strategyFilter = (strategy ?? "").trim();
  const themeFilter = (theme ?? "").trim();
  const hasClassFilter = !!(sectorFilter || industryFilter || strategyFilter || themeFilter);

  // Single query: member indices' classification JOINed with their daily
  // closes AND a LATERAL lookup for stock_num from the latest sec_composition
  // snapshot (covers ALL compositioned indices, not just ETF-tracked ones —
  // index_exts only covers 147 of 222 compositioned indices).
  // sec_classification.name carries the index display name, so no separate
  // identity JOIN is needed. The SELECT lives in the sql view
  // data_viz/api/services/analysis/market-beta/chart/industry_series.
  //
  // COMPOSITION-ONLY FILTER: the EXISTS subquery restricts to indices that
  // have at least one sec_composition snapshot. Indices without ANY
  // composition data are dropped entirely.
  // Precomputed mean/var aggregation rows from stats.industry_basic_stats
  // (built by builds.industry). Returns rows for ALL 4 pool_size slices —
  // the frontend filters to the user-selected slice for the overlay.
  // pool_size decodes to its TEXT label in the SELECT (the UI contract);
  // ORDER BY keeps the pre-refactor TEXT presentation order via
  // label_dim.sort_order (pool codes are semantic-ordered, not
  // alphabetical).
  const [chartRows, aggRows, labelRows, benchmarks] = await Promise.all([
    querySqlView<DbMarketBetaChartRow>(
      "data_viz/api/services/analysis/market-beta/chart/industry_series",
      { industry_id: industryId },
    ),
    querySqlView<DbMarketBetaAggRow>(
      "data_viz/api/services/analysis/market-beta/chart/industry_agg",
      {
        industry_id: industryId,
        pool_size_decode: sqlFrag(labelDecodeSql("pool_size", "pool_size")),
        pool_size_sort: sqlFrag(POOL_SIZE_SORT_SQL),
      },
    ),
    querySqlView<{ industry_label: string | null }>(
      "data_viz/api/services/analysis/market-beta/chart/industry_label",
      { industry_id: industryId },
    ),
    fetchBenchmarks(),
  ]);

  const byCode = groupChartRowsByCode(chartRows);

  const aggregation: MarketBetaAggRow[] = aggRows.map((r) => ({
    date: formatDate(r.date),
    pool_size: r.pool_size,
    index_count: r.index_count == null ? null : Number(r.index_count),
    mean_close: toNum(r.mean_close),
    var_price: toNum(r.var_price),
    mean_pe: toNum(r.mean_pe),
    total_trading_amount: toNum(r.total_trading_amount),
  }));

  // When a classification filter is active, fetch the per-code meta rows and
  // filter member indices via matchesClassification(). Industry and strategy
  // filters are mutually exclusive (handled by matchesClassification). Index
  // codes are already bare, so they map directly to the meta rows.
  let indices = Array.from(byCode.values());
  if (hasClassFilter) {
    const metaRows = await listMarketBetaMetaRows();
    const classMap = new Map<string, DbMarketBetaMetaRow>();
    for (const m of metaRows) {
      classMap.set(m.code, m);
    }
    indices = indices.filter((idx) => {
      const meta = classMap.get(idx.code);
      if (!meta) return false;
      return matchesClassification(meta, sectorFilter, industryFilter, strategyFilter, themeFilter);
    });
  }

  return {
    industry_id: industryId,
    industry_label: labelRows[0]?.industry_label ?? "",
    indices,
    aggregation,
    benchmarks,
  };
}

/**
 * Fetch chart data (close series + stock_num) for a SINGLE index code.
 * Used when the user clicks an L3 index chip under a strategy/theme —
 * strategy-primary indices may not have an industry_id, so the standard
 * industry-based chart endpoint can't find them.
 *
 * Returns an MarketBetaChartResponse with at most one index in
 * `indices` (empty if the code has no index_basic_stats rows). Aggregation
 * is always empty (no precomputed mean/var for a single index). Benchmarks
 * are included so the dropdown still works.
 */
export async function getMarketBetaChartByCode(
  rawCode: string,
): Promise<MarketBetaChartResponse> {
  const code = stripExchangeSuffix((rawCode ?? "").trim());
  if (!code) throw new Error("Missing 'code' parameter");

  const [chartRows, benchmarks] = await Promise.all([
    querySqlView<DbMarketBetaChartRow>(
      "data_viz/api/services/analysis/market-beta/chart/code_series",
      { code },
    ),
    fetchBenchmarks(),
  ]);

  // Build the single index entry (if any rows found).
  const indices: MarketBetaIndex[] = [];
  if (chartRows.length > 0) {
    const r0 = chartRows[0];
    const idx: MarketBetaIndex = {
      code: r0.code,
      name: r0.name ?? "",
      exchange: r0.exchange ?? null,
      rows: chartRows.map((r) => ({
        date: formatDate(r.date),
        close: toNum(r.close),
        stock_num: r.stock_num == null ? null : Number(r.stock_num),
      })),
    };
    indices.push(idx);
  }

  return {
    industry_id: code,
    industry_label: indices[0]?.name ?? code,
    indices,
    aggregation: [],
    benchmarks,
  };
}
