/**
 * Put/Call OI Ratio chart option builder for annual-sentiment.
 * Uses React-based tooltip formatters via tooltipComponents.tsx.
 */
import {
  ATM_GRAY,
  DOWN_COLOR,
  FUTURES_EXPIRY_DOT,
  IV_BLUE,
  MA20_COLOR,
  axisColors,
  commonDataZoom,
  commonLegend,
  commonGrid,
} from "@/theme/chart-palette";
import { breakArraysAtGaps, safeMa } from "@/lib/series";
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
  ratioBroken: (number | null)[],
  expiryMarkers: ExpiryMarker[],
): ExpiryMarkerDataItem[] {
  const dateIndexMap = new Map<string, number>();
  brokenDates.forEach((d, i) => dateIndexMap.set(d, i));

  const expiryData: ExpiryMarkerDataItem[] = [];
  for (const m of expiryMarkers) {
    const idx = dateIndexMap.get(m.tradingDate);
    if (idx == null) continue;
    let ratioVal = ratioBroken[idx];
    if (ratioVal == null || !Number.isFinite(ratioVal)) {
      for (let j = idx - 1; j >= 0; j--) {
        const v = ratioBroken[j];
        if (v != null && Number.isFinite(v)) {
          ratioVal = v;
          break;
        }
      }
    }
    if (ratioVal == null || !Number.isFinite(ratioVal)) continue;
    expiryData.push({ value: [idx, ratioVal], marker: m });
  }
  return expiryData;
}

export function buildPcRatioOption(
  dates: string[],
  pcRatio: number[],
  themeMode: "light" | "dark",
  expiryMarkers: ExpiryMarker[] = [],
): EChartsOption {
  const c = axisColors(themeMode);
  const colors: TooltipColors = {
    textColor: c.textColor,
    tooltipBg: c.tooltipBg,
    splitLineColor: c.splitLineColor,
  };
  const ma5 = safeMa(pcRatio, 5);
  const ma20 = safeMa(pcRatio, 20);
  const broken = breakArraysAtGaps(dates, [pcRatio, ma5, ma20]);

  const expiryData = buildExpiryData(broken.dates, broken.arrays[0], expiryMarkers);
  const axisTooltip = makeAxisTooltipFormatter(colors);
  const dotTooltip = makeExpiryDotTooltip(colors);

  return {
    backgroundColor: "transparent",
    animation: false,
    grid: commonGrid({ left: 50, right: 20, bottom: 50 }),
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
      name: "P/C Ratio",
      nameTextStyle: { color: c.textColor, fontSize: 10 },
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: { color: c.textColor, fontSize: 10 },
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