/**
 * MA-Spread analysis - listMovAveSpreadCodes + getMovAveSpreadChart.
 * Extracted from the former analysis.service.ts.
 */
import { queryRows, formatDate, toNum } from "../../lib/db.js";
import type { QueryResultRow } from "pg";
import { stripExchangeSuffix } from "../../lib/classify-etf.js";
import { stripped } from "./_shared.js";
import type {
  MaSpreadSecType,
  MovAveSpreadCodeRow,
  MovAveSpreadCodesResponse,
  MovAveSpreadChartResponse,
  MovAveSpreadDetailRow,
  MovAveSpreadPairSeries,
  MovAveSpreadLatestGap,
  MovAveSpreadValleyLow,
  SectorNode,
  IndustryNode,
} from "../../../shared/types.js";

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
  open: number | null;
  high: number | null;
  low: number | null;
  trading_amount: number | null;
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
  // 5 rolling population σ columns (Bollinger band widths) from the detail row.
  std_5days: number | null;
  std_20days: number | null;
  std_60days: number | null;
  std_120days: number | null;
  std_255days: number | null;
}

// ----------------------------------------------------------------------------
//  Helpers
// ----------------------------------------------------------------------------

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

/** Pick the rolling population σ (Bollinger band width) for the given
 *  MA window from a chart row. The σ columns are stored on the detail row
 *  as std_{W}days (W = window). Used to draw the ±k×σ envelope around the
 *  long MA on Price/MA pair charts. */
function pickStd(r: DbChartRow, window: number): number | null {
  switch (window) {
    case 5:   return toNum(r.std_5days);
    case 20:  return toNum(r.std_20days);
    case 60:  return toNum(r.std_60days);
    case 120: return toNum(r.std_120days);
    case 255: return toNum(r.std_255days);
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
  /** SQL expression for the open column. */
  openExpr: string;
  /** SQL expression for the high column. */
  highExpr: string;
  /** SQL expression for the low column. */
  lowExpr: string;
  /** SQL expression for the trading_amount column. */
  tradingAmtExpr: string;
}

const SEC_SOURCES: Record<MaSpreadSecType, SecSource> = {
  etf: {
    identityTable: "stats.etf_identity",
    chartFromClause:
      "FROM analysis.mov_ave_spreads_detail d\n" +
      "  JOIN stats.etf_basic_stats   b ON b.date = d.date AND b.code = d.code\n" +
      "  LEFT JOIN stats.etf_adjustment a ON a.date = d.date AND a.code = d.code\n" +
      "  LEFT JOIN stats.etf_liquidity_margin lm ON lm.date = d.date AND lm.code = d.code\n" +
      "  LEFT JOIN stats.etf_tech_stats  t ON t.date = d.date AND t.code = d.code",
    priceExpr: "COALESCE(a.adj_close, b.close)",
    openExpr: "COALESCE(a.adj_open, b.open)",
    highExpr: "COALESCE(a.adj_high, b.high)",
    lowExpr: "COALESCE(a.adj_low, b.low)",
    tradingAmtExpr: "lm.trading_amount",
  },
  index: {
    identityTable: "stats.index_identity",
    chartFromClause:
      "FROM analysis.mov_ave_spreads_detail d\n" +
      "  JOIN stats.index_basic_stats b ON b.date = d.date AND b.code = d.code\n" +
      "  LEFT JOIN stats.index_tech_stats t ON t.date = d.date AND t.code = d.code",
    priceExpr: "b.close",
    openExpr: "b.open",
    highExpr: "b.high",
    lowExpr: "b.low",
    tradingAmtExpr: "b.trading_amount",
  },
  stock: {
    identityTable: "stats.stock_identity",
    chartFromClause:
      "FROM analysis.mov_ave_spreads_detail d\n" +
      "  JOIN stats.stock_basic_stats b ON b.date = d.date AND b.code = d.code\n" +
      "  LEFT JOIN stats.stock_tech_stats t ON t.date = d.date AND t.code = d.code",
    priceExpr: "b.close",
    openExpr: "b.open",
    highExpr: "b.high",
    lowExpr: "b.low",
    tradingAmtExpr: "b.trading_amount",
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
      ${src.openExpr} AS open,
      ${src.highExpr} AS high,
      ${src.lowExpr} AS low,
      ${src.tradingAmtExpr} AS trading_amount,
      t.ma5, t.ma20, t.ma60, t.ma120, t.ma255,
      d.price_vs_ma5, d.price_vs_ma20, d.price_vs_ma60,
      d.price_vs_ma120, d.price_vs_ma255,
      d.ma5_vs_ma20, d.ma5_vs_ma60, d.ma5_vs_ma120, d.ma5_vs_ma255,
      d.price_slope, d.ma5_slope, d.ma20_slope, d.ma60_slope, d.ma120_slope, d.ma255_slope,
      d.price_curvature, d.ma5_curvature, d.ma20_curvature, d.ma60_curvature,
      d.ma120_curvature, d.ma255_curvature,
      d.std_5days, d.std_20days, d.std_60days, d.std_120days, d.std_255days
    ${src.chartFromClause}
    WHERE d.sec_type = $2
      AND REGEXP_REPLACE(d.code, '\\.(SZ|SS|BJ|HK)$', '') = $1::text
    ORDER BY d.date ASC
  `;
}

// ----------------------------------------------------------------------------
//  Valley-low query — fetch peaks_and_floors rows directly by (sec_type, code)
//  so each mov_ave_peaks_and_floors.date is plotted ONCE on the chart. The
//  previous implementation JOINed peaks_and_floors to mov_ave_spreads_detail
//  via d.peaks_and_floors_date (the "nearest preceding extreme" mapping),
//  which smeared each extreme's extreme_val across every detail date that
//  mapped to it — producing a marker on essentially every detail row. This
//  direct query avoids that smearing entirely.
// ----------------------------------------------------------------------------
function buildValleyLowsSql(): string {
  return `
    SELECT date, extreme_val, nearby_extreme_date
    FROM analysis.mov_ave_peaks_and_floors
    WHERE sec_type = $2
      AND REGEXP_REPLACE(code, '\\.(SZ|SS|BJ|HK)$', '') = $1::text
    ORDER BY date ASC
  `;
}

interface DbValleyLowRow extends QueryResultRow {
  date: Date | string;
  extreme_val: number | null;
  nearby_extreme_date: Date | string | null;
}

function buildNameSql(secType: MaSpreadSecType): string {
  const src = SEC_SOURCES[secType];
  return `
    SELECT DISTINCT ON (code) code, name
    FROM ${src.identityTable}
    WHERE REGEXP_REPLACE(code, '\\.(SZ|SS|BJ|HK)$', '') = $1::text
    ORDER BY code, date DESC
  `;
}

export async function getMovAveSpreadChart(
  rawCode: string,
  rawSecType: string | undefined | null,
): Promise<MovAveSpreadChartResponse> {
  const secType = normalizeSecType(rawSecType);
  const target = stripped(rawCode);

  // Fetch chart rows + name + valley lows in parallel.
  const [chartRows, nameRows, valleyLowRows] = await Promise.all([
    queryRows<DbChartRow>(buildChartSql(secType), [target, secType]),
    queryRows<{ name: string | null }>(buildNameSql(secType), [target]),
    queryRows<DbValleyLowRow>(buildValleyLowsSql(), [target, secType]),
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
    const open = toNum(r.open);
    const high = toNum(r.high);
    const low = toNum(r.low);
    const tradingAmount = toNum(r.trading_amount);
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
        long_std: pickStd(r, maLong),
        open,
        high,
        low,
        trading_amount: tradingAmount,
      };
      series.rows.push(row);
    }
  }

  // Valley lows: one entry per peaks_and_floors row for this code. Plotted
  // directly by the frontend as red down-triangle markers — no per-detail-row
  // smearing.
  const valley_lows: MovAveSpreadValleyLow[] = valleyLowRows
    .map((r) => ({
      date: formatDate(r.date),
      extreme_val: toNum(r.extreme_val) ?? 0,
      nearby_extreme_date: r.nearby_extreme_date != null
        ? formatDate(r.nearby_extreme_date)
        : null,
    }))
    .filter((v) => Number.isFinite(v.extreme_val));

  return {
    code: target,
    name,
    pairs: PAIR_ORDER.map(([ms, ml]) => byPair.get(`${ms}/${ml}`)!),
    valley_lows,
  };
}

// ----------------------------------------------------------------------------
//  listMovAveSpreadThemes — two-level L1 sector → L2 industry → items tree for
//  the MA-Spread page's ThemeSelector. Mirrors listPerfAttrThemes() in
//  perf-attribution.ts but draws codes from analysis.mov_ave_spreads_detail
//  (filtered by sec_type) and supports etf / index / stock.
//
//  Labels come precomputed from stats.sec_classification (denormalized onto
//  the table by build_classification.py — no catalog JOIN needed). Codes that
//  don't have a sec_classification row are bucketed under sector 'OTHER' /
//  industry '未分类' so the user still sees them in the selector.
// ----------------------------------------------------------------------------

/** Whitelisted sec_type → sec_classification type discriminator (safe for
 *  string interpolation in the SQL). */
const MA_SPREAD_META_TYPE: Record<MaSpreadSecType, string> = {
  etf: "etf",
  index: "index",
  stock: "stock",
};

interface DbMaSpreadMetaRow extends QueryResultRow {
  code: string;
  name: string;
  sector_id: string;
  sector_label: string;
  industry_id: string;
  industry_label: string;
  industry_slug: string;
}

export async function listMovAveSpreadThemes(
  rawSecType: string | undefined | null,
): Promise<SectorNode[]> {
  const secType = normalizeSecType(rawSecType);
  const metaType = MA_SPREAD_META_TYPE[secType];
  const sql = `
    WITH spread_codes AS (
      SELECT DISTINCT code
      FROM analysis.mov_ave_spreads_detail
      WHERE sec_type = $1::text
    )
    SELECT
      sc.code,
      COALESCE(m.name, '')             AS name,
      COALESCE(m.sector_id,       'OTHER')  AS sector_id,
      COALESCE(m.sector_label,    '其他')   AS sector_label,
      COALESCE(m.industry_id,     'OTHER')  AS industry_id,
      COALESCE(m.industry_label,  '未分类') AS industry_label,
      COALESCE(m.industry_slug,   'other')  AS industry_slug
    FROM spread_codes sc
    LEFT JOIN stats.sec_classification m ON m.code = sc.code AND m.type = $2::text
  `;
  const rows = await queryRows<DbMaSpreadMetaRow>(sql, [secType, metaType]);

  const sectorMap = new Map<string, {
    sector_label: string;
    industries: Map<string, IndustryNode>;
  }>();

  for (const r of rows) {
    // Strip exchange suffix so item codes match the codes returned by
    // listMovAveSpreadCodes (which also strips the suffix).
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
