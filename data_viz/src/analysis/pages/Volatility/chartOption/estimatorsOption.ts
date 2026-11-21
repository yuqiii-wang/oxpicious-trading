/**
 * Estimator comparison option — the 20-day window's five estimators
 * side by side (close-to-close HV vs Parkinson / Garman-Klass /
 * Rogers-Satchell / Yang-Zhang), percent annualized. The range
 * estimators use the same intraday information differently — the
 * spread between the lines is the estimator-efficiency story (CC
 * least efficient, YZ the most complete daily-OHLC form).
 *
 * NO dataZoom slider of its own: the merged vol box shows the time
 * slider on its FIRST chart only (chartOption/volLevelsOption.ts), and
 * this chart keeps an inside-only zoom (the slider component hidden)
 * so the echarts.connect group still drives its viewport. The raw
 * close price rides along on its own right-hand axis for context.
 */
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import { baseChartOption, commonTooltip } from "@/shared/charts/base-chart";
import {
  axisColors,
  commonDataZoom,
  commonGrid,
  MUTED_PALETTE,
} from "@/theme/chart-palette";
import type { VolatilityRow } from "@shared/types";
import { buildCloseSeries, closeAxis } from "./closeOverlay";

interface SeriesSpec {
  key: "hv_20" | "parkinson_vol_20" | "garman_klass_vol_20"
     | "rogers_satchell_vol_20" | "yang_zhang_vol_20";
  label: string;
  color: string;
  dashed?: boolean;
}

const SERIES: SeriesSpec[] = [
  { key: "hv_20", label: "Close-to-close 20d", color: MUTED_PALETTE[7] },
  // Blue is reserved for the Close overlay — Parkinson rides green.
  { key: "parkinson_vol_20", label: "Parkinson", color: MUTED_PALETTE[2] },
  { key: "garman_klass_vol_20", label: "Garman-Klass", color: MUTED_PALETTE[1] },
  { key: "rogers_satchell_vol_20", label: "Rogers-Satchell", color: MUTED_PALETTE[3] },
  { key: "yang_zhang_vol_20", label: "Yang-Zhang", color: MUTED_PALETTE[4], dashed: true },
];

export function buildEstimatorsOption(
  rows: VolatilityRow[],
  mode: ThemeMode,
): EChartsOption {
  const c = axisColors(mode);
  // Close first — the price context line behind the estimator curves.
  const series = [
    buildCloseSeries(rows, 1),
    ...SERIES.map((s) => ({
    type: "line" as const,
    name: s.label,
    data: rows.map((r) => r[s.key]),
    showSymbol: false,
    lineStyle: {
      width: s.key === "hv_20" ? 2 : 1.3,
      type: s.dashed ? ("dashed" as const) : ("solid" as const),
      color: s.color,
    },
    itemStyle: { color: s.color },
    emphasis: { focus: "series" as const },
  })),
  ];

  return baseChartOption(mode, {
    grid: commonGrid(),
    dataZoom: commonDataZoom({ show: false }),
    tooltip: commonTooltip(mode, {
      valueFormatter: (v) => (v == null ? "—" : `${Number(v).toFixed(2)}%`),
    }),
    legend: { data: ["Close", ...SERIES.map((s) => s.label)] },
    xAxis: {
      type: "category",
      data: rows.map((r) => r.date),
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: { color: c.textColor, fontSize: 9 },
    },
    yAxis: [
      {
        type: "value",
        name: "annualized vol (%)",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLabel: { color: c.textColor, fontSize: 9, formatter: "{value}%" },
        splitLine: { lineStyle: { color: c.splitLineColor } },
      },
      closeAxis(mode),
    ],
    series,
  });
}
