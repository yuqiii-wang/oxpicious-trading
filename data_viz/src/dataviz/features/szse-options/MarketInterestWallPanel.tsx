/**
 * Market Interest Wall — single horizontal stacked bar chart with date selector.
 *
 * Per-strike OI stacked by expiry month, calls to the right (positive) and puts
 * to the left (negative). Spot/max-pain/OI-weighted strike lines overlaid as
 * horizontal markLines.
 *
 * Mirrors plot_market_interest_evolution() in plot_szse_options.py.
 */

import ChartCard from "@/components/ChartCard";
import EChart from "@/components/EChart";
import { useStore } from "@/store/filters";
import type { OptionsRow } from "@shared/types";
import {
  DOWN_COLOR,
  MA20_COLOR,
  MA60_COLOR,
  MUTED_INLINE_COLOR,
  PRICE_SCALE,
  SPOT_COLOR,
  UP_COLOR,
  axisColors,
  commonLegend,
  commonGrid,
  expiryBlueColor,
} from "@/theme/chart-palette";
import { computeSnapshotStats } from "@/lib/options-stats";
import { fmtPct, fmtMil, fmtNum } from "@/lib/series";
import { makeWallTooltipFormatter } from "./marketInterestTooltip";
import type { EChartsOption } from "echarts";

interface Props {
  rows: OptionsRow[];
  selectedDate: string;
}

function expirySortKey(em: string): number {
  return parseInt(em.replace("月", ""));
}

/**
 * Collect unified strike list across all snapshots so each subplot uses the
 * same y-axis (matches the Python implementation).
 */
function collectUnifiedStrikes(snapshots: OptionsRow[][]): number[] {
  const set = new Set<number>();
  for (const snap of snapshots) {
    for (const r of snap) set.add(r.strike_price);
  }
  return Array.from(set).sort((a, b) => a - b);
}

/**
 * Collect all expiry months across all snapshots and assign global colors
 * using a blue gradient palette (dark = nearest, light = farthest).
 */
function buildExpiryColorMap(snapshots: OptionsRow[][]): Map<string, string> {
  const all = new Set<string>();
  for (const snap of snapshots) {
    for (const r of snap) all.add(r.expiry_month);
  }
  const sorted = Array.from(all).sort((a, b) => expirySortKey(a) - expirySortKey(b));
  const n = sorted.length;
  const map = new Map<string, string>();
  sorted.forEach((em, i) => {
    map.set(em, expiryBlueColor(i, n));
  });
  return map;
}

function findClosestStrikeIdx(unifiedStrikes: number[], target: number): number {
  return unifiedStrikes.reduce(
    (best, k, i) => (Math.abs(k - target) < Math.abs(unifiedStrikes[best] - target) ? i : best),
    0,
  );
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type MarkLineDataItem = any;

function buildWallOption(
  snap: OptionsRow[],
  unifiedStrikes: number[],
  expiryColorMap: Map<string, string>,
  label: string,
  dateStr: string,
): EChartsOption {
  const themeMode = useStore.getState().themeMode;
  const c = axisColors(themeMode);
  const textColor = c.textColor;
  const splitColor = c.splitLineColor;
  const tooltipBg = c.tooltipBg;

  const stats = computeSnapshotStats(snap);
  if (!stats) {
    return {
      backgroundColor: "transparent",
      title: {
        text: `${label}\n[No data]`,
        left: "center",
        top: "center",
        textStyle: { color: textColor, fontSize: 11, fontWeight: 400 },
      },
    };
  }

  // Per-expiry OI by strike
  const expiryMonths = Array.from(new Set(snap.map((r) => r.expiry_month))).sort(
    (a, b) => expirySortKey(a) - expirySortKey(b),
  );

  const series: EChartsOption["series"] = [];
  expiryMonths.forEach((em) => {
    const emSnap = snap.filter((r) => r.expiry_month === em);
    const callOiByK = new Map<number, number>();
    const putOiByK = new Map<number, number>();
    for (const r of emSnap) {
      if (r.option_type === "CALL") {
        callOiByK.set(r.strike_price, (callOiByK.get(r.strike_price) ?? 0) + r.open_interest);
      } else {
        putOiByK.set(r.strike_price, (putOiByK.get(r.strike_price) ?? 0) + r.open_interest);
      }
    }
    const color = expiryColorMap.get(em) ?? MUTED_INLINE_COLOR;

    // Call OI to the right (positive) — all call series share stack "call"
    series.push({
      type: "bar",
      name: `${em} C`,
      stack: "call",
      data: unifiedStrikes.map((k) => callOiByK.get(k) ?? 0),
      itemStyle: { color, opacity: 0.78 },
      emphasis: { focus: "series" },
    });
    // Put OI to the left (negative) — all put series share stack "put"
    series.push({
      type: "bar",
      name: `${em} P`,
      stack: "put",
      data: unifiedStrikes.map((k) => -(putOiByK.get(k) ?? 0)),
      itemStyle: { color, opacity: 0.78 },
      emphasis: { focus: "series" },
    });
  });

  // MarkLines for Call Wall / Put Wall / Max Pain / OI-weighted.
  // Using markLine (not separate line series) avoids distorting the value xAxis.
  const markLineData: MarkLineDataItem[] = [];

  // Spot: scatter series with single dot at 0 OI x-axis position
  const spotIdx = findClosestStrikeIdx(unifiedStrikes, stats.S_raw);
  const spotStrike = fmtNum(unifiedStrikes[spotIdx] / PRICE_SCALE);
  series.push({
    type: "scatter",
    name: "Spot",
    data: [{ value: [0, spotStrike] }],
    symbol: "circle",
    symbolSize: 6,
    itemStyle: { color: SPOT_COLOR, opacity: 0.8 },
    zlevel: 10,
    silent: true,
  });

  // Wall dominance: only display the dominant wall when one side's wall OI is
  // more than 33% larger than the other (ratio > 1.33). When both walls are
  // comparable (within 33%), display both — the market is balanced.
  const WALL_DOMINANCE_RATIO = 1.33;
  let showCallWall = stats.callWall != null;
  let showPutWall = stats.putWall != null;
  if (showCallWall && showPutWall) {
    if (stats.callWallOi >= stats.putWallOi * WALL_DOMINANCE_RATIO) {
      showPutWall = false; // call wall dominates
    } else if (stats.putWallOi >= stats.callWallOi * WALL_DOMINANCE_RATIO) {
      showCallWall = false; // put wall dominates
    }
  }

  if (showCallWall && stats.callWall != null) {
    const cwIdx = findClosestStrikeIdx(unifiedStrikes, stats.callWall);
    markLineData.push({
      yAxis: cwIdx,
      label: {
        show: true,
        position: "middle",
        formatter: "Call Wall",
        color: UP_COLOR,
        fontSize: 8,
      },
      lineStyle: { color: UP_COLOR, type: "dashed" as const, width: 1.0, opacity: 0.35 },
    });
  }
  if (showPutWall && stats.putWall != null) {
    const pwIdx = findClosestStrikeIdx(unifiedStrikes, stats.putWall);
    markLineData.push({
      yAxis: pwIdx,
      label: {
        show: true,
        position: "middle",
        formatter: "Put Wall",
        color: DOWN_COLOR,
        fontSize: 8,
      },
      lineStyle: { color: DOWN_COLOR, type: "dashed" as const, width: 1.0, opacity: 0.35 },
    });
  }
  if (stats.maxPain != null) {
    const mpIdx = findClosestStrikeIdx(unifiedStrikes, stats.maxPain);
    markLineData.push({
      yAxis: mpIdx,
      label: { show: false },
      lineStyle: { color: MA60_COLOR, type: "dashed" as const, width: 0.9, opacity: 0.28 },
    });
  }
  if (stats.oiWeighted != null) {
    const owIdx = findClosestStrikeIdx(unifiedStrikes, stats.oiWeighted);
    markLineData.push({
      yAxis: owIdx,
      label: { show: false },
      lineStyle: { color: MA20_COLOR, type: "dotted" as const, width: 0.9, opacity: 0.28 },
    });
  }

  if (series.length > 0 && markLineData.length > 0) {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (series[series.length - 1] as any).markLine = {
      symbol: ["none", "none"],
      silent: true,
      data: markLineData,
    };
  }

  const gexStr = (stats.netGex >= 0 ? "+" : "") + fmtMil(stats.netGex);
  const ivStr = stats.atmIv != null ? fmtPct(stats.atmIv * 100) : "—";
  const skStr =
    stats.ivSkew != null && Number.isFinite(stats.ivSkew)
      ? (stats.ivSkew * 100 >= 0 ? "+" : "") + fmtPct(stats.ivSkew * 100)
      : "—";
  const cwStr = stats.callWall != null ? fmtNum(stats.callWall / PRICE_SCALE) : "—";
  const pwStr = stats.putWall != null ? fmtNum(stats.putWall / PRICE_SCALE) : "—";

  return {
    backgroundColor: "transparent",
    animation: false,
    grid: commonGrid({ left: 70, right: 20, top: 50, bottom: 36 }),
    title: {
      text: `${label}  (${dateStr})  Spot=${fmtNum(stats.S)}  CW=${cwStr}  PW=${pwStr}  P/C=${fmtNum(stats.pcRatio)}  IV=${ivStr}  Skew=${skStr}  GEX=${gexStr}`,
      left: "left",
      textStyle: { color: textColor, fontSize: 10, fontFamily: "monospace" },
    },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      backgroundColor: tooltipBg,
      borderColor: splitColor,
      textStyle: { color: textColor, fontSize: 10 },
      formatter: makeWallTooltipFormatter(unifiedStrikes),
    },
    legend: commonLegend(themeMode, { top: 22, type: "scroll" }),
    xAxis: {
      type: "value",
      name: "OI (contracts)  Call→ | ←Put",
      nameLocation: "middle",
      nameGap: 24,
      nameTextStyle: { color: textColor, fontSize: 9 },
      axisLine: { lineStyle: { color: textColor } },
      axisLabel: {
        color: textColor,
        fontSize: 9,
        formatter: (v: number) => {
          const a = Math.abs(v);
          if (a >= 1e6) return fmtMil(v);
          if (a >= 1000) return fmtNum(v / 1000) + "K";
          return fmtNum(v);
        },
      },
      splitLine: { lineStyle: { color: splitColor, type: "dashed", opacity: 0.4 } },
    },
    yAxis: {
      type: "category",
      data: unifiedStrikes.map((k) => fmtNum(k / PRICE_SCALE)),
      inverse: true,
      axisLine: { lineStyle: { color: textColor } },
      axisLabel: { color: textColor, fontSize: 8 },
      splitLine: { show: false },
    },
    series,
  };
}

export default function MarketInterestWallPanel({ rows, selectedDate }: Props) {
  // Build unified strikes + expiry color map from ALL rows so the y-axis and
  // colors stay stable as the user changes the selected date.
  const allDatesSnap = rows;
  const unifiedStrikes = collectUnifiedStrikes([allDatesSnap]);
  const expiryColorMap = buildExpiryColorMap([allDatesSnap]);
  const snap = rows.filter((r) => r.date === selectedDate);
  const selectedOption = buildWallOption(
    snap,
    unifiedStrikes,
    expiryColorMap,
    "Market Interest Wall",
    selectedDate,
  );

  return (
    <ChartCard
      title="Market Interest Wall (by Expiry)"
      subtitle="OI by strike, stacked by expiry · Call → (right) · Put ← (left) · Blue gradient: dark=near expiry, light=far expiry · Spot / Call Wall / Put Wall / MaxPain / OI-weighted lines"
      height={420}
    >
      <EChart option={selectedOption} height={400} />
    </ChartCard>
  );
}
