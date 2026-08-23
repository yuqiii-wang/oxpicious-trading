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
import type { IvSkewRow } from "@shared/types";
import type { EChartsOption } from "echarts";

export type IvSkewMode = "daily" | "ma5" | "ma20" | "ma60";

const RR_FIELD_MAP: Record<IvSkewMode, keyof IvSkewRow> = {
  daily: "risk_reversal_25d",
  ma5: "rr25_ma5",
  ma20: "rr25_ma20",
  ma60: "rr25_ma60",
};

const MODE_LABELS: Record<IvSkewMode, string> = {
  daily: "Daily",
  ma5: "MA5",
  ma20: "MA20",
  ma60: "MA60",
};

/** Unique sorted axis dates for the IV skew rows (shared with click handlers). */
export function ivSkewAxisDates(ivSkewRows: IvSkewRow[]): string[] {
  return Array.from(new Set(ivSkewRows.map((r) => r.date))).sort();
}

export function buildIvSkewTimeSeriesOption(
  ivSkewRows: IvSkewRow[],
  selectedDate: string,
  mode: IvSkewMode = "daily",
): EChartsOption {
  const themeMode = useStore.getState().themeMode;
  const c = axisColors(themeMode);
  const textColor = c.textColor;
  const splitColor = c.splitLineColor;

  if (ivSkewRows.length === 0) {
    return {
      backgroundColor: "transparent",
      title: {
        text: `IV Skew · 25Δ Risk Reversal (C−P) · ${MODE_LABELS[mode]}  [No data]`,
        left: "center",
        top: "center",
        textStyle: { color: textColor, fontSize: 11, fontWeight: 400 },
      },
    };
  }

  const field = RR_FIELD_MAP[mode];

  // Unique sorted dates across all expiry groups
  const dates = Array.from(new Set(ivSkewRows.map((r) => r.date))).sort();

  // Map date -> expiry_month -> RR value
  const valueMap = new Map<string, Map<string, number | null>>();
  for (const r of ivSkewRows) {
    if (!valueMap.has(r.date)) valueMap.set(r.date, new Map());
    const val = r[field];
    const numVal = typeof val === "number" ? val : val != null ? Number(val) : null;
    valueMap
      .get(r.date)!
      .set(r.expiry_month, numVal != null && Number.isFinite(numVal) ? numVal : null);
  }

  // Expiry months sorted by nearest to the selected date's month
  const expiryMonths = Array.from(new Set(ivSkewRows.map((r) => r.expiry_month))).sort(
    (a, b) => {
      const da = Math.abs(a.localeCompare(selectedDate.slice(0, 7)));
      const db = Math.abs(b.localeCompare(selectedDate.slice(0, 7)));
      return da - db;
    },
  );
  const nExpiries = expiryMonths.length;

  // Per-expiry RR series (thin reference lines)
  const expirySeries: EChartsOption["series"] = expiryMonths.map((ym, ei) => {
    const data: (number | null)[] = dates.map((d) => {
      const map = valueMap.get(d);
      if (!map) return null;
      const v = map.get(ym);
      return v != null && Number.isFinite(v) ? v : null;
    });
    const color = expiryBlueColor(ei, nExpiries);
    return {
      type: "line" as const,
      name: `${MODE_LABELS[mode]} ${ym}`,
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

  // Mean RR series across expiry groups (thick main curve)
  const meanData: (number | null)[] = dates.map((d) => {
    const map = valueMap.get(d);
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
    name: `${MODE_LABELS[mode]} Mean`,
    showSymbol: false,
    smooth: false,
    connectNulls: false,
    lineStyle: { color: IV_BLUE, width: 2.5, opacity: 0.95 },
    itemStyle: { color: IV_BLUE },
    data: meanData,
    z: 3,
  };

  // Zero line: RR = 0 means OTM calls and puts are equally priced
  const zeroLine = {
    type: "line" as const,
    name: "Zero",
    data: [],
    markLine: {
      symbol: ["none" as const, "none" as const],
      silent: true,
      lineStyle: { color: textColor, type: "dashed" as const, opacity: 0.3 },
      data: [{ yAxis: 0 }],
      tooltip: { show: false },
      label: { show: false },
    },
  };

  // Mark the selected date on the mean curve
  const selectedIdx = dates.indexOf(selectedDate);
  const markPointData: { name: string; coord: [string, number]; value: number }[] = [];
  if (selectedIdx >= 0 && meanData[selectedIdx] != null) {
    markPointData.push({
      name: "selected",
      coord: [selectedDate, meanData[selectedIdx] as number],
      value: meanData[selectedIdx] as number,
    });
  }

  const visibleLegendData = expiryMonths.map((ym) => `${MODE_LABELS[mode]} ${ym}`);
  visibleLegendData.unshift(`${MODE_LABELS[mode]} Mean`);

  return {
    backgroundColor: "transparent",
    animation: false,
    grid: commonGrid({ left: 56, right: 36, top: 30, bottom: 28 }),
    title: {
      text: `IV Skew · 25Δ Risk Reversal (C−P) · ${MODE_LABELS[mode]} · vol pts`,
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
        const p = params as Array<{
          seriesName: string;
          value: [string, number | null];
          componentType?: string;
        }>;
        if (!Array.isArray(p) || p.length === 0) return "";
        const date = p[0]?.value?.[0] ?? "";
        const lines: string[] = [date];
        for (const item of p) {
          if (item.componentType === "markLine") continue;
          const v = item.value?.[1];
          if (v != null && Number.isFinite(v)) {
            lines.push(`${item.seriesName}: ${fmtNum(v, 2)}`);
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
      data: dates,
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
      name: "RR 25Δ",
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
    dataZoom: commonDataZoom({ xAxisIndex: 0 }, 0, 100),
    series: [meanSeries, ...expirySeries, zeroLine],
  };
}
