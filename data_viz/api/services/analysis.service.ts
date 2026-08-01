/**
 * Analysis Commons service — exposes the analyses stored in the `analysis`
 * schema to the frontend.
 *
 * Backs the "MA-Spread" commons page (single-table model, ETF + Index):
 *   • listMovAveSpreadCodes()   — one row per security code with first/last
 * *                                 date, n_dates, and the latest snapshot of
 * *                                 all 9 gap_values (drives the list page).
 *   • getMovAveSpreadChart()    — all 9 pair time series for one security
 * *                                 (drives the 3×3 grid of small charts).
 *
 * Every call is scoped by `secType` ('etf' | 'index' | 'stock'); the detail
 * table carries a `sec_type` column that the queries filter
 * on, and the source OHLCV / MA JOINs branch accordingly:
 *   • etf   — stats.etf_basic_stats + LEFT JOIN stats.etf_adjustment
 *             (price = COALESCE(adj_close, close)) + stats.etf_tech_stats
 *   • index — stats.index_basic_stats (price = close) + stats.index_tech_stats
 *   • stock — (reserved) stats.stock_basic_stats + stats.stock_tech_stats
 *             (stock_tech_stats does not yet exist; stock sec_type will return
 *             empty results until that table is created and the build script
 *             populates stock rows).
 *
 * Detail table is WIDE: analysis.mov_ave_spreads_detail has one row per
 * (sec_type, code, date) with 9 gap columns (5 price_vs_ma* + 4 ma5_vs_ma*).
 * The chart endpoint JOINs with the sec-type-appropriate source tables to
 * recover the raw short/long values (price + MAs) needed to plot the two
 * curves + colored fill between them.
 */
import { queryRows, formatDate, toNum } from "../lib/db.js";
import type { QueryResultRow } from "pg";
import { stripExchangeSuffix } from "../lib/classify-etf.js";
import type {
  MaSpreadSecType,
  MovAveSpreadCodeRow,
  MovAveSpreadCodesResponse,
  MovAveSpreadChartResponse,
  MovAveSpreadDetailRow,
  MovAveSpreadPairSeries,
  MovAveSpreadLatestGap,
  PerfAttrCodeRow,
  PerfAttrCodesResponse,
  PerfAttrChartResponse,
  PerfAttrAttributionResponse,
  PerfAttrBenchmarkRow,
  PerfAttrSecType,
  SectorNode,
  IndustryNode,
  CapitalFlowIndustryRow,
  CapitalFlowIndustriesResponse,
  CapitalFlowChartRow,
  CapitalFlowChartResponse,
  CapitalFlowBenchmarkRow,
  CapitalFlowBenchmarksResponse,
} from "../../shared/types.js";

// ----------------------------------------------------------------------------
//  Pair configuration — canonical 9 pairs in display order.
//  ma_short = 0 is the price sentinel; ma_short = 5 uses ma5.
//  gap_column is the detail-table column holding this pair's gap_value.
// ----------------------------------------------------------------------------
type PairSpec = [ma_short: number, ma_long: number, gap_column: string];

const PAIR_ORDER: PairSpec[] = [
  [0, 5,   "price_vs_ma5"],
  [0, 20,  "price_vs_ma20"],
  [0, 60,  "price_vs_ma60"],
  [0, 120, "price_vs_ma120"],
  [0, 255, "price_vs_ma255"],
  [5, 20,  "ma5_vs_ma20"],
  [5, 60,  "ma5_vs_ma60"],
  [5, 120, "ma5_vs_ma120"],
  [5, 255, "ma5_vs_ma255"],
];

const VALID_SEC_TYPES: ReadonlySet<MaSpreadSecType> = new Set(["etf", "index", "stock"]);

function normalizeSecType(raw: string | undefined | null): MaSpreadSecType {
  const v = (raw ?? "").trim().toLowerCase();
  if (!VALID_SEC_TYPES.has(v as MaSpreadSecType)) {
    throw new Error(`Invalid sec_type: ${raw!}. Expected 'etf', 'index', or 'stock'.`);
  }
  return v as MaSpreadSecType;
}

// ----------------------------------------------------------------------------
//  DB row types
// ----------------------------------------------------------------------------
interface DbCodeRow extends QueryResultRow {
  code: string;
  name: string;
  first_date: Date | string;
  last_date: Date | string;
  n_dates: number;
  // 9 latest gap columns from the wide detail row at MAX(date).
  price_vs_ma5: number | null;
  price_vs_ma20: number | null;
  price_vs_ma60: number | null;
  price_vs_ma120: number | null;
  price_vs_ma255: number | null;
  ma5_vs_ma20: number | null;
  ma5_vs_ma60: number | null;
  ma5_vs_ma120: number | null;
  ma5_vs_ma255: number | null;
  // All-time max gain / max loss across all 9 pairs (fractional).
  max_gain: number | null;
  max_loss: number | null;
  max_spread: number | null;
}

interface DbChartRow extends QueryResultRow {
  date: Date | string;
  price: number | null;
  ma5: number | null;
  ma20: number | null;
  ma60: number | null;
  ma120: number | null;
  ma255: number | null;
  // 9 gap columns from the detail row.
  price_vs_ma5: number | null;
  price_vs_ma20: number | null;
  price_vs_ma60: number | null;
  price_vs_ma120: number | null;
  price_vs_ma255: number | null;
  ma5_vs_ma20: number | null;
  ma5_vs_ma60: number | null;
  ma5_vs_ma120: number | null;
  ma5_vs_ma255: number | null;
  // 10 slope/curvature columns from the detail row.
  price_slope: number | null;
  ma5_slope: number | null;
  ma20_slope: number | null;
  ma60_slope: number | null;
  ma120_slope: number | null;
  ma255_slope: number | null;
  ma5_curvature: number | null;
  ma20_curvature: number | null;
  ma60_curvature: number | null;
  ma120_curvature: number | null;
  ma255_curvature: number | null;
  price_curvature: number | null;
}

// ----------------------------------------------------------------------------
//  Helpers
// ----------------------------------------------------------------------------
/** Strip the exchange suffix from a DB code ("510050.SS" → "510050").
 *  For index codes (already bare, e.g. "000300") this is a no-op. */
function stripped(code: string): string {
  return stripExchangeSuffix(code);
}

/** Build the display label for a (ma_short, ma_long) pair. */
function pairLabel(maShort: number, maLong: number): string {
  return maShort === 0 ? `Price/MA${maLong}` : `MA${maShort}/MA${maLong}`;
}

/** Pick the long-MA value for a chart row given the ma_long window. */
function pickLong(r: DbChartRow, maLong: number): number | null {
  switch (maLong) {
    case 5:   return toNum(r.ma5);
    case 20:  return toNum(r.ma20);
    case 60:  return toNum(r.ma60);
    case 120: return toNum(r.ma120);
    case 255: return toNum(r.ma255);
    default:  return null;
  }
}

/** Pick the slope (1st derivative) of MA{window} from a chart row. */
function pickSlope(r: DbChartRow, window: number): number | null {
  switch (window) {
    case 5:   return toNum(r.ma5_slope);
    case 20:  return toNum(r.ma20_slope);
    case 60:  return toNum(r.ma60_slope);
    case 120: return toNum(r.ma120_slope);
    case 255: return toNum(r.ma255_slope);
    default:  return null;
  }
}

/** Pick the curvature (2nd derivative) of MA{window} from a chart row. */
function pickCurvature(r: DbChartRow, window: number): number | null {
  switch (window) {
    case 5:   return toNum(r.ma5_curvature);
    case 20:  return toNum(r.ma20_curvature);
    case 60:  return toNum(r.ma60_curvature);
    case 120: return toNum(r.ma120_curvature);
    case 255: return toNum(r.ma255_curvature);
    default:  return null;
  }
}

// ----------------------------------------------------------------------------
//  Per-sec_type source-table config — used to branch the chart JOINs and
//  the name lookup. ETFs use etf_basic_stats + etf_adjustment + etf_tech_stats
//  (price = COALESCE(adj_close, close)); indices use index_basic_stats +
//  index_tech_stats (price = close, no adjustment table).
// ----------------------------------------------------------------------------
interface SecSource {
  /** Schema-qualified identity table for the asset name lookup. */
  identityTable: string;
  /** FROM clause for the chart query — already includes the JOINs needed to
   *  recover price + all 5 MAs alongside the 9 gap columns. The detail
   *  table alias is `d` and is filtered by `d.sec_type = $2`. */
  chartFromClause: string;
  /** SQL expression for the per-row price column. */
  priceExpr: string;
}

const SEC_SOURCES: Record<MaSpreadSecType, SecSource> = {
  etf: {
    identityTable: "stats.etf_identity",
    chartFromClause:
      "FROM analysis.mov_ave_spreads_detail d\n" +
      "  JOIN stats.etf_basic_stats   b ON b.date = d.date AND b.code = d.code\n" +
      "  LEFT JOIN stats.etf_adjustment a ON a.date = d.date AND a.code = d.code\n" +
      "  LEFT JOIN stats.etf_tech_stats  t ON t.date = d.date AND t.code = d.code",
    priceExpr: "COALESCE(a.adj_close, b.close)",
  },
  index: {
    identityTable: "stats.index_identity",
    chartFromClause:
      "FROM analysis.mov_ave_spreads_detail d\n" +
      "  JOIN stats.index_basic_stats b ON b.date = d.date AND b.code = d.code\n" +
      "  LEFT JOIN stats.index_tech_stats t ON t.date = d.date AND t.code = d.code",
    priceExpr: "b.close",
  },
  // Stock is registered so the codes endpoint (which only touches
  // mov_ave_spreads_detail + stock_identity, both of which exist) returns an
  // empty list gracefully. The chart endpoint references stock_tech_stats
  // (which does NOT yet exist) and will fail with 500 if called directly —
  // but the UI never calls it for stock because codes returns empty (no
  // stock rows are populated by the build script yet). Replace this branch
  // with a real stock_tech_stats JOIN once that table is created.
  stock: {
    identityTable: "stats.stock_identity",
    chartFromClause:
      "FROM analysis.mov_ave_spreads_detail d\n" +
      "  JOIN stats.stock_basic_stats b ON b.date = d.date AND b.code = d.code\n" +
      "  LEFT JOIN stats.stock_tech_stats t ON t.date = d.date AND t.code = d.code",
    priceExpr: "b.close",
  },
};

// ----------------------------------------------------------------------------
//  listMovAveSpreadCodes — one row per asset code with first/last date,
//  n_dates, and the latest snapshot of all 9 gap_values (for sparkline /
//  sort). Server-side: DISTINCT ON (code) picks the latest wide detail row
//  per code; the 9 gap columns are passed through to TypeScript, which
//  assembles them into the latest_gaps array.
// ----------------------------------------------------------------------------
function buildCodesSql(secType: MaSpreadSecType): string {
  const src = SEC_SOURCES[secType];
  return `
    WITH latest_name AS (
      SELECT DISTINCT ON (code) code, name
      FROM ${src.identityTable}
      ORDER BY code, date DESC
    ),
    code_dates AS (
      SELECT
        code,
        MIN(date) AS first_date,
        MAX(date) AS last_date,
        COUNT(DISTINCT date) AS n_dates
      FROM analysis.mov_ave_spreads_detail
      WHERE sec_type = $1
      GROUP BY code
    ),
    latest_row AS (
      SELECT DISTINCT ON (code) *
      FROM analysis.mov_ave_spreads_detail
      WHERE sec_type = $1
      ORDER BY code, date DESC
    ),
    code_ranges AS (
      SELECT
        code,
        GREATEST(
          MAX(price_vs_ma5), MAX(price_vs_ma20), MAX(price_vs_ma60),
          MAX(price_vs_ma120), MAX(price_vs_ma255),
          MAX(ma5_vs_ma20), MAX(ma5_vs_ma60), MAX(ma5_vs_ma120), MAX(ma5_vs_ma255)
        ) AS max_gain,
        LEAST(
          MIN(price_vs_ma5), MIN(price_vs_ma20), MIN(price_vs_ma60),
          MIN(price_vs_ma120), MIN(price_vs_ma255),
          MIN(ma5_vs_ma20), MIN(ma5_vs_ma60), MIN(ma5_vs_ma120), MIN(ma5_vs_ma255)
        ) AS max_loss
      FROM analysis.mov_ave_spreads_detail
      WHERE sec_type = $1
      GROUP BY code
    )
    SELECT
      cd.code,
      COALESCE(n.name, '')   AS name,
      cd.first_date,
      cd.last_date,
      cd.n_dates,
      lr.price_vs_ma5, lr.price_vs_ma20, lr.price_vs_ma60,
      lr.price_vs_ma120, lr.price_vs_ma255,
      lr.ma5_vs_ma20, lr.ma5_vs_ma60, lr.ma5_vs_ma120, lr.ma5_vs_ma255,
      cr.max_gain,
      cr.max_loss,
      (cr.max_gain - cr.max_loss) AS max_spread
    FROM code_dates cd
    LEFT JOIN latest_name n  ON n.code  = cd.code
    LEFT JOIN latest_row lr ON lr.code = cd.code
    LEFT JOIN code_ranges cr ON cr.code = cd.code
    ORDER BY (cr.max_gain - cr.max_loss) DESC NULLS LAST, cd.code
  `;
}

export async function listMovAveSpreadCodes(
  rawSecType: string | undefined | null,
): Promise<MovAveSpreadCodesResponse> {
  const secType = normalizeSecType(rawSecType);
  const rows = await queryRows<DbCodeRow>(buildCodesSql(secType), [secType]);
  const codes: MovAveSpreadCodeRow[] = rows.map((r) => {
    // Build the latest_gaps array from the 9 wide gap columns.
    const latestGaps: MovAveSpreadLatestGap[] = PAIR_ORDER.map(
      ([maShort, maLong, gapCol]) => ({
        ma_short: maShort,
        ma_long: maLong,
        gap_value: toNum(r[gapCol as keyof DbCodeRow]),
      }),
    );
    return {
      code: stripped(r.code),
      name: r.name ?? "",
      first_date: formatDate(r.first_date),
      last_date: formatDate(r.last_date),
      n_dates: Number(r.n_dates) || 0,
      latest_gaps: latestGaps,
      max_gain: toNum(r.max_gain),
      max_loss: toNum(r.max_loss),
      max_spread: toNum(r.max_spread),
    };
  });
  return { codes };
}

// ----------------------------------------------------------------------------
//  getMovAveSpreadChart — all 9 pair time series for one asset.
//
//  JOINs analysis.mov_ave_spreads_detail with the asset-appropriate source
//  tables (etf_basic_stats + etf_adjustment + etf_tech_stats for ETFs;
//  index_basic_stats + index_tech_stats for indices) to recover:
//    • price (COALESCE(adj_close, close) for ETFs; close for indices)
//    • ma5 / ma20 / ma60 / ma120 / ma255
//  …alongside the 9 precomputed gap_value columns. Client-side, we fan each
//  row out into 9 pair series entries (short_value, long_value, gap_value).
// ----------------------------------------------------------------------------
function buildChartSql(secType: MaSpreadSecType): string {
  const src = SEC_SOURCES[secType];
  return `
    SELECT
      d.date,
      ${src.priceExpr} AS price,
      t.ma5, t.ma20, t.ma60, t.ma120, t.ma255,
      d.price_vs_ma5, d.price_vs_ma20, d.price_vs_ma60,
      d.price_vs_ma120, d.price_vs_ma255,
      d.ma5_vs_ma20, d.ma5_vs_ma60, d.ma5_vs_ma120, d.ma5_vs_ma255,
      d.price_slope, d.ma5_slope, d.ma20_slope, d.ma60_slope, d.ma120_slope, d.ma255_slope,
      d.price_curvature, d.ma5_curvature, d.ma20_curvature, d.ma60_curvature,
      d.ma120_curvature, d.ma255_curvature
    ${src.chartFromClause}
    WHERE d.sec_type = $2
      AND REGEXP_REPLACE(d.code, '\\.(SZ|SS|SH)$', '') = $1::text
    ORDER BY d.date ASC
  `;
}

function buildNameSql(secType: MaSpreadSecType): string {
  const src = SEC_SOURCES[secType];
  return `
    SELECT DISTINCT ON (code) code, name
    FROM ${src.identityTable}
    WHERE REGEXP_REPLACE(code, '\\.(SZ|SS|SH)$', '') = $1::text
    ORDER BY code, date DESC
  `;
}

export async function getMovAveSpreadChart(
  rawCode: string,
  rawSecType: string | undefined | null,
): Promise<MovAveSpreadChartResponse> {
  const secType = normalizeSecType(rawSecType);
  const target = stripped(rawCode);

  // Fetch chart rows + name in parallel.
  const [chartRows, nameRows] = await Promise.all([
    queryRows<DbChartRow>(buildChartSql(secType), [target, secType]),
    queryRows<{ name: string | null }>(buildNameSql(secType), [target]),
  ]);

  const name = nameRows[0]?.name ?? "";

  // Initialize the 9 pair series in canonical order.
  const byPair = new Map<string, MovAveSpreadPairSeries>();
  for (const [ms, ml] of PAIR_ORDER) {
    const key = `${ms}/${ml}`;
    byPair.set(key, {
      ma_short: ms,
      ma_long: ml,
      pair_label: pairLabel(ms, ml),
      rows: [],
    });
  }

  // Fan each chart row out into 9 pair entries.
  for (const r of chartRows) {
    const dateStr = formatDate(r.date);
    const price = toNum(r.price);
    const ma5 = toNum(r.ma5);
    for (const [maShort, maLong, gapCol] of PAIR_ORDER) {
      const series = byPair.get(`${maShort}/${maLong}`);
      if (!series) continue;
      const shortVal = maShort === 0 ? price : ma5;
      const longVal = pickLong(r, maLong);
      const gapVal = toNum(r[gapCol as keyof DbChartRow]);
      // slope/curvature: when ma_short = 0 the short series is price, so use
      // price_slope / price_curvature; otherwise use the short MA's derivatives.
      const shortSlope = maShort === 0 ? toNum(r.price_slope) : pickSlope(r, maShort);
      const shortCurv  = maShort === 0 ? toNum(r.price_curvature) : pickCurvature(r, maShort);
      const row: MovAveSpreadDetailRow = {
        date: dateStr,
        short_value: shortVal,
        long_value: longVal,
        gap_value: gapVal,
        short_slope: shortSlope,
        short_curvature: shortCurv,
        long_slope: pickSlope(r, maLong),
        long_curvature: pickCurvature(r, maLong),
      };
      series.rows.push(row);
    }
  }

  return {
    code: target,
    name,
    pairs: PAIR_ORDER.map(([ms, ml]) => byPair.get(`${ms}/${ml}`)!),
  };
}

// ============================================================================
//  Performance Attribution — ETF/Index subjects × Index benchmarks
//    analysis.sec_alloc_perf_attribution
//    PK: (code, date, sec_type, benchmark_code)
// ============================================================================

/** Whitelisted sec_type → name table mapping (safe for string interpolation). */
const PERF_ATTR_NAME_TABLE: Record<string, string> = {
  etf: "stats.etf_identity",
  index: "stats.index_identity",
};

interface DbPerfAttrCodeRow extends QueryResultRow {
  code: string;
  name: string;
  first_date: Date | string;
  last_date: Date | string;
  n_dates: number;
  benchmarks: string[];
  latest_active_return: number | null;
  avg_abs_active_return: number | null;
}

export async function listPerfAttrCodes(
  secType: PerfAttrSecType,
): Promise<PerfAttrCodesResponse> {
  const nameTable = PERF_ATTR_NAME_TABLE[secType] ?? PERF_ATTR_NAME_TABLE.etf;
  const sql = `
    WITH latest_name AS (
      SELECT DISTINCT ON (code) code, name
      FROM ${nameTable}
      ORDER BY code, date DESC
    ),
    code_stats AS (
      SELECT
        code,
        MIN(date) AS first_date,
        MAX(date) AS last_date,
        COUNT(DISTINCT date) AS n_dates,
        AVG(ABS(active_return)) AS avg_abs_active_return
      FROM analysis.sec_alloc_perf_attribution
      WHERE sec_type = $1::text
      GROUP BY code
    ),
    bench_list AS (
      SELECT code, ARRAY_AGG(DISTINCT benchmark_code ORDER BY benchmark_code) AS benchmarks
      FROM analysis.sec_alloc_perf_attribution
      WHERE sec_type = $1::text
      GROUP BY code
    ),
    latest_active AS (
      SELECT DISTINCT ON (code) code, active_return AS latest_active_return
      FROM analysis.sec_alloc_perf_attribution
      WHERE sec_type = $1::text
        AND benchmark_code = '000300'
      ORDER BY code, date DESC
    )
    SELECT
      cs.code,
      COALESCE(n.name, '') AS name,
      cs.first_date,
      cs.last_date,
      cs.n_dates,
      COALESCE(bl.benchmarks, '{}') AS benchmarks,
      la.latest_active_return,
      cs.avg_abs_active_return
    FROM code_stats cs
    LEFT JOIN latest_name n  ON n.code  = cs.code
    LEFT JOIN bench_list bl  ON bl.code = cs.code
    LEFT JOIN latest_active la ON la.code = cs.code
    ORDER BY cs.avg_abs_active_return DESC NULLS LAST, cs.code
  `;
  const rows = await queryRows<DbPerfAttrCodeRow>(sql, [secType]);
  const codes: PerfAttrCodeRow[] = rows.map((r) => ({
    code: stripped(r.code),
    name: r.name ?? "",
    first_date: formatDate(r.first_date),
    last_date: formatDate(r.last_date),
    n_dates: Number(r.n_dates) || 0,
    benchmarks: Array.isArray(r.benchmarks) ? r.benchmarks : [],
    latest_active_return: toNum(r.latest_active_return),
    avg_abs_active_return: toNum(r.avg_abs_active_return),
  }));
  return { sec_type: secType, codes };
}

// ----------------------------------------------------------------------------

interface DbPerfAttrAttributionRow extends QueryResultRow {
  benchmark_code: string;
  benchmark_name: string | null;
  date: Date | string;
  subject_return: number | null;
  benchmark_return: number | null;
  active_return: number | null;
  code_sec_shared_weight: number | null;
  benchmark_sec_shared_weight: number | null;
  etf_amount_ratio: number | null;
  benchmark_etf_amount: number | null;
  code_etf_amount: number | null;
  is_broad_market: boolean | null;
}

export async function getPerfAttrAttribution(
  rawCode: string,
  secType: PerfAttrSecType,
): Promise<PerfAttrAttributionResponse> {
  const target = stripped(rawCode);
  const nameTable = PERF_ATTR_NAME_TABLE[secType] ?? PERF_ATTR_NAME_TABLE.etf;
  const sql = `
    WITH latest_date AS (
      SELECT MAX(date) AS max_date
      FROM analysis.sec_alloc_perf_attribution
      WHERE sec_type = $1::text
        AND REGEXP_REPLACE(code, '\\.(SZ|SS|SH)$', '') = $2::text
    ),
    subject_name AS (
      SELECT DISTINCT ON (code) code, name
      FROM ${nameTable}
      WHERE REGEXP_REPLACE(code, '\\.(SZ|SS|SH)$', '') = $2::text
      ORDER BY code, date DESC
    )
    SELECT
      a.benchmark_code,
      bi.name AS benchmark_name,
      a.date,
      a.subject_return,
      a.benchmark_return,
      a.active_return,
      a.code_sec_shared_weight,
      a.benchmark_sec_shared_weight,
      a.etf_amount_ratio_benchmark_to_code AS etf_amount_ratio,
      a.benchmark_etf_amount,
      a.code_etf_amount,
      bm.is_broad_market
    FROM analysis.sec_alloc_perf_attribution a
    CROSS JOIN latest_date ld
    LEFT JOIN LATERAL (
      SELECT DISTINCT ON (code) code, name
      FROM stats.index_identity
      WHERE code = a.benchmark_code
      ORDER BY code, date DESC
    ) bi ON true
    LEFT JOIN LATERAL (
      -- Broad-market flag from stats.sec_index_tags: TRUE iff ANY tag for
      -- this benchmark index has is_broad_market = TRUE. NULL when the
      -- benchmark has no tags (e.g. unclassified index).
      SELECT BOOL_OR(is_broad_market) AS is_broad_market
      FROM stats.sec_index_tags
      WHERE code = a.benchmark_code
    ) bm ON true
    WHERE a.sec_type = $1::text
      AND REGEXP_REPLACE(a.code, '\\.(SZ|SS|SH)$', '') = $2::text
      AND a.date = ld.max_date
    ORDER BY a.benchmark_code
  `;
  const [attrRows, nameRows] = await Promise.all([
    queryRows<DbPerfAttrAttributionRow>(sql, [secType, target]),
    queryRows<{ name: string | null }>(
      `SELECT DISTINCT ON (code) code, name FROM ${nameTable} WHERE REGEXP_REPLACE(code, '\\.(SZ|SS|SH)$', '') = $1::text ORDER BY code, date DESC`,
      [target],
    ),
  ]);

  const benchmarks: PerfAttrBenchmarkRow[] = attrRows.map((r) => ({
    benchmark_code: r.benchmark_code,
    benchmark_name: r.benchmark_name ?? "",
    date: formatDate(r.date),
    subject_return: toNum(r.subject_return),
    benchmark_return: toNum(r.benchmark_return),
    active_return: toNum(r.active_return),
    code_sec_shared_weight: toNum(r.code_sec_shared_weight),
    benchmark_sec_shared_weight: toNum(r.benchmark_sec_shared_weight),
    etf_amount_ratio: toNum(r.etf_amount_ratio),
    benchmark_etf_amount: toNum(r.benchmark_etf_amount),
    code_etf_amount: toNum(r.code_etf_amount),
    is_broad_market: r.is_broad_market === null ? null : Boolean(r.is_broad_market),
  }));

  return {
    code: target,
    name: nameRows[0]?.name ?? "",
    sec_type: secType,
    latest_date: benchmarks[0]?.date ?? "",
    benchmarks,
  };
}

// ----------------------------------------------------------------------------
//  listPerfAttrThemes — two-level L1 sector → L2 industry → items tree for the
//  Perf Attribution page's ThemeSelector. Mirrors listThemes() in
//  etf-margin.service.ts and listIndexThemes() in index-baseline.service.ts,
//  but only includes codes that have rows in analysis.sec_alloc_perf_attribution
//  for the requested sec_type.
// ----------------------------------------------------------------------------

/** Whitelisted sec_type → meta-table mapping (safe for string interpolation).
 *  Both ETF and index classification now live in the unified stats.sec_classification
 *  table, discriminated by the `type` column. */
const PERF_ATTR_META_TABLE: Record<string, string> = {
  etf: "stats.sec_classification",
  index: "stats.sec_classification",
};

/** Whitelisted sec_type → type discriminator for sec_classification WHERE clause. */
const PERF_ATTR_META_TYPE: Record<string, string> = {
  etf: "etf",
  index: "index",
};

interface DbPerfAttrMetaRow extends QueryResultRow {
  code: string;
  name: string;
  sector_id: string;
  sector_label: string;
  industry_id: string;
  industry_label: string;
  industry_slug: string;
}

export async function listPerfAttrThemes(
  secType: PerfAttrSecType,
): Promise<SectorNode[]> {
  const metaTable = PERF_ATTR_META_TABLE[secType] ?? PERF_ATTR_META_TABLE.etf;
  const metaType = PERF_ATTR_META_TYPE[secType] ?? PERF_ATTR_META_TYPE.etf;
  // Select distinct codes that have perf-attr rows, then join with the
  // classification meta table (sec_classification filtered by type) to recover
  // sector/industry classification. Labels are denormalized onto
  // sec_classification — no JOIN to a catalog table is needed.
  const sql = `
    WITH perf_codes AS (
      SELECT DISTINCT code
      FROM analysis.sec_alloc_perf_attribution
      WHERE sec_type = $1::text
    )
    SELECT
      pc.code,
      COALESCE(m.name, '')             AS name,
      COALESCE(m.sector_id,       'OTHER')  AS sector_id,
      COALESCE(m.sector_label,    '其他')   AS sector_label,
      COALESCE(m.industry_id,     'OTHER')  AS industry_id,
      COALESCE(m.industry_label,  '未分类') AS industry_label,
      COALESCE(m.industry_slug,   'other')  AS industry_slug
    FROM perf_codes pc
    LEFT JOIN ${metaTable} m ON m.code = pc.code AND m.type = $2::text
  `;
  const rows = await queryRows<DbPerfAttrMetaRow>(sql, [secType, metaType]);

  const sectorMap = new Map<string, {
    sector_label: string;
    industries: Map<string, IndustryNode>;
  }>();

  for (const r of rows) {
    // Strip exchange suffix so the items[] codes match the codes returned by
    // listPerfAttrCodes (which also strips the suffix).
    const code = stripExchangeSuffix(r.code);
    if (!code) continue;
    const item = { code, name: r.name ?? "" };

    if (!sectorMap.has(r.sector_id)) {
      sectorMap.set(r.sector_id, { sector_label: r.sector_label, industries: new Map() });
    }
    const sector = sectorMap.get(r.sector_id)!;
    if (!sector.industries.has(r.industry_id)) {
      sector.industries.set(r.industry_id, {
        industry_id: r.industry_id,
        industry_label: r.industry_label,
        industry_slug: r.industry_slug,
        count: 0,
        items: [],
      });
    }
    const ind = sector.industries.get(r.industry_id)!;
    ind.items.push(item);
    ind.count++;
  }

  const sectors: SectorNode[] = [];
  for (const [sector_id, sector] of sectorMap) {
    const industries = Array.from(sector.industries.values()).sort((a, b) => {
      if (a.industry_id === "OTHER") return 1;
      if (b.industry_id === "OTHER") return -1;
      return b.count - a.count;
    });
    sectors.push({
      sector_id,
      sector_label: sector.sector_label,
      count: industries.reduce((sum, i) => sum + i.count, 0),
      industries,
    });
  }
  sectors.sort((a, b) => {
    if (a.sector_id === "OTHER") return 1;
    if (b.sector_id === "OTHER") return -1;
    return b.count - a.count;
  });
  return sectors;
}

// ----------------------------------------------------------------------------

interface DbPerfAttrChartRow extends QueryResultRow {
  date: Date | string;
  subject_return: number | null;
  benchmark_return: number | null;
  active_return: number | null;
  etf_amount_ratio: number | null;
  benchmark_etf_amount: number | null;
  code_etf_amount: number | null;
  benchmark_etf_num: number | null;
  code_etf_num: number | null;
  benchmark_industry_id: string | null;
  code_industry_id: string | null;
  benchmark_industry_etf_amount: number | null;
  code_industry_etf_amount: number | null;
  benchmark_industry_etf_num: number | null;
  code_industry_etf_num: number | null;
  subject_close: number | null;
  benchmark_close: number | null;
  corr_5d: number | null;
  corr_20d: number | null;
  corr_60d: number | null;
  corr_255d: number | null;
}

/** Per-sec_type subject source-table JOIN + price expression for the
 *  close-price lookup in getPerfAttrChart. Mirrors the SEC_SOURCES pattern
 *  above but keyed on the perf-attr table alias `a` (not `d`).
 *
 *  `industryCodeExpr` resolves the subject's industry lookup key — for ETF
 *  subjects, the linked parent index's code (joined to sec_classification
 *  type='index' for industry_id); for index subjects, the bare subject code. */
const PERF_ATTR_SUBJECT_SOURCE: Record<PerfAttrSecType, {
  joinClause: string;
  priceExpr: string;
  industryCodeExpr: string;
}> = {
  etf: {
    joinClause:
      "LEFT JOIN stats.etf_basic_stats   sb ON sb.date = a.date AND sb.code = a.code\n" +
      "LEFT JOIN stats.etf_adjustment    sa ON sa.date = a.date AND sa.code = a.code",
    priceExpr: "COALESCE(sa.adj_close, sb.close)",
    // ETF subject → look up parent_index_code, then the index's industry_id.
    industryCodeExpr:
      "(SELECT sc_etf.parent_index_code FROM stats.sec_classification sc_etf " +
      " WHERE sc_etf.code = a.code AND sc_etf.type = 'etf' AND sc_etf.parent_index_code <> '' " +
      " LIMIT 1)",
  },
  index: {
    joinClause:
      "LEFT JOIN stats.index_basic_stats  sb ON sb.date = a.date AND sb.code = a.code",
    priceExpr: "sb.close",
    // Index subject → own industry_id (strip exchange suffix just in case).
    industryCodeExpr: "REGEXP_REPLACE(a.code, '\\.(SZ|SS|SH)$', '')",
  },
};

export async function getPerfAttrChart(
  rawCode: string,
  rawBenchmarkCode: string,
  secType: PerfAttrSecType,
): Promise<PerfAttrChartResponse> {
  const target = stripped(rawCode);
  const benchmarkCode = rawBenchmarkCode.trim();
  const nameTable = PERF_ATTR_NAME_TABLE[secType] ?? PERF_ATTR_NAME_TABLE.etf;
  const subjSrc = PERF_ATTR_SUBJECT_SOURCE[secType] ?? PERF_ATTR_SUBJECT_SOURCE.etf;

  const [chartRows, nameRows, benchNameRows] = await Promise.all([
    queryRows<DbPerfAttrChartRow>(
      `SELECT a.date,
              a.subject_return,
              a.benchmark_return,
              a.active_return,
              a.etf_amount_ratio_benchmark_to_code AS etf_amount_ratio,
              a.benchmark_etf_amount,
              a.code_etf_amount,
              ieb.etf_num AS benchmark_etf_num,
              iec.etf_num AS code_etf_num,
              bindustry.industry_id AS benchmark_industry_id,
              cindustry.industry_id AS code_industry_id,
              etbind.total_etf_amt AS benchmark_industry_etf_amount,
              etcind.total_etf_amt AS code_industry_etf_amount,
              etbind.etf_num AS benchmark_industry_etf_num,
              etcind.etf_num AS code_industry_etf_num,
              a.corr_5d,
              a.corr_20d,
              a.corr_60d,
              a.corr_255d,
              ${subjSrc.priceExpr} AS subject_close,
              ib.close AS benchmark_close
       FROM analysis.sec_alloc_perf_attribution a
       ${subjSrc.joinClause}
       LEFT JOIN stats.index_basic_stats ib ON ib.date = a.date AND ib.code = a.benchmark_code
       LEFT JOIN stats.index_exts ieb ON ieb.date = a.date AND ieb.code = a.benchmark_code
       LEFT JOIN stats.index_exts iec ON iec.date = a.date AND iec.code = a.code
       -- Benchmark index industry (constant per benchmark_code)
       LEFT JOIN LATERAL (
         SELECT industry_id FROM stats.sec_classification
         WHERE code = a.benchmark_code AND type = 'index'
         LIMIT 1
       ) bindustry ON true
       -- Subject industry (constant per subject code): resolved via
       -- industryCodeExpr which differs by sec_type (ETF → parent index's
       -- industry; Index → own industry).
       LEFT JOIN LATERAL (
         SELECT industry_id FROM stats.sec_classification
         WHERE code = ${subjSrc.industryCodeExpr} AND type = 'index'
         LIMIT 1
       ) cindustry ON true
       -- Industry-level ETF trading amounts (per date, per industry_id).
       -- NULL when the industry_id is NULL or no ETF tracks any index in
       -- that industry on this date.
       LEFT JOIN stats.etf_trading_amt etbind
         ON etbind.date = a.date AND etbind.code = bindustry.industry_id
       LEFT JOIN stats.etf_trading_amt etcind
         ON etcind.date = a.date AND etcind.code = cindustry.industry_id
       WHERE a.sec_type = $1::text
         AND REGEXP_REPLACE(a.code, '\\.(SZ|SS|SH)$', '') = $2::text
         AND a.benchmark_code = $3::text
       ORDER BY a.date ASC`,
      [secType, target, benchmarkCode],
    ),
    queryRows<{ name: string | null }>(
      `SELECT DISTINCT ON (code) code, name FROM ${nameTable} WHERE REGEXP_REPLACE(code, '\\.(SZ|SS|SH)$', '') = $1::text ORDER BY code, date DESC`,
      [target],
    ),
    queryRows<{ name: string | null }>(
      `SELECT DISTINCT ON (code) code, name FROM stats.index_identity WHERE code = $1::text ORDER BY code, date DESC`,
      [benchmarkCode],
    ),
  ]);

  return {
    code: target,
    name: nameRows[0]?.name ?? "",
    benchmark_code: benchmarkCode,
    benchmark_name: benchNameRows[0]?.name ?? "",
    rows: chartRows.map((r) => ({
      date: formatDate(r.date),
      subject_return: toNum(r.subject_return),
      benchmark_return: toNum(r.benchmark_return),
      active_return: toNum(r.active_return),
      etf_amount_ratio: toNum(r.etf_amount_ratio),
      benchmark_etf_amount: toNum(r.benchmark_etf_amount),
      code_etf_amount: toNum(r.code_etf_amount),
      benchmark_etf_num: r.benchmark_etf_num == null ? null : Number(r.benchmark_etf_num),
      code_etf_num: r.code_etf_num == null ? null : Number(r.code_etf_num),
      benchmark_industry_id: r.benchmark_industry_id ?? null,
      code_industry_id: r.code_industry_id ?? null,
      benchmark_industry_etf_amount: toNum(r.benchmark_industry_etf_amount),
      code_industry_etf_amount: toNum(r.code_industry_etf_amount),
      benchmark_industry_etf_num: r.benchmark_industry_etf_num == null ? null : Number(r.benchmark_industry_etf_num),
      code_industry_etf_num: r.code_industry_etf_num == null ? null : Number(r.code_industry_etf_num),
      subject_close: toNum(r.subject_close),
      benchmark_close: toNum(r.benchmark_close),
      corr_5d: toNum(r.corr_5d),
      corr_20d: toNum(r.corr_20d),
      corr_60d: toNum(r.corr_60d),
      corr_255d: toNum(r.corr_255d),
    })),
  };
}

// ============================================================================
//  Capital Flow — Industry × Broad-Market Benchmark
//    analysis.capital_flow
//    PK: (date, industry_id, benchmark_code)
//
//  Three endpoints:
//    listCapitalFlowIndustries()  — one row per industry_id (with latest
//                                   pure/observed popularity stats for sort).
//    getCapitalFlowBenchmarks(id) — per-benchmark breakdown for one industry.
//    getCapitalFlowChart(id, bc)  — per-date time series for one pair.
// ============================================================================
interface DbCapitalFlowIndustryRow extends QueryResultRow {
  industry_id: string;
  industry_label: string;
  first_date: Date | string;
  last_date: Date | string;
  n_dates: number;
  n_benchmarks: number;
  latest_pure_popularity: number | null;
  latest_observed_popularity: number | null;
  latest_retention: number | null;
  avg_pure_growth: number | null;
}

export async function listCapitalFlowIndustries(): Promise<CapitalFlowIndustriesResponse> {
  // For each industry: aggregate across all paired benchmarks on the latest
  // date (SUM pure/observed popularity, since one industry × multiple
  // benchmarks yields multiple rows per date). Also compute average
  // pure_growth across all dates/benchmarks as a trend strength indicator.
  const sql = `
    WITH industry_dates AS (
      SELECT industry_id, MIN(date) AS first_date, MAX(date) AS last_date,
             COUNT(DISTINCT date) AS n_dates,
             COUNT(DISTINCT benchmark_code) AS n_benchmarks
      FROM analysis.capital_flow
      GROUP BY industry_id
    ),
    latest_per_industry AS (
      SELECT DISTINCT ON (industry_id) industry_id, date AS latest_date
      FROM analysis.capital_flow
      ORDER BY industry_id, date DESC
    ),
    latest_sums AS (
      SELECT cf.industry_id,
             SUM(cf.pure_popularity)         AS latest_pure_popularity,
             SUM(cf.observed_popularity)     AS latest_observed_popularity,
             SUM(cf.pure_popularity) /
               NULLIF(SUM(cf.observed_popularity), 0) AS latest_retention
      FROM analysis.capital_flow cf
      JOIN latest_per_industry l ON l.industry_id = cf.industry_id
                                AND l.latest_date = cf.date
      GROUP BY cf.industry_id
    ),
    avg_growth AS (
      SELECT industry_id, AVG(pure_growth) AS avg_pure_growth
      FROM analysis.capital_flow
      WHERE pure_growth IS NOT NULL
      GROUP BY industry_id
    )
    SELECT
      id.industry_id,
      COALESCE(sc.industry_label, id.industry_id) AS industry_label,
      id.first_date,
      id.last_date,
      id.n_dates,
      id.n_benchmarks,
      ls.latest_pure_popularity,
      ls.latest_observed_popularity,
      ls.latest_retention,
      ag.avg_pure_growth
    FROM industry_dates id
    LEFT JOIN latest_sums ls ON ls.industry_id = id.industry_id
    LEFT JOIN avg_growth ag  ON ag.industry_id  = id.industry_id
    LEFT JOIN LATERAL (
      SELECT industry_label FROM stats.sec_classification
      WHERE industry_id = id.industry_id AND type = 'index'
      LIMIT 1
    ) sc ON true
    ORDER BY ls.latest_pure_popularity DESC NULLS LAST, id.industry_id
  `;
  const rows = await queryRows<DbCapitalFlowIndustryRow>(sql, []);
  const industries: CapitalFlowIndustryRow[] = rows.map((r) => ({
    industry_id: r.industry_id,
    industry_label: r.industry_label ?? "",
    first_date: formatDate(r.first_date),
    last_date: formatDate(r.last_date),
    n_dates: Number(r.n_dates) || 0,
    n_benchmarks: Number(r.n_benchmarks) || 0,
    latest_pure_popularity: toNum(r.latest_pure_popularity),
    latest_observed_popularity: toNum(r.latest_observed_popularity),
    latest_retention: toNum(r.latest_retention),
    avg_pure_growth: toNum(r.avg_pure_growth),
  }));
  return { industries };
}

// ----------------------------------------------------------------------------

interface DbCapitalFlowBenchmarkRow extends QueryResultRow {
  benchmark_code: string;
  benchmark_label: string;
  avg_w_i: number | null;
  avg_w_b: number | null;
  total_pure_popularity: number | null;
  total_observed_popularity: number | null;
  avg_pure_growth: number | null;
  n_dates: number;
  first_date: Date | string;
  last_date: Date | string;
}

export async function getCapitalFlowBenchmarks(
  rawIndustryId: string,
): Promise<CapitalFlowBenchmarksResponse> {
  const industryId = (rawIndustryId ?? "").trim();
  if (!industryId) {
    throw new Error("Missing 'industry_id' parameter");
  }
  const sql = `
    SELECT
      cf.benchmark_code,
      COALESCE(bi.name, cf.benchmark_label, cf.benchmark_code) AS benchmark_label,
      AVG(cf.industry_overlap_weight)  AS avg_w_i,
      AVG(cf.benchmark_overlap_weight) AS avg_w_b,
      SUM(cf.pure_popularity)         AS total_pure_popularity,
      SUM(cf.observed_popularity)     AS total_observed_popularity,
      AVG(cf.pure_growth)             AS avg_pure_growth,
      COUNT(DISTINCT cf.date)         AS n_dates,
      MIN(cf.date)                    AS first_date,
      MAX(cf.date)                    AS last_date
    FROM analysis.capital_flow cf
    INNER JOIN stats.sec_index_tags sit
      ON sit.code = cf.benchmark_code AND sit.is_broad_market = TRUE
    LEFT JOIN LATERAL (
      SELECT DISTINCT ON (code) code, name
      FROM stats.index_identity
      WHERE code = cf.benchmark_code
      ORDER BY code, date DESC
    ) bi ON true
    WHERE cf.industry_id = $1::text
    GROUP BY cf.benchmark_code, bi.name, cf.benchmark_label
    ORDER BY SUM(cf.pure_popularity) DESC NULLS LAST, cf.benchmark_code
  `;
  const [benchRows, labelRows] = await Promise.all([
    queryRows<DbCapitalFlowBenchmarkRow>(sql, [industryId]),
    queryRows<{ industry_label: string | null }>(
      `SELECT industry_label FROM stats.sec_classification
       WHERE industry_id = $1::text AND type = 'index' LIMIT 1`,
      [industryId],
    ),
  ]);
  const benchmarks: CapitalFlowBenchmarkRow[] = benchRows.map((r) => ({
    benchmark_code: r.benchmark_code,
    benchmark_label: r.benchmark_label ?? "",
    avg_w_i: toNum(r.avg_w_i),
    avg_w_b: toNum(r.avg_w_b),
    total_pure_popularity: toNum(r.total_pure_popularity),
    total_observed_popularity: toNum(r.total_observed_popularity),
    avg_pure_growth: toNum(r.avg_pure_growth),
    n_dates: Number(r.n_dates) || 0,
    first_date: formatDate(r.first_date),
    last_date: formatDate(r.last_date),
  }));
  return {
    industry_id: industryId,
    industry_label: labelRows[0]?.industry_label ?? "",
    benchmarks,
  };
}

// ----------------------------------------------------------------------------

interface DbCapitalFlowChartRow extends QueryResultRow {
  date: Date | string;
  benchmark_code: string;
  industry_etf_amount: number | null;
  industry_etf_num: number | null;
  industry_return: number | null;
  benchmark_etf_amount: number | null;
  benchmark_etf_num: number | null;
  benchmark_return: number | null;
  industry_overlap_weight: number | null;
  benchmark_overlap_weight: number | null;
  industry_overlap_amount: number | null;
  benchmark_overlap_amount: number | null;
  pure_flow: number | null;
  pure_growth: number | null;
  pure_popularity: number | null;
  observed_popularity: number | null;
  popularity_retention: number | null;
}

/** Map one DB chart row to the API row shape (shared by all benchmarks). */
function mapCapitalFlowChartRow(r: DbCapitalFlowChartRow): CapitalFlowChartRow {
  return {
    date: formatDate(r.date),
    industry_etf_amount: toNum(r.industry_etf_amount),
    industry_etf_num: r.industry_etf_num == null ? null : Number(r.industry_etf_num),
    industry_return: toNum(r.industry_return),
    benchmark_etf_amount: toNum(r.benchmark_etf_amount),
    benchmark_etf_num: r.benchmark_etf_num == null ? null : Number(r.benchmark_etf_num),
    benchmark_return: toNum(r.benchmark_return),
    industry_overlap_weight: toNum(r.industry_overlap_weight),
    benchmark_overlap_weight: toNum(r.benchmark_overlap_weight),
    industry_overlap_amount: toNum(r.industry_overlap_amount),
    benchmark_overlap_amount: toNum(r.benchmark_overlap_amount),
    pure_flow: toNum(r.pure_flow),
    pure_growth: toNum(r.pure_growth),
    pure_popularity: toNum(r.pure_popularity),
    observed_popularity: toNum(r.observed_popularity),
    popularity_retention: toNum(r.popularity_retention),
  };
}

/**
 * Fetch the per-date time series for one industry × a SET of benchmark codes.
 *
 * Replaces the old single-benchmark `getCapitalFlowChart` so the frontend can
 * request only the specific benchmarks it wants to plot (e.g. just 000300 +
 * 000852) in a single DB round-trip, instead of loading every benchmark for
 * the industry. Returns one `CapitalFlowChartResponse` per requested code, in
 * the requested order. Codes with no rows still yield an entry (empty rows).
 */
export async function getCapitalFlowCharts(
  rawIndustryId: string,
  rawBenchmarkCodes: string[] | null | undefined,
): Promise<CapitalFlowChartResponse[]> {
  const industryId = (rawIndustryId ?? "").trim();
  if (!industryId) throw new Error("Missing 'industry_id' parameter");

  // Trim + dedupe + drop empty codes.
  const codes = Array.from(new Set(
    (rawBenchmarkCodes ?? [])
      .map((c) => (c ?? "").trim())
      .filter((c) => c.length > 0),
  ));
  if (codes.length === 0) return [];

  const sql = `
    SELECT
      cf.benchmark_code,
      cf.date,
      cf.industry_etf_amount,
      cf.industry_etf_num,
      cf.industry_return,
      cf.benchmark_etf_amount,
      cf.benchmark_etf_num,
      cf.benchmark_return,
      cf.industry_overlap_weight,
      cf.benchmark_overlap_weight,
      cf.industry_overlap_amount,
      cf.benchmark_overlap_amount,
      cf.pure_flow,
      cf.pure_growth,
      cf.pure_popularity,
      cf.observed_popularity,
      cf.popularity_retention
    FROM analysis.capital_flow cf
    WHERE cf.industry_id = $1::text
      AND cf.benchmark_code = ANY($2::text[])
    ORDER BY cf.benchmark_code, cf.date ASC
  `;
  const [chartRows, indLabelRows, benchNameRows] = await Promise.all([
    queryRows<DbCapitalFlowChartRow>(sql, [industryId, codes]),
    queryRows<{ industry_label: string | null }>(
      `SELECT industry_label FROM stats.sec_classification
       WHERE industry_id = $1::text AND type = 'index' LIMIT 1`,
      [industryId],
    ),
    queryRows<{ code: string; name: string | null }>(
      `SELECT DISTINCT ON (code) code, name FROM stats.index_identity
       WHERE code = ANY($1::text[]) ORDER BY code, date DESC`,
      [codes],
    ),
  ]);

  const industryLabel = indLabelRows[0]?.industry_label ?? "";
  const benchNameMap = new Map<string, string>();
  for (const r of benchNameRows) benchNameMap.set(r.code, r.name ?? "");

  // Group rows by benchmark_code (rows are already ordered by code then date).
  const byCode = new Map<string, DbCapitalFlowChartRow[]>();
  for (const r of chartRows) {
    const arr = byCode.get(r.benchmark_code);
    if (arr) arr.push(r);
    else byCode.set(r.benchmark_code, [r]);
  }

  // Emit one response per requested code, in the requested order.
  return codes.map((bc) => ({
    industry_id: industryId,
    industry_label: industryLabel,
    benchmark_code: bc,
    benchmark_label: benchNameMap.get(bc) ?? "",
    rows: (byCode.get(bc) ?? []).map(mapCapitalFlowChartRow),
  }));
}

// ----------------------------------------------------------------------------
//  listCapitalFlowThemes — two-level L1 sector → L2 industry tree for the
//  Capital Flow page's ThemeSelector. Mirrors listPerfAttrThemes(), but the
//  selectable unit is an INDUSTRY (analysis.capital_flow.industry_id), not a
//  code. Each industry is classified via stats.sec_classification (type='index')
//  so it inherits the same sector/industry taxonomy used elsewhere.
//
//  items[] is left empty: the page plots the industry as a whole, so there are
//  no per-code children to render. industry count = number of dates the
//  industry has data for (a useful "how much data" signal on the chip).
// ----------------------------------------------------------------------------
interface DbCapitalFlowThemeRow extends QueryResultRow {
  industry_id: string;
  sector_id: string;
  sector_label: string;
  industry_label: string;
  industry_slug: string;
  n_dates: number;
  n_benchmarks: number;
}

export async function listCapitalFlowThemes(): Promise<SectorNode[]> {
  const sql = `
    WITH flow_industries AS (
      SELECT
        industry_id,
        COUNT(DISTINCT date)           AS n_dates,
        COUNT(DISTINCT benchmark_code) AS n_benchmarks
      FROM analysis.capital_flow
      GROUP BY industry_id
    )
    SELECT
      fi.industry_id,
      COALESCE(m.sector_id,      'OTHER')  AS sector_id,
      COALESCE(m.sector_label,   '其他')   AS sector_label,
      COALESCE(m.industry_label, fi.industry_id) AS industry_label,
      COALESCE(m.industry_slug,  LOWER(fi.industry_id)) AS industry_slug,
      fi.n_dates,
      fi.n_benchmarks
    FROM flow_industries fi
    LEFT JOIN LATERAL (
      SELECT sector_id, sector_label, industry_label, industry_slug
      FROM stats.sec_classification
      WHERE industry_id = fi.industry_id AND type = 'index'
      LIMIT 1
    ) m ON true
  `;
  const rows = await queryRows<DbCapitalFlowThemeRow>(sql, []);

  const sectorMap = new Map<string, {
    sector_label: string;
    industries: Map<string, IndustryNode & { n_dates: number; n_benchmarks: number }>;
  }>();

  for (const r of rows) {
    if (!sectorMap.has(r.sector_id)) {
      sectorMap.set(r.sector_id, { sector_label: r.sector_label, industries: new Map() });
    }
    const sector = sectorMap.get(r.sector_id)!;
    if (!sector.industries.has(r.industry_id)) {
      sector.industries.set(r.industry_id, {
        industry_id: r.industry_id,
        industry_label: r.industry_label,
        industry_slug: r.industry_slug,
        count: 0,
        items: [],
        n_dates: Number(r.n_dates) || 0,
        n_benchmarks: Number(r.n_benchmarks) || 0,
      });
    }
  }

  const sectors: SectorNode[] = [];
  for (const [sector_id, sector] of sectorMap) {
    const industries = Array.from(sector.industries.values()).sort((a, b) => {
      if (a.industry_id === "OTHER") return 1;
      if (b.industry_id === "OTHER") return -1;
      return b.n_dates - a.n_dates;
    });
    // Per-industry chip count = number of broad-market benchmarks paired
    // with this industry (informative and stable across selections).
    for (const ind of industries) ind.count = ind.n_benchmarks;
    sectors.push({
      sector_id,
      sector_label: sector.sector_label,
      count: industries.reduce((sum, i) => sum + i.n_benchmarks, 0),
      industries: industries.map(({ n_dates, n_benchmarks, ...rest }) => rest),
    });
  }
  sectors.sort((a, b) => {
    if (a.sector_id === "OTHER") return 1;
    if (b.sector_id === "OTHER") return -1;
    return b.count - a.count;
  });
  return sectors;
}
