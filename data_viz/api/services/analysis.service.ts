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
      d.ma5_slope, d.ma20_slope, d.ma60_slope, d.ma120_slope, d.ma255_slope,
      d.ma5_curvature, d.ma20_curvature, d.ma60_curvature,
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
      // slope/curvature: short MA has none when ma_short = 0 (price).
      const shortSlope = maShort === 0 ? null : pickSlope(r, maShort);
      const shortCurv  = maShort === 0 ? null : pickCurvature(r, maShort);
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
  amount_ratio: number | null;
  benchmark_amount: number | null;
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
      a.amount_ratio_benchmark_to_code AS amount_ratio,
      a.benchmark_amount
    FROM analysis.sec_alloc_perf_attribution a
    CROSS JOIN latest_date ld
    LEFT JOIN LATERAL (
      SELECT DISTINCT ON (code) code, name
      FROM stats.index_identity
      WHERE code = a.benchmark_code
      ORDER BY code, date DESC
    ) bi ON true
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
    amount_ratio: toNum(r.amount_ratio),
    benchmark_amount: toNum(r.benchmark_amount),
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

/** Whitelisted sec_type → meta-table mapping (safe for string interpolation). */
const PERF_ATTR_META_TABLE: Record<string, string> = {
  etf: "stats.etf_meta",
  index: "stats.index_meta",
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
  // Select distinct codes that have perf-attr rows, then join with the
  // classification meta table to recover sector/industry classification.
  const sql = `
    WITH perf_codes AS (
      SELECT DISTINCT code
      FROM analysis.sec_alloc_perf_attribution
      WHERE sec_type = $1::text
    )
    SELECT
      pc.code,
      COALESCE(m.name, '')           AS name,
      COALESCE(m.sector_id,     'OTHER')     AS sector_id,
      COALESCE(m.sector_label,  '其他')       AS sector_label,
      COALESCE(m.industry_id,   'OTHER')     AS industry_id,
      COALESCE(m.industry_label,'未分类')     AS industry_label,
      COALESCE(m.industry_slug, 'other')     AS industry_slug
    FROM perf_codes pc
    LEFT JOIN ${metaTable} m ON m.code = pc.code
  `;
  const rows = await queryRows<DbPerfAttrMetaRow>(sql, [secType]);

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
  amount_ratio: number | null;
}

export async function getPerfAttrChart(
  rawCode: string,
  rawBenchmarkCode: string,
  secType: PerfAttrSecType,
): Promise<PerfAttrChartResponse> {
  const target = stripped(rawCode);
  const benchmarkCode = rawBenchmarkCode.trim();
  const nameTable = PERF_ATTR_NAME_TABLE[secType] ?? PERF_ATTR_NAME_TABLE.etf;

  const [chartRows, nameRows, benchNameRows] = await Promise.all([
    queryRows<DbPerfAttrChartRow>(
      `SELECT a.date, a.subject_return, a.benchmark_return, a.active_return,
              a.amount_ratio_benchmark_to_code AS amount_ratio
       FROM analysis.sec_alloc_perf_attribution a
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
      amount_ratio: toNum(r.amount_ratio),
    })),
  };
}
