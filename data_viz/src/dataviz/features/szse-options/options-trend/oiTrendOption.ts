/**
 * Total Open Interest Trend chart option builder.
 * Extracted from AnnualSentimentPanel.tsx for the merged OptionsTrendPanel.
 * Uses shared expiryTooltip.ts for tooltip formatters.
 */
import {
  DOWN_COLOR,
  UP_COLOR,
  axisColors,
  commonLegend,
  commonGrid,
} from "@/theme/chart-palette";
import { breakArraysAtGaps, fmtNum } from "@/lib/series";
import {
  EXPIRY_MARKERS_SERIES_NAME,
  buildExpiryData,
  makeAxisTooltipFormatter,
  makeExpiryDotTooltip,
} from "./expiryTooltip";
import type { ExpiryMarker } from "./sharedData";
import type { EChartsOption } from "echarts";

interface BrokenData {
  dates: string[];
  arrays: Array<Array<number | null>>;
}

function buildOiTrendOptionFromBroken(
  broken: BrokenData,
  themeMode: "light" | "dark",
  expiryMarkers: ExpiryMarker[] = [],
  dataZoom: EChartsOption["dataZoom"] = undefined,
): EChartsOption {
  const c = axisColors(themeMode);
  const colors = { textColor: c.textColor, tooltipBg: c.tooltipBg, splitLineColor: c.splitLineColor };
  const expiryData = buildExpiryData(broken.dates, [broken.arrays[0], broken.arrays[1]], expiryMarkers);
  const axisTooltip = makeAxisTooltipFormatter(colors);
  const dotTooltip = makeExpiryDotTooltip(colors, " contracts");

  return {
    backgroundColor: "transparent",
    animation: false,
    grid: commonGrid({ left: 56, right: 20, bottom: 50, top: 36 }),
    dataZoom,
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
      data: broken.dates,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: { color: c.textColor, fontSize: 9, rotate: 30 },
    },
    yAxis: {
      type: "value",
      scale: true,
      name: "OI (mil contracts)",
      nameTextStyle: { color: c.textColor, fontSize: 10 },
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: { color: c.textColor, fontSize: 10, formatter: (v: number) => fmtNum(v) + " mil" },
      splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
    },
    series: [
      {
        type: "line",
        name: "Call OI",
        data: broken.arrays[0],
        smooth: false,
        symbol: "none",
        areaStyle: { opacity: 0.12 },
        lineStyle: { color: UP_COLOR, width: 1.2 },
      },
      {
        type: "line",
        name: "Put OI",
        data: broken.arrays[1],
        smooth: false,
        symbol: "none",
        areaStyle: { opacity: 0.12 },
        lineStyle: { color: DOWN_COLOR, width: 1.2 },
      },
      {
        id: "oi-expiry-markers",
        type: "scatter",
        name: EXPIRY_MARKERS_SERIES_NAME,
        data: expiryData,
        symbolSize: 10,
        symbol: "circle",
        itemStyle: {
          color: "#d32f2f",
          borderColor: "#ffffff",
          borderWidth: 1.5,
          shadowBlur: 4,
          shadowColor: "rgba(211, 47, 47, 0.4)",
        },
        z: 10,
        tooltip: {
          show: true,
          trigger: "item",
          backgroundColor: c.tooltipBg,
          borderColor: "#d32f2f",
          textStyle: { color: c.textColor, fontSize: 11 },
          formatter: dotTooltip,
        },
      },
    ],
  };
}

export function buildOiTrendOption(
  dates: string[],
  callOi: number[],
  putOi: number[],
  themeMode: "light" | "dark",
  expiryMarkers: ExpiryMarker[] = [],
  dataZoom: EChartsOption["dataZoom"] = undefined,
): EChartsOption {
  const callMil = callOi.map((v) => v / 1e6);
  const putMil = putOi.map((v) => v / 1e6);
  const broken = breakArraysAtGaps(dates, [callMil, putMil]);
  return buildOiTrendOptionFromBroken(broken, themeMode, expiryMarkers, dataZoom);
}

export function buildOiTrendOptionWithBroken(
  brokenDates: string[],
  callMilBroken: (number | null)[],
  putMilBroken: (number | null)[],
  themeMode: "light" | "dark",
  expiryMarkers: ExpiryMarker[] = [],
  dataZoom: EChartsOption["dataZoom"] = undefined,
): EChartsOption {
  return buildOiTrendOptionFromBroken(
    { dates: brokenDates, arrays: [callMilBroken, putMilBroken] },
    themeMode,
    expiryMarkers,
    dataZoom,
  );
}