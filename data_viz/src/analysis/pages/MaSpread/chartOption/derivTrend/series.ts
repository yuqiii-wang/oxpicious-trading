/**
 * Slopes/Curvatures series + right axis — the family's derivative lines
 * (Price + MA5/20/60, or Price + EMA6/20/60 while an EMA pair is
 * selected) replace the amt bars on the right axis, riding the RSI-style
 * band-offset mapping (values transformed into the [0,100] band of the
 * 0-400 axis so the lines hug the bottom quarter, away from the price
 * trend). The first line carries a dashed zero guide at the pinned zero
 * position, mirroring the RSI overlay's 70/30 guides. The tooltip reads
 * the RAW spec values — only the drawn series carry the transform.
 */
import { fmtNum } from "@/lib/series";
import type { AxisColors, SeriesList } from "../_common";
import type { DerivTrendSpec } from "../_common/types";
import { DERIV_ZERO_POS, derivBandToOriginal, toDerivBand } from "./scale";

/** The derivative lines on the offset right axis (z 3, band-mapped);
 *  the first line carries the dashed zero guide. */
export function appendDerivTrendSeries(
  series: SeriesList,
  derivTrend: DerivTrendSpec,
  maxAbs: number,
  axisLineColor: string,
): void {
  derivTrend.lines.forEach((l, li) => {
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

/** Band-offset derivative axis (the RSI overlay's pattern): fixed 0-400
 *  range so the transformed lines (band [0,100], zero pinned at 50) hug
 *  the bottom quarter of the shared grid, away from the price trend.
 *  Tick labels inside the band read back in ORIGINAL units —
 *  ±maxAbs at the band edges, 0 at center; labels above 100 stay blank
 *  (the 100-400 stretch is reserved headroom, not data). */
export function buildDerivRightAxis(
  c: AxisColors,
  mode: "slope" | "curvature",
  maxAbs: number,
) {
  return {
    type: "value" as const,
    min: 0,
    max: 400,
    interval: 50,
    name: mode === "slope" ? "Slope" : "Curvature",
    nameTextStyle: { color: c.textColor, fontSize: 9 },
    axisLine: { lineStyle: { color: c.axisLineColor } },
    axisLabel: {
      color: c.textColor,
      fontSize: 9,
      formatter: (v: number) =>
        v <= 100 ? fmtNum(derivBandToOriginal(v, maxAbs)) : "",
    },
    splitLine: { show: false },
  };
}
