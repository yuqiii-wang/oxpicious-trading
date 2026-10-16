/**
 * OhlcChart — the generic base daily-OHLC chart (any security whose
 * rows fit the shared StockDailyRow shape).
 *
 * Single source of truth for the daily OHLC plot. Used by:
 *   • StockPanel (Stock page) directly — wrapped in a ChartCard
 *     with a date-range slider and return badges.
 *   • CodeTrendChart (the per-security trend card — AI page, Recent
 *     Movements, live pages, the composition-pie expansion chart, …)
 *     as its inner chart.
 *
 * Renders OHLC bars (shared `ohlcSeries`) + MA5/MA20/MA60/MA120 (computed
 * client-side from close — the stock daily view does not carry precomputed
 * MA columns) + PE ratio on a twin axis (when available, with estimated PE
 * drawn as a faint series). Falls back to a close line when OHLC components
 * are sparse. OHLC + MAs are rebased to % change from the first valid close
 * in "percentage" mode (the default).
 *
 * Margin + liquidity overlays (mirror EtfMarginPanel):
 *   • RZ (融资 cash borrow) green fill UP from middle on a hidden twin axis.
 *   • RQ (融券 sec borrow) red fill DOWN from middle on the same hidden axis.
 *   • Trading-turnover bars (成交金额, 亿元) on a visible right axis, colored
 *     by price-up/down.
 *
 * Dividend events (利润分配/分红) from stats.stock_dividends are overlaid as
 * gold diamond markPoints on the ex-dividend date. The dividend amount is
 * shown in the axis tooltip when the user hovers the event day.
 *
 * Split (this directory) — this file is the React shell only:
 *   • types.ts        props + marker/span types + stable constants
 *   • chartOption.ts  pure base-option builder (the heavy memo)
 *   • triggerOverlay.ts       forecast trigger overlay payload builder
 *   • useVisibleRange.ts      onVisibleRangeChange dataZoom reporter hook
 *   • useFocusDate.ts         focusDateRequest dataZoom jump hook
 */
import { useEffect, useMemo, useRef, memo } from "react";
import EChart from "@/shared/charts/EChart";
import type { CodeTrendStreakInfo } from "@/shared/charts/CodeTrendTooltip";
import { useStore } from "@/store/filters";
import { breakArraysAtGaps } from "@/lib/series";
import type { ECharts, EChartsOption } from "echarts";
import { buildOhlcOption, MARKPOINT_HOST_SERIES_ID } from "./chartOption";
import { buildTriggerOverlay } from "./triggerOverlay";
import { useVisibleRangeReporter } from "./useVisibleRange";
import { useFocusDateJump } from "./useFocusDate";
import {
  NO_DIVIDENDS,
  NO_HIGHLIGHT_DATES,
  NO_HIGHLIGHT_SPANS,
  NO_TRADE_SIGNALS,
  TRIGGER_ACTION_SERIES_ID,
  TRIGGER_SERIES_ID,
} from "./types";
import type {
  OhlcForecastAction,
  OhlcChartProps,
  TriggerAnchor,
} from "./types";
import type { SignalMarkPointData } from "@/shared/charts/signalMarker";
import {
  emphasizeSignalMarker,
} from "@/shared/charts/signalMarker";

function OhlcChart({ rows, ohlcMode, height = 250, dividends = NO_DIVIDENDS, dataZoomStart, dataZoomEnd, onDateClick, regimeSpans, trendSpans, tradeSignals = NO_TRADE_SIGNALS, selectedSignalDate = null, showPe, highlightDates = NO_HIGHLIGHT_DATES, highlightSpans = NO_HIGHLIGHT_SPANS, highlightHorizonDays = 1, highlightAction = null, onHighlightSettled, onVisibleRangeChange, focusDateRequest, onChartReady }: OhlcChartProps) {
  const themeMode = useStore((s) => s.themeMode);

  // Chart x-axis dates (with gap-break inserts) — used by the onCanvasClick
  // handler to map a click index back to a date string. Mirrors the broken
  // dates computed inside the option useMemo.
  const chartDates = useMemo(
    () => breakArraysAtGaps(rows.map((r) => r.date), [rows.map(() => null)]).dates,
    [rows],
  );

  // Anchor stash for the trigger overlay effect (assigned inside the
  // option build) — see TriggerAnchor in types.ts.
  const triggerAnchorRef = useRef<TriggerAnchor | null>(null);

  // Trade-signal markPoint stash (assigned inside the option build) —
  // read by the selected-signal-date emphasis effect below.
  const markPointDataRef = useRef<SignalMarkPointData[] | null>(null);

  // Date → streak context of the trigger overlay's DARK spans, read by
  // the axis tooltip formatter at hover time (declared here so the
  // option closure can reference it; the overlay memo below refreshes
  // it on every highlight change — no option rebuild on a row click).
  const streakInfoByDateRef = useRef<Map<string, CodeTrendStreakInfo>>(new Map());

  // Date → the trigger overlay's forecast-action labels (the clicked
  // bucket's registered buy/sell), read by the axis tooltip formatter
  // the same way as the streak context above.
  const forecastActionByDateRef = useRef<Map<string, OhlcForecastAction>>(new Map());

  const option = useMemo<EChartsOption>(
    () => buildOhlcOption({
      rows,
      ohlcMode,
      themeMode,
      dataZoomStart,
      dataZoomEnd,
      dividends,
      regimeSpans,
      trendSpans,
      tradeSignals,
      showPe,
      triggerAnchorRef,
      markPointDataRef,
      streakInfoByDateRef,
      forecastActionByDateRef,
    }),
    [rows, themeMode, ohlcMode, dividends, dataZoomStart, dataZoomEnd, regimeSpans, trendSpans, tradeSignals, showPe],
  );

  // ---- Forecast trigger-day overlay (imperative incremental update) -----
  // The overlay payload for the placeholder series inside the base option:
  // pinpoint circles on the fixed signal rail at each merged signal's MID
  // day, the union-merged light forward-window + dark streak-period
  // markArea spans, and — when the clicked bucket registered as a signal
  // strategy (highlightAction) — the buy/sell triangle labels at each mid
  // day (the tradeSignals markPoint style). Built LIGHT (no chart
  // rebuild) from the anchor stashed by the base option build; keyed on
  // `option` so it re-reads a refreshed anchor after every base rebuild.
  const triggerOverlay = useMemo(
    () => buildTriggerOverlay({
      anchor: triggerAnchorRef.current,
      highlightDates,
      highlightSpans,
      highlightHorizonDays,
      highlightAction,
      streakInfoByDateRef,
      forecastActionByDateRef,
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [highlightDates, highlightSpans, highlightHorizonDays, highlightAction, option],
  );

  // Apply the overlay with a MERGE setOption on the placeholder series
  // only — a row click costs a data swap on one scatter series instead
  // of a full notMerge rebuild of every candle. `option` in the overlay
  // deps re-applies it after a base rebuild (notMerge wiped it).
  // An empty→empty transition skips the setOption entirely (the no-op
  // merge still costs ~80 ms on a 1700-category chart); either way the
  // caller is told the overlay has settled so it can unfreeze the UI.
  const chartRef = useRef<ECharts | null>(null);
  const overlayEmptyRef = useRef(true);

  useVisibleRangeReporter(chartRef, chartDates, onVisibleRangeChange);
  useFocusDateJump(chartRef, chartDates, focusDateRequest);

  useEffect(() => {
    const chart = chartRef.current;
    const empty =
      triggerOverlay.points.length === 0 &&
      triggerOverlay.actionPoints.length === 0 &&
      !triggerOverlay.markArea;
    try {
      if (chart && !(empty && overlayEmptyRef.current)) {
        chart.setOption(
          {
            series: [
              {
                id: TRIGGER_SERIES_ID,
                data: triggerOverlay.points,
                markArea: triggerOverlay.markArea ?? { silent: true, data: [] },
              },
              {
                id: TRIGGER_ACTION_SERIES_ID,
                data: triggerOverlay.actionPoints,
              },
            ],
          } as unknown as EChartsOption,
          { notMerge: false },
        );
      }
    } finally {
      // Always settle — the caller's freeze must never stick, even if
      // the chart threw (disposed mid-update, HMR, ...).
      overlayEmptyRef.current = empty;
      onHighlightSettled?.();
    }
  }, [triggerOverlay, onHighlightSettled]);

  // ---- Selected-signal-date emphasis (markPoint-only merge) ------------
  // Enlarge + glow the selected date's trade-signal marker(s) — the
  // Recent Movements marker-click feedback. A markPoint-only merge on
  // the host series: a click costs one small setOption instead of a
  // full notMerge rebuild of the 1700-candle chart. Re-applied after
  // every base rebuild (`option` dep) since notMerge wiped the
  // emphasis; when nothing is selected the plain built data stands.
  useEffect(() => {
    const chart = chartRef.current;
    const data = markPointDataRef.current;
    if (!chart || !data || data.length === 0) return;
    const merged =
      selectedSignalDate == null
        ? data
        : data.map((mp) =>
            mp.name !== "Dividend" && mp.coord[0] === selectedSignalDate
              ? emphasizeSignalMarker(mp)
              : mp,
          );
    chart.setOption(
      { series: [{ id: MARKPOINT_HOST_SERIES_ID, markPoint: { data: merged, label: { show: false } } }] } as unknown as EChartsOption,
      { notMerge: false },
    );
  }, [selectedSignalDate, option]);

  return (
    <EChart
      option={option}
      height={height}
      onReady={(c) => {
        chartRef.current = c;
        onChartReady?.(c);
      }}
      onCanvasClick={onDateClick ? (idx) => {
        const date = chartDates[idx];
        if (date) onDateClick(date);
      } : undefined}
    />
  );
}

// Identity-stable props (stable empty defaults upstream + memoized
// chartOptions in callers) let unrelated parent re-renders — chip
// mounts, spinner flips, row selection — skip this subtree entirely.
export default memo(OhlcChart);
