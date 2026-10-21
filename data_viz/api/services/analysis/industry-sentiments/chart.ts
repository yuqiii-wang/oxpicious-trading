/**
 * Industry Sentiments charts — getIndustrySentimentsChart (per-index close
 * series for ONE industry + the precomputed mean/var aggregation rows per
 * pool_size slice) and getIndustrySentimentsChartByCode (a single index,
 * for strategy-primary indices without an industry_id). Both overlay the
 * broad-market benchmark close series. Member values are rebased to 100
 * client-side.
 * Split out of the former monolithic industry-sentiments.ts.
 */
import { queryRows, formatDate, toNum } from "@api/lib/db";
import type { QueryResultRow } from "pg";
import { stripExchangeSuffix } from "@api/lib/classify-etf";
import { matchesClassification } from "@api/services/_shared";
import { labelDecodeSql, POOL_SIZE_SORT_SQL } from "@api/services/label-codes";
import type {
  IndustrySentimentsIndexRow,
  IndustrySentimentsIndex,
  IndustrySentimentsAggRow,
  IndustrySentimentsChartResponse,
} from "@shared/types";
import {
  listIndustrySentimentsMetaRows,
  type DbIndustrySentimentsMetaRow,
} from "./meta.js";

interface DbIndustrySentimentsChartRow extends QueryResultRow {
  code: string;
  name: string;
  exchange: string | null;
  date: Date | string;
  close: number | null;
  stock_num: number | null;
}

interface DbIndustrySentimentsAggRow extends QueryResultRow {
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
const INDUSTRY_SENTIMENTS_BENCHMARKS = [
  { code: "000300", name: "沪深300", stockNum: 300 },
  { code: "000016", name: "上证50", stockNum: 50 },
  { code: "000852", name: "中证1000", stockNum: 1000 },
  { code: "932000", name: "中证2000", stockNum: 2000 },
  { code: "000688", name: "科创50", stockNum: 50 },
];

/** Fetch + shape the broad-market benchmark close series shared by both
 *  chart endpoints. One entry per benchmark with close rows, in the
 *  canonical INDUSTRY_SENTIMENTS_BENCHMARKS order; benchmarks with no close
 *  data are omitted. Rebased to 100 client-side, same as member indices. */
async function fetchBenchmarks(): Promise<IndustrySentimentsIndex[]> {
  const benchmarkCodes = INDUSTRY_SENTIMENTS_BENCHMARKS.map((b) => b.code);
  const benchmarkRows = await queryRows<{ code: string; date: Date | string; close: number | null }>(
    `SELECT code, date, close FROM stats.index_basic_stats
     WHERE code = ANY($1::text[]) ORDER BY code, date ASC`,
    [benchmarkCodes],
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
  const benchmarks: IndustrySentimentsIndex[] = [];
  for (const b of INDUSTRY_SENTIMENTS_BENCHMARKS) {
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
  chartRows: DbIndustrySentimentsChartRow[],
): Map<string, IndustrySentimentsIndex> {
  const byCode = new Map<string, IndustrySentimentsIndex>();
  for (const r of chartRows) {
    let idx = byCode.get(r.code);
    if (!idx) {
      idx = { code: r.code, name: r.name ?? "", exchange: r.exchange ?? null, rows: [] };
      byCode.set(r.code, idx);
    }
    const row: IndustrySentimentsIndexRow = {
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
 * (INDUSTRY_SENTIMENTS_BENCHMARKS) so the frontend can overlay any subset
 * via the benchmark dropdown.
 *
 * Indices classified into the industry but lacking any index_basic_stats
 * rows are omitted (nothing to plot). The shared date axis is the union of
 * all member indices' trading days — handled client-side by the chart.
 */
export async function getIndustrySentimentsChart(
  rawIndustryId: string,
  sector?: string | null,
  industry?: string | null,
  strategy?: string | null,
  theme?: string | null,
): Promise<IndustrySentimentsChartResponse> {
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
  // identity JOIN is needed.
  //
  // COMPOSITION-ONLY FILTER: the EXISTS subquery restricts to indices that
  // have at least one sec_composition snapshot. Indices without ANY
  // composition data are dropped entirely.
  const chartSql = `
    WITH stock_counts AS (
        SELECT code, snapshot_date,
               COUNT(DISTINCT stock_code) AS stock_num
        FROM stats.sec_composition
        WHERE source_type = 'index'
        GROUP BY code, snapshot_date
    )
    SELECT
      sc.code,
      COALESCE(sc.name, '') AS name,
      sc.exchange,
      ib.date,
      ib.close,
      latest.stock_num
    FROM stats.sec_classification sc
    JOIN stats.index_basic_stats ib
      ON ib.code = sc.code
    LEFT JOIN LATERAL (
        SELECT stock_num
        FROM stock_counts sc2
        WHERE sc2.code = ib.code
        ORDER BY snapshot_date DESC
        LIMIT 1
    ) latest ON true
    WHERE sc.type = 'index'
      AND sc.is_active = TRUE
      AND sc.industry_id = $1::text
      AND EXISTS (
          SELECT 1 FROM stats.sec_composition sc3
          WHERE sc3.code = sc.code AND sc3.source_type = 'index'
      )
    ORDER BY sc.code, ib.date ASC
  `;
  // Precomputed mean/var aggregation rows from stats.industry_basic_stats
  // (built by builds.industry). Returns rows for ALL 4 pool_size slices —
  // the frontend filters to the user-selected slice for the overlay.
  // pool_size decodes to its TEXT label in the SELECT (the UI contract);
  // ORDER BY keeps the pre-refactor TEXT presentation order via
  // label_dim.sort_order (pool codes are semantic-ordered, not
  // alphabetical).
  const aggSql = `
    SELECT date,
           ${labelDecodeSql("pool_size", "pool_size")} AS pool_size,
           index_count, mean_close, var_price, mean_pe, total_trading_amount
    FROM stats.industry_basic_stats
    WHERE industry_id = $1::text
    ORDER BY ${POOL_SIZE_SORT_SQL}, date ASC
  `;
  const [chartRows, aggRows, labelRows, benchmarks] = await Promise.all([
    queryRows<DbIndustrySentimentsChartRow>(chartSql, [industryId]),
    queryRows<DbIndustrySentimentsAggRow>(aggSql, [industryId]),
    queryRows<{ industry_label: string | null }>(
      `SELECT industry_label FROM stats.sec_classification
       WHERE industry_id = $1::text AND type = 'index' LIMIT 1`,
      [industryId],
    ),
    fetchBenchmarks(),
  ]);

  const byCode = groupChartRowsByCode(chartRows);

  const aggregation: IndustrySentimentsAggRow[] = aggRows.map((r) => ({
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
    const metaRows = await listIndustrySentimentsMetaRows();
    const classMap = new Map<string, DbIndustrySentimentsMetaRow>();
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
 * Returns an IndustrySentimentsChartResponse with at most one index in
 * `indices` (empty if the code has no index_basic_stats rows). Aggregation
 * is always empty (no precomputed mean/var for a single index). Benchmarks
 * are included so the dropdown still works.
 */
export async function getIndustrySentimentsChartByCode(
  rawCode: string,
): Promise<IndustrySentimentsChartResponse> {
  const code = stripExchangeSuffix((rawCode ?? "").trim());
  if (!code) throw new Error("Missing 'code' parameter");

  const chartSql = `
    WITH stock_counts AS (
        SELECT code, snapshot_date,
               COUNT(DISTINCT stock_code) AS stock_num
        FROM stats.sec_composition
        WHERE source_type = 'index'
        GROUP BY code, snapshot_date
    )
    SELECT
      sc.code,
      COALESCE(sc.name, '') AS name,
      sc.exchange,
      ib.date,
      ib.close,
      latest.stock_num
    FROM stats.sec_classification sc
    JOIN stats.index_basic_stats ib
      ON ib.code = sc.code
    LEFT JOIN LATERAL (
        SELECT stock_num
        FROM stock_counts sc2
        WHERE sc2.code = ib.code
        ORDER BY snapshot_date DESC
        LIMIT 1
    ) latest ON true
    WHERE sc.type = 'index'
      AND sc.code = $1::text
    ORDER BY ib.date ASC
  `;
  const [chartRows, benchmarks] = await Promise.all([
    queryRows<DbIndustrySentimentsChartRow>(chartSql, [code]),
    fetchBenchmarks(),
  ]);

  // Build the single index entry (if any rows found).
  const indices: IndustrySentimentsIndex[] = [];
  if (chartRows.length > 0) {
    const r0 = chartRows[0];
    const idx: IndustrySentimentsIndex = {
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
