/**
 * spot-skew ECharts option builders — the Smile Chronology panel.
 *
 * Two views (see docs/options_vol_smile_study.md for the semantics):
 *
 *   • Today   — the selected date's skew TERM STRUCTURE: RR at the chosen
 *               wing per expiry group (sign-colored bars) + each group's
 *               ATM IV. The "just today's skew" snapshot in time.
 *   • History — 3 stacked grids sharing one time axis + dataZoom:
 *               spot (price), ATM IV (smile LEVEL — the VIX analog) and
 *               the RR skew (smile TILT — the SKEW-index analog) with a
 *               front-expiry series, an all-expiry mean and a ±2σ(20d)
 *               extreme-skew envelope around its MA20.
 *
 * Style follows the repo's shared palette/option helpers (chart-palette.ts)
 * — no per-chart CSS, colors resolve from colors.css tokens.
 */
import {
  baseChartOption,
  commonTooltip,
} from "@/shared/charts/base-chart";
import {
  ATM_GRAY,
  DOWN_COLOR,
  FUTURES_SPOT,
  IV_BLUE,
  MUTED_PALETTE,
  SPOT_COLOR,
  UP_COLOR,
  axisColors,
  commonDataZoom,
  commonLegend,
} from "@/theme/chart-palette";
import { fmtNum } from "@/lib/series";
import type { ThemeMode } from "@/store/filters";
import type {
  SkewDayPoint,
  SkewDelta,
  SkewExpirySlice,
} from "./spotSkewData";
import type { EChartsOption } from "echarts";

/** Translucent band fill (BOLL_BAND_FILL hue, use-site opacity). */
const BAND_FILL = "rgba(52,152,219,0.10)";

const RR_LABEL: Record<SkewDelta, string> = {
  25: "RR25",
  10: "RR10",
};

/** Colored series dot inside tooltip rows (repo ColoredDot convention). */
function tooltipDot(color: string): string {
  return (
    `<span style="display:inline-block;width:9px;height:9px;border-radius:50%;` +
    `background:${color};margin-right:5px;border:1px solid rgba(0,0,0,0.2);` +
    `vertical-align:middle;"></span>`
  );
}

function tooltipHeader(c: { splitLineColor: string }, text: string): string {
  return (
    `<div style="font-weight:600;border-bottom:1px solid ${c.splitLineColor};` +
    `margin-bottom:4px;padding-bottom:2px;">${text}</div>`
  );
}

function tooltipRow(
  color: string | null,
  label: string,
  value: number | null,
  unit: string,
  valueColor?: string,
): string {
  const dot = color != null ? tooltipDot(color) : "";
  const v = value != null ? fmtNum(value, 2) : "—";
  const styled =
    valueColor != null && value != null
      ? `<b style="color:${valueColor}">${v}${unit}</b>`
      : `<b>${v}${unit}</b>`;
  return `<div style="line-height:1.7;">${dot}${label}: ${styled}</div>`;
}

/**
 * Today view — RR term structure by expiry for one date.
 * Bars: RR (vol pts), green = call wing richer, red = put wing richer.
 * Line: ATM IV per expiry (right axis).
 */
export function buildTodayOption(
  slices: SkewExpirySlice[],
  date: string,
  delta: SkewDelta,
  themeMode: ThemeMode,
): EChartsOption {
  const c = axisColors(themeMode);
  const rr = RR_LABEL[delta];

  return baseChartOption(themeMode, {
    title: {
      text: `Skew Term Structure · ${date} · ${rr} by expiry · vol pts`,
      left: 4,
      top: 0,
      textStyle: { color: c.textColor, fontSize: 11, fontWeight: 600 },
    },
    tooltip: commonTooltip(themeMode, {
      axisPointer: { type: "shadow" },
      borderWidth: 1,
      padding: [6, 10],
      textStyle: { color: c.textColor, fontSize: 12 },
      formatter: (params: unknown) => {
        const list = params as Array<{
          dataIndex: number;
          seriesName: string;
          value: number | null;
        }>;
        const i = list[0]?.dataIndex ?? 0;
        const s = slices[i];
        if (!s) return "";
        const rrColor = s.rr != null && s.rr >= 0 ? UP_COLOR : DOWN_COLOR;
        return (
          tooltipHeader(
            c,
            `Expiry ${s.label} (${s.expiryDate}, DTE ${s.dte})`,
          ) +
          tooltipRow(rrColor, `${rr} (C−P)`, s.rr, " vp", rrColor) +
          tooltipRow(IV_BLUE, "ATM IV", s.atmIv, " %")
        );
      },
    }),
    legend: commonLegend(themeMode, { data: [`${rr} (C−P)`, "ATM IV"] }),
    grid: { left: 56, right: 56, top: 40, bottom: 44 },
    xAxis: {
      type: "category",
      data: slices.map((s) => s.label),
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: { color: c.textColor, fontSize: 9 },
      axisTick: { alignWithLabel: true },
    },
    yAxis: [
      {
        type: "value",
        name: `${rr} vol pts`,
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { show: false },
        axisLabel: { color: c.textColor, fontSize: 9 },
        splitLine: { lineStyle: { color: c.splitLineColor } },
      },
      {
        type: "value",
        name: "ATM IV %",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { show: false },
        axisLabel: { color: c.textColor, fontSize: 9 },
        splitLine: { show: false },
        scale: true,
      },
    ],
    series: [
      {
        name: `${rr} (C−P)`,
        type: "bar",
        barMaxWidth: 26,
        data: slices.map((s) =>
          s.rr != null
            ? {
                value: s.rr,
                itemStyle: { color: s.rr >= 0 ? UP_COLOR : DOWN_COLOR },
              }
            : null,
        ),
        markLine: {
          silent: true,
          symbol: "none",
          label: { show: false },
          lineStyle: { color: ATM_GRAY, type: "dashed", width: 1 },
          data: [{ yAxis: 0 }],
        },
      },
      {
        name: "ATM IV",
        type: "line",
        yAxisIndex: 1,
        symbol: "circle",
        symbolSize: 5,
        itemStyle: { color: IV_BLUE },
        lineStyle: { color: IV_BLUE, width: 1.5 },
        data: slices.map((s) => s.atmIv),
      },
    ],
  });
}

/**
 * History view — spot / level (ATM IV + model-free vol index) / RR (tilt)
 * over time, 3 grids on one shared time axis with a spanning dataZoom,
 * crosshair link and a markLine at the selected snapshot date on every
 * grid. `volIndex` (optional, aligned to `points` by date) overlays the
 * 30-day model-free VIX-style index on the LEVEL grid.
 */
export function buildHistoryOption(
  points: SkewDayPoint[],
  selectedDate: string,
  delta: SkewDelta,
  themeMode: ThemeMode,
  volIndex?: (number | null)[],
): EChartsOption {
  const c = axisColors(themeMode);
  const rr = RR_LABEL[delta];
  const dates = points.map((p) => p.date);
  const hasSel = selectedDate !== "" && dates.includes(selectedDate);
  const selMark = hasSel
    ? {
        silent: true,
        symbol: "none",
        label: { show: false },
        lineStyle: { color: ATM_GRAY, type: "dashed" as const, width: 1 },
        data: [{ xAxis: selectedDate }],
      }
    : undefined;

  const mkXAxis = (showLabel: boolean, gridIndex: number) => ({
    type: "category" as const,
    gridIndex,
    data: dates,
    axisLine: { lineStyle: { color: c.axisLineColor } },
    axisTick: { show: false },
    axisLabel: { show: showLabel, color: c.textColor, fontSize: 9 },
  });

  const spotSeries = points.map((p) => p.spot);
  const atmSeries = points.map((p) => p.atmIvFront);
  const rrFrontSeries = points.map((p) => p.rrFront);
  const rrMeanSeries = points.map((p) => p.rrMean);
  // Stacked-area band trick: transparent base = lower bound, stacked
  // delta fills to the upper bound (nulls stay honest gaps).
  const bandLow = points.map((p) => p.rrBandLow);
  const bandSpan = points.map((p) =>
    p.rrBandLow != null && p.rrBandHigh != null
      ? p.rrBandHigh - p.rrBandLow
      : null,
  );

  const option = baseChartOption(themeMode, {
    title: {
      text: `Smile Chronology · Spot vs Level (ATM IV) vs Skew (${rr}) · vol pts`,
      left: 4,
      top: 0,
      textStyle: { color: c.textColor, fontSize: 11, fontWeight: 600 },
    },
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    tooltip: commonTooltip(themeMode, {
      axisPointer: { type: "line", lineStyle: { color: "#999999" } },
      borderWidth: 1,
      padding: [6, 10],
      textStyle: { color: c.textColor, fontSize: 12 },
      formatter: (params: unknown) => {
        const list = params as Array<{
          dataIndex: number;
          seriesName: string;
          value: number | null;
          color?: string;
        }>;
        const idx = list[0]?.dataIndex ?? -1;
        const p = idx >= 0 ? points[idx] : undefined;
        if (!p) return "";

        // Units per series; RR rows color their value green/red like the
        // bars (positive = call wing richer, negative = put wing richer).
        const unitOf: Record<string, string> = {
          Spot: "",
          "ATM IV": " %",
          "MF Vol 30d": " %",
          [`${rr} front`]: " vp",
          [`${rr} mean`]: " vp",
        };
        const rows = list
          .filter((item) => item.value != null)
          .map((item) => {
            const v = item.value as number;
            const valueColor =
              item.seriesName.startsWith(rr) ? (v >= 0 ? UP_COLOR : DOWN_COLOR) : undefined;
            return tooltipRow(
              item.color ?? null,
              item.seriesName,
              item.value,
              unitOf[item.seriesName] ?? "",
              valueColor,
            );
          });
        if (p.rrMa20 != null) {
          rows.push(tooltipRow(null, `${rr} MA20`, p.rrMa20, " vp"));
        }
        return tooltipHeader(c, p.date) + rows.join("");
      },
    }),
    legend: commonLegend(themeMode, {
      data: [
        "Spot",
        "ATM IV",
        ...(volIndex != null ? ["MF Vol 30d"] : []),
        `${rr} front`,
        `${rr} mean`,
        "±2σ (20d)",
      ],
    }),
    dataZoom: commonDataZoom({ xAxisIndex: [0, 1, 2] }),
    // Multi-grid stacked layout (3 grids, one per sub-plot).
    grid: [
      { left: 56, right: 56, top: 30, height: 128 },
      { left: 56, right: 56, top: 190, height: 128 },
      { left: 56, right: 56, top: 350, height: 150 },
    ],
    xAxis: [mkXAxis(false, 0), mkXAxis(false, 1), mkXAxis(true, 2)],
    yAxis: [
      {
        gridIndex: 0,
        name: "Spot",
        scale: true,
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { show: false },
        axisLabel: { color: c.textColor, fontSize: 9 },
        splitLine: { lineStyle: { color: c.splitLineColor } },
      },
      {
        gridIndex: 1,
        name: "ATM IV %",
        scale: true,
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { show: false },
        axisLabel: { color: c.textColor, fontSize: 9 },
        splitLine: { lineStyle: { color: c.splitLineColor } },
      },
      {
        gridIndex: 2,
        name: `${rr} vp`,
        scale: true,
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { show: false },
        axisLabel: { color: c.textColor, fontSize: 9 },
        splitLine: { lineStyle: { color: c.splitLineColor } },
      },
    ],
    series: [
      {
        name: "Spot",
        type: "line",
        xAxisIndex: 0,
        yAxisIndex: 0,
        symbol: "none",
        lineStyle: { color: SPOT_COLOR, width: 1.4 },
        areaStyle: { color: "rgba(44,62,80,0.06)" },
        data: spotSeries,
        ...(selMark ? { markLine: selMark } : {}),
      },
      {
        name: "ATM IV",
        type: "line",
        xAxisIndex: 1,
        yAxisIndex: 1,
        symbol: "none",
        lineStyle: { color: IV_BLUE, width: 1.4 },
        data: atmSeries,
        ...(selMark ? { markLine: selMark } : {}),
      },
      ...(volIndex != null
        ? [
            {
              // 30d model-free VIX-style index (analysis.options_vol_index)
              // on the LEVEL grid — the smile level CBOE's VIX measures.
              name: "MF Vol 30d",
              type: "line" as const,
              xAxisIndex: 1,
              yAxisIndex: 1,
              symbol: "none",
              connectNulls: false,
              lineStyle: { color: MUTED_PALETTE[4], width: 1.4 },
              itemStyle: { color: MUTED_PALETTE[4] },
              data: volIndex,
            },
          ]
        : []),
      {
        name: "±2σ (20d)",
        type: "line",
        stack: "rrBand",
        xAxisIndex: 2,
        yAxisIndex: 2,
        symbol: "none",
        silent: true,
        lineStyle: { opacity: 0 },
        tooltip: { show: false },
        data: bandLow,
      },
      {
        // Same name as the base series → one legend item toggles both
        // (the band is base + stacked-fill; they must hide together).
        name: "±2σ (20d)",
        type: "line",
        stack: "rrBand",
        xAxisIndex: 2,
        yAxisIndex: 2,
        symbol: "none",
        silent: true,
        lineStyle: { opacity: 0 },
        tooltip: { show: false },
        areaStyle: { color: BAND_FILL },
        data: bandSpan,
        ...(selMark ? { markLine: selMark } : {}),
      },
      {
        name: `${rr} mean`,
        type: "line",
        xAxisIndex: 2,
        yAxisIndex: 2,
        symbol: "none",
        lineStyle: { color: ATM_GRAY, width: 1.1, type: "dashed" },
        data: rrMeanSeries,
      },
      {
        name: `${rr} front`,
        type: "line",
        xAxisIndex: 2,
        yAxisIndex: 2,
        symbol: "none",
        connectNulls: false,
        lineStyle: { color: FUTURES_SPOT, width: 1.8 },
        data: rrFrontSeries,
        markLine: {
          silent: true,
          symbol: "none",
          label: { show: false },
          lineStyle: { color: ATM_GRAY, type: "dashed", width: 1 },
          data: [{ yAxis: 0 }],
        },
      },
    ],
  });
  return option;
}
