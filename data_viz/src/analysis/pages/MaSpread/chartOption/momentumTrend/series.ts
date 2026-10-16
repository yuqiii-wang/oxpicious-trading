/**
 * Momentum overlay series + right axis — the Momentum section's
 * lookback-adaptive lines (fixed 5/20d anchors + adaptive short/mid/long)
 * replace the amt bars on the right axis, riding the derivTrend-style
 * band-offset mapping (signed ratios transformed into the [0,100] band
 * of the 0-400 axis so the lines hug the bottom quarter, away from the
 * price trend; zero pinned mid-band). The first line carries the dashed
 * zero guide. Axis tick labels read back in ORIGINAL units (percent).
 */
import { fmtPct } from "@/lib/series";
import type { AxisColors, SeriesList } from "../_common";
import type { MomentumTrendSpec } from "../_common";
import {
  DERIV_ZERO_POS,
  derivBandToOriginal,
  derivMaxAbsOf,
  toDerivBand,
} from "../derivTrend/scale";

/** The momentum lines on the offset right axis (z 3, band-mapped);
 *  the first line carries the dashed zero guide. */
export function appendMomentumTrendSeries(
  series: SeriesList,
  momentumTrend: MomentumTrendSpec,
  axisLineColor: string,
): void {
  const maxAbs = derivMaxAbsOf(momentumTrend.lines);
  momentumTrend.lines.forEach((l, li) => {
    series.push({
      type: "line",
      name: l.name,
      data: l.data.map((v) =>
        v != null && Number.isFinite(v) ? toDerivBand(v, maxAbs) : null,
      ),
      symbol: "none",
      yAxisIndex: 1,
      lineStyle: { color: l.color, width: 1.2, opacity: 0.85 },
      z: 3,
      clip: true,
      ...(li === 0
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
              data: [{ yAxis: DERIV_ZERO_POS }],
            },
          }
        : {}),
    });
  });
}

/** Band-offset momentum axis (the derivTrend overlay's pattern): fixed
 *  0-400 range so the transformed lines (band [0,100], zero pinned at
 *  50) hug the bottom quarter of the shared grid. Tick labels inside the
 *  band read back in ORIGINAL units — ±maxAbs at the band edges, 0 at
 *  center, formatted as percent (momentum is a signed fractional ratio);
 *  labels above 100 stay blank (the 100-400 stretch is reserved
 *  headroom, not data). */
export function buildMomentumRightAxis(c: AxisColors, maxAbs: number) {
  return {
    type: "value" as const,
    min: 0,
    max: 400,
    interval: 50,
    name: "Mom",
    nameTextStyle: { color: c.textColor, fontSize: 9 },
    axisLine: { lineStyle: { color: c.axisLineColor } },
    axisLabel: {
      color: c.textColor,
      fontSize: 9,
      formatter: (v: number) =>
        v <= 100
          ? fmtPct(derivBandToOriginal(v, maxAbs) * 100, 1)
          : "",
    },
    splitLine: { show: false },
  };
}
