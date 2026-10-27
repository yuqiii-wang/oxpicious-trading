/**
 * MA-Spread chart blocks — the DEFAULT load split into three per-table
 * async blocks (pairs / ema / shared) plus their SQL builders and the
 * pair-series mappers, and the monolithic getMovAveSpreadChart composition.
 * Split out of the former monolithic mov-ave-spreads.ts.
 */
import { queryRows, formatDate, toNum } from "@api/lib/db";
import { codeVariants } from "@api/lib/classify-etf";
import { stripped } from "@api/services/analysis/_shared";
import type {
  MaSpreadSecType,
  MovAveSpreadChartResponse,
  MovAveSpreadChartPairsResponse,
  MovAveSpreadChartEmaResponse,
  MovAveSpreadChartSharedResponse,
  MovAveSpreadPairRow,
  MovAveSpreadPairSeries,
  MovAveSpreadPairKind,
  MovAveSpreadSharedMetricsRow,
} from "@shared/types";
import {
  SEC_SOURCES,
  CHART_ANALYSIS_TABLES,
  chartCtesSql,
  chartJoinSql,
  buildNameSql,
  normalizeSecType,
  secTypeParam,
} from "./sec-sources.js";
import {
  PAIR_ORDER,
  EMA_PAIR_ORDER,
  pairLabel,
  pickLong,
  pickSlope,
  pickCurvature,
  pickStd,
  pickEmaLong,
  pickEmaSlope,
  pickEmaCurvature,
  pickEmaStd,
  type DbChartRow,
} from "./pairs-config.js";
import {
  OHLC_SHARED_PERIODS,
  OHLC_SHARED_STEMS,
  ohlcPivotCteSql,
  ohlcPivotSelectSql,
} from "./ohlc-pivot.js";

// ----------------------------------------------------------------------------
//  Chart BLOCK SQL builders — the same CTE-prefetch pattern as the old
//  monolithic chart query, but scoped to ONE block's tables each, so a block
//  request never reads, transfers, or serializes the columns the other
//  blocks own (the monolithic /chart joined ~6 tables into ~90 columns; for
//  a full stock history the Node-side fan-out + JSON of that single response
//  dominated the latency).
// ----------------------------------------------------------------------------

/** REQUIRED pairs block: the stats price/MA tables joined to the detail
 *  gaps/derivatives — everything the default Price/MA5 chart + the 9
 *  Simple-MA chips need. No EMA detail, no RSI, no OHLC pivot. */
function buildChartPairsSql(secType: MaSpreadSecType): string {
  const src = SEC_SOURCES[secType];
  const tables = src.chartTables;
  return `
    WITH d AS MATERIALIZED (
      SELECT * FROM analysis.mov_ave_spreads_detail
      WHERE sec_type = $1
        AND code = ANY($2::text[])
    ),
    ${chartCtesSql(tables)}
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
      d.price_slope, d.ma5_slope, d.ma20_slope,
      d.price_curvature, d.ma5_curvature, d.ma20_curvature,
      d.std_5days, d.std_20days, d.std_60days, d.std_120days, d.std_255days
    FROM d
    ${chartJoinSql(tables)}
    ORDER BY d.date ASC
  `;
}

/** Best-effort EMA block: the EMA detail table (gaps/derivatives/σ) plus
 *  the price columns the EMA pair rows carry. No RSI, no OHLC pivot. */
function buildChartEmaSql(secType: MaSpreadSecType): string {
  const src = SEC_SOURCES[secType];
  const tables = [...src.chartTables, ...CHART_ANALYSIS_TABLES.filter((t) => t.alias === "ema")];
  return `
    WITH d AS MATERIALIZED (
      SELECT * FROM analysis.mov_ave_spreads_detail
      WHERE sec_type = $1
        AND code = ANY($2::text[])
    ),
    ${chartCtesSql(tables)}
    SELECT
      d.date,
      ${src.priceExpr} AS price,
      ${src.openExpr} AS open,
      ${src.highExpr} AS high,
      ${src.lowExpr} AS low,
      ${src.tradingAmtExpr} AS trading_amount,
      t.ema6, t.ema12, t.ema26,
      d.price_slope, d.price_curvature,
      ema.ema6_slope, ema.ema12_slope,
      ema.ema6_curvature, ema.ema12_curvature,
      ema.price_vs_ema6, ema.price_vs_ema12, ema.price_vs_ema26,
      ema.ema6_vs_ema12, ema.ema6_vs_ema26, ema.ema12_vs_ema26,
      ema.std_5days AS ema_std_5days,
      ema.std_12days AS ema_std_12days,
      ema.std_26days AS ema_std_26days
    FROM d
    ${chartJoinSql(tables)}
    ORDER BY d.date ASC
  `;
}

/** Best-effort shared block: the tooltip metrics — RSI table + OHLC pivot +
 *  the detail table's amt slopes / market shares. No stats price tables
 *  (sec_type binds through the $1 parameter at call time). */
function buildChartSharedSql(): string {
  const tables = CHART_ANALYSIS_TABLES.filter((t) => t.alias === "rsi");
  return `
    WITH d AS MATERIALIZED (
      SELECT * FROM analysis.mov_ave_spreads_detail
      WHERE sec_type = $1
        AND code = ANY($2::text[])
    ),
    ${chartCtesSql(tables)},
    ${ohlcPivotCteSql(OHLC_SHARED_PERIODS, OHLC_SHARED_STEMS)}
    SELECT
      d.date,
      d.trading_amt_ma5_slope, d.trading_amt_ma20_slope, d.trading_amt_ma60_slope,
      d.trading_amt_ma120_slope, d.trading_amt_ma255_slope,
      d.trading_amt_market_share_ma5, d.trading_amt_market_share_ma20,
      d.trading_amt_market_share_ma60, d.trading_amt_market_share_ma120,
      d.trading_amt_market_share_ma255,
      rsi.rsi_3days, rsi.rsi_6days, rsi.rsi_10days, rsi.rsi_14days, rsi.rsi_20days,
      rsi.rsi_60days,
      ted.is_typical_3days, ted.is_typical_6days, ted.is_typical_10days,
      ted.is_typical_14days, ted.is_typical_20days, ted.is_typical_60days,
      ${ohlcPivotSelectSql(OHLC_SHARED_PERIODS, "sh_", OHLC_SHARED_STEMS)}
    FROM d
    ${chartJoinSql(tables)}
    LEFT JOIN analysis.trend_episode_days ted
      ON ted.sec_type = d.sec_type AND ted.code = d.code AND ted.date = d.date
    LEFT JOIN ohlc ON ohlc.code = d.code AND ohlc.date = d.date
    ORDER BY d.date ASC
  `;
}

/** Initialize the 9 pair series of one family (Simple MA or EMA) in
 *  canonical order — shared by the pairs/ema block mappers below. */
function initPairSeries(
  order: readonly (readonly [number, number, ...unknown[]])[],
  kind: MovAveSpreadPairKind,
  prefix: string,
): Map<string, MovAveSpreadPairSeries> {
  const byPair = new Map<string, MovAveSpreadPairSeries>();
  for (const [ms, ml] of order) {
    byPair.set(`${prefix}${ms}/${ml}`, {
      ma_short: ms,
      ma_long: ml,
      pair_label: pairLabel(ms, ml, kind),
      kind,
      rows: [],
    });
  }
  return byPair;
}

/** Pairs-block mapper: fan each date out into the 9 Simple-MA pair rows. */
function mapPricePairRows(chartRows: DbChartRow[]): MovAveSpreadPairSeries[] {
  const byPair = initPairSeries(PAIR_ORDER, "price", "price-");
  for (const r of chartRows) {
    const dateStr = formatDate(r.date);
    const price = toNum(r.price);
    const ma5 = toNum(r.ma5);
    const open = toNum(r.open);
    const high = toNum(r.high);
    const low = toNum(r.low);
    const tradingAmount = toNum(r.trading_amount);
    for (const [maShort, maLong, gapCol] of PAIR_ORDER) {
      const series = byPair.get(`price-${maShort}/${maLong}`);
      if (!series) continue;
      const shortVal = maShort === 0 ? price : ma5;
      // Derivatives: the DB-stored direct diffs (value[t] - value[t-1])
      // — price_slope/price_curvature for the price leg, ma{5,20}_* for
      // the MA legs; windows beyond 20 have no stored derivatives (null).
      const row: MovAveSpreadPairRow = {
        date: dateStr,
        short_value: shortVal,
        long_value: pickLong(r, maLong),
        gap_value: toNum(r[gapCol as keyof DbChartRow]),
        short_slope: maShort === 0 ? toNum(r.price_slope) : pickSlope(r, maShort),
        short_curvature: maShort === 0 ? toNum(r.price_curvature) : pickCurvature(r, maShort),
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
  return PAIR_ORDER.map(([ms, ml]) => byPair.get(`price-${ms}/${ml}`)!);
}

/** Ema-block mapper: fan each date out into the 6 TYPICAL EMA pair rows.
 *  short = price (ma_short=0), ema6 or ema12; long ∈ {6,12,26} — the
 *  classic 12/26 spread IS the MACD line's components. gap_value / slope /
 *  curvature come from the EMA detail table (alias `ema`); long_std is the
 *  rolling price σ for the long EMA's window. */
function mapEmaPairRows(chartRows: DbChartRow[]): MovAveSpreadPairSeries[] {
  const byPair = initPairSeries(EMA_PAIR_ORDER, "ema", "ema-");
  for (const r of chartRows) {
    const dateStr = formatDate(r.date);
    const price = toNum(r.price);
    const open = toNum(r.open);
    const high = toNum(r.high);
    const low = toNum(r.low);
    const tradingAmount = toNum(r.trading_amount);
    for (const [maShort, maLong, gapCol] of EMA_PAIR_ORDER) {
      const series = byPair.get(`ema-${maShort}/${maLong}`);
      if (!series) continue;
      const row: MovAveSpreadPairRow = {
        date: dateStr,
        short_value: maShort === 0 ? price : pickEmaLong(r, maShort),
        long_value: pickEmaLong(r, maLong),
        gap_value: toNum(r[gapCol as keyof DbChartRow]),
        // Derivatives: price legs carry the SMA table's price_slope /
        // price_curvature; EMA legs the EMA table's stored ema{6,12}_*
        // direct diffs (the 26 leg carries none).
        short_slope: maShort === 0 ? toNum(r.price_slope) : pickEmaSlope(r, maShort),
        short_curvature: maShort === 0 ? toNum(r.price_curvature) : pickEmaCurvature(r, maShort),
        long_slope: pickEmaSlope(r, maLong),
        long_curvature: pickEmaCurvature(r, maLong),
        long_std: pickEmaStd(r, maLong),
        open,
        high,
        low,
        trading_amount: tradingAmount,
      };
      series.rows.push(row);
    }
  }
  return EMA_PAIR_ORDER.map(([ms, ml]) => byPair.get(`ema-${ms}/${ml}`)!);
}

/** Shared-block mapper: the per-date tooltip-metrics rows (RSI /
 *  trading-amt slope+share / rolling-window OHLC). */
function mapSharedRows(chartRows: DbChartRow[]): MovAveSpreadSharedMetricsRow[] {
  return chartRows.map((r) => ({
    date: formatDate(r.date),
    rsi_3days: toNum(r.rsi_3days),
    rsi_6days: toNum(r.rsi_6days),
    rsi_10days: toNum(r.rsi_10days),
    rsi_14days: toNum(r.rsi_14days),
    rsi_20days: toNum(r.rsi_20days),
    rsi_60days: toNum(r.rsi_60days),
    is_typical_3days: toNum(r.is_typical_3days),
    is_typical_6days: toNum(r.is_typical_6days),
    is_typical_10days: toNum(r.is_typical_10days),
    is_typical_14days: toNum(r.is_typical_14days),
    is_typical_20days: toNum(r.is_typical_20days),
    is_typical_60days: toNum(r.is_typical_60days),
    trading_amt_ma5_slope: toNum(r.trading_amt_ma5_slope),
    trading_amt_ma20_slope: toNum(r.trading_amt_ma20_slope),
    trading_amt_ma60_slope: toNum(r.trading_amt_ma60_slope),
    trading_amt_ma120_slope: toNum(r.trading_amt_ma120_slope),
    trading_amt_ma255_slope: toNum(r.trading_amt_ma255_slope),
    trading_amt_market_share_ma5: toNum(r.trading_amt_market_share_ma5),
    trading_amt_market_share_ma20: toNum(r.trading_amt_market_share_ma20),
    trading_amt_market_share_ma60: toNum(r.trading_amt_market_share_ma60),
    trading_amt_market_share_ma120: toNum(r.trading_amt_market_share_ma120),
    trading_amt_market_share_ma255: toNum(r.trading_amt_market_share_ma255),
    open_20d: toNum(r.sh_open_20d),
    high_20d: toNum(r.sh_high_20d),
    low_20d: toNum(r.sh_low_20d),
    open_60d: toNum(r.sh_open_60d),
    high_60d: toNum(r.sh_high_60d),
    low_60d: toNum(r.sh_low_60d),
    open_120d: toNum(r.sh_open_120d),
    high_120d: toNum(r.sh_high_120d),
    low_120d: toNum(r.sh_low_120d),
    open_255d: toNum(r.sh_open_255d),
    high_255d: toNum(r.sh_high_255d),
    low_255d: toNum(r.sh_low_255d),
  }));
}

// ----------------------------------------------------------------------------
//  getMovAveSpreadChart — the DEFAULT chart load for one asset: the 18
//  Simple-MA + EMA pair series (trimmed rows) + ONE shared per-date
//  tooltip-metrics array. The Amt/MA pairs, the OHLC extrema, the
//  band-break streaks, and the px-vol states are served on demand by
//  getMovAveSpreadChartExtras so the panel renders as soon as the pair
//  views are ready.
//
//  JOINs analysis.mov_ave_spreads_detail with the asset-appropriate source
//  tables (per-code prefetched CTEs, see chartCtesSql) to recover:
//    • price (COALESCE(adj_close, close) for ETFs; close for indices/stocks)
//    • ma5 / ma20 / ma60 / ma120 / ma255 + the typical ema6 / ema12 / ema26
//  …alongside the precomputed gap_value columns. Client-side, each row is
//  fanned out into the 9 Simple-MA + 6 typical EMA pair series entries
//  (short_value, long_value, gap_value) plus one shared metrics row.
// ----------------------------------------------------------------------------

/** REQUIRED chart block — the 9 Simple-MA pair series + the display name.
 *  Drives the default Price/MA5 chart, the MA chip grid, and the shared
 *  date axis; its landing unblocks the panel's first render. */
export async function getMovAveSpreadChartPairs(
  rawCode: string,
  rawSecType: string | undefined | null,
): Promise<MovAveSpreadChartPairsResponse> {
  const secType = normalizeSecType(rawSecType);
  const target = stripped(rawCode);
  const variants = codeVariants(target);
  const [chartRows, nameRows] = await Promise.all([
    queryRows<DbChartRow>(buildChartPairsSql(secType),
      [secTypeParam(secType), variants]),
    queryRows<{ name: string | null }>(buildNameSql(secType), [variants]),
  ]);
  return {
    code: target,
    name: nameRows[0]?.name ?? "",
    pairs: mapPricePairRows(chartRows),
  };
}

/** Best-effort chart block — the 6 TYPICAL EMA pair series (EMA detail
 *  table). The EMA chip grid populates when this lands; a failure just
 *  leaves those chips absent (the frontend guards on pair presence). */
export async function getMovAveSpreadChartEma(
  rawCode: string,
  rawSecType: string | undefined | null,
): Promise<MovAveSpreadChartEmaResponse> {
  const secType = normalizeSecType(rawSecType);
  const target = stripped(rawCode);
  const variants = codeVariants(target);
  const chartRows = await queryRows<DbChartRow>(buildChartEmaSql(secType),
      [secTypeParam(secType), variants]);
  // Industry has no stats tech_stats source — the chart subquery returns
  // NULL ema columns. Recompute them here from the returned price series
  // with the SAME recursion the pipeline's compute_emas uses
  // (adjust=False: the first observation seeds the EMA; NaN days carry
  // the previous value forward). Rows are single-code, date-ascending.
  if (secType === "industry") {
    const prices = chartRows.map((r) => toNum(r.price));
    for (const span of [6, 12, 26] as const) {
      const col = `ema${span}` as keyof DbChartRow;
      const alpha = 2 / (span + 1);
      let prev: number | null = null;
      for (let i = 0; i < chartRows.length; i++) {
        const v = prices[i];
        if (v != null) {
          prev = prev == null ? v : alpha * v + (1 - alpha) * prev;
        }
        (chartRows[i] as Record<string, unknown>)[col as string] = prev;
      }
    }
  }
  return { code: target, pairs: mapEmaPairRows(chartRows) };
}

/** Best-effort chart block — the per-date tooltip metrics (RSI table +
 *  OHLC pivot + detail amt slopes/market shares). The chart's tooltip
 *  formatters already tolerate its absence. */
export async function getMovAveSpreadChartShared(
  rawCode: string,
  rawSecType: string | undefined | null,
): Promise<MovAveSpreadChartSharedResponse> {
  const secType = normalizeSecType(rawSecType);
  const target = stripped(rawCode);
  const variants = codeVariants(target);
  const chartRows = await queryRows<DbChartRow>(buildChartSharedSql(),
      [secTypeParam(secType), variants]);
  return { code: target, shared: mapSharedRows(chartRows) };
}

/** The DEFAULT full chart response — composition of the three blocks (kept
 *  for the monolithic /chart endpoint and any non-panel consumer; the panel
 *  itself fetches the blocks directly for progressive rendering). */
export async function getMovAveSpreadChart(
  rawCode: string,
  rawSecType: string | undefined | null,
): Promise<MovAveSpreadChartResponse> {
  const [pairs, ema, shared] = await Promise.all([
    getMovAveSpreadChartPairs(rawCode, rawSecType),
    getMovAveSpreadChartEma(rawCode, rawSecType),
    getMovAveSpreadChartShared(rawCode, rawSecType),
  ]);
  return {
    code: pairs.code,
    name: pairs.name,
    pairs: [...pairs.pairs, ...ema.pairs],
    shared: shared.shared,
  };
}
