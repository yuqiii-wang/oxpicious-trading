/**
 * RSI Trends series + right axis — the six Wilder-RSI window lines ride
 * the offset right axis (0-400 → RSI 0-100 = bottom quarter of the
 * grid), with dashed 70/30 overbought/oversold guides on the first
 * series (the tooltip's RSI row uses the same thresholds).
 */
import type { AxisColors, SeriesList } from "../_common";
import type { RsiTrendLine } from "./build";

/** The RSI lines on the offset right axis (z 3); the first line
 *  carries the 70/30 guides. */
export function appendRsiTrendSeries(
  series: SeriesList,
  lines: RsiTrendLine[],
  axisLineColor: string,
): void {
  lines.forEach((s, si) => {
    series.push({
      type: "line",
      name: s.name,
      data: s.data,
      symbol: "none",
      yAxisIndex: 1,
      lineStyle: { color: s.color, width: 1.2, opacity: 0.85 },
      z: 3,
      clip: true,
      ...(si === 0
        ? {
            markLine: {
              silent: true as const,
              symbol: ["none", "none"] as [string, string],
              label: { show: false },
              lineStyle: {
                type: "dashed" as const,
                color: axisLineColor,
                width: 1,
                opacity: 0.55,
              },
              data: [{ yAxis: 70 }, { yAxis: 30 }],
            },
          }
        : {}),
    });
  });
}

/** Band-offset RSI axis: 0-400 so the meaningful 0-100 range hugs the
 *  bottom quarter of the shared grid. Labels above 100 stay blank (the
 *  150-400 stretch is reserved headroom, not data); 0/50/100 read like
 *  a normal RSI scale. */
export function buildRsiRightAxis(c: AxisColors) {
  return {
    type: "value" as const,
    min: 0,
    max: 400,
    interval: 50,
    name: "RSI",
    nameTextStyle: { color: c.textColor, fontSize: 9 },
    axisLine: { lineStyle: { color: c.axisLineColor } },
    axisLabel: {
      color: c.textColor,
      fontSize: 9,
      formatter: (v: number) => (v <= 100 ? String(v) : ""),
    },
    splitLine: { show: false },
  };
}
