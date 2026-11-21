/**
 * Overnight / intraday / realized decomposition option — the 20-day
 * overnight-gap vol vs the open-to-close intraday vol (which leg
 * carries the volatility — the A-share gap-regime read) plus the
 * 5-minute realized vol where intraday bars exist (NULL outside the
 * ~90-day retention window), percent annualized on one axis. The
 * bipower jump ratio rides the right axis (0 when calm, > 0 on
 * jump-dominated sessions).
 *
 * NO dataZoom slider of its own: the merged vol box shows the time
 * slider on its FIRST chart only (chartOption/volLevelsOption.ts), and
 * this chart keeps an inside-only zoom (the slider component hidden)
 * so the echarts.connect group still drives its viewport. The raw
 * close price rides a THIRD axis (offset right of the jump-ratio
 * axis) for context.
 */
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import { baseChartOption, commonTooltip } from "@/shared/charts/base-chart";
import {
  axisColors,
  commonDataZoom,
  commonGrid,
  MUTED_PALETTE,
  SPOT_COLOR,
} from "@/theme/chart-palette";
import type { VolatilityRow } from "@shared/types";
import { buildCloseSeries, closeAxis } from "./closeOverlay";

export function buildDecompositionOption(
  rows: VolatilityRow[],
  mode: ThemeMode,
): EChartsOption {
  const c = axisColors(mode);
  const hasRv = rows.some((r) => r.rv_5min != null);

  return baseChartOption(mode, {
    // Two right-hand axes (jump ratio at the edge, price offset past
    // it) — the extra margin clears the price labels.
    grid: commonGrid({ right: 72 }),
    dataZoom: commonDataZoom({ show: false }),
    tooltip: commonTooltip(mode, {
      valueFormatter: (v) => (v == null ? "—" : `${Number(v).toFixed(2)}`),
    }),
    legend: {
      data: hasRv
        ? ["Close", "Overnight 20d", "Intraday 20d", "RV 5-min", "Jump ratio"]
        : ["Close", "Overnight 20d", "Intraday 20d"],
    },
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
      {
        type: "value",
        name: "jump ratio",
        position: "right",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        min: 0,
        axisLabel: { color: c.textColor, fontSize: 9 },
        splitLine: { show: false },
      },
      closeAxis(mode, 36),
    ],
    series: [
      // Close first — the price context line behind the vol split.
      buildCloseSeries(rows, 2),
      {
        type: "line",
        name: "Overnight 20d",
        data: rows.map((r) => r.overnight_vol_20),
        showSymbol: false,
        lineStyle: { width: 1.5, color: MUTED_PALETTE[6] },
        itemStyle: { color: MUTED_PALETTE[6] },
        areaStyle: { opacity: 0.08 },
        emphasis: { focus: "series" },
      },
      {
        type: "line",
        name: "Intraday 20d",
        data: rows.map((r) => r.intraday_vol_20),
        showSymbol: false,
        lineStyle: { width: 1.5, color: SPOT_COLOR },
        itemStyle: { color: SPOT_COLOR },
        emphasis: { focus: "series" },
      },
      ...(hasRv
        ? [{
            type: "line" as const,
            name: "RV 5-min",
            data: rows.map((r) => r.rv_5min),
            showSymbol: false,
            // Blue is reserved for the Close overlay — RV rides green.
            lineStyle: { width: 1.2, color: MUTED_PALETTE[2], type: "dotted" as const, opacity: 0.9 },
            itemStyle: { color: MUTED_PALETTE[2] },
            emphasis: { focus: "series" as const },
          }]
        : []),
      ...(hasRv
        ? [{
            type: "bar" as const,
            name: "Jump ratio",
            yAxisIndex: 1,
            data: rows.map((r) => r.jump_ratio_5min),
            barWidth: "55%",
            itemStyle: { color: MUTED_PALETTE[3], opacity: 0.35 },
            emphasis: { focus: "series" as const },
          }]
        : []),
    ],
  });
}
