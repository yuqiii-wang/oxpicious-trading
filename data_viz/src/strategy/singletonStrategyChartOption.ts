/**
 * ECharts option builder for the singleton strategy backtest chart.
 *
 * Renders:
 *   1. OHLC candlesticks (primary y-axis, left)
 *   2. MA5 + MA60 lines (primary y-axis)
 *   3. Trading amount bars (secondary y-axis, right)
 *   4. BUY markers (green dots; dark green for top-3 confidence) at fill_price
 *   5. SELL markers (red dots; dark red for top-3 losses) at fill_price
 *   6. Rich tooltip on hover showing full decision details
 *   7. Total return % as a graphic text annotation
 *
 * Normalization: every price-derived series (OHLC, MA5, MA60, B/S marker
 * positions) is rebased so the FIRST BUY fill = 100. The anchor
 * (summary.first_buy_fill_price, precomputed by the Python backtest and
 * stored on strategy_results) is the SAME value each trade_decision's
 * normalized_fill_price is rebased against, so the chart's OHLC/MA frame
 * and the per-decision normalized index stay consistent. A dashed "Base 100"
 * reference line is drawn at y=100. Trading amount is a volume measure on
 * its own axis and is NOT rebased. Tooltips always show ACTUAL prices
 * (looked up from the raw OHLC row / decision.fill_price) so real values
 * remain visible despite the rebased axis. If there is no BUY (anchor is
 * null), the chart falls back to absolute prices.
 */
import React from "react";
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import { ohlcSeries } from "@/lib/ohlc";
import { fmtNum, fmtPct } from "@/lib/series";
import { renderReactElement, tooltipComponents } from "@/lib/react-tooltip-renderer";
import {
  MA5_COLOR,
  MA60_COLOR,
  UP_COLOR,
  DOWN_COLOR,
  PE_COLOR,
  axisColors,
  commonLegend,
  commonGrid,
  commonDataZoom,
} from "@/theme/chart-palette";
import { createMarkerTooltipFormatter, createFcSellTooltipFormatter, createFcSellFallbackTooltipFormatter } from "./tooltips";
import type {
  StrategyBacktestResponse,
  StrategyDecision,
  StrategyOhlcRow,
  StrategyPeriodType,
  StrategyForecast1mResponse,
} from "@shared/types";

/**
 * Selected risk-analytics period — set when the user clicks a bar in the
 * RiskPanel chart. The main OHLC chart shades the corresponding date range
 * green (gain) or red (loss) so the viewer can see which trading days drove
 * that period's P&L.
 */
export interface SelectedPeriod {
  periodType: StrategyPeriodType;
  periodValue: string;
  /** true → gain (green shade), false → loss (red shade). */
  isGain: boolean;
}

interface BuildOptionParams {
  data: StrategyBacktestResponse;
  themeMode: ThemeMode;
  /** Currently-selected risk period (null = no shading). */
  selectedPeriod?: SelectedPeriod | null;
  /** 1-month forward forecast (8 scenarios + mean). When present and
   *  non-empty, the chart appends 20 forecast days to the x-axis and draws:
   *    - a light-purple ±2σ shade (envelope between lower and upper band)
   *    - 8 light-purple dashed close-price curves
   *    - 8 light dashed P&L curves + a mean P&L curve
   *  Forecast close prices are converted from forecast-norm (base=100 at
   *  forecast_date close) → backtest-norm (base=100 at first_buy_fill_price)
   *  via stats.anchor_close / stats.first_buy_fill_price so they align with
   *  the OHLC frame. P&L (realized_pnl_forecast) is already in backtest-norm
   *  money and plots directly on the Total P&L y-axis. */
  forecast?: StrategyForecast1mResponse | null;
  /** Mutable ref holding the currently-hovered forecast scenario name (or
   *  null). Updated by a capture-phase mousemove listener in
   *  SingletonStrategyPage; read by the tooltip formatter to highlight the
   *  hovered curve and dim the others. */
  hoveredScenarioRef?: { current: string | null };
}

/**
 * Map a "YYYY-MM-DD" date string to its period label, mirroring the Python
 * `strategy._risks.periods.period_value` helper (year=YYYY, season=YYYY-Qn,
 * month=YYYY-MM). Used to find the OHLC x-axis range that falls inside a
 * selected risk period.
 */
function dateToPeriodValue(dateStr: string, periodType: StrategyPeriodType): string {
  // dateStr is "YYYY-MM-DD" (formatDate normalizes to this).
  const y = dateStr.slice(0, 4);
  if (periodType === "year") return y;
  if (periodType === "month") return dateStr.slice(0, 7); // YYYY-MM
  // season: Q1=Jan-Mar, Q2=Apr-Jun, Q3=Jul-Sep, Q4=Oct-Dec
  const m = parseInt(dateStr.slice(5, 7), 10);
  const q = Math.floor((m - 1) / 3) + 1;
  return `${y}-Q${q}`;
}

/**
 * Find the inclusive [startIdx, endIdx] range of OHLC dates whose period label
 * matches `periodValue`. Returns null if no OHLC date falls in this period
 * (e.g. period predates the backtest or lies outside the dataZoom window).
 */
function findPeriodRange(
  dates: string[],
  periodType: StrategyPeriodType,
  periodValue: string,
): [number, number] | null {
  let start = -1;
  let end = -1;
  for (let i = 0; i < dates.length; i++) {
    if (dateToPeriodValue(dates[i], periodType) === periodValue) {
      if (start < 0) start = i;
      end = i;
    }
  }
  if (start < 0) return null;
  return [start, end];
}

/** Apply alpha transparency to a #RRGGBB hex color → rgba() string. */
function withAlphaHex(hex: string, alpha: number): string {
  const m = /^#([0-9a-fA-F]{6})$/.exec(hex.trim());
  if (!m) return hex;
  const r = parseInt(m[1].slice(0, 2), 16);
  const g = parseInt(m[1].slice(2, 4), 16);
  const b = parseInt(m[1].slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

/** Strip the ``__MIX__<json>__`` prefix from a mixed-mode signal_reason,
 *  returning only the human-readable part. Binary-mode reasons (no prefix)
 *  are returned unchanged. The structured per-algo breakdown tooltip lives
 *  ONLY in the DecisionTable (not in the in-plot tooltip). */
const MIX_PREFIX = "__MIX__";
function stripMixPrefix(reason: string | null | undefined): string {
  if (!reason || !reason.startsWith(MIX_PREFIX)) return reason ?? "";
  const rest = reason.slice(MIX_PREFIX.length);
  const end = rest.indexOf("__");
  if (end < 0) return reason;
  return rest.slice(end + 2);
}

export function buildSingletonStrategyOption({
  data,
  themeMode,
  selectedPeriod = null,
  forecast = null,
  hoveredScenarioRef,
}: BuildOptionParams): EChartsOption {
  const c = axisColors(themeMode);
  const ohlcDates = data.ohlc.map((r) => r.date);

  // ---- 1-month forward forecast: append 20 forecast day-labels to the
  // x-axis and pad all existing series with nulls so they don't draw in the
  // forecast region. Forecast close prices are converted from forecast-norm
  // (base=100 at forecast_date close) → backtest-norm (base=100 at
  // first_buy_fill_price) so they align with the OHLC frame.
  const HORIZON = 20;
  const fcRows = forecast?.rows ?? [];
  const fcStats = forecast?.stats ?? null;
  const hasForecast = fcRows.length > 0 && fcStats != null;
  // Conversion factor forecast-norm → backtest-norm. Both anchors must be
  // present; otherwise we skip the price overlay (P&L still plots since it's
  // already in backtest-norm money).
  const fcConv = hasForecast && fcStats!.first_buy_fill_price && fcStats!.first_buy_fill_price > 0
    ? fcStats!.anchor_close / fcStats!.first_buy_fill_price : null;

  // Forecast day labels: "F+1" .. "F+20" (compact; keeps the x-axis readable).
  const fcLabels = hasForecast
    ? Array.from({ length: HORIZON }, (_, i) => `F+${i + 1}`)
    : [];
  const dates = hasForecast ? [...ohlcDates, ...fcLabels] : ohlcDates;
  const padLen = hasForecast ? HORIZON : 0;
  /** Pad an existing series data array with `padLen` nulls (forecast region). */
  const pad = <T,>(arr: Array<T | null>): Array<T | null> =>
    padLen > 0 ? [...arr, ...Array<null>(padLen)] : arr;

  // ---- Selected-period shading: if a risk-analytics bar is selected,
  // compute the OHLC x-axis index range that falls inside that period and
  // shade it green (gain) or red (loss) via a markArea on the OHLC series.
  // The shade spans the full y-range so it reads as a vertical highlight
  // band over the period's trading days.
  const periodRange = selectedPeriod
    ? findPeriodRange(dates, selectedPeriod.periodType, selectedPeriod.periodValue)
    : null;
  const periodShadeColor = selectedPeriod
    ? (selectedPeriod.isGain ? UP_COLOR : DOWN_COLOR)
    : null;

  // ---- Normalization: rebase every price-derived series to 100 at the
  // first BUY fill. The anchor (first_buy_fill_price) is precomputed by the
  // Python backtest and stored on strategy_results; it's the SAME value each
  // trade_decision.normalized_fill_price is rebased against, so the chart's
  // OHLC/MA frame and the per-decision normalized index stay consistent.
  // Tooltips keep showing actual prices (from data.ohlc / decision.fill_price).
  // Trading amount is a volume measure on its own axis and is NOT rebased.
  // If there is no BUY (first_buy_fill_price is null), fall back to actual
  // prices (scale=1).
  const normBase = data.summary.first_buy_fill_price;
  const isNormalized = normBase != null && normBase > 0;
  const normScale = isNormalized ? 100 / normBase! : 1;
  const norm = (v: number | null | undefined): number | null =>
    v != null && Number.isFinite(v) ? v * normScale : null;

  // OHLC data: [open, close, low, high] — rebased when normalized. Padded
  // with nulls over the forecast region (forecast has its own series).
  const ohlcData = pad(data.ohlc.map((r) =>
    [norm(r.open), norm(r.close), norm(r.low), norm(r.high)] as Array<number | null>));

  // MA lines — rebased in lockstep (same scale) so they stay aligned with
  // the candle closes and keep their relative shape.
  const ma5Data = pad(data.ohlc.map((r) => norm(r.ma5)));
  const ma60Data = pad(data.ohlc.map((r) => norm(r.ma60)));

  // Trading amount bars — NOT rebased (different unit, own y-axis).
  const amtData = pad(data.ohlc.map((r) => r.trading_amount));

  // Build a date→index map for placing B/S markers on the correct x position.
  const dateIdx = new Map<string, number>();
  dates.forEach((d, i) => dateIdx.set(d, i));

  // Build a date→dailyRow map for tooltip display of unrealized_pnl.
  const dailyByDate = new Map<string, typeof data.daily[number]>();
  for (const d of data.daily) dailyByDate.set(d.trade_date, d);

  // Total P&L curve — aligned to OHLC dates (null before first BUY / when
  // no daily row exists). Plotted on its own y-axis (index 2) since it's in
  // normalized money, a different scale from both price (~100) and trading amt.
  // Padded with nulls over the forecast region (forecast P&L has its own series).
  const totalPnlData = pad(data.ohlc.map((r) => {
    const daily = dailyByDate.get(r.date);
    return daily ? daily.total_pnl : null;
  }));

  // Identify top-3 highest-confidence BUYs and top-3 biggest-loss SELLs for
  // highlight coloring (dark green / dark red). Computed client-side from the
  // decisions array — same logic as the Python risk pipeline.
  const topConfBuyNos = new Set<number>();
  data.decisions
    .filter((d) => d.side === "BUY")
    .sort((a, b) => b.qty - a.qty)
    .slice(0, 3)
    .forEach((d) => topConfBuyNos.add(d.decision_no));

  const topLossSellNos = new Set<number>();
  data.decisions
    .filter((d) => d.side === "SELL")
    .sort((a, b) => a.realized_pnl - b.realized_pnl)
    .slice(0, 3)
    .forEach((d) => topLossSellNos.add(d.decision_no));

  const DARK_GREEN = "#006400";
  const DARK_RED = "#8B0000";

  // Buy/Sell marker data: [xIndex, fillPrice, decision] — scatter series
  // use the category index as x so they align with the OHLC bars. The
  // plotted fillPrice is rebased; the decision object keeps actual prices
  // (and its precomputed normalized_fill_price) for the tooltip.
  // FORECAST SELLs are excluded from sellMarkers — they are drawn as purple
  // dots separately (fcSellData) so the chart doesn't show red S dots in the
  // forecast region.
  const FORECAST_SELL_MARKER_PREFIX = "FORECAST SELL";
  const buyMarkers: Array<[number, number, StrategyDecision]> = [];
  const sellMarkers: Array<[number, number, StrategyDecision]> = [];
  for (const d of data.decisions) {
    const idx = dateIdx.get(d.exec_date);
    if (idx == null) continue;
    // Skip forecast sells — rendered as purple dots, not red S markers.
    if (d.side === "SELL" && d.signal_reason?.startsWith(FORECAST_SELL_MARKER_PREFIX)) {
      continue;
    }
    const plotPrice = norm(d.fill_price) ?? d.fill_price;
    if (d.side === "BUY") {
      buyMarkers.push([idx, plotPrice, d]);
    } else {
      sellMarkers.push([idx, plotPrice, d]);
    }
  }

  // Total return annotation text
  const retPct = data.summary.total_return_pct;
  const retColor = retPct >= 0 ? UP_COLOR : DOWN_COLOR;
  const anchorDate = data.summary.first_buy_date;
  const retText = `Total Return: ${retPct >= 0 ? "+" : ""}${fmtPct(retPct)}  (${fmtNum(data.summary.n_buys)}B / ${fmtNum(data.summary.n_sells)}S)${isNormalized && anchorDate ? `  | Base=100 @ ${anchorDate}` : ""}`;

  // Tooltip formatter for B/S markers — shows full decision detail.
  // fill_price is the ACTUAL trade price; the precomputed normalized_fill_price
  // (base=100 at first BUY) is appended in parentheses when normalization is
  // on so the user can connect the marker's y-position to its real price.
  // normalized_mean_buy_price (the cost basis) is shown for SELLs alongside
  // the realized_pnl so the viewer can see what avg buy price the SELL is
  // exiting against.
  const markerTooltipFormatter = createMarkerTooltipFormatter({
    upColor: UP_COLOR,
    downColor: DOWN_COLOR,
    textColor: c.textColor,
    isNormalized,
    fmtNum,
    stripMixPrefix,
  });

  // ---- Forecast series (8 mirror/flip/random curves + 1 mean). Built only when
  // forecast data is present. Each series is padded with nulls over the OHLC
  // region so the curve only draws over the 20 forecast days at the right edge.
  const FC_PURPLE = "#9575CD";
  const fcShade = withAlphaHex(FC_PURPLE, 0.12);   // ±2σ envelope fill
  const fcCurve = withAlphaHex(FC_PURPLE, 0.5);    // 8 dashed curves
  const fcMean = withAlphaHex(FC_PURPLE, 0.9);     // mean curve (solid)

  // Group rows by scenario → {scenario: [20 rows ordered by forecast_day]}.
  const fcByScenario = new Map<string, typeof fcRows>();
  for (const r of fcRows) {
    const arr = fcByScenario.get(r.scenario) ?? [];
    arr.push(r);
    fcByScenario.set(r.scenario, arr);
  }
  // 10 display scenarios (mean is handled separately as the 11th).
  // 2 for 255d/20d ratio:            mirror + flip
  // 2 for 0.5*ratio:                 mirror + flip
  // 2 for 1:1:                       mirror + flip
  // 2 for maxstd ratio:              mirror + flip (peak 1y 255d std / 20d)
  // 2 for 0.5σ random:               random walk + opposite trend
  const fcOrder = [
    "mir_255d_std_scale", "flip_255d_std_scale",
    "mir_255d_std_half_scale", "flip_255d_std_half_scale",
    "mir_20d_std_scale", "flip_20d_std_scale",
    "mir_255d_max_std_scale", "flip_255d_max_std_scale",
    "rand", "rand_opp",
  ] as const;

  /** Build a padded series: nulls over OHLC region EXCEPT the last actual
   *  date (which carries the anchor so the curve connects to the last candle)
   *  + values over the 20 forecast days. */
  const fcSeries = <T,>(vals: Array<T | null>, anchor: T | null = null): Array<T | null> =>
    padLen > 0
      ? [...Array<null>(Math.max(0, ohlcDates.length - 1)), anchor, ...vals]
      : vals;

  // Convert a forecast close (forecast-norm base=100@forecast_date) to the
  // backtest-norm frame (base=100@first_buy_fill_price). Returns null when
  // the conversion anchor is unavailable.
  const fcCloseToPlot = (v: number): number | null =>
    fcConv != null ? v * fcConv : null;

  // Anchor = last actual close in backtest-norm (100 * fcConv = anchor_close
  // rebased to 100@first_buy_fill_price). Prepended to each forecast close
  // curve at the last OHLC date index so the curve visually connects to the
  // last actual candle instead of starting disconnected at F+1.
  const fcAnchor = fcConv != null ? 100 * fcConv : null;

  // Per-scenario close paths (backtest-norm) + PnL paths (already backtest-norm,
  // starting at last_total_pnl so they connect to the actual Total P&L curve).
  const fcClose: Record<string, Array<number | null>> = {};
  const fcPnl: Record<string, number[]> = {};
  for (const sc of [...fcOrder, "mean"]) {
    const arr = fcByScenario.get(sc) ?? [];
    fcClose[sc] = arr.map((r) => fcCloseToPlot(r.close_price));
    fcPnl[sc] = arr.map((r) => r.realized_pnl_forecast);
  }

  // ±2σ band: computed from sigma_daily (20d daily log-return std). The band
  // is a simple ±2σ cumulative drift envelope:
  //   upper[t] = 100 * exp(2 * sigma * sqrt((t+1)/20))
  //   lower[t] = 100 * exp(-2 * sigma * sqrt((t+1)/20))
  // converted to backtest-norm via fcConv. Uses the same stacking trick as
  // before: lower as transparent base + (upper - lower) as the fill area.
  const sigmaDaily = fcStats?.sigma_daily ?? 0;
  const bandLowerRaw: Array<number | null> = [];
  const bandUpperRaw: Array<number | null> = [];
  for (let t = 0; t < HORIZON; t++) {
    const drift = 2 * sigmaDaily * Math.sqrt((t + 1) / HORIZON);
    const lo = 100 * Math.exp(-drift);
    const hi = 100 * Math.exp(drift);
    bandLowerRaw.push(fcCloseToPlot(lo));
    bandUpperRaw.push(fcCloseToPlot(hi));
  }
  const bandLower = fcSeries(bandLowerRaw, fcAnchor);
  const bandUpper = fcSeries(
    bandUpperRaw.map((u, i) => {
      const d = bandLowerRaw[i];
      return u != null && d != null ? u - d : null;
    }),
    0,
  );

  // Forecast price series: ±2σ band (2 stacked series) + 8 dashed curves +
  // mean (solid). All on yAxis 0 (price), drawn only over the forecast region.
  const forecastPriceSeries = hasForecast ? [
    // ±2σ shade — lower boundary (transparent line, stack base).
    {
      name: "Forecast ±2σ",
      type: "line",
      data: bandLower,
      yAxisIndex: 0,
      stack: "fcBand",
      symbol: "none",
      lineStyle: { color: "transparent", width: 0 },
      showInLegend: false,
      emphasis: { disabled: true },
      z: 1,
    } as EChartsOption["series"] extends Array<infer S> ? S : never,
    // ±2σ shade — upper fill (transparent line, light-purple area).
    {
      name: "Forecast ±2σ",
      type: "line",
      data: bandUpper,
      yAxisIndex: 0,
      stack: "fcBand",
      symbol: "none",
      lineStyle: { color: "transparent", width: 0 },
      areaStyle: { color: fcShade },
      emphasis: { disabled: true },
      z: 2,
    } as EChartsOption["series"] extends Array<infer S> ? S : never,
    // 10 dashed scenario curves. triggerLineEvent makes them clickable so
    // the user can select a scenario by clicking its curve — the handler
    // in SingletonStrategyPage parses the seriesName to extract the scenario.
    // emphasis.focus:"self" ensures ONLY the hovered curve gets the emphasis
    // style. All other series have emphasis.disabled:true so they stay frozen
    // (no blinking) when the mouse moves over the forecast region.
    ...fcOrder.map((sc) => ({
      name: `FC ${sc}`,
      type: "line" as const,
      data: fcSeries(fcClose[sc] ?? [], fcAnchor),
      yAxisIndex: 0,
      symbol: "none",
      smooth: false,
      triggerLineEvent: true,
      cursor: "pointer",
      lineStyle: { color: fcCurve, width: 1, type: "dashed" as const },
      emphasis: {
        focus: "self" as const,
        lineStyle: { color: fcMean, width: 2.5, type: "solid" as const },
      },
      z: 3,
    })),
    // Mean forecast curve (solid, more opaque). Frozen — no emphasis.
    {
      name: "FC mean",
      type: "line",
      data: fcSeries(fcClose["mean"] ?? [], fcAnchor),
      yAxisIndex: 0,
      symbol: "none",
      smooth: false,
      lineStyle: { color: fcMean, width: 1.5 },
      emphasis: { disabled: true },
      z: 4,
    } as EChartsOption["series"] extends Array<infer S> ? S : never,
  ] : [];

  // Forecast P&L series removed from the chart per request. P&L forecast
  // data still flows to the Risk Analytics panel and Decision Table via
  // the API response; fcPnl is kept for the forecast-region tooltip.

  // ---- Forecast sell dots. When a scenario is selected, the child seq's
  // forecast SELL decisions are in data.decisions — plot purple dots at
  // the SELECTED scenario's close price for each forecast day so the dots
  // sit exactly ON the scenario's dashed curve (using fill_price, the
  // worst-case sell fill, left dots visibly below the curve). When no
  // scenario is selected (parent seq, no forecast decisions), fall back
  // to the mean forecast rows as a visual reference.
  const FORECAST_SELL_PREFIX = "FORECAST SELL";
  const forecastSellDecisions = data.decisions
    .filter((d) => d.side === "SELL" && d.signal_reason?.startsWith(FORECAST_SELL_PREFIX))
    .sort((a, b) => a.decision_no - b.decision_no);
  const fcSellHasDecisions = forecastSellDecisions.length > 0;
  // Forecast-sell decisions are SPARSE: the algo only emits a SELL on signal
  // days + the final-liquidation day (F+20). So neither the x-position nor
  // the y-position can use the array index — both must use the forecast day
  // parsed from each decision's signal_reason
  // (format: "FORECAST SELL F+{n}: {scenario} scenario, ..."). The dot is
  // plotted at the scenario's CLOSE price for that day so it sits exactly ON
  // the selected dashed curve (fill_price = worst-case sell fill sits below
  // the close and made the dots appear misaligned with the curve).
  const fcSellData = fcSellHasDecisions
    ? forecastSellDecisions.map((d) => {
        const reason = d.signal_reason ?? "";
        const dayM = reason.match(/F\+(\d+):/);
        const scM = reason.match(/:\s+(\S+)\s+scenario,/);
        const t = dayM ? parseInt(dayM[1], 10) - 1 : null; // 0-based forecast day
        const sc = scM ? scM[1] : null;
        const scClose = sc != null && t != null ? fcClose[sc]?.[t] : null;
        const y = scClose != null ? scClose : (norm(d.fill_price) ?? d.fill_price);
        const xIdx = t != null ? ohlcDates.length + t : ohlcDates.length;
        return { value: [xIdx, y], decision: d };
      }).filter((d) => d.value[1] != null)
    : (fcByScenario.get("mean") ?? []).map((r, i) => ({
        value: [ohlcDates.length + i, fcCloseToPlot(r.close_price)],
        forecastRow: r,
      })).filter((d) => d.value[1] != null);

  return {
    // Disable ECharts animation. The chart is rebuilt (notMerge:true in
    // EChart.tsx) whenever selectedPeriod changes (i.e. on every risk-bar
    // click); with animation on, that re-animates the whole OHLC/MA frame
    // each click. Turning it off means the shade just appears over the
    // already-rendered base chart — no "base animation" on bar click.
    animation: false,
    backgroundColor: "transparent",
    title: {
      text: `${data.code} ${data.name}`,
      subtext: retText,
      subtextStyle: { color: retColor, fontSize: 14, fontWeight: 700 },
      left: 10,
      top: 4,
      textStyle: { color: c.textColor, fontSize: 14 },
    },
    legend: commonLegend(themeMode, {
      data: [
        "OHLC", "MA5", "MA60", "Trading Amt", "Total P&L", "BUY", "SELL",
        ...(hasForecast ? ["Forecast ±2σ", "FC mean", "FC Sell"] : []),
      ],
    }),
    grid: commonGrid({ top: 60, bottom: 50, left: 60, right: 130 }),
    xAxis: {
      type: "category",
      data: dates,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: { color: c.textColor, fontSize: 10 },
      splitLine: { show: false },
      boundaryGap: true,
      axisTick: { alignWithLabel: true },
    },
    yAxis: [
      {
        type: "value",
        name: isNormalized ? "Price (Base=100)" : "Price",
        position: "left",
        scale: true,
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: { color: c.textColor, fontSize: 10 },
        splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed" } },
        nameTextStyle: { color: c.textColor, fontSize: 10 },
      },
      {
        type: "value",
        name: "Trading Amt",
        position: "right",
        scale: true,
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: {
          color: c.textColor,
          fontSize: 10,
          formatter: (v: number) => fmtNum(v / 1e8, 1) + "亿",
        },
        splitLine: { show: false },
        nameTextStyle: { color: c.textColor, fontSize: 10 },
      },
      {
        type: "value",
        name: "Total P&L",
        position: "right",
        offset: 60,
        scale: true,
        axisLine: { lineStyle: { color: PE_COLOR } },
        axisLabel: { color: c.textColor, fontSize: 10 },
        splitLine: { show: false },
        nameTextStyle: { color: PE_COLOR, fontSize: 10 },
      },
    ],
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "cross" },
      backgroundColor: c.tooltipBg,
      borderColor: c.axisLineColor,
      textStyle: { color: c.textColor, fontSize: 11 },
      formatter: (params: unknown) => {
        const arr = params as Array<{
          seriesName: string;
          data: unknown;
          marker: string;
          dataIndex: number;
        }>;
        if (!Array.isArray(arr) || arr.length === 0) return "";
        const di = arr[0].dataIndex ?? 0;
        const date = dates[di] ?? "";
        const row: StrategyOhlcRow | undefined = data.ohlc[di];

        if (!row && hasForecast) {
          const fcDay = di - ohlcDates.length;
          if (fcDay < 0 || fcDay >= HORIZON) return "";
          const dayLabel = `F+${fcDay + 1}`;
          const hovered = hoveredScenarioRef?.current ?? null;
          const children: React.ReactNode[] = [
            React.createElement("b", { style: { color: FC_PURPLE } }, `${dayLabel} · Forecast`),
          ];
          for (const sc of [...fcOrder, "mean"]) {
            const closeArr = fcClose[sc] ?? [];
            const pnlArr = fcPnl[sc] ?? [];
            const closeNorm = closeArr[fcDay];
            const pnlVal = pnlArr[fcDay];
            if (closeNorm == null || !Number.isFinite(closeNorm)) continue;
            const actualClose = isNormalized
              ? closeNorm / normScale
              : closeNorm;
            const pnlStr = pnlVal != null && Number.isFinite(pnlVal)
              ? ` | P&L: ${pnlVal >= 0 ? "+" : ""}${fmtNum(pnlVal, 2)}`
              : "";
            const label = sc === "mean" ? "mean" : sc;
            if (sc === hovered) {
              children.push(
                React.createElement("b", { style: { color: FC_PURPLE, fontSize: 12 } },
                  `▶ ${label}: ${fmtNum(actualClose, 2)}${pnlStr}`),
              );
            } else if (hovered != null) {
              children.push(
                React.createElement("span", { style: { opacity: 0.4, fontSize: 10 } },
                  `${label}: ${fmtNum(actualClose, 2)}${pnlStr}`),
              );
            } else {
              children.push(
                React.createElement(React.Fragment, null, `${label}: ${fmtNum(actualClose, 2)}${pnlStr}`),
              );
            }
          }
          return renderReactElement(React.createElement(React.Fragment, null, children));
        }
        if (!row) return "";
        const children: React.ReactNode[] = [
          tooltipComponents.Header({ children: date }),
        ];
        for (const p of arr) {
          if (p.seriesName === "OHLC") {
            children.push(React.createElement(React.Fragment, null,
              `${p.marker}O ${fmtNum(row.open)}  H ${fmtNum(row.high)}  L ${fmtNum(row.low)}  C ${fmtNum(row.close)}`));
          } else if (p.seriesName === "MA5") {
            children.push(React.createElement(React.Fragment, null, `${p.marker}MA5: ${fmtNum(row.ma5)}`));
          } else if (p.seriesName === "MA60") {
            children.push(React.createElement(React.Fragment, null, `${p.marker}MA60: ${fmtNum(row.ma60)}`));
          } else if (p.seriesName === "Trading Amt") {
            children.push(React.createElement(React.Fragment, null, `${p.marker}Amt: ${fmtNum((p.data as number) / 1e8, 2)}亿`));
          }
        }
        const decision = data.decisions.find((d) => d.exec_date === date);
        if (decision) {
          const sc = decision.side === "BUY" ? UP_COLOR : DOWN_COLOR;
          children.push(
            React.createElement("b", { style: { color: sc } },
              `${decision.side} #${decision.decision_no}`),
            ` @ ${fmtNum(decision.fill_price, 4)} | ${stripMixPrefix(decision.signal_reason)}`,
          );
        }
        const daily = dailyByDate.get(date);
        if (daily) {
          const upColor = daily.unrealized_pnl >= 0 ? UP_COLOR : DOWN_COLOR;
          const tpColor = daily.total_pnl >= 0 ? UP_COLOR : DOWN_COLOR;
          const rrColor = daily.return_rate >= 0 ? UP_COLOR : DOWN_COLOR;
          children.push(
            React.createElement(React.Fragment, null, [
              "Unrealized: ",
              React.createElement("b", { style: { color: upColor } },
                `${daily.unrealized_pnl >= 0 ? "+" : ""}${fmtNum(daily.unrealized_pnl, 2)}`),
              " | Total: ",
              React.createElement("b", { style: { color: tpColor } },
                `${daily.total_pnl >= 0 ? "+" : ""}${fmtNum(daily.total_pnl, 2)}`),
              ` | Pos: ${fmtNum(daily.position_value, 2)} (${fmtNum(daily.total_qty, 1)})`,
            ]),
          );
          children.push(
            React.createElement(React.Fragment, null, [
              "Return: ",
              React.createElement("b", { style: { color: rrColor } },
                `${daily.return_rate >= 0 ? "+" : ""}${fmtNum(daily.return_rate * 100, 2)}%`),
              "/yr",
              ` | Hold: ${fmtNum(daily.normalized_mean_buy_period, 1)}d`,
              ` | Sharpe: full=${fmtNum(daily.sharpe_ratio, 2)}`,
              ` | 255d=${fmtNum(daily.sharpe_ratio_255d, 2)}`,
              ` | 500d=${fmtNum(daily.sharpe_ratio_500d, 2)}`,
            ]),
          );
        }
        return renderReactElement(React.createElement(React.Fragment, null, children));
      },
    },
    dataZoom: commonDataZoom({}, 60, 100),
    series: [
      // emphasis.disabled freezes non-forecast series so they don't blink
      // when the mouse hovers over the forecast region. Only forecast curves
      // have emphasis enabled (with focus:"self" for granular highlighting).
      {
        ...ohlcSeries(ohlcData, { name: "OHLC", yAxisIndex: 0, z: 10 }),
        emphasis: { disabled: true },
      },
      {
        name: "MA5",
        type: "line",
        data: ma5Data,
        yAxisIndex: 0,
        smooth: false,
        symbol: "none",
        lineStyle: { color: MA5_COLOR, width: 1.2 },
        emphasis: { disabled: true },
        z: 5,
      },
      {
        name: "MA60",
        type: "line",
        data: ma60Data,
        yAxisIndex: 0,
        smooth: false,
        symbol: "none",
        lineStyle: { color: MA60_COLOR, width: 1.2 },
        emphasis: { disabled: true },
        z: 5,
        // Base-100 reference line: anchors the first BUY fill at y=100 so
        // the viewer can instantly see gains (above) vs losses (below) from
        // the entry point. Only drawn when normalization is active.
        ...(isNormalized ? {
          markLine: {
            symbol: "none",
            silent: true,
            lineStyle: { color: c.axisLineColor, type: "dashed", width: 1 },
            label: {
              formatter: "Base 100",
              color: c.textColor,
              fontSize: 9,
              position: "insideEndTop",
            },
            data: [{ yAxis: 100 }],
          },
        } : {}),
        // Selected-period highlight band — a translucent vertical shade over
        // the OHLC trading days that fall inside the clicked risk-analytics
        // period. Green = gain period, red = loss period. Attached to the
        // MA60 line series (a `line` type reliably renders markArea, whereas
        // the custom OHLC series does not). Only rendered when a period is
        // selected AND the OHLC data covers dates in that period. No border —
        // just the color fill (the "base" shade), per request.
        ...(periodRange && periodShadeColor ? {
          markArea: {
            silent: true,
            z: 0,
            itemStyle: {
              color: withAlphaHex(periodShadeColor, 0.22),
            },
            label: {
              show: true,
              formatter: selectedPeriod.periodValue,
              color: periodShadeColor,
              fontSize: 10,
              fontWeight: 700,
              position: "insideTopLeft",
            },
            data: [[
              { xAxis: periodRange[0] },
              { xAxis: periodRange[1] },
            ]],
          },
        } : {}),
      },
      {
        name: "Trading Amt",
        type: "bar",
        data: amtData,
        yAxisIndex: 1,
        barWidth: "60%",
        itemStyle: {
          color: (params: { dataIndex: number }) => {
            const r = data.ohlc[params.dataIndex];
            if (!r || r.close == null || r.open == null) return c.splitLineColor;
            return r.close >= r.open
              ? "rgba(39, 174, 96, 0.25)"
              : "rgba(192, 57, 43, 0.25)";
          },
        },
        emphasis: { disabled: true },
        z: 1,
      },
      // Total P&L curve — cumulative realized + unrealized P&L in normalized
      // money, plotted on its own y-axis (index 2). Null before the first BUY
      // (no daily row). Sharpe ratios derived from Δtotal_pnl are shown in the
      // axis tooltip alongside the daily portfolio state.
      {
        name: "Total P&L",
        type: "line",
        data: totalPnlData,
        yAxisIndex: 2,
        smooth: false,
        symbol: "none",
        lineStyle: { color: PE_COLOR, width: 1.5 },
        emphasis: { disabled: true },
        z: 6,
      },
      // BUY markers — green dots at fill_price (dark green for top-3 confidence)
      {
        name: "BUY",
        type: "scatter",
        data: buyMarkers.map(([idx, price, d]) => ({
          value: [idx, price],
          decision: d,
          itemStyle: {
            color: topConfBuyNos.has(d.decision_no) ? DARK_GREEN : UP_COLOR,
            borderColor: "#fff",
            borderWidth: 1,
          },
        })),
        xAxisIndex: 0,
        yAxisIndex: 0,
        symbol: "circle",
        symbolSize: 10,
        z: 20,
        tooltip: { formatter: markerTooltipFormatter },
        emphasis: { disabled: true },
        label: {
          show: true,
          position: "bottom",
          formatter: "B",
          fontSize: 9,
          fontWeight: 700,
          color: UP_COLOR,
          distance: -3,
        },
      },
      // SELL markers — red dots at fill_price (dark red for top-3 losses)
      {
        name: "SELL",
        type: "scatter",
        data: sellMarkers.map(([idx, price, d]) => ({
          value: [idx, price],
          decision: d,
          itemStyle: {
            color: topLossSellNos.has(d.decision_no) ? DARK_RED : DOWN_COLOR,
            borderColor: "#fff",
            borderWidth: 1,
          },
        })),
        xAxisIndex: 0,
        yAxisIndex: 0,
        symbol: "circle",
        symbolSize: 10,
        z: 20,
        tooltip: { formatter: markerTooltipFormatter },
        emphasis: { disabled: true },
        label: {
          show: true,
          position: "top",
          formatter: "S",
          fontSize: 9,
          fontWeight: 700,
          color: DOWN_COLOR,
          distance: -3,
        },
      },
      // ---- Forecast sell dots (purple). When a scenario is selected, dots
      // follow the SELECTED scenario's curve via the forecast SELL decisions'
      // fill_price. When no scenario is selected, dots fall back to the mean
      // forecast close prices as a visual reference.
      ...(hasForecast && fcSellData.length > 0 ? [{
        name: "FC Sell",
        type: "scatter" as const,
        data: fcSellData,
        xAxisIndex: 0,
        yAxisIndex: 0,
        symbol: "circle",
        symbolSize: 6,
        z: 25,
        itemStyle: {
          color: fcMean,
          borderColor: "#fff",
          borderWidth: 0.5,
        },
        emphasis: { disabled: true },
        tooltip: {
          formatter: fcSellHasDecisions
            ? createFcSellTooltipFormatter({
                fcPurple: FC_PURPLE,
                upColor: UP_COLOR,
                downColor: DOWN_COLOR,
                textColor: c.textColor,
                isNormalized,
                fmtNum,
                stripMixPrefix,
              })
            : createFcSellFallbackTooltipFormatter(),
        },
      }] : []),
      // ---- Forecast overlay (10 mirror/flip/random + 1 mean). Appended so
      // they render above the OHLC/MA frame. ±2σ shade + 10 dashed price
      // curves + mean (price axis). P&L forecast curves are intentionally
      // omitted from the chart; P&L forecast data still appears in the
      // Risk Analytics panel and Decision Table.
      ...forecastPriceSeries,
    ],
  };
}
