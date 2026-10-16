/**
 * Roof/floor group series — pushed in the pair chart's original layer
 * order: the rolling envelope + trendlines + anchor markers after the
 * Bollinger block (z 4 / z 12 / z 13).
 */
import type { SeriesList } from "../_common";
import type { RoofFloorState } from "./state";
import { ROOF_COLOR, FLOOR_COLOR } from "./colors";

/** The rolling window bound envelope (dashed, subtle — visualizes the
 *  enabled period and bounds every bar in it), the roof/floor
 *  trendlines (drawn from the earlier anchor to the clicked date), and
 *  the anchor markers (the four extrema that determine the two lines). */
export function appendRoofFloorTrendSeries(
  series: SeriesList,
  state: RoofFloorState,
): void {
  if (!(state.rfAligned && state.rfW != null)) return;
  const rfW = state.rfW;
  series.push({
    type: "line",
    name: `High(${rfW}d)`,
    data: state.roofHighLine,
    symbol: "none",
    lineStyle: { color: ROOF_COLOR, width: 1, type: "dashed", opacity: 0.55 },
    z: 4,
    clip: true,
  });
  series.push({
    type: "line",
    name: `Low(${rfW}d)`,
    data: state.floorLowLine,
    symbol: "none",
    lineStyle: { color: FLOOR_COLOR, width: 1, type: "dashed", opacity: 0.55 },
    z: 4,
    clip: true,
  });

  // Roof trendline — the line determined by the window's top + 2nd highs,
  // drawn from the earlier anchor to the clicked date (stops there).
  if (state.roofSeg != null) {
    series.push({
      type: "line",
      name: `Roof(${rfW}d)`,
      data: state.roofSeg,
      symbol: "none",
      lineStyle: { color: ROOF_COLOR, width: 1.8 },
      z: 12,
      clip: true,
    });
  }
  // Floor trendline — the line determined by the window's top + 2nd lows.
  if (state.floorSeg != null) {
    series.push({
      type: "line",
      name: `Floor(${rfW}d)`,
      data: state.floorSeg,
      symbol: "none",
      lineStyle: { color: FLOOR_COLOR, width: 1.8 },
      z: 12,
      clip: true,
    });
  }
  if (state.anchorItems.length > 0) {
    series.push({
      type: "scatter",
      name: "_roofFloorAnchors",
      data: state.anchorItems,
      symbolSize: 9,
      z: 13,
      tooltip: { show: false },
    });
  }
}
