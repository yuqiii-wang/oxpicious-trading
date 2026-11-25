/**
 * volSignalCurveOption — the volatility FAMILY's chart on the
 * Short-Term Tech signals surface: the Commons Volatility page's TOP
 * chart (the historical-vol levels plot — close overlay + HV 20d +
 * EWMA λ=0.94, percent annualized on the left axis, the raw close on
 * its own right-hand price axis) rebuilt for the signal context, with
 * the family's signal events as the shared buy ▲ / sell ▼ markers
 * anchored ON THE CLOSE CURVE (user-directed for the crowdedness
 * family's chart, the same treatment: the actions read on the traded
 * curve; the vol curves stay the trigger evidence beside them). The
 * plotted axes are the family's own trigger axes (hv_20 / ewma_vol —
 * the volLevelsOption's hv_5/60/252 window context stays on the
 * Commons page).
 *
 * Data: /api/analysis/volatility/series (fetchVolatilitySeries — the
 * same call the Commons page's code group makes; one row per
 * (code, date) with the close joined at read time).
 *
 * The close line + price axis build here as root-`echarts` literals
 * (NOT the Commons page's closeOverlay helper — that module types
 * through `echarts/charts` LineSeriesOption, a type instantiation
 * structurally incompatible with the root SeriesOption the shared
 * signalMarker layer uses; the crowdednessCurveOption precedent:
 * signal-context builders keep every series a root-typed literal).
 */
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import { baseChartOption, commonTooltip } from "@/shared/charts/base-chart";
import { fmtDate } from "@/shared/datetime/display";
import {
  axisColors,
  commonDataZoom,
  commonGrid,
  MA20_COLOR,
  MUTED_PALETTE,
  PALETTE_HI,
} from "@/theme/chart-palette";
import type { VolatilityRow } from "@shared/types";
import {
  signalEventScatterSeries,
  type SignalEventPoint,
} from "./signalMarker";

export function buildVolSignalCurveOption(
  rows: VolatilityRow[],
  mode: ThemeMode,
  opts?: {
    signalEvents?: readonly SignalEventPoint[];
    selectedDate?: string | null;
  },
): EChartsOption {
  const c = axisColors(mode);
  const dates = rows.map((r) => r.date);
  // the close line drawn FIRST so the vol curves paint on top of it
  const series: EChartsOption["series"] = [
    {
      name: "Close",
      type: "line",
      yAxisIndex: 1,
      data: rows.map((r) => r.close),
      showSymbol: false,
      lineStyle: { width: 1.5, color: PALETTE_HI, opacity: 0.9 },
      itemStyle: { color: PALETTE_HI },
      emphasis: { focus: "series" },
      tooltip: { show: false },
    },
    {
      name: "HV 20d",
      type: "line",
      data: rows.map((r) => r.hv_20),
      showSymbol: false,
      lineStyle: { width: 1.4, color: MA20_COLOR },
      itemStyle: { color: MA20_COLOR },
      emphasis: { focus: "series" },
    },
    {
      name: "EWMA (λ=0.94)",
      type: "line",
      data: rows.map((r) => r.ewma_vol),
      showSymbol: false,
      lineStyle: { width: 2, type: "dashed", color: MUTED_PALETTE[2] },
      itemStyle: { color: MUTED_PALETTE[2] },
      emphasis: { focus: "series" },
    },
  ];

  // the family's events as the shared marker layer's points, anchored
  // on the close curve (the right-hand price axis)
  const events = opts?.signalEvents ?? [];
  let markerNames: string[] = [];
  if (events.length > 0) {
    const yByDate = new Map<string, number | null | undefined>();
    rows.forEach((r) => {
      yByDate.set(r.date, r.close);
    });
    const markerSeries = signalEventScatterSeries(events, yByDate, {
      selectedDate: opts?.selectedDate ?? null,
      axisIndices: { xAxisIndex: 0, yAxisIndex: 1 },
      z: 12,
    });
    series.push(...markerSeries);
    markerNames = markerSeries.map((s) => String(s.name));
  }

  return baseChartOption(mode, {
    grid: commonGrid({ bottom: 50 }),
    dataZoom: commonDataZoom(),
    tooltip: commonTooltip(mode, {
      valueFormatter: (v) => (v == null ? "—" : `${Number(v).toFixed(2)}`),
    }),
    legend: {
      data: ["Close", "HV 20d", "EWMA (λ=0.94)", ...markerNames],
    },
    xAxis: {
      type: "category",
      data: dates,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: { color: c.textColor, fontSize: 9, formatter: (v: string) => fmtDate(v) },
    },
    yAxis: [
      {
        type: "value",
        name: "annualized vol (%)",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLabel: { color: c.textColor, fontSize: 9, formatter: "{value}%" },
        splitLine: { lineStyle: { color: c.splitLineColor } },
      },
      // the right-hand price axis (the closeOverlay closeAxis shape —
      // root-typed here; see the module doc)
      {
        type: "value",
        position: "right",
        scale: true,
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { show: false },
        axisLabel: { color: c.textColor, fontSize: 9 },
      },
    ],
    series,
  });
}
