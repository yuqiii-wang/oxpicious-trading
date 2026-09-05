/**
 * ECharts option builder for the PE & Dividend band-break streak chart —
 * the shared streakBands rendering (light window zones + darker per-streak
 * break bands) applied to a single valuation metric series (pe_ma20 /
 * dividend_yield).
 *
 * The guide's final approach, ported 1:1: both layers are drawn against
 * the ONE STATIC band edge of the anchor window (the trailing `period`
 * metric observations ending at the anchor date — latest row by default;
 * clicking the chart or a streak table row re-anchors), and the break
 * streaks are detected CLIENT-SIDE against that same edge so a shaded
 * stretch can only ever cover metric values the chart actually shows
 * inside the drawn zone. The DB streak rows (analysis.pe_and_dividend_pct_streaks,
 * tested against each month's OWN moving band) drive the table and select
 * the anchor; they are deliberately not used for shading.
 *
 * A single-series metric participates in the SHARED streak style through
 * the same code path as the MaSpread price charts by folding the value
 * into both the high and low legs (highs = lows = value) — percentile
 * windows and the close-based breakout then reduce exactly to the
 * single-series semantics, and the markArea geometry + colors come from
 * the one shared module (@/shared/charts/streakBands), so the two pages'
 * purple/yellow styling cannot drift.
 */
import type { EChartsOption } from "echarts";
import {
  STREAK_HIGH_ACCENT_COLOR,
  STREAK_HIGH_SHADE_COLOR,
  STREAK_LOW_ACCENT_COLOR,
  STREAK_LOW_SHADE_COLOR,
  computeBreakStreaks,
  computeStreakBandWindow,
  longStreakToMarkArea,
  streakShadeMarkAreas,
  type LongBandStreak,
  type StreakBandWindow,
  type StreakMarkAreaDatum,
} from "@/shared/charts/streakBands";
import { IV_BLUE, axisColors, commonDataZoom, commonGrid, commonLegend } from "@/theme/chart-palette";
import type { ThemeMode } from "@/store/filters";
import type { PeAndDividendStreak, PeAndDividendStreakMetric } from "@shared/types";

/** One observation row of the audited metric (non-NULL values only). */
export interface MetricObsRow {
  date: string;
  value: number;
}

export interface StreakChartInput {
  /** Non-NULL observations of the metric (ascending by date). */
  obs: MetricObsRow[];
  metric: PeAndDividendStreakMetric;
  period: number;
  pct: number;
  /** Anchor index into `obs` (null = the latest row). */
  anchorIdx: number | null;
  /** The clicked DB streak (table row) — anchors nothing itself; its side
   *  + end date pick the DETECTED streak to emphasize with a border. */
  selectedStreak: PeAndDividendStreak | null;
  themeMode: ThemeMode;
}

export interface StreakChartBuild {
  option: EChartsOption;
  /** The anchor window actually drawn (null when too few observations). */
  win: StreakBandWindow | null;
  /** Client-detected break streaks inside the anchor window. */
  streaks: { high: LongBandStreak[]; low: LongBandStreak[] } | null;
  /** The detected streak emphasized as the clicked row's counterpart
   *  (same side, span containing the clicked streak's end date). */
  emphasized: LongBandStreak | null;
}

/** Fold a metric observation into the streakBands row shape — the single
 *  series serves as both legs (high = low = value) and as the close. */
function toBandRows(obs: MetricObsRow[]): Array<{
  date: string;
  short_value: number;
  high: number;
  low: number;
}> {
  return obs.map((r) => ({
    date: r.date,
    short_value: r.value,
    high: r.value,
    low: r.value,
  }));
}

/** Build the option. Returns win/streaks null when the window cannot be
 *  computed (fewer than 1 usable observation row). */
export function buildStreakChartOption(
  input: StreakChartInput,
): StreakChartBuild {
  const { obs, metric, period, pct, anchorIdx, selectedStreak, themeMode } = input;
  const c = axisColors(themeMode);
  const dates = obs.map((r) => r.date);
  const values = obs.map((r) => r.value);
  const lineColor = IV_BLUE;
  const fmtVal = (v: number): string =>
    metric === "dividend_yield" ? `${(v * 100).toFixed(2)}%` : String(Math.round(v * 100) / 100);

  const rows = toBandRows(obs);
  const win = rows.length > 0
    ? computeStreakBandWindow(rows, period, pct, anchorIdx)
    : null;
  const streaks = win != null ? computeBreakStreaks(rows, win) : null;

  // Emphasized detected streak: same side as the clicked DB streak and a
  // span containing its end date (the anchor) — the static-edge
  // counterpart of the archive row.
  let emphasized: LongBandStreak | null = null;
  if (win != null && streaks != null && selectedStreak != null) {
    const side = selectedStreak.side === "high" ? streaks.high : streaks.low;
    emphasized =
      side.find(
        (s) => s.startDate <= selectedStreak.endDate && selectedStreak.endDate <= s.endDate,
      ) ?? null;
  }

  // The canonical per-side shading assembly from the shared module: light
  // anchor-window zone + one darker band per detected streak.
  const markAreas =
    win != null && streaks != null ? streakShadeMarkAreas(win, streaks) : null;

  const series: EChartsOption["series"] = [
    {
      type: "line",
      name: metric === "pe_ma20" ? "PE MA20" : "Dividend yield",
      data: values,
      symbol: "none",
      lineStyle: { color: lineColor, width: 1.4 },
      z: 5,
    },
  ];

  if (win != null && streaks != null) {
    // Emphasized band first (bordered, drawn under the plain bands so the
    // border stays visible) — a separate silent series keeps the per-side
    // legend toggles independent of the selection.
    if (emphasized != null) {
      const accent =
        emphasized.side === "high" ? STREAK_HIGH_ACCENT_COLOR : STREAK_LOW_ACCENT_COLOR;
      const shade =
        emphasized.side === "high" ? STREAK_HIGH_SHADE_COLOR : STREAK_LOW_SHADE_COLOR;
      const band = longStreakToMarkArea(emphasized, win);
      // Bordered band: same geometry as longStreakToMarkArea, with the
      // accent border the legend-side bands don't carry (per-datum
      // itemStyle is supported by markArea at runtime; the shared tuple
      // type only spells out the plain color corner).
      const emData = [
        {
          xAxis: band[0].xAxis,
          yAxis: band[0].yAxis,
          itemStyle: { color: shade, borderColor: accent, borderWidth: 1.4 },
        },
        { xAxis: band[1].xAxis, yAxis: band[1].yAxis },
      ] as unknown as StreakMarkAreaDatum;
      series.push({
        type: "scatter",
        name: "Selected streak",
        data: [null],
        symbol: "rect",
        symbolSize: [10, 8],
        itemStyle: { color: accent, opacity: 0.45, borderColor: shade },
        z: 2,
        markArea: {
          silent: true as const,
          itemStyle: { borderWidth: 0 },
          data: [emData],
        },
      });
    }
    const sides: Array<{
      hasData: boolean;
      name: string;
      accent: string;
      shade: string;
      data: StreakMarkAreaDatum[];
    }> = [
      {
        hasData: streaks.high.length > 0,
        name: "High streaks",
        accent: STREAK_HIGH_ACCENT_COLOR,
        shade: STREAK_HIGH_SHADE_COLOR,
        data: markAreas!.high,
      },
      {
        hasData: streaks.low.length > 0,
        name: "Low streaks",
        accent: STREAK_LOW_ACCENT_COLOR,
        shade: STREAK_LOW_SHADE_COLOR,
        data: markAreas!.low,
      },
    ];
    for (const s of sides) {
      if (!s.hasData) continue;
      series.push({
        type: "scatter",
        name: s.name,
        data: [null],
        symbol: "rect",
        symbolSize: [10, 8],
        itemStyle: { color: s.accent, opacity: 0.45, borderColor: s.shade },
        z: 1,
        markArea: {
          silent: true as const,
          itemStyle: { borderWidth: 0 },
          data: s.data,
        },
      });
    }
  }

  const option: EChartsOption = {
    animation: false,
    backgroundColor: "transparent",
    legend: commonLegend(themeMode),
    grid: commonGrid({ bottom: 64 }),
    tooltip: {
      trigger: "axis",
      backgroundColor: c.tooltipBg,
      textStyle: { color: c.textColor, fontSize: 11 },
      valueFormatter: (v) => (typeof v === "number" ? fmtVal(v) : "—"),
    },
    xAxis: {
      type: "category",
      data: dates,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: { color: c.textColor, fontSize: 9 },
      axisTick: { show: false },
    },
    yAxis: {
      type: "value",
      scale: true,
      axisLine: { show: false },
      splitLine: { lineStyle: { color: c.splitLineColor } },
      axisLabel: {
        color: c.textColor,
        fontSize: 9,
        formatter: (v: number) =>
          metric === "dividend_yield" ? `${(v * 100).toFixed(1)}%` : fmtNumCompact(v),
      },
    },
    dataZoom: commonDataZoom(),
    series,
  };

  return { option, win, streaks, emphasized };
}

/** Compact axis number (≤2 decimals, trailing zeros stripped). */
function fmtNumCompact(v: number): string {
  const s = v >= 100 ? v.toFixed(0) : v.toFixed(2);
  return s.replace(/\.?0+$/, "");
}
