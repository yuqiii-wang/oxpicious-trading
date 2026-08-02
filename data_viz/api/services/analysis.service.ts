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
  IndustrySentimentsIndexRow,
  IndustrySentimentsIndex,
  IndustrySentimentsAggRow,
  IndustrySentimentsChartResponse,
  IndustryCorrelationRow,
  IndustryCorrelationsResponse,
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
        COUNT(DISTINCT date) AS n_dates
      FROM analysis.sec_alloc_perf_attribution
      WHERE sec_type = $1::text
      GROUP BY code
    ),
    bench_list AS (
      SELECT code, ARRAY_AGG(DISTINCT benchmark_code ORDER BY benchmark_code) AS benchmarks
      FROM analysis.sec_alloc_perf_attribution
      WHERE sec_type = $1::text
      GROUP BY code
    )
    SELECT
      cs.code,
      COALESCE(n.name, '') AS name,
      cs.first_date,
      cs.last_date,
      cs.n_dates,
      COALESCE(bl.benchmarks, '{}') AS benchmarks
    FROM code_stats cs
    LEFT JOIN latest_name n  ON n.code  = cs.code
    LEFT JOIN bench_list bl  ON bl.code = cs.code
    ORDER BY cs.n_dates DESC NULLS LAST, cs.code
  `;
  const rows = await queryRows<DbPerfAttrCodeRow>(sql, [secType]);
  const codes: PerfAttrCodeRow[] = rows.map((r) => ({
    code: stripped(r.code),
    name: r.name ?? "",
    first_date: formatDate(r.first_date),
    last_date: formatDate(r.last_date),
    n_dates: Number(r.n_dates) || 0,
    benchmarks: Array.isArray(r.benchmarks) ? r.benchmarks : [],
  }));
  return { sec_type: secType, codes };
}

// ----------------------------------------------------------------------------

interface DbPerfAttrAttributionRow extends QueryResultRow {
  benchmark_code: string;
  benchmark_name: string | null;
  date: Date | string;
  code_sec_shared_weight: number | null;
  benchmark_sec_shared_weight: number | null;
  etf_amount_ratio: number | null;
  benchmark_etf_amount: number | null;
  code_etf_amount: number | null;
  is_broad_market: boolean | null;
  benchmark_return: number | null;
  subject_return: number | null;
}

export async function getPerfAttrAttribution(
  rawCode: string,
  secType: PerfAttrSecType,
  date?: string | null,
): Promise<PerfAttrAttributionResponse> {
  const target = stripped(rawCode);
  const nameTable = PERF_ATTR_NAME_TABLE[secType] ?? PERF_ATTR_NAME_TABLE.etf;
  // When a specific date is requested ($3), use it directly; otherwise fall
  // back to MAX(date) (latest trading day). COALESCE keeps a single SQL shape
  // so the prepared-statement cache stays effective.
  //
  // Fractional returns (benchmark_return, subject_return) are computed
  // on-the-fly via LATERAL joins to stats.index_basic_stats — they are NOT
  // stored as DB columns. The return = (close_t - close_{t-1}) / close_{t-1}.
  // Subject return uses index_basic_stats (correct for sec_type='index';
  // returns NULL for ETF subjects since their codes carry exchange suffixes
  // that don't match index_basic_stats.code).
  const sql = `
    WITH target_date AS (
      SELECT COALESCE(
        $3::date,
        (SELECT MAX(date) FROM analysis.sec_alloc_perf_attribution
         WHERE sec_type = $1::text
           AND REGEXP_REPLACE(code, '\\.(SZ|SS|SH)$', '') = $2::text)
      ) AS max_date
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
      a.code_sec_shared_weight,
      a.benchmark_sec_shared_weight,
      a.etf_amount_ratio_benchmark_to_code AS etf_amount_ratio,
      a.benchmark_etf_amount,
      a.code_etf_amount,
      bm.is_broad_market,
      CASE
        WHEN ib.close IS NOT NULL AND pb.close IS NOT NULL AND pb.close != 0
        THEN (ib.close - pb.close) / pb.close
        ELSE NULL
      END AS benchmark_return,
      CASE
        WHEN sb.close IS NOT NULL AND ps.close IS NOT NULL AND ps.close != 0
        THEN (sb.close - ps.close) / ps.close
        ELSE NULL
      END AS subject_return
    FROM analysis.sec_alloc_perf_attribution a
    CROSS JOIN target_date ld
    LEFT JOIN LATERAL (
      SELECT DISTINCT ON (code) code, name
      FROM stats.index_identity
      WHERE code = a.benchmark_code
      ORDER BY code, date DESC
    ) bi ON true
    LEFT JOIN LATERAL (
      SELECT close FROM stats.index_basic_stats
      WHERE code = a.benchmark_code AND date = a.date
    ) ib ON true
    LEFT JOIN LATERAL (
      SELECT close FROM stats.index_basic_stats
      WHERE code = a.benchmark_code AND date < a.date
      ORDER BY date DESC LIMIT 1
    ) pb ON true
    LEFT JOIN LATERAL (
      SELECT close FROM stats.index_basic_stats
      WHERE code = a.code AND date = a.date
    ) sb ON true
    LEFT JOIN LATERAL (
      SELECT close FROM stats.index_basic_stats
      WHERE code = a.code AND date < a.date
      ORDER BY date DESC LIMIT 1
    ) ps ON true
    LEFT JOIN LATERAL (
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
    queryRows<DbPerfAttrAttributionRow>(sql, [secType, target, date ?? null]),
    queryRows<{ name: string | null }>(
      `SELECT DISTINCT ON (code) code, name FROM ${nameTable} WHERE REGEXP_REPLACE(code, '\\.(SZ|SS|SH)$', '') = $1::text ORDER BY code, date DESC`,
      [target],
    ),
  ]);

  const benchmarks: PerfAttrBenchmarkRow[] = attrRows.map((r) => {
    const br = toNum(r.benchmark_return);
    const sr = toNum(r.subject_return);
    return {
      benchmark_code: r.benchmark_code,
      benchmark_name: r.benchmark_name ?? "",
      date: formatDate(r.date),
      code_sec_shared_weight: toNum(r.code_sec_shared_weight),
      benchmark_sec_shared_weight: toNum(r.benchmark_sec_shared_weight),
      etf_amount_ratio: toNum(r.etf_amount_ratio),
      benchmark_etf_amount: toNum(r.benchmark_etf_amount),
      code_etf_amount: toNum(r.code_etf_amount),
      is_broad_market: r.is_broad_market === null ? null : Boolean(r.is_broad_market),
      benchmark_return: br,
      subject_return: sr,
      active_return: br == null || sr == null ? null : sr - br,
    };
  });

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
  etf_amount_ratio: number | null;
  etf_amount_ratio_ma5: number | null;
  benchmark_etf_amount: number | null;
  code_etf_amount: number | null;
  benchmark_etf_num: number | null;
  code_etf_num: number | null;
  subject_close: number | null;
  benchmark_close: number | null;
  corr_5d: number | null;
  corr_20d: number | null;
  corr_60d: number | null;
  corr_255d: number | null;
}

/** Per-sec_type subject source-table JOIN + price expression for the
 *  close-price lookup in getPerfAttrChart. Mirrors the SEC_SOURCES pattern
 *  above but keyed on the perf-attr table alias `a` (not `d`). */
const PERF_ATTR_SUBJECT_SOURCE: Record<PerfAttrSecType, {
  joinClause: string;
  priceExpr: string;
}> = {
  etf: {
    joinClause:
      "LEFT JOIN stats.etf_basic_stats   sb ON sb.date = a.date AND sb.code = a.code\n" +
      "LEFT JOIN stats.etf_adjustment    sa ON sa.date = a.date AND sa.code = a.code",
    priceExpr: "COALESCE(sa.adj_close, sb.close)",
  },
  index: {
    joinClause:
      "LEFT JOIN stats.index_basic_stats  sb ON sb.date = a.date AND sb.code = a.code",
    priceExpr: "sb.close",
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

  const [chartRows, nameRows, benchNameRows, benchLinkedEtfs, codeLinkedEtfs] = await Promise.all([
    queryRows<DbPerfAttrChartRow>(
      `SELECT a.date,
              a.etf_amount_ratio_benchmark_to_code AS etf_amount_ratio,
              a.etf_amount_ratio_benchmark_to_code_ma5 AS etf_amount_ratio_ma5,
              a.benchmark_etf_amount,
              a.code_etf_amount,
              ieb.etf_num AS benchmark_etf_num,
              iec.etf_num AS code_etf_num,
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
    // ETFs tracking the benchmark index (parent_index_code = benchmark_code).
    queryRows<{ code: string; name: string | null }>(
      `SELECT code, name FROM stats.sec_classification
       WHERE type = 'etf' AND parent_index_code = $1::text
       ORDER BY name NULLS LAST, code`,
      [benchmarkCode],
    ),
    // ETFs tracking the subject index (parent_index_code = code). For ETF
    // subjects (sec_type='etf') this returns nothing — the subject IS the ETF.
    queryRows<{ code: string; name: string | null }>(
      `SELECT code, name FROM stats.sec_classification
       WHERE type = 'etf' AND parent_index_code = $1::text
       ORDER BY name NULLS LAST, code`,
      [target],
    ),
  ]);

  return {
    code: target,
    name: nameRows[0]?.name ?? "",
    benchmark_code: benchmarkCode,
    benchmark_name: benchNameRows[0]?.name ?? "",
    rows: chartRows.map((r) => ({
      date: formatDate(r.date),
      etf_amount_ratio: toNum(r.etf_amount_ratio),
      etf_amount_ratio_ma5: toNum(r.etf_amount_ratio_ma5),
      benchmark_etf_amount: toNum(r.benchmark_etf_amount),
      code_etf_amount: toNum(r.code_etf_amount),
      benchmark_etf_num: r.benchmark_etf_num == null ? null : Number(r.benchmark_etf_num),
      code_etf_num: r.code_etf_num == null ? null : Number(r.code_etf_num),
      subject_close: toNum(r.subject_close),
      benchmark_close: toNum(r.benchmark_close),
      corr_5d: toNum(r.corr_5d),
      corr_20d: toNum(r.corr_20d),
      corr_60d: toNum(r.corr_60d),
      corr_255d: toNum(r.corr_255d),
    })),
    benchmark_linked_etfs: benchLinkedEtfs.map((r) => ({
      code: r.code,
      name: r.name ?? r.code,
    })),
    code_linked_etfs: codeLinkedEtfs.map((r) => ({
      code: r.code,
      name: r.name ?? r.code,
    })),
  };
}

// ============================================================================
//  Industry Sentiments — member index values, rebased to 100 client-side
//
//  NO analysis.industry_sentiments table — the former cross-sectional
//  aggregation (max / min / mean / median / var of subject_return /
//  benchmark_return / active_return percentages) has been DROPPED. Mixing
//  scales across indices with different price levels was misleading, and
//  the broad-market-removal step was opaque.
//
//  NEW APPROACH: each industry's plot shows its member INDEX VALUES directly,
//  rebased to 100 at the start of the displayed (zoom) window. Rebased-to-100
//  makes member indices comparable regardless of absolute price level (e.g.
//  CSI 500 ~5500pts and SSE 50 ~2600pts plot on a common scale, so a +10%
//  move on either looks equally large). The rebasing is computed in the
//  BROWSER (IndustrySentimentsPage.tsx) from raw daily closes returned here.
//
//  Data source (queried directly — no aggregation table intermediary):
//    stats.index_basic_stats.close   (raw daily index closes)
//    JOIN stats.sec_classification    (type='index') for industry membership
//    stats.sec_composition            (stock_num → pool_size classification)
//    analysis.industry_sentiments     (precomputed mean/var per pool_size slice)
//
//  COMPOSITION-ONLY FILTER
//    Both endpoints restrict to indices that have at least one
//    stats.sec_composition snapshot (source_type='index'). Indices WITHOUT
//    any composition data are dropped entirely — they are never returned to
//    the frontend, and industries whose indices all lack composition do not
//    appear in the themes tree at all.
//
//  Two endpoints:
//    listIndustrySentimentsThemes()  — L1 sector → L2 industry tree from
//       stats.sec_classification (type='index') directly. industries.count =
//       number of member indices in the industry (informative "how many
//       indices contribute" chip). items[] left empty (the page plots the
//       industry as a whole, not per-code children).
//    getIndustrySentimentsChart(id)  — per-index close time series for ONE
//       industry + precomputed mean/var aggregation rows per pool_size slice.
//       Returns one entry per member index in the industry, each with its raw
//       daily close series AND stock_num (for pool_size classification +
//       tooltip display). Indices with no index_basic_stats rows are omitted.
// ============================================================================
interface DbIndustrySentimentsThemeRow extends QueryResultRow {
  industry_id: string;
  sector_id: string;
  sector_label: string;
  industry_label: string;
  industry_slug: string;
  /** Count of member indices (stats.sec_classification type='index') in this
   *  industry. Drives the per-industry chip count on the ThemeSelector. */
  member_count: number;
}

export async function listIndustrySentimentsThemes(): Promise<SectorNode[]> {
  // Build the L1 sector → L2 industry tree directly from sec_classification
  // (type='index'). No analysis-table intermediary — every classified index
  // contributes its (sector_id, industry_id) tag. Per-industry count = number
  // of member indices.
  //
  // COMPOSITION-ONLY FILTER: only indices with at least one sec_composition
  // snapshot (source_type='index') are counted. Industries whose indices ALL
  // lack composition data do not appear in the tree at all.
  const sql = `
    SELECT
      industry_id,
      COALESCE(NULLIF(sector_id, ''),      'OTHER')  AS sector_id,
      COALESCE(NULLIF(sector_label, ''),   '其他')   AS sector_label,
      COALESCE(NULLIF(industry_label, ''), industry_id) AS industry_label,
      COALESCE(NULLIF(industry_slug, ''),  LOWER(industry_id)) AS industry_slug,
      COUNT(*) AS member_count
    FROM stats.sec_classification sc
    WHERE type = 'index'
      AND industry_id IS NOT NULL
      AND industry_id <> ''
      AND COALESCE(sector_id, '') <> 'DEBT'
      AND EXISTS (
          SELECT 1 FROM stats.sec_composition sc2
          WHERE sc2.code = sc.code AND sc2.source_type = 'index'
      )
    GROUP BY industry_id, sector_id, sector_label, industry_label, industry_slug
  `;
  const rows = await queryRows<DbIndustrySentimentsThemeRow>(sql, []);

  const sectorMap = new Map<string, {
    sector_label: string;
    industries: Map<string, IndustryNode & { member_count: number }>;
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
        count: Number(r.member_count) || 0,
        items: [],
        member_count: Number(r.member_count) || 0,
      });
    }
  }

  const sectors: SectorNode[] = [];
  for (const [sector_id, sector] of sectorMap) {
    const industries = Array.from(sector.industries.values()).sort((a, b) => {
      if (a.industry_id === "OTHER") return 1;
      if (b.industry_id === "OTHER") return -1;
      return b.member_count - a.member_count;
    });
    sectors.push({
      sector_id,
      sector_label: sector.sector_label,
      count: industries.reduce((sum, i) => sum + i.member_count, 0),
      industries: industries.map(({ member_count, ...rest }) => rest),
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
  mean_rebased: number | null;
  var_rebased: number | null;
}

/** Broad-market benchmark indices offered in the UI benchmark dropdown.
 *  Each is fetched as a close series and rebased to 100 client-side (same as
 *  member indices). The frontend renders a multi-select dropdown so the user
 *  can tick any subset to overlay on the chart. */
const INDUSTRY_SENTIMENTS_BENCHMARKS = [
  { code: "000300", name: "沪深300", stockNum: 300 },
  { code: "000016", name: "上证50", stockNum: 50 },
  { code: "000852", name: "中证1000", stockNum: 1000 },
  { code: "000688", name: "科创50", stockNum: 50 },
];

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
): Promise<IndustrySentimentsChartResponse> {
  const industryId = (rawIndustryId ?? "").trim();
  if (!industryId) throw new Error("Missing 'industry_id' parameter");

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
      AND sc.industry_id = $1::text
      AND EXISTS (
          SELECT 1 FROM stats.sec_composition sc3
          WHERE sc3.code = sc.code AND sc3.source_type = 'index'
      )
    ORDER BY sc.code, ib.date ASC
  `;
  // Precomputed mean/var aggregation rows from analysis.industry_sentiments.
  // Returns rows for ALL 4 pool_size slices — the frontend filters to the
  // user-selected slice for the overlay.
  const aggSql = `
    SELECT date, pool_size, index_count, mean_rebased, var_rebased
    FROM analysis.industry_sentiments
    WHERE industry_id = $1::text
    ORDER BY pool_size, date ASC
  `;
  const benchmarkCodes = INDUSTRY_SENTIMENTS_BENCHMARKS.map((b) => b.code);
  const [chartRows, aggRows, labelRows, benchmarkRows] = await Promise.all([
    queryRows<DbIndustrySentimentsChartRow>(chartSql, [industryId]),
    queryRows<DbIndustrySentimentsAggRow>(aggSql, [industryId]),
    queryRows<{ industry_label: string | null }>(
      `SELECT industry_label FROM stats.sec_classification
       WHERE industry_id = $1::text AND type = 'index' LIMIT 1`,
      [industryId],
    ),
    // Benchmarks: close series for each broad-market benchmark in
    // INDUSTRY_SENTIMENTS_BENCHMARKS. Rebased to 100 client-side same as
    // member indices. The frontend renders a multi-select dropdown so the
    // user can tick any subset to overlay.
    queryRows<{ code: string; date: Date | string; close: number | null }>(
      `SELECT code, date, close FROM stats.index_basic_stats
       WHERE code = ANY($1::text[]) ORDER BY code, date ASC`,
      [benchmarkCodes],
    ),
  ]);

  // Group flat chart rows by index code, preserving first-seen order.
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

  const aggregation: IndustrySentimentsAggRow[] = aggRows.map((r) => ({
    date: formatDate(r.date),
    pool_size: r.pool_size,
    index_count: r.index_count == null ? null : Number(r.index_count),
    mean_rebased: toNum(r.mean_rebased),
    var_rebased: toNum(r.var_rebased),
  }));

  // Build benchmarks array — one entry per benchmark with close rows, in the
  // canonical INDUSTRY_SENTIMENTS_BENCHMARKS order. Benchmarks with no close
  // data are omitted.
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

  return {
    industry_id: industryId,
    industry_label: labelRows[0]?.industry_label ?? "",
    indices: Array.from(byCode.values()),
    aggregation,
    benchmarks,
  };
}

// ----------------------------------------------------------------------------
//  Industry Correlations — pairwise rolling Pearson correlation between two
//  industries' mean_rebased series, one row per (date, industry_id,
//  benchmark_industry_id, pool_size). Drives the "Correlation" expandable
//  chart on the IndustrySentiments page (multi-industry mode only).
//
//  Source: analysis.industry_correlations (built by
//  analyze_industry_correlations.py from analysis.industry_sentiments).
//
//  Order convention: rows are stored with industry_id < benchmark_industry_id
//  (lexicographic, COLLATE "C"). The API returns rows matching either
//  direction of the user-selected industry_ids set — i.e. any pair where
//  both endpoints are in industry_ids (regardless of which is "subject" vs
//  "benchmark" in the stored row).
//
//  Same-pool slices only: industry_pool_size = benchmark_industry_pool_size
//  (cross-pool comparisons are NOT materialized). The `pool_size` query
//  param selects the slice (default 'all').
// ----------------------------------------------------------------------------
interface DbIndustryCorrelationRow extends QueryResultRow {
  industry_id: string;
  benchmark_industry_id: string;
  date: Date | string;
  industry_mean_corr_5d: number | null;
  industry_mean_corr_20d: number | null;
  industry_mean_corr_60d: number | null;
  industry_mean_corr_255d: number | null;
}

const VALID_INDUSTRY_CORR_POOLS = new Set(["all", "small", "mid", "large"]);

/** Lookup table for industry_label by industry_id, populated lazily inside
 *  getIndustryCorrelations() so the response carries human-readable
 *  industry labels alongside the bare IDs (the frontend uses them for the
 *  pair labels in the legend and tooltip). */
async function fetchIndustryLabels(
  industryIds: string[],
): Promise<Map<string, string>> {
  if (industryIds.length === 0) return new Map();
  const rows = await queryRows<{ industry_id: string; industry_label: string | null }>(
    `SELECT industry_id, COALESCE(NULLIF(industry_label, ''), industry_id) AS industry_label
     FROM (
       SELECT DISTINCT industry_id, industry_label
       FROM stats.sec_classification
       WHERE type = 'index' AND industry_id = ANY($1::text[])
     ) t`,
    [industryIds],
  );
  const m = new Map<string, string>();
  for (const r of rows) {
    m.set(r.industry_id, r.industry_label ?? r.industry_id);
  }
  // Fallback: any ID without a label maps to itself.
  for (const id of industryIds) if (!m.has(id)) m.set(id, id);
  return m;
}

export async function getIndustryCorrelations(
  rawIndustryIds: string[],
  rawPoolSize: string,
): Promise<IndustryCorrelationsResponse> {
  const industryIds = (rawIndustryIds ?? [])
    .map((s) => (s ?? "").trim())
    .filter((s) => s.length > 0);
  if (industryIds.length < 2) {
    throw new Error(
      `Need at least 2 distinct industry_ids (got ${industryIds.length}).`,
    );
  }
  // Deduplicate (case-sensitive) — the user might pass the same ID twice.
  const uniqueIds = Array.from(new Set(industryIds));
  if (uniqueIds.length < 2) {
    throw new Error(
      `Need at least 2 DISTINCT industry_ids (got ${uniqueIds.length}).`,
    );
  }
  const poolSize = VALID_INDUSTRY_CORR_POOLS.has(rawPoolSize)
    ? (rawPoolSize as "all" | "small" | "mid" | "large")
    : "all";

  // Build the list of (a, b) pairs where a < b lexicographically (COLLATE
  // "C", matching the CHECK constraint). For each pair, the stored row
  // uses the lexicographically-smaller ID as `industry_id`. The SQL uses
  // `(a, b)` tuples for an IN clause.
  const pairs: Array<[string, string]> = [];
  for (let i = 0; i < uniqueIds.length; i++) {
    for (let j = i + 1; j < uniqueIds.length; j++) {
      const [x, y] = [uniqueIds[i], uniqueIds[j]];
      // Sort using simple code-point comparison (matches COLLATE "C" for
      // ASCII strings — all industry_ids are ASCII uppercase + underscore).
      const pair: [string, string] = x < y ? [x, y] : [y, x];
      pairs.push(pair);
    }
  }

  // Build parameterized IN clause: each pair is ($n, $n+1). With N pairs
  // we need 2N placeholders. Cap at a reasonable number to avoid huge queries
  // (the UI is unlikely to select more than ~10 industries → 45 pairs →
  // 90 placeholders, well within PostgreSQL's 65535 limit).
  const pairPlaceholders = pairs
    .map((_, i) => `($${i * 2 + 1}::text, $${i * 2 + 2}::text)`)
    .join(", ");
  const pairParams = pairs.flat();

  const sql = `
    SELECT
      industry_id,
      benchmark_industry_id,
      date,
      industry_mean_corr_5d,
      industry_mean_corr_20d,
      industry_mean_corr_60d,
      industry_mean_corr_255d
    FROM analysis.industry_correlations
    WHERE industry_pool_size = $${pairParams.length + 1}::text
      AND benchmark_industry_pool_size = $${pairParams.length + 1}::text
      AND (industry_id, benchmark_industry_id) IN (${pairPlaceholders})
    ORDER BY date ASC, industry_id, benchmark_industry_id
  `;
  const params = [...pairParams, poolSize];

  const [rows, labelMap] = await Promise.all([
    queryRows<DbIndustryCorrelationRow>(sql, params),
    fetchIndustryLabels(uniqueIds),
  ]);

  const correlations: IndustryCorrelationRow[] = rows.map((r) => ({
    industry_id: r.industry_id,
    benchmark_industry_id: r.benchmark_industry_id,
    industry_label: labelMap.get(r.industry_id) ?? r.industry_id,
    benchmark_industry_label: labelMap.get(r.benchmark_industry_id) ?? r.benchmark_industry_id,
    date: formatDate(r.date),
    pool_size: poolSize,
    corr_5d: toNum(r.industry_mean_corr_5d),
    corr_20d: toNum(r.industry_mean_corr_20d),
    corr_60d: toNum(r.industry_mean_corr_60d),
    corr_255d: toNum(r.industry_mean_corr_255d),
  }));

  return {
    industry_ids: uniqueIds,
    pool_size: poolSize,
    correlations,
  };
}
