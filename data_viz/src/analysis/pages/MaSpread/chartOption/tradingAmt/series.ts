/**
 * Trading-amount bar series (the header switch's "amt" position) —
 * lowkey up/down bars on the pair chart's secondary y-axis, plus the
 * "Amt (亿)" right axis they ride.
 */
import { fmtNum } from "@/lib/series";
import { UP_COLOR, DOWN_COLOR } from "@/theme/chart-palette";
import type { AxisColors, SeriesList } from "../_common";

/** Null-cleared trading-amount bar data. */
export function amtBarDataOf(
  tradingAmts: Array<number | null>,
): Array<number | null> {
  return tradingAmts.map((v) => (v != null && Number.isFinite(v) ? v : null));
}

/** Split the bars into up/down days — a price pair classifies by
 *  close ≥ open, an MA pair by short ≥ long; undated rows default up. */
export function splitAmtUpDown(
  barData: Array<number | null>,
  isPricePair: boolean,
  opens: Array<number | null>,
  shorts: Array<number | null>,
  longs: Array<number | null>,
): { upData: Array<number | null>; downData: Array<number | null> } {
  const n = barData.length;
  const upData: Array<number | null> = new Array(n).fill(null);
  const downData: Array<number | null> = new Array(n).fill(null);
  for (let i = 0; i < n; i++) {
    const amt = barData[i];
    if (amt == null) continue;
    let isUp: boolean;
    if (isPricePair) {
      const o = opens[i];
      const cl = shorts[i];
      isUp = o != null && cl != null ? cl >= o : true;
    } else {
      const s = shorts[i];
      const l = longs[i];
      isUp = s != null && l != null ? s >= l : true;
    }
    if (isUp) {
      upData[i] = amt;
    } else {
      downData[i] = amt;
    }
  }
  return { upData, downData };
}

/** The two lowkey stacked bar series on the secondary axis (z 0). */
export function appendAmtBarSeries(
  series: SeriesList,
  upData: Array<number | null>,
  downData: Array<number | null>,
): void {
  const barOpacity = 0.15;
  const barWidth = "50%";
  series.push({
    type: "bar",
    name: "Amt Up",
    data: upData,
    yAxisIndex: 1,
    barWidth,
    itemStyle: { color: UP_COLOR, opacity: barOpacity },
    z: 0,
    stack: "amt",
  });
  series.push({
    type: "bar",
    name: "Amt Down",
    data: downData,
    yAxisIndex: 1,
    barWidth,
    itemStyle: { color: DOWN_COLOR, opacity: barOpacity },
    z: 0,
    stack: "amt",
  });
}

/** The "Amt (亿)" right axis the amt bars ride (also the amt envelope's
 *  amount axis). */
export function buildAmtRightAxis(c: AxisColors) {
  return {
    type: "value" as const,
    scale: true,
    name: "Amt (亿)",
    nameTextStyle: { color: c.textColor, fontSize: 9 },
    axisLine: { lineStyle: { color: c.axisLineColor } },
    axisLabel: {
      color: c.textColor,
      fontSize: 9,
      formatter: (v: number) => fmtNum(v / 1e8, 1),
    },
    splitLine: { show: false },
  };
}
