/**
 * Vol regime option — where today's vol sits in its own trailing-year
 * distribution: the hv_20 / ewma_vol trailing-252 percentile ranks
 * (0..1, left axis, with 10% / 90% guide lines) and the hv_20 z-score
 * (right axis). The house "how unusual is today" read — percentile
 * > 0.9 = stretched-high vol regime, < 0.1 = compressed.
 *
 * NO dataZoom slider of its own: the merged vol box shows the time
 * slider on its FIRST chart only (chartOption/volLevelsOption.ts), and
 * this chart keeps an inside-only zoom (the slider component hidden)
 * so the echarts.connect group still drives its viewport. The raw
 * close price rides a THIRD axis (offset right of the z-score axis)
 * for context.
 */
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import { baseChartOption, commonTooltip } from "@/shared/charts/base-chart";
import {
  axisColors,
  commonDataZoom,
  commonGrid,
  MUTED_PALETTE,
  UP_COLOR,
  DOWN_COLOR,
} from "@/theme/chart-palette";
import type { VolatilityRow } from "@shared/types";
import { buildCloseSeries, closeAxis } from "./closeOverlay";

export function buildRegimeOption(
  rows: VolatilityRow[],
  mode: ThemeMode,
): EChartsOption {
  const c = axisColors(mode);
  const dates = rows.map((r) => r.date);

  return baseChartOption(mode, {
    // Two right-hand axes (z-score at the edge, price offset past it) —
    // the extra margin clears the price labels.
    grid: commonGrid({ right: 72 }),
    dataZoom: commonDataZoom({ show: false }),
    tooltip: commonTooltip(mode, {}),
    legend: {
      data: ["Close", "HV20 pct", "EWMA pct", "HV20 z"],
    },
    xAxis: {
      type: "category",
      data: dates,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: { color: c.textColor, fontSize: 9 },
    },
    yAxis: [
      {
        type: "value",
        name: "percentile (0..1)",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        min: 0,
        max: 1,
        axisLabel: { color: c.textColor, fontSize: 9 },
        splitLine: { lineStyle: { color: c.splitLineColor } },
      },
      {
        type: "value",
        name: "z-score",
        position: "right",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLabel: { color: c.textColor, fontSize: 9 },
        splitLine: { show: false },
      },
      closeAxis(mode, 36),
    ],
    series: [
      // Close first — the price context line behind the gauges (blue is
      // reserved for it; the percentile lines ride warm hues).
      buildCloseSeries(rows, 2),
      {
        type: "line",
        name: "HV20 pct",
        data: rows.map((r) => r.hv_20_pct_rank_252),
        showSymbol: false,
        lineStyle: { width: 1.6, color: MUTED_PALETTE[1] },
        itemStyle: { color: MUTED_PALETTE[1] },
        emphasis: { focus: "series" },
        markLine: {
          silent: true,
          symbol: "none",
          label: { fontSize: 9, formatter: "{b}" },
          data: [
            { name: "90%", yAxis: 0.9, lineStyle: { color: DOWN_COLOR, type: "dashed", opacity: 0.6 } },
            { name: "10%", yAxis: 0.1, lineStyle: { color: UP_COLOR, type: "dashed", opacity: 0.6 } },
          ],
        },
      },
      {
        type: "line",
        name: "EWMA pct",
        data: rows.map((r) => r.ewma_vol_pct_rank_252),
        showSymbol: false,
        lineStyle: { width: 1.2, color: MUTED_PALETTE[2], type: "dashed" },
        itemStyle: { color: MUTED_PALETTE[2] },
        emphasis: { focus: "series" },
      },
      {
        type: "line",
        name: "HV20 z",
        yAxisIndex: 1,
        data: rows.map((r) => r.hv_20_z_252),
        showSymbol: false,
        lineStyle: { width: 1, color: MUTED_PALETTE[7], opacity: 0.8 },
        itemStyle: { color: MUTED_PALETTE[7] },
        emphasis: { focus: "series" },
      },
    ],
  });
}
