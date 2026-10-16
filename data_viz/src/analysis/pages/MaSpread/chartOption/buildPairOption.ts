/**
 * ECharts option builder for the MA-Spread pair chart (SMA + EMA).
 *
 * Orchestrator only — every header/row button group owns its computation
 * and series in its own subdirectory (bollinger/, rsiTrend/, derivTrend/,
 * tradingAmt/, roofFloor/, streaks/, pxVol/, ohlcGaps/); the plumbing the
 * groups share lives in _common/. The always-on core (pair curves, gap
 * fill, axes) stays here.
 *
 * Implementation:
 *   For Price/MA pairs (ma_short === 0):
 *     - OHLC bars (shared ohlcSeries from @/lib/ohlc) — replaces the close line
 *     - Long MA line  (z=5, no stack)
 *     - Gap fill stack (3 series: _base, _pos, _neg)
 *     - Bollinger envelope (optional)
 *     - Trading amount bars on secondary y-axis — REPLACED, while
 *       rsiTrendMode is on, by the shared block's Wilder-RSI window lines
 *       (3/6/10/14/20/60d) on the SAME plot: the right axis is range-offset
 *       (0-400) so the RSI 0-100 readings hug the bottom quarter of the
 *       plot, away from the price trend as much as one grid allows; and
 *       REPLACED, while a Roof/Floor Trendlines window button is enabled
 *       (no higher-precedence overlay), by that window's change-since-
 *       last-anchor bars — Δ Low above / Δ High below a dashed zero
 *       central line (roofFloor/changeSince.ts)
 *
 *   For MA/MA pairs (ma_short === 5):
 *     - Short MA line + Long MA line
 *     - Gap fill stack (3 series: _base, _pos, _neg)
 *     - Trading amount bars on secondary y-axis (same RSI + roof/floor
 *       replacements)
 */
import { ohlcSeries, type OhlcMode } from "@/lib/ohlc";
import { baseChartOption, commonTooltip } from "@/shared/charts/base-chart";
import {
  MA120_COLOR,
  UP_COLOR,
  DOWN_COLOR,
  SPOT_COLOR,
  axisColors,
  commonLegend,
  commonGrid,
  commonDataZoom,
} from "@/theme/chart-palette";
import type { ThemeMode } from "@/store/filters";
import type { EChartsOption } from "echarts";
import type {
  MovAveSpreadOhlcRow,
  MovAveSpreadOhlcGapDay,
  MovAveSpreadPairSeries,
  MovAveSpreadRoofFloorEstimateRow,
  MovAveSpreadRoofFloorRebound,
  MovAveSpreadSharedMetricsRow,
  RoofFloorEstimateMethod,
} from "@shared/types";
import {
  shortLabel,
  type TrendSpansRecord,
} from "./trendBands";
import type {
  DerivTrendSpec,
  MomentumTrendSpec,
  SeriesList,
  ShadeOverlay,
  TooltipContext,
  TradingAmtMode,
} from "./_common";
import {
  makeFmtPrice,
  pctBaseOf,
  sharedNum,
  shadeOverlaySeries,
} from "./_common";
import { computePairBollinger, appendBollingerSeries } from "./bollinger";
import {
  buildRsiArrays,
  buildRsiFlagArrays,
  rsiTrendLines,
  appendRsiTrendSeries,
  buildRsiRightAxis,
} from "./rsiTrend";
import {
  derivMaxAbsOf,
  appendDerivTrendSeries,
  buildDerivRightAxis,
} from "./derivTrend";
import {
  SHARED_AMT_SLOPE_KEYS,
  SHARED_AMT_SHARE_KEYS,
  amtBarDataOf,
  splitAmtUpDown,
  appendAmtBarSeries,
  buildAmtRightAxis,
} from "./tradingAmt";
import type {
  LongBandStreak,
} from "@/shared/charts/streakBands";
import {
  changeSinceLowName,
  changeSinceHighName,
  appendChangeSinceSeries,
  buildChangeSinceRightAxis,
  changeSinceMaxAbsOf,
} from "./roofFloor";
import {
  buildRoofFloorState,
  buildRoofFloorEstimateState,
  appendRoofFloorTrendSeries,
  buildReboundAuditMarks,
  appendReboundAuditSeries,
} from "./roofFloor";
import {
  computeStreakShading,
  streakLegendLabel,
  appendStreakSeries,
} from "./streaks";
import { buildGapInfo } from "./ohlcGaps";
import { appendMomentumTrendSeries, buildMomentumRightAxis } from "./momentumTrend";
import { buildPairTooltipFormatter } from "./tooltipFormatter";

export interface BuildPairOptionArgs {
  /** The pair's full time series (trimmed rows — the drawn curves). */
  pair: MovAveSpreadPairSeries;
  /** Per-date tooltip metrics shared across pairs (the chart response's
   *  `shared` array) — RSI, trading-amt MA slopes/market shares, rolling-
   *  window OHLC. Index-aligned with pair.rows; null renders those tooltip
   *  readouts as "—". */
  shared: MovAveSpreadSharedMetricsRow[] | null;
  /** Current theme mode (light / dark) for axis + tooltip colors. */
  themeMode: ThemeMode;
  /** Bollinger multiplier k in MA ± k × σ. Default 2. */
  bollingerK?: number;
  /** Trading amount display mode. Defaults to "lowkey". The bars also
   *  yield the right-axis slot to the Roof/Floor Trendlines window's
   *  change-since-last-anchor plot while a window button is enabled
   *  (below the rsiTrend/derivTrend overlays in the slot's precedence). */
  tradingAmtMode?: TradingAmtMode;
  /** RSI-trend replacement: swap the trading-amt bars for the shared
   *  block's Wilder-RSI window lines on the SAME plot — the right axis is
   *  offset (0-400) so the RSI 0-100 lines hug the bottom quarter.
   *  Defaults to false. `rsiWindows` (the RSI Trends section's days-row
   *  pick, non-empty when rsiTrendMode is on) restricts the drawn lines
   *  to those window lengths; null/empty draws all six. */
  rsiTrendMode?: boolean;
  rsiWindows?: readonly number[] | null;
  /** The RSI Trends population toggle (null = all days). */
  rsiPopulation?: "typical" | "re-entry" | null;
  /** Slope/Curvature overlay (the header toggle's two derivative modes):
   *  per-date derivative lines on the right axis, replacing the amt bars
   *  (same exclusive slot as rsiTrendMode) — the panel reads them
   *  straight from the DB-stored derivative columns: price + the 5/20-day
   *  MA (or 6/20-day EMA) direct diffs (value[t] − value[t−1]; curvature
   *  = that slope's diff; no averaging, no client-side computation).
   *  Null when off. Values are raw price-unit diffs — NOT rebased in
   *  percentage ohlcMode (the tooltip row rebases for display). Drawing
   *  uses the RSI-style band offset: one shared full-series scale maps
   *  the signed range into the [0,100] band of the fixed 0-400 right
   *  axis (zero pinned mid-band) so the lines hug the bottom quarter;
   *  tick labels read back in original units. */
  derivTrend?: DerivTrendSpec | null;
  /** Momentum overlay (the section's button row): the picked terms'
   *  per-date lookback-adaptive momentum lines on the right axis,
   *  replacing the amt bars (same exclusive slot, below rsiTrend /
   *  derivTrend in the precedence). Null when off. Values are signed
   *  fractional ratios drawn through the derivTrend-style band offset
   *  (one shared full-series scale, zero pinned mid-band) so the lines
   *  hug the bottom quarter of the fixed 0-400 right axis. */
  momentumTrend?: MomentumTrendSpec | null;
  /** Display mode for price-derived series. */
  ohlcMode?: OhlcMode;
  /** Enabled rolling-OHLC window (trading days) — null/undefined = off.
   *  When set, the window's rolling High/Low envelope is drawn and the
   *  roof/floor trendlines are armed. */
  roofFloorWindow?: number | null;
  /** Index into pair.rows of the clicked chart date — the roof/floor
   *  trendlines are drawn from the window's (top, 2nd) extrema at this
   *  date and stop here. */
  roofFloorClickIdx?: number | null;
  /** Rolling-window OHLC extrema rows (index-aligned with pair.rows). */
  roofFloorRows?: MovAveSpreadOhlcRow[] | null;
  /** Roof/floor LEVEL-ESTIMATE rows (the "rfest" extras group,
   *  index-aligned with pair.rows) — read at roofFloorClickIdx (latest
   *  row when null) when roofFloorEstMethod is set. */
  roofFloorEstRows?: MovAveSpreadRoofFloorEstimateRow[] | null;
  /** The picked estimate method (the Est-levels toggle) — null/undefined
   *  = the estimate overlay is off. */
  roofFloorEstMethod?: RoofFloorEstimateMethod | null;
  /** Roof/floor REBOUND-AUDIT events (the "rfaudit" extras group) —
   *  when a window is enabled, its cohort's events draw as larger
   *  triangle markers at the rebounded recorded extremes — only the
   *  ones before the chosen date and near that side's median event
   *  level (the median roof/floor). */
  roofFloorRebounds?: MovAveSpreadRoofFloorRebound[] | null;
  /** The anchor window's WHOLE-WINDOW long horizontal break streaks (one
   *  per side, or null when that side has no streak starting in the
   *  window) — the window's same-side DB streaks merged into one span,
   *  computed in the panel. Drawn as ONE darker horizontal band from the
   *  window's constant band edge to the merged extreme, over the span's
   *  own price dates. */
  longStreaks?: { high: LongBandStreak[]; low: LongBandStreak[] } | null;
  /** Selected streak lookback window (trading rows) — null/undefined = the
   *  High/Low Streaks row is off (no shading). */
  streakPeriod?: number | null;
  /** Selected streak band tightness (percent) — only used together with
   *  streakPeriod. */
  streakPct?: number | null;
  /** Anchor index into pair.rows for the streak band window — the trailing
   *  streakPeriod rows BEFORE (and incl.) this row; null = latest row.
   *  Driven by clicking a chart date. */
  streakAnchorIdx?: number | null;
  /** Px-Vol States overlay (selected speed × vol combo): precomputed
   *  markArea rects over the consecutive runs of matching dates + the
   *  legend label + the accent color. Null when the row is off. */
  pxVolShade?: ShadeOverlay | null;
  /** Market-regime overlay (stats.market_regime_spans — the selected
   *  regime's contiguous spans): same markArea shape, one per picked
   *  regime. Rendered identically to pxVolShade. */
  regimeShades?: Array<ShadeOverlay>;
  /** Top-10% OHLC Days: the gaps store's per-day anatomy rows for this
   *  code (null when the group is off/unloaded). With ``ohlcGapActive``
   *  the hovered day's pattern + gap legs join the tooltip. */
  ohlcGapRows?: MovAveSpreadOhlcGapDay[] | null;
  /** A Top-10% OHLC Days toggle is on. */
  ohlcGapActive?: boolean;
  /** The code's TREND spans (analysis.trend_days via the trend-spans
   *  API — the signals tier's RSI re-entry classification, shared with
   *  the rule cells) for the tooltip's per-date trend readout. Null
   *  while loading / on fetch failure — the tooltip row is omitted. */
  trendSpans?: TrendSpansRecord | null;
}

export function buildPairOption({
  pair,
  shared,
  themeMode,
  bollingerK = 2,
  tradingAmtMode = "lowkey",
  rsiTrendMode = false,
  rsiWindows = null,
  rsiPopulation = null,
  derivTrend = null,
  momentumTrend = null,
  ohlcMode = "absolute",
  roofFloorWindow = null,
  roofFloorClickIdx = null,
  roofFloorRows = null,
  roofFloorEstRows = null,
  roofFloorEstMethod = null,
  roofFloorRebounds = null,
  longStreaks = null,
  streakPeriod = null,
  streakPct = null,
  streakAnchorIdx = null,
  pxVolShade = null,
  regimeShades = null,
  ohlcGapRows = null,
  ohlcGapActive = false,
  trendSpans = null,
}: BuildPairOptionArgs): EChartsOption {
  const c = axisColors(themeMode);
  const rows = pair.rows;
  const n = rows.length;

  const isPricePair = pair.ma_short === 0;

  const dates = rows.map((r) => r.date);
  const shorts = rows.map((r) => r.short_value);
  const longs = rows.map((r) => r.long_value);
  // Price derivatives (the only persisted ones) — non-null only on
  // Price/* pairs' rows (the price legs).
  const shortSlopes = rows.map((r) => r.short_slope);
  const shortCurvs = rows.map((r) => r.short_curvature);
  const longStds = rows.map((r) => r.long_std);
  const opens = rows.map((r) => r.open);
  const highs = rows.map((r) => r.high);
  const lows = rows.map((r) => r.low);
  const tradingAmts = rows.map((r) => r.trading_amount);

  // ---- rsiTrend group ---------------------------------------------------
  // Tooltip-only per-date metrics (RSI, trading-amt slopes/shares, rolling-
  // window OHLC) are shared across all pairs — one copy in the chart
  // response's `shared` array, read here at the same index.
  const rsiArrays = buildRsiArrays(shared, n);
  const rsiFlags =
    rsiPopulation != null ? buildRsiFlagArrays(shared, n) : null;
  const rsiTrendSpecs = rsiTrendLines(
    rsiArrays, rsiWindows, rsiPopulation, rsiFlags,
  );

  // ---- derivTrend group --------------------------------------------------
  // One scale SHARED by all the family's lines so their magnitudes stay
  // comparable (see derivTrend/scale.ts).
  const derivMaxAbs = derivTrend != null ? derivMaxAbsOf(derivTrend.lines) : 0;

  // ---- tradingAmt group ---------------------------------------------------
  // The long window's amt-MA slope / market share (tooltip readouts) and
  // the null-cleared bar data.
  const amtMaSlopeOfLong = rows.map((_, i) =>
    sharedNum(shared, i, SHARED_AMT_SLOPE_KEYS[pair.ma_long] ?? "trading_amt_ma255_slope"),
  );

  const amtMarketShareOfLong = rows.map((_, i) =>
    sharedNum(shared, i, SHARED_AMT_SHARE_KEYS[pair.ma_long] ?? "trading_amt_market_share_ma255"),
  );

  const ohlcOpens = rows.map((_, i) => sharedNum(shared, i, `open_${pair.ma_long}d` as keyof MovAveSpreadSharedMetricsRow));
  const ohlcHighs = rows.map((_, i) => sharedNum(shared, i, `high_${pair.ma_long}d` as keyof MovAveSpreadSharedMetricsRow));
  const ohlcLows = rows.map((_, i) => sharedNum(shared, i, `low_${pair.ma_long}d` as keyof MovAveSpreadSharedMetricsRow));

  // The code's TREND spans (analysis.trend_days — the signals tier's
  // RSI re-entry classification) for the tooltip's per-date readout;
  // the Trend section's SHADES arrive via regimeShades (the shared
  // shade-overlay path). REPLACES the retired client-side close-vs-MA60
  // classifier (2026-10-15 — the MA60 leg identified turns months
  // late).
  const hasTrendSpans = trendSpans != null
    && Object.keys(trendSpans).length > 0;

  // ---- Percentage mode base --------------------------------------------
  const base = pctBaseOf(shorts);
  const fmtPrice = makeFmtPrice(ohlcMode, base);

  // OHLC bar data (core pair chart)
  const ohlcData: Array<Array<number | null>> = new Array(n).fill(null);
  if (isPricePair) {
    for (let i = 0; i < n; i++) {
      const o = opens[i];
      const cl = shorts[i];
      const l = lows[i];
      const h = highs[i];
      if (o != null && cl != null && l != null && h != null) {
        ohlcData[i] = [o, cl, l, h];
      }
    }
  }

  // Gap fill stack (core pair chart)
  const baseData: Array<number | null> = new Array(n).fill(null);
  const posData: Array<number | null> = new Array(n).fill(null);
  const negData: Array<number | null> = new Array(n).fill(null);

  for (let i = 0; i < n; i++) {
    const s = shorts[i];
    const l = longs[i];
    if (s == null || l == null) continue;
    const diff = s - l;
    baseData[i] = Math.min(s, l);
    if (diff >= 0) {
      posData[i] = diff;
      negData[i] = 0;
    } else {
      posData[i] = 0;
      negData[i] = -diff;
    }
  }

  const sColor = SPOT_COLOR;
  const lColor = MA120_COLOR;
  const isEma = pair.kind === "ema";
  const sName = shortLabel(pair.ma_short, pair.kind);
  const lName = isEma ? `EMA${pair.ma_long}` : `MA${pair.ma_long}`;

  // ---- bollinger group ----------------------------------------------------
  const boll = computePairBollinger(isPricePair, longs, longStds, bollingerK);
  const showBoll = boll.showBoll;

  // ---- roofFloor group -----------------------------------------------------
  const roofFloor = buildRoofFloorState({
    n,
    dates,
    highs,
    lows,
    roofFloorWindow,
    roofFloorClickIdx,
    roofFloorRows,
    textColor: c.textColor,
  });

  // ---- roofFloor estimate overlay (the Est-levels toggle) ------------------
  // The chosen date's estimated roof/floor levels under the picked method,
  // drawn as two horizontal dashed lines (markLine entries merged with the
  // anchor mark line onto the long-MA series below).
  const rfEstimate = buildRoofFloorEstimateState({
    n,
    roofFloorWindow,
    roofFloorClickIdx,
    estRows: roofFloorEstRows ?? null,
    estMethod: roofFloorEstMethod ?? null,
    fmtValue: fmtPrice,
  });
  const estMarkData = rfEstimate.markLine?.markLine.data ?? [];
  const anchorMarkData = roofFloor.anchorMarkLine?.markLine.data ?? [];
  const longMarkData = [...anchorMarkData, ...estMarkData];

  // ---- roofFloor rebound-audit marks (the window's event cohort) ----------
  // Larger triangles at the qualifying rebounded extremes: strictly
  // before the chosen date and near that side's median event level.
  const rfAuditMarks = roofFloorWindow != null
    ? buildReboundAuditMarks(roofFloorRebounds ?? null, roofFloorWindow, n, dates, roofFloorClickIdx)
    : null;

  // ---- roofFloor change-since plot -----------------------------------------
  // The enabled window's change-since-last-anchor lines own the right-axis
  // slot (below the rsiTrend/derivTrend overlays): one shared scale maps
  // both sides into the band, zero pinned mid-band as the central line.
  const changeSinceActive = roofFloor.rfAligned && roofFloor.rfW != null;
  const changeSinceMaxAbs = changeSinceActive
    ? changeSinceMaxAbsOf([
        roofFloor.roofFloorWinArrays?.changeSinceLastLow ?? [],
        roofFloor.roofFloorWinArrays?.changeSinceLastHigh ?? [],
      ])
    : 1;

  // ---- streaks group ---------------------------------------------------
  const streakData = computeStreakShading(
    rows,
    streakPeriod,
    streakPct,
    streakAnchorIdx,
    longStreaks,
  );
  const streakLabel = streakLegendLabel(streakPeriod, streakPct);

  // Legend data
  const legendData: string[] = [sName, lName];
  if (showBoll) {
    legendData.push(boll.upperName, boll.lowerName);
  }
  if (roofFloor.rfAligned && roofFloor.rfW != null) {
    legendData.push(`High(${roofFloor.rfW}d)`, `Low(${roofFloor.rfW}d)`);
    if (roofFloor.roofSeg != null) legendData.push(`Roof(${roofFloor.rfW}d)`);
    if (roofFloor.floorSeg != null) legendData.push(`Floor(${roofFloor.rfW}d)`);
  }
  if (rsiTrendMode) {
    legendData.push(...rsiTrendSpecs.map((s) => s.name));
  } else if (derivTrend != null) {
    legendData.push(...derivTrend.lines.map((l) => l.name));
  } else if (momentumTrend != null) {
    legendData.push(...momentumTrend.lines.map((l) => l.name));
  } else if (changeSinceActive && roofFloor.rfW != null) {
    // The roof/floor window's change-since-last-anchor plot owns the
    // right-axis slot (replaces the amt bars).
    legendData.push(
      changeSinceLowName(roofFloor.rfW),
      changeSinceHighName(roofFloor.rfW),
    );
  } else if (tradingAmtMode !== "off") {
    legendData.push("Amt Up", "Amt Down");
  }
  if (streakData != null) {
    legendData.push(`High ${streakLabel}`, `Low ${streakLabel}`);
  }
  const shadeOverlays: ShadeOverlay[] = [
    ...(pxVolShade != null ? [pxVolShade] : []),
    ...(regimeShades ?? []),
  ];
  for (const so of shadeOverlays) {
    legendData.push(so.label);
  }

  const amtBarData = amtBarDataOf(tradingAmts);

  // ---- Build series ----------------------------------------------------
  const echartsSeries: SeriesList = [];

  if (isPricePair) {
    echartsSeries.push(ohlcSeries(ohlcData, { name: sName, z: 5 }));
  } else {
    echartsSeries.push({
      type: "line",
      name: sName,
      data: shorts,
      symbol: "none",
      lineStyle: { color: sColor, width: 1.4 },
      z: 5,
    });
  }

  echartsSeries.push({
    type: "line",
    name: lName,
    data: longs,
    symbol: "none",
    lineStyle: { color: lColor, width: 1.4 },
    z: 5,
    // One shared markLine block: the roof/floor anchor date line (xAxis;
    // textColor) + the estimate level lines (yAxis; per-item lineStyle +
    // value labels override the block defaults).
    ...(longMarkData.length > 0
      ? {
          markLine: {
            silent: true,
            symbol: ["none", "none"],
            label: { show: false },
            lineStyle: {
              type: "dashed",
              color: c.textColor,
              width: 1,
              opacity: 0.75,
            },
            data: longMarkData,
          },
        }
      : {}),
  });

  echartsSeries.push({
    type: "line",
    name: "_base",
    data: baseData,
    stack: "gapFill",
    symbol: "none",
    lineStyle: { opacity: 0 },
    z: 1,
  });
  echartsSeries.push({
    type: "line",
    name: "_pos",
    data: posData,
    stack: "gapFill",
    symbol: "none",
    lineStyle: { opacity: 0 },
    areaStyle: { color: UP_COLOR, opacity: 0.4 },
    z: 2,
  });
  echartsSeries.push({
    type: "line",
    name: "_neg",
    data: negData,
    stack: "gapFill",
    symbol: "none",
    lineStyle: { opacity: 0 },
    areaStyle: { color: DOWN_COLOR, opacity: 0.4 },
    z: 2,
  });

  // Bollinger envelope (bollinger group).
  appendBollingerSeries(echartsSeries, boll);

  // Roof/floor-window overlay series (roofFloor group).
  appendRoofFloorTrendSeries(echartsSeries, roofFloor);

  // Rebound-audit marks (roofFloor group — larger triangles at the
  // rebounded extremes before the chosen date and near the median
  // roof/floor; the enabled window's event cohort).
  if (roofFloorWindow != null) {
    appendReboundAuditSeries(echartsSeries, rfAuditMarks, roofFloorWindow);
  }

  // Per-side high/low streak shading series (streaks group) — same
  // toggle-per-legend-entry pattern as the shade overlays below. Both
  // sides always carry the window zone rect when the combo is selected;
  // the dark band only when that side broke the band.
  appendStreakSeries(echartsSeries, streakData, streakLabel, 1);

  // Px-Vol States shading + market-regime spans (one rect-marked legend
  // series per overlay — z=0 so the shade sits behind the streak layers
  // it may overlap).
  for (const so of shadeOverlays) {
    if (so.data.length === 0) continue;
    echartsSeries.push(shadeOverlaySeries({
      name: so.label,
      data: so.data,
      accent: so.accent,
      z: 0,
    }));
  }

  if (rsiTrendMode) {
    // RSI trend lines replace the amt bars on the SAME plot (rsiTrend
    // group).
    appendRsiTrendSeries(echartsSeries, rsiTrendSpecs, c.axisLineColor);
  } else if (derivTrend != null) {
    // Slope/Curvature overlay (derivTrend group).
    appendDerivTrendSeries(echartsSeries, derivTrend, derivMaxAbs, c.axisLineColor);
  } else if (momentumTrend != null) {
    // Momentum overlay (momentumTrend group) — same exclusive right-axis
    // slot, below rsiTrend/derivTrend in the precedence.
    appendMomentumTrendSeries(echartsSeries, momentumTrend, c.axisLineColor);
  } else if (changeSinceActive) {
    // The enabled roof/floor window's change-since-last-anchor lines
    // (roofFloor group) — Δ Low above / Δ High below the pinned zero
    // central line — replace the amt bars while a window button is on.
    appendChangeSinceSeries(echartsSeries, roofFloor, c.axisLineColor, changeSinceMaxAbs);
  } else if (tradingAmtMode !== "off") {
    // Lowkey trading-amount bars (tradingAmt group).
    const { upData, downData } = splitAmtUpDown(amtBarData, isPricePair, opens, shorts, longs);
    appendAmtBarSeries(echartsSeries, upData, downData);
  }

  const grid = commonGrid({ left: 55, right: 55, bottom: 50 });

  // ---- Build tooltip context -------------------------------------------
  // Top-10% OHLC Days tooltip info: the gaps store's anatomy aligned to
  // pair.rows dates (null unless a toggle is on and the rows landed).
  const ohlcGapInfo = buildGapInfo(ohlcGapRows, ohlcGapActive, dates);

  const tooltipContext: TooltipContext = {
    dates,
    shorts,
    longs,
    shortSlopes,
    shortCurvs,
    longStds,
    opens,
    highs,
    lows,
    tradingAmts,
    rsi3: rsiArrays[3],
    rsi6: rsiArrays[6],
    rsi10: rsiArrays[10],
    rsi14: rsiArrays[14],
    rsi20: rsiArrays[20],
    rsi60: rsiArrays[60],
    amtMaSlopeOfLong,
    amtMarketShareOfLong,
    ohlcOpens,
    ohlcHighs,
    ohlcLows,
    roofFloorWindow: roofFloor.rfAligned ? roofFloor.rfW : null,
    roofFloorWinArrays: roofFloor.roofFloorWinArrays,
    ohlcGapInfo,
    trendSpans,
    hasTrendSpans,
    hasBase: base.hasBase,
    baseVal: base.baseVal,
    showBoll,
    upperData: boll.upperData,
    lowerData: boll.lowerData,
    bollBase: boll.bollBase,
    pair,
    isPricePair,
    sName,
    lName,
    tradingAmtMode,
    ohlcMode,
    derivTrend,
    momentumTrend,
  };

  // Shared preamble (transparent bg, animations off, themed fragments) via
  // baseChartOption; the line-snap axisPointer replaces the common cross one
  // and the React-element tooltip formatter overrides the default content.
  return baseChartOption(themeMode, {
    grid,
    dataZoom: commonDataZoom(),
    tooltip: commonTooltip(themeMode, {
      axisPointer: { type: "line", snap: true },
      formatter: buildPairTooltipFormatter(tooltipContext),
    }),
    legend: commonLegend(themeMode, { itemWidth: 12, itemHeight: 7, data: legendData }),
    xAxis: {
      type: "category",
      data: dates,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: {
        color: c.textColor,
        fontSize: 9,
        formatter: (v: string) => v.slice(0, 7),
        interval: Math.max(1, Math.floor(n / 6)),
      },
      splitLine: { show: false },
    },
    yAxis: [
      {
        type: "value",
        scale: true,
        name: ohlcMode === "percentage" ? "%" : undefined,
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: {
          color: c.textColor,
          fontSize: 9,
          formatter: (v: number) => fmtPrice(v),
        },
        splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
      },
      rsiTrendMode
        ? buildRsiRightAxis(c)
        : derivTrend != null
          ? buildDerivRightAxis(c, derivTrend.mode, derivMaxAbs)
          : momentumTrend != null
            ? buildMomentumRightAxis(
                c, derivMaxAbsOf(momentumTrend.lines))
            : changeSinceActive
              ? buildChangeSinceRightAxis(c, changeSinceMaxAbs)
              : buildAmtRightAxis(c),
    ],
    series: echartsSeries,
  });
}
