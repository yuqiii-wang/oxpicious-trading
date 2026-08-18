/**
 * Total Open Interest Trend chart option builder for annual-sentiment.
 * Uses React-based tooltip formatters via tooltipComponents.tsx.
 */
import {
  DOWN_COLOR,
  FUTURES_EXPIRY_DOT,
  UP_COLOR,
  axisColors,
  commonDataZoom,
  commonLegend,
  commonGrid,
} from "@/theme/chart-palette";
import { breakArraysAtGaps } from "@/lib/series";
import {
  makeAxisTooltipFormatter,
  makeExpiryDotTooltip,
  EXPIRY_MARKERS_SERIES_NAME,
  type TooltipColors,
} from "./tooltipComponents";
import type { ExpiryMarker, ExpiryMarkerDataItem } from "./types";
import type { EChartsOption } from "echarts";

const EXPIRY_MARKERS_SERIES_ID = "expiry-markers";

function buildExpiryData(
  brokenDates: string[],
  callBroken: (number | null)[],
  putBroken: (number | null)[],
  expiryMarkers: ExpiryMarker[],
): ExpiryMarkerDataItem[] {
  const dateIndexMap = new Map<string, number>();
  brokenDates.forEach((d, i) => dateIndexMap.set(d, i));

  const expiryData: ExpiryMarkerDataItem[] = [];
  for (const m of expiryMarkers) {
    const idx = dateIndexMap.get(m.tradingDate);
    if (idx == null) continue;
    let callVal = callBroken[idx];
    let putVal = putBroken[idx];
    if (callVal == null || !Number.isFinite(callVal) || putVal == null || !Number.isFinite(putVal)) {
      for (let j = idx - 1; j >= 0; j--) {
        const cv = callBroken[j];
        const pv = putBroken[j];
        if (cv != null && Number.isFinite(cv) && pv != null && Number.isFinite(pv)) {
          callVal = cv;
          putVal = pv;
          break;
        }
      }
    }
    if (callVal == null || !Number.isFinite(callVal) || putVal == null || !Number.isFinite(putVal)) continue;
    expiryData.push({ value: [idx, callVal + putVal], marker: m });
  }
  return expiryData;
}

export function buildOiTrendOption(
  dates: string[],
  callOi: number[],
  putOi: number[],
  themeMode: "light" | "dark",
  expiryMarkers: ExpiryMarker[] = [],
): EChartsOption {
  const c = axisColors(themeMode);
  const colors: TooltipColors = {
    textColor: c.textColor,
    tooltipBg: c.tooltipBg,
    splitLineColor: c.splitLineColor,
  };

  const callMil = callOi.map((v) => v / 1e6);
  const putMil = putOi.map((v) => v / 1e6);
  const broken = breakArraysAtGaps(dates, [callMil, putMil]);

  const expiryData = buildExpiryData(broken.dates, broken.arrays[0], broken.arrays[1], expiryMarkers);
  const axisTooltip = makeAxisTooltipFormatter(colors);
  const dotTooltip = makeExpiryDotTooltip(colors, " contracts");

  return {
    backgroundColor: "transparent",
    animation: false,
    grid: commonGrid({ left: 56, right: 20, bottom: 50 }),
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
      axisLabel: { color: c.textColor, fontSize: 10, formatter: (v: number) => `${v} mil` },
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
        id: EXPIRY_MARKERS_SERIES_ID,
        type: "scatter",
        name: EXPIRY_MARKERS_SERIES_NAME,
        data: expiryData,
        symbolSize: 10,
        symbol: "circle",
        itemStyle: {
          color: FUTURES_EXPIRY_DOT,
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
          borderColor: FUTURES_EXPIRY_DOT,
          textStyle: { color: c.textColor, fontSize: 11 },
          formatter: dotTooltip,
        },
      },
    ],
  };
}