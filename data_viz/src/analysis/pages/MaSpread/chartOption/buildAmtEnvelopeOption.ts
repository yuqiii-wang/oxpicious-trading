/**
 * Amt Envelope chart — rendered when an Amt/MA pair is selected.
 *
 * Orchestrator only — the button groups the view shares own their
 * computation and series in their subdirectories (bollinger/, streaks/,
 * tradingAmt/, pxVol/, ohlcGaps/, _common/); the envelope's own layers
 * (lowkey H/L band + price line, the 5-MA envelope band, the amt bars)
 * stay here.
 *
 * Shows trading_amount BARS + a slim envelope of all 5 trading_amt_ma lines
 * on the right y-axis (yuan), with the OHLC/price curve shown lowkey
 * (dimmed) on the left y-axis (price).
 */
import { baseChartOption, commonTooltip } from "@/shared/charts/base-chart";
import {
  MA5_COLOR,
  MA20_COLOR,
  MA60_COLOR,
  MA120_COLOR,
  MA255_COLOR,
  UP_COLOR,
  DOWN_COLOR,
  SPOT_COLOR,
  BOLL_BAND_FILL,
  axisColors,
  commonLegend,
  commonGrid,
  commonDataZoom,
} from "@/theme/chart-palette";
import type { ThemeMode } from "@/store/filters";
import type { EChartsOption } from "echarts";
import type {
  MovAveSpreadPairSeries,
  MovAveSpreadAmtRow,
  MovAveSpreadSharedMetricsRow,
  MovAveSpreadOhlcGapDay,
} from "@shared/types";
import type { OhlcMode } from "@/lib/ohlc";
import type {
  LongBandStreak,
} from "@/shared/charts/streakBands";
import { makeFmtPrice, pctBaseOf, sharedNum, shadeOverlaySeries } from "./_common";
import type { SeriesList, ShadeOverlay } from "./_common";
import { computeAmtBollinger, appendAmtBollingerSeries } from "./bollinger";
import {
  SHARED_AMT_SLOPE_KEYS,
  SHARED_AMT_SHARE_KEYS,
  buildAmtRightAxis,
} from "./tradingAmt";
import {
  computeStreakShading,
  streakLegendLabel,
  appendStreakSeries,
} from "./streaks";
import { buildGapInfo } from "./ohlcGaps";
import { buildAmtTooltipFormatter } from "./tooltipFormatter";

/** Trading-amount MA windows in canonical order. */
const AMT_MA_WINDOWS = [5, 20, 60, 120, 255] as const;

/** Color for each trading_amt_ma window. */
const AMT_MA_COLORS: Record<number, string> = {
  5:   MA5_COLOR,
  20:  MA20_COLOR,
  60:  MA60_COLOR,
  120: MA120_COLOR,
  255: MA255_COLOR,
};

export interface BuildAmtEnvelopeOptionArgs {
  /** The selected Amt/MA pair's full time series (rows carry the 5
   *  trading_amt_ma envelope columns). */
  pair: Omit<MovAveSpreadPairSeries, "rows"> & { rows: MovAveSpreadAmtRow[] };
  /** Per-date tooltip metrics shared across pairs (chart response `shared`)
   *  — the amt MA slopes / market shares the tooltip shows. Index-aligned
   *  with pair.rows; null renders those readouts as "—". */
  shared: MovAveSpreadSharedMetricsRow[] | null;
  /** Current theme mode (light / dark). */
  themeMode: ThemeMode;
  /** Display mode for the lowkey price series (absolute / percentage). */
  ohlcMode?: OhlcMode;
  /** Bollinger multiplier k in MA ± k × σ. Default 2.0; 0 = hidden. */
  bollingerK?: number;
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
  /** Market-regime overlays (stats.market_regime_spans — each picked
   *  regime's contiguous spans), rendered identically. */
  regimeShades?: Array<ShadeOverlay>;
  /** Top-10% OHLC Days: the gaps store's per-day anatomy rows for this
   *  code (null when the group is off/unloaded). With ``ohlcGapActive``
   *  the hovered day's pattern + gap legs join the tooltip. */
  ohlcGapRows?: MovAveSpreadOhlcGapDay[] | null;
  /** A Top-10% OHLC Days toggle is on. */
  ohlcGapActive?: boolean;
}

/** Build the ECharts option for the Amt Envelope chart. */
export function buildAmtEnvelopeOption({
  pair,
  shared,
  themeMode,
  ohlcMode = "absolute",
  bollingerK = 2,
  longStreaks = null,
  streakPeriod = null,
  streakPct = null,
  streakAnchorIdx = null,
  pxVolShade = null,
  regimeShades = null,
  ohlcGapRows = null,
  ohlcGapActive = false,
}: BuildAmtEnvelopeOptionArgs): EChartsOption {
  const c = axisColors(themeMode);
  const rows = pair.rows;
  const n = rows.length;
  const selectedWindow = pair.ma_long;

  const dates = rows.map((r) => r.date);
  const highs = rows.map((r) => r.high);
  const lows = rows.map((r) => r.low);
  const tradingAmts = rows.map((r) => r.trading_amount);

  // long_std holds trading_amt_stdW for amt pairs (set by the backend).
  const longStds = rows.map((r) => r.long_std);

  const amtMaArrays: Record<number, Array<number | null>> = {
    5:   rows.map((r) => r.trading_amt_ma5),
    20:  rows.map((r) => r.trading_amt_ma20),
    60:  rows.map((r) => r.trading_amt_ma60),
    120: rows.map((r) => r.trading_amt_ma120),
    255: rows.map((r) => r.trading_amt_ma255),
  };
  // Slopes / market shares are per-date values shared across pairs — read
  // from the chart response's `shared` row at the same index.
  const amtMaSlopeArrays: Record<number, Array<number | null>> = Object.fromEntries(
    AMT_MA_WINDOWS.map((w) => [
      w,
      rows.map((_, i) => sharedNum(shared, i, SHARED_AMT_SLOPE_KEYS[w])),
    ]),
  );
  const amtMarketShareArrays: Record<number, Array<number | null>> = Object.fromEntries(
    AMT_MA_WINDOWS.map((w) => [
      w,
      rows.map((_, i) => sharedNum(shared, i, SHARED_AMT_SHARE_KEYS[w])),
    ]),
  );

  // Envelope band
  const envUpper: Array<number | null> = new Array(n).fill(null);
  const envLower: Array<number | null> = new Array(n).fill(null);
  for (let i = 0; i < n; i++) {
    const vals: number[] = [];
    for (const w of AMT_MA_WINDOWS) {
      const v = amtMaArrays[w][i];
      if (v != null && Number.isFinite(v)) vals.push(v);
    }
    if (vals.length > 0) {
      envUpper[i] = Math.max(...vals);
      envLower[i] = Math.min(...vals);
    }
  }

  // ---- Percentage mode base ----
  const base = pctBaseOf(highs);
  const fmtPrice = makeFmtPrice(ohlcMode, base);

  // ---- Build series ----
  const echartsSeries: SeriesList = [];

  // ---- High/low streak shading (purple above / yellow below the band) ---
  // Active when BOTH nested buttons are picked (period then pct) — the
  // streaks group's shared computation (see streaks/build.ts for the
  // window/zone/streak semantics). All values are raw prices (the price
  // y-axis stays raw in every display mode). Two series (high + low side,
  // z=0 price y-axis, behind everything) — legend entries toggle each
  // side.
  const streakData = computeStreakShading(
    rows,
    streakPeriod,
    streakPct,
    streakAnchorIdx,
    longStreaks,
  );
  const streakLabel = streakLegendLabel(streakPeriod, streakPct);

  // Lowkey high-low band
  const hlBase: Array<number | null> = lows.slice();
  const hlDelta: Array<number | null> = new Array(n).fill(null);
  for (let i = 0; i < n; i++) {
    const h = highs[i];
    const l = lows[i];
    if (h != null && l != null) {
      hlDelta[i] = h - l;
    }
  }
  echartsSeries.push({
    type: "line",
    name: "_hlBase",
    data: hlBase,
    stack: "hlBand",
    symbol: "none",
    lineStyle: { opacity: 0 },
    yAxisIndex: 0,
    z: 0,
  });
  echartsSeries.push({
    type: "line",
    name: "_hlDelta",
    data: hlDelta,
    stack: "hlBand",
    symbol: "none",
    lineStyle: { opacity: 0 },
    areaStyle: { color: SPOT_COLOR, opacity: 0.08 },
    yAxisIndex: 0,
    z: 0,
  });

  // Lowkey price line (close proxy)
  const closeProxy: Array<number | null> = new Array(n).fill(null);
  for (let i = 0; i < n; i++) {
    const h = highs[i];
    const l = lows[i];
    if (h != null && l != null) {
      closeProxy[i] = (h + l) / 2;
    }
  }
  echartsSeries.push({
    type: "line",
    name: "Price",
    data: closeProxy,
    symbol: "none",
    lineStyle: { color: SPOT_COLOR, width: 1, opacity: 0.25 },
    yAxisIndex: 0,
    z: 1,
  });

  // Envelope fill band
  echartsSeries.push({
    type: "line",
    name: "_envBase",
    data: envLower,
    stack: "envBand",
    symbol: "none",
    lineStyle: { opacity: 0 },
    yAxisIndex: 1,
    z: 2,
  });
  echartsSeries.push({
    type: "line",
    name: "_envDelta",
    data: envUpper.map((u, i) => {
      const l = envLower[i];
      return u != null && l != null ? u - l : null;
    }),
    stack: "envBand",
    symbol: "none",
    lineStyle: { opacity: 0 },
    areaStyle: { color: BOLL_BAND_FILL, opacity: 0.10 },
    yAxisIndex: 1,
    z: 2,
  });

  // Trading amount bars
  const selectedAmtMa = amtMaArrays[selectedWindow];
  const amtUpData: Array<number | null> = new Array(n).fill(null);
  const amtDownData: Array<number | null> = new Array(n).fill(null);
  for (let i = 0; i < n; i++) {
    const amt = tradingAmts[i];
    const ma = selectedAmtMa[i];
    if (amt == null || !Number.isFinite(amt)) continue;
    const isUp = ma != null && Number.isFinite(ma) ? amt >= ma : true;
    if (isUp) amtUpData[i] = amt;
    else amtDownData[i] = amt;
  }
  echartsSeries.push({
    type: "bar",
    name: "Amt Above",
    data: amtUpData,
    yAxisIndex: 1,
    barWidth: "60%",
    stack: "amt",
    itemStyle: { color: UP_COLOR, opacity: 0.42 },
    z: 3,
  });
  echartsSeries.push({
    type: "bar",
    name: "Amt Below",
    data: amtDownData,
    yAxisIndex: 1,
    barWidth: "60%",
    stack: "amt",
    itemStyle: { color: DOWN_COLOR, opacity: 0.42 },
    z: 3,
  });

  // 5 trading_amt_ma lines
  for (const w of AMT_MA_WINDOWS) {
    const isSelected = w === selectedWindow;
    echartsSeries.push({
      type: "line",
      name: `Amt MA${w}`,
      data: amtMaArrays[w],
      symbol: "none",
      smooth: 0.2,
      lineStyle: {
        color: AMT_MA_COLORS[w],
        width: isSelected ? 1.5 : 0.8,
        opacity: isSelected ? 0.85 : 0.4,
      },
      yAxisIndex: 1,
      z: isSelected ? 7 : 5,
    });
  }

  // ---- Bollinger envelope around selected trading_amt_ma -----------
  // (bollinger group).
  const amtBoll = computeAmtBollinger(selectedAmtMa, longStds, bollingerK);
  appendAmtBollingerSeries(echartsSeries, amtBoll);

  // ---- Legend ----
  const legendData: string[] = ["Price", "Amt Above", "Amt Below"];
  for (const w of AMT_MA_WINDOWS) {
    legendData.push(`Amt MA${w}`);
  }
  if (amtBoll.showBoll) {
    legendData.push(amtBoll.upperName, amtBoll.lowerName);
  }
  // Per-side high/low streak shading series (rect legend marker + that
  // side's markArea: light window zone + the darker whole-window long
  // streak band) — same toggle-per-legend-entry pattern as the
  // shade overlays below. Both sides always carry the window zone rect
  // when the combo is selected.
  appendStreakSeries(echartsSeries, streakData, streakLabel, 0);
  if (streakData != null) {
    if (streakData.high.length > 0) legendData.push(`High ${streakLabel}`);
    if (streakData.low.length > 0) legendData.push(`Low ${streakLabel}`);
  }

  // Px-Vol States + market-regime shading — same rect-legend + markArea
  // pattern as the streak shading above (one legend series per overlay).
  const amtShadeOverlays: ShadeOverlay[] = [
    ...(pxVolShade != null ? [pxVolShade] : []),
    ...(regimeShades ?? []),
  ];
  for (const so of amtShadeOverlays) {
    if (so.data.length === 0) continue;
    legendData.push(so.label);
    echartsSeries.push(shadeOverlaySeries({
      name: so.label,
      data: so.data,
      accent: so.accent,
      z: 0,
    }));
  }

  const grid = commonGrid({ left: 55, right: 65, bottom: 50 });

  // ---- Build tooltip ----
  const formatter = buildAmtTooltipFormatter({
    dates,
    tradingAmts,
    amtMaArrays,
    amtMaSlopeArrays,
    amtMarketShareArrays,
    highs,
    lows,
    selectedWindow,
    amtMaWindows: AMT_MA_WINDOWS,
    fmtPrice,
    // Top-10% OHLC Days tooltip info (active only when a toggle is on):
    // the gaps store's anatomy aligned to the chart's date axis.
    gapInfo: buildGapInfo(ohlcGapRows, ohlcGapActive, dates),
  });

  // Shared preamble (transparent bg, animations off, themed fragments) via
  // baseChartOption; the line-snap axisPointer replaces the common cross one
  // and the React-element tooltip formatter overrides the default content.
  return baseChartOption(themeMode, {
    grid,
    dataZoom: commonDataZoom(),
    tooltip: commonTooltip(themeMode, {
      axisPointer: { type: "line", snap: true },
      formatter,
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
        name: ohlcMode === "percentage" ? "%" : "Price",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: {
          color: c.textColor,
          fontSize: 9,
          formatter: (v: number) => fmtPrice(v),
        },
        splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.3 } },
      },
      buildAmtRightAxis(c),
    ],
    series: echartsSeries,
  });
}
