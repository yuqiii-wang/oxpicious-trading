import { useStore } from "@/store/filters";
import {
  axisColors,
  commonDataZoom,
  commonGrid,
  commonLegend,
  expiryBlueColor,
  IV_BLUE,
} from "@/theme/chart-palette";
import { fmtNum } from "@/lib/series";
import { expiryToYyyyMm } from "./expiryUtils";
import type { DailySkew } from "./types";
import type { SkewnessCorrRow } from "@shared/types";
import type { EChartsOption } from "echarts";

export type CorrMode = "ma5" | "ma20" | "ma60";

const CORR_FIELD_MAP: Record<CorrMode, keyof SkewnessCorrRow> = {
  ma5: "corr_skewness_ma5_vs_spot_ma5",
  ma20: "corr_skewness_ma20_vs_spot_ma20",
  ma60: "corr_skewness_ma60_vs_spot_ma60",
};

const CORR_MODE_LABELS: Record<CorrMode, string> = {
  ma5: "MA5",
  ma20: "MA20",
  ma60: "MA60",
};

export function buildCorrTimeSeriesOption(
  dailySkew: DailySkew[],
  corrRows: SkewnessCorrRow[],
  selectedDate: string,
  mode: CorrMode = "ma5",
): EChartsOption {
  const themeMode = useStore.getState().themeMode;
  const c = axisColors(themeMode);
  const textColor = c.textColor;
  const splitColor = c.splitLineColor;

  if (dailySkew.length === 0) {
    return {
      backgroundColor: "transparent",
      title: {
        text: `Skewness–Spot Whole-Period Correlation · ${CORR_MODE_LABELS[mode]}  [No data]`,
        left: "center",
        top: "center",
        textStyle: { color: textColor, fontSize: 11, fontWeight: 400 },
      },
    };
  }

  const dates = dailySkew.map((d) => d.date);

  const corrField = CORR_FIELD_MAP[mode];

  // Map corr data by date -> expiry_month -> value
  const corrMap = new Map<string, Map<string, number | null>>();
  for (const r of corrRows) {
    if (!corrMap.has(r.date)) corrMap.set(r.date, new Map());
    const val = r[corrField];
    const numVal = typeof val === "number" ? val : val != null ? Number(val) : null;
    corrMap.get(r.date)!.set(r.expiry_month, numVal != null && Number.isFinite(numVal) ? numVal : null);
  }

  // Collect expiry months from corr data, sorted by nearest to selected
  const expiryMonths = Array.from(new Set(corrRows.map((r) => r.expiry_month)))
    .sort((a, b) => {
      const da = Math.abs(a.localeCompare(selectedDate.slice(0, 7)));
      const db = Math.abs(b.localeCompare(selectedDate.slice(0, 7)));
      return da - db;
    });
  const nExpiries = expiryMonths.length;

  const axisDates = dates;

  // Build per-expiry correlation series (thin, for reference)
  const corrSeries: EChartsOption["series"] = expiryMonths.map((ym, ei) => {
    const data: (number | null)[] = axisDates.map((d) => {
      const map = corrMap.get(d);
      if (!map) return null;
      const val = map.get(ym);
      return val != null && Number.isFinite(val) ? val : null;
    });
    const color = expiryBlueColor(ei, nExpiries);
    return {
      type: "line" as const,
      name: `${CORR_MODE_LABELS[mode]} ${ym}`,
      showSymbol: false,
      smooth: false,
      connectNulls: false,
      lineStyle: { color, width: 1, opacity: 0.5 },
      itemStyle: { color },
      data,
      z: 1,
      tooltip: { show: false },
    };
  });

  // Build mean correlation series (thick, main curve) — average across all expiry groups
  const meanData: (number | null)[] = axisDates.map((d) => {
    const map = corrMap.get(d);
    if (!map) return null;
    const vals: number[] = [];
    for (const ym of expiryMonths) {
      const v = map.get(ym);
      if (v != null && Number.isFinite(v)) vals.push(v);
    }
    if (vals.length === 0) return null;
    return vals.reduce((a, b) => a + b, 0) / vals.length;
  });

  const meanSeries: EChartsOption["series"] = {
    type: "line" as const,
    name: `${CORR_MODE_LABELS[mode]} Mean`,
    showSymbol: false,
    smooth: false,
    connectNulls: false,
    lineStyle: { color: IV_BLUE, width: 2.5, opacity: 0.95 },
    itemStyle: { color: IV_BLUE },
    data: meanData,
    z: 3,
  };

  // Center zero line
  const zeroLine = {
    type: "line" as const,
    name: "Zero",
    data: [],
    markLine: {
      symbol: ["none" as const, "none" as const],
      silent: true,
      lineStyle: { color: textColor, type: "dashed", opacity: 0.3 },
      data: [{ yAxis: 0 }],
      tooltip: { show: false },
      label: { show: false },
    },
  };

  // Find the selected date index for markPoint
  const selectedIdx = dates.indexOf(selectedDate);
  const markPointData: { name: string; coord: [string, number]; value: number }[] = [];
  if (selectedIdx >= 0 && meanData[selectedIdx] != null) {
    markPointData.push({
      name: "selected",
      coord: [selectedDate, meanData[selectedIdx] as number],
      value: meanData[selectedIdx] as number,
    });
  }

  const visibleLegendData = expiryMonths.map((ym) => `${CORR_MODE_LABELS[mode]} ${ym}`);
  visibleLegendData.unshift(`${CORR_MODE_LABELS[mode]} Mean`);

  return {
    backgroundColor: "transparent",
    animation: false,
    grid: commonGrid({ left: 56, right: 36, top: 30, bottom: 28 }),
    title: {
      text: `Skewness–Spot Whole-Period Correlation · ${CORR_MODE_LABELS[mode]}`,
      left: "left",
      textStyle: { color: textColor, fontSize: 10, fontWeight: 600 },
    },
    tooltip: {
      trigger: "axis",
      axisPointer: {
        type: "cross",
        snap: true,
        lineStyle: { color: textColor, type: "dashed", opacity: 0.5 },
        label: {
          color: textColor,
          fontSize: 9,
          backgroundColor: c.tooltipBg,
          borderColor: c.splitLineColor,
          borderWidth: 1,
          padding: [3, 5],
        },
      },
      backgroundColor: c.tooltipBg,
      borderColor: splitColor,
      textStyle: { color: textColor, fontSize: 11 },
      formatter: (params: unknown) => {
        const p = params as Array<{ seriesName: string; value: [string, number | null]; componentType?: string }>;
        if (!Array.isArray(p) || p.length === 0) return "";
        const date = p[0]?.value?.[0] ?? "";
        const lines: string[] = [date];
        for (const item of p) {
          if (item.componentType === "markLine") continue;
          const v = item.value?.[1];
          if (v != null && Number.isFinite(v)) {
            lines.push(`${item.seriesName}: ${fmtNum(v, 3)}`);
          }
        }
        return lines.join("<br/>");
      },
    },
    legend: commonLegend(themeMode, {
      top: 10,
      data: visibleLegendData,
      show: visibleLegendData.length <= 8,
      textStyle: { fontSize: 9 },
    }),
    xAxis: {
      type: "category",
      data: axisDates,
      name: "Date",
      nameLocation: "middle",
      nameGap: 20,
      nameTextStyle: { color: textColor, fontSize: 9 },
      axisLine: { lineStyle: { color: textColor } },
      axisLabel: { color: textColor, fontSize: 9 },
      splitLine: { show: false },
      boundaryGap: false,
    },
    yAxis: {
      type: "value",
      scale: true,
      min: -1,
      max: 1,
      name: "Correlation",
      nameLocation: "middle",
      nameGap: 36,
      nameTextStyle: { color: textColor, fontSize: 9 },
      axisLine: { lineStyle: { color: textColor } },
      axisLabel: {
        color: textColor,
        fontSize: 9,
        formatter: (v: number) => fmtNum(v, 1),
      },
      splitLine: {
        lineStyle: { color: splitColor, type: "dashed", opacity: 0.4 },
      },
    },
    // Hidden dataZoom (no visible slider) — synced from the skew chart's slider
    dataZoom: commonDataZoom({ show: false, xAxisIndex: 0 }, 0, 100),
    series: [meanSeries, ...corrSeries, zeroLine],
  };
}
