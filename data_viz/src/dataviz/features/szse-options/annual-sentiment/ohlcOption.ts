/**
 * ETF OHLC chart option builder for annual-sentiment.
 * Uses React-based tooltip formatters via tooltipComponents.tsx.
 * Exports buildOhlcOption for use by SzseOptionsPage and other consumers.
 */
import {
  DOWN_COLOR,
  UP_COLOR,
  axisColors,
  commonDataZoom,
  commonLegend,
  commonGrid,
} from "@/theme/chart-palette";
import {
  ohlcSeries,
  rebasePriceArrays,
  type OhlcMode,
} from "@/lib/ohlc";
import { makeOhlcAxisTooltipFormatter, type TooltipColors } from "./tooltipComponents";
import type { EtfOhlcvResponse } from "@shared/types";
import type { EChartsOption } from "echarts";

export function buildOhlcOption(
  ohlcv: EtfOhlcvResponse,
  themeMode: "light" | "dark",
  ohlcMode: OhlcMode,
): EChartsOption {
  const c = axisColors(themeMode);
  const colors: TooltipColors = {
    textColor: c.textColor,
    tooltipBg: c.tooltipBg,
    splitLineColor: c.splitLineColor,
  };

  const rows = ohlcv.rows;
  const dates = rows.map((r) => r.date);
  const open = rows.map((r) => r.open);
  const close = rows.map((r) => r.close);
  const low = rows.map((r) => r.low);
  const high = rows.map((r) => r.high);

  const { rebased } = rebasePriceArrays(
    { open, close, low, high },
    ohlcMode,
  );

  const candleData = rows.map((_, i) => [
    rebased.open[i],
    rebased.close[i],
    rebased.low[i],
    rebased.high[i],
  ]);

  const volumes = rows.map((r) => ({
    value: r.volume / 100,
    itemStyle: {
      color: r.close >= r.open ? UP_COLOR : DOWN_COLOR,
      opacity: 0.4,
    },
  }));

  const axisTooltip = makeOhlcAxisTooltipFormatter(colors, ohlcMode);

  return {
    backgroundColor: "transparent",
    animation: false,
    grid: commonGrid({ left: 56, right: 56, bottom: 50 }),
    dataZoom: commonDataZoom(),
    tooltip: {
      trigger: "axis",
      axisPointer: {
        type: "cross",
        snap: true,
        label: {
          backgroundColor: c.tooltipBg,
          borderColor: c.splitLineColor,
          borderWidth: 1,
          padding: [3, 5],
          color: c.textColor,
          fontSize: 10,
        },
      },
      backgroundColor: c.tooltipBg,
      borderColor: c.splitLineColor,
      textStyle: { color: c.textColor, fontSize: 11 },
      formatter: axisTooltip,
    },
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    legend: commonLegend(themeMode),
    xAxis: {
      type: "category",
      data: dates,
      boundaryGap: true,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: { color: c.textColor, fontSize: 9, rotate: 30 },
      splitLine: { show: false },
    },
    yAxis: [
      {
        type: "value",
        scale: true,
        name: ohlcMode === "percentage" ? "%" : "Price (元)",
        nameTextStyle: { color: c.textColor, fontSize: 10 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: {
          color: c.textColor,
          fontSize: 10,
        },
        splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
      },
      {
        type: "value",
        scale: true,
        name: "Volume (mil)",
        nameTextStyle: { color: c.textColor, fontSize: 10 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: { color: c.textColor, fontSize: 10 },
        splitLine: { show: false },
      },
    ],
    series: [
      ohlcSeries(candleData, { name: "OHLC" }),
      {
        type: "bar",
        name: "Volume",
        yAxisIndex: 1,
        data: volumes,
        barWidth: "70%",
        z: 1,
      },
    ],
  };
}