/**
 * Overlap-heatmap option builder — the FIRST heatmap in the repo: an
 * ECharts `type: "heatmap"` over two category axes (rows = base family A,
 * columns = other family B) + a continuous visualMap. The cell color
 * encodes the active metric (overlap % / lift / Jaccard); the tooltip
 * always carries the full stat line. Follows the ui-charts contract:
 * `baseChartOption` preamble, theme via the `mode` argument, colors from
 * `@/theme/chart-palette` CSS-var tokens (no hard-coded hex).
 */
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import type { SignalOverlapAuditRow } from "@shared/types";
import { baseChartOption, commonTooltip } from "@/shared/charts/base-chart";
import {
  axisColors,
  commonGrid,
  IV_BLUE,
  MA20_COLOR,
  NEUTRAL_FILL,
  PALETTE_HI,
} from "@/theme/chart-palette";

/** The heatmap's color metric — the page toolbar's toggle. */
export type HeatmapMetric = "overlap" | "lift" | "jaccard";

/** Lift saturates the color scale past this (sparse pairs reach 100s× —
 *  the tooltip keeps the raw number, the color only says "beyond"). */
export const LIFT_COLOR_CAP = 5;

/** One matrix cell — an API overlap row + the display labels resolved at
 *  build time (base = matrix row family, other = column family). */
export type OverlapCell = SignalOverlapAuditRow & {
  baseLabel: string;
  otherLabel: string;
};

export interface OverlapHeatmapDatum {
  /** Family display labels, ordered by event count desc (the axes). */
  familyCodes: number[];
  familyLabels: string[];
  /** The API overlap rows — display labels resolve inside the builder. */
  cells: SignalOverlapAuditRow[];
}

function cellColorValue(
  cell: SignalOverlapAuditRow,
  metric: HeatmapMetric,
): number {
  if (metric === "overlap") return cell.overlap_pct;
  if (metric === "jaccard") return cell.jaccard;
  return Math.min(cell.lift, LIFT_COLOR_CAP);
}

function cellLabelText(
  cell: SignalOverlapAuditRow,
  metric: HeatmapMetric,
): string {
  if (metric === "overlap") return `${Math.round(cell.overlap_pct * 100)}%`;
  if (metric === "jaccard") return `${Math.round(cell.jaccard * 100)}%`;
  return `${cell.lift.toFixed(1)}×`;
}

function metricColorRamp(metric: HeatmapMetric): string[] {
  // Gray (chance-level / sparse) → blue for the 0..1 set metrics,
  // gray → orange for lift (a different KIND of reading: enrichment —
  // two stops so past-cap pairs saturate at orange, not a new hue).
  return metric === "lift"
    ? [NEUTRAL_FILL, MA20_COLOR]
    : [NEUTRAL_FILL, IV_BLUE, PALETTE_HI];
}

export function buildOverlapHeatmapOption(
  data: OverlapHeatmapDatum,
  metric: HeatmapMetric,
  mode: ThemeMode,
): EChartsOption {
  const c = axisColors(mode);
  const { familyCodes, familyLabels, cells } = data;
  const colIndex = new Map(familyCodes.map((code, i) => [code, i]));
  const labelByCode = new Map(
    familyCodes.map((code, i) => [code, familyLabels[i] ?? `#${code}`]),
  );

  const seriesData = cells
    .filter(
      (cell) =>
        colIndex.has(cell.signal_type) && colIndex.has(cell.other_signal_type),
    )
    .map((cell) => ({
      value: [
        colIndex.get(cell.other_signal_type),
        colIndex.get(cell.signal_type),
        cellColorValue(cell, metric),
      ],
      cell: {
        ...cell,
        baseLabel: labelByCode.get(cell.signal_type) ?? `#${cell.signal_type}`,
        otherLabel:
          labelByCode.get(cell.other_signal_type) ?? `#${cell.other_signal_type}`,
      },
    }));

  const visualMax = metric === "lift" ? LIFT_COLOR_CAP : 1;
  const visualFormatter = (v: number): string =>
    metric === "lift"
      ? v >= LIFT_COLOR_CAP
        ? `≥${LIFT_COLOR_CAP}×`
        : `${v.toFixed(1)}×`
      : `${Math.round(v * 100)}%`;

  return baseChartOption(mode, {
    legend: null,
    grid: commonGrid({ left: 8, right: 70, top: 10, bottom: 10, containLabel: true }),
    tooltip: commonTooltip(mode, {
      trigger: "item",
      axisPointer: undefined,
      formatter: (params) => {
        const cell = (params as { data?: { cell?: OverlapCell } }).data?.cell;
        if (cell == null) return "";
        const pct = `${(cell.overlap_pct * 100).toFixed(1)}%`;
        const exp = `${(cell.expected_pct * 100).toFixed(1)}%`;
        return [
          `<b>${cell.baseLabel} → ${cell.otherLabel}</b>`,
          `overlap ${pct} of the base family's ${cell.n_base} signal days` +
            ` (${cell.n_overlap} shared)`,
          `chance expectation ${exp} — lift ${cell.lift.toFixed(2)}×`,
          `Jaccard ${(cell.jaccard * 100).toFixed(1)}%`,
          `same action ${cell.n_same_action} / opposite ${cell.n_opposite_action}`,
        ].join("<br/>");
      },
    }),
    visualMap: {
      type: "continuous",
      min: 0,
      max: visualMax,
      calculable: false,
      orient: "vertical",
      right: 4,
      top: "middle",
      itemHeight: 120,
      textStyle: { color: c.textColor, fontSize: 10 },
      formatter: visualFormatter,
      inRange: { color: metricColorRamp(metric) },
    },
    xAxis: {
      type: "category",
      data: familyLabels,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisTick: { show: false },
      axisLabel: { color: c.textColor, fontSize: 10, rotate: 35 },
      splitArea: { show: false },
    },
    yAxis: {
      type: "category",
      data: familyLabels,
      inverse: true,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisTick: { show: false },
      axisLabel: { color: c.textColor, fontSize: 10 },
      splitArea: { show: false },
    },
    series: [
      {
        type: "heatmap",
        data: seriesData,
        itemStyle: {
          borderColor: c.axisLineColor,
          borderWidth: 1,
        },
        label: {
          show: true,
          fontSize: 9,
          color: c.textColor,
          formatter: (params) => {
            const cell = (params as { data?: { cell?: OverlapCell } }).data
              ?.cell;
            return cell == null ? "" : cellLabelText(cell, metric);
          },
        },
        emphasis: { itemStyle: { shadowBlur: 6, shadowColor: "rgba(0,0,0,0.4)" } },
      },
    ],
  });
}
