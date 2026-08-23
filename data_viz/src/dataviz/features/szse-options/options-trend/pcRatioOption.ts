/**
 * Put/Call OI Ratio chart option builder.
 * Extracted from AnnualSentimentPanel.tsx for the merged OptionsTrendPanel.
 * Uses shared expiryTooltip.ts for tooltip formatters.
 */
import {
  ATM_GRAY,
  DOWN_COLOR,
  IV_BLUE,
  MA20_COLOR,
  UP_COLOR,
  axisColors,
  commonLegend,
  commonGrid,
} from "@/theme/chart-palette";
import { breakArraysAtGaps, fmtNum, safeMa } from "@/lib/series";
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

function buildPcRatioOptionFromBroken(
  broken: BrokenData,
  themeMode: "light" | "dark",
  expiryMarkers: ExpiryMarker[] = [],
  dataZoom: EChartsOption["dataZoom"] = undefined,
  yRange?: [number, number],
): EChartsOption {
  const c = axisColors(themeMode);
  const colors = { textColor: c.textColor, tooltipBg: c.tooltipBg, splitLineColor: c.splitLineColor };
  const expiryData = buildExpiryData(broken.dates, [broken.arrays[0]], expiryMarkers);
  const axisTooltip = makeAxisTooltipFormatter(colors);
  const dotTooltip = makeExpiryDotTooltip(colors);

  return {
    backgroundColor: "transparent",
    animation: false,
    grid: commonGrid({ left: 50, right: 20, bottom: 50, top: 36 }),
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
      // Frozen y-range (from the full dataset) keeps the scale constant when
      // the cohort selection changes; omitted → ECharts auto extent
      ...(yRange ? { min: yRange[0], max: yRange[1] } : {}),
      name: "P/C Ratio",
      nameTextStyle: { color: c.textColor, fontSize: 10 },
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: { color: c.textColor, fontSize: 10, formatter: (v: number) => fmtNum(v) },
      splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
    },
    series: [
      {
        type: "line",
        name: "P/C OI Ratio",
        data: broken.arrays[0],
        smooth: false,
        symbol: "none",
        lineStyle: { color: IV_BLUE, width: 1.2 },
        markLine: {
          symbol: "none",
          silent: true,
          data: [{ yAxis: 1.0 }],
          lineStyle: { color: ATM_GRAY, type: "dotted", width: 0.8, opacity: 0.6 },
        },
      },
      {
        type: "line",
        name: "MA5",
        data: broken.arrays[1],
        smooth: false,
        symbol: "none",
        lineStyle: { color: DOWN_COLOR, width: 1, opacity: 0.7 },
      },
      {
        type: "line",
        name: "MA20",
        data: broken.arrays[2],
        smooth: false,
        symbol: "none",
        lineStyle: { color: MA20_COLOR, width: 1, opacity: 0.7 },
      },
      {
        id: "pc-expiry-markers",
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

export function buildPcRatioOption(
  dates: string[],
  pcRatio: number[],
  themeMode: "light" | "dark",
  expiryMarkers: ExpiryMarker[] = [],
  dataZoom: EChartsOption["dataZoom"] = undefined,
): EChartsOption {
  const ma5 = safeMa(pcRatio, 5);
  const ma20 = safeMa(pcRatio, 20);
  const broken = breakArraysAtGaps(dates, [pcRatio, ma5, ma20]);
  return buildPcRatioOptionFromBroken(broken, themeMode, expiryMarkers, dataZoom);
}

export function buildPcRatioOptionWithBroken(
  brokenDates: string[],
  pcRatioBroken: (number | null)[],
  ma5Broken: (number | null)[],
  ma20Broken: (number | null)[],
  themeMode: "light" | "dark",
  expiryMarkers: ExpiryMarker[] = [],
  dataZoom: EChartsOption["dataZoom"] = undefined,
  yRange?: [number, number],
): EChartsOption {
  return buildPcRatioOptionFromBroken(
    { dates: brokenDates, arrays: [pcRatioBroken, ma5Broken, ma20Broken] },
    themeMode,
    expiryMarkers,
    dataZoom,
    yRange,
  );
}