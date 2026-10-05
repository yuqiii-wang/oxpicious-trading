/**
 * Roof/floor group series — pushed in the pair chart's original layer
 * order: the shade carriers + band-event markers right after the gap
 * fill (z 1 carriers / z 13-14 markers), the rolling envelope +
 * trendlines + anchor markers after the Bollinger block (z 4 / z 12 /
 * z 13).
 */
import type { SeriesList } from "../_common";
import { shadeOverlaySeries } from "../_common/shadeOverlaySeries";
import { STREAK_HIGH_ACCENT_COLOR } from "@/shared/charts/streakBands";
import type { RoofFloorState } from "./state";
import {
  ROOF_COLOR,
  FLOOR_COLOR,
  OHLC_SHADE_TOP_NAME,
  OHLC_SHADE_BOTTOM_NAME,
} from "./colors";

/** The two per-side shade carrier series (rect legend marker in the
 *  side's accent color; toggling a side hides its windows' rects) plus
 *  the four band-event scatters — SOLID triangle = bounce (rejection
 *  off the band), NAIL pin = breach (a directional entry into the band
 *  from outside: up through the roof's drawn edge, down through the
 *  floor's — the pin's TIP points in the break's direction and the pin
 *  is 2x the bounce mark's height, so it reads as nailed through the
 *  band edge). Underscore names keep the event markers out of the
 *  legend (the "shade" chip is the sole control). */
export function appendRoofFloorShadeSeries(
  series: SeriesList,
  state: RoofFloorState,
): void {
  if (state.shadeTopData.length > 0) {
    series.push(shadeOverlaySeries({
      name: OHLC_SHADE_TOP_NAME,
      data: state.shadeTopData,
      accent: STREAK_HIGH_ACCENT_COLOR,
      z: 1,
    }));
  }
  if (state.shadeBottomData.length > 0) {
    series.push(shadeOverlaySeries({
      name: OHLC_SHADE_BOTTOM_NAME,
      data: state.shadeBottomData,
      accent: FLOOR_COLOR,
      z: 1,
    }));
  }
  if (state.events.bounceUp.length > 0) {
    series.push({
      type: "scatter",
      name: "_bandBounceUp",
      data: state.events.bounceUp,
      symbol: "triangle",
      symbolSize: 8,
      symbolRotate: 0,
      itemStyle: { color: STREAK_HIGH_ACCENT_COLOR },
      z: 14,
      tooltip: { show: false },
    });
  }
  if (state.events.bounceDn.length > 0) {
    series.push({
      type: "scatter",
      name: "_bandBounceDn",
      data: state.events.bounceDn,
      symbol: "triangle",
      symbolSize: 8,
      symbolRotate: 180,
      itemStyle: { color: FLOOR_COLOR },
      z: 14,
      tooltip: { show: false },
    });
  }
  if (state.events.breachUp.length > 0) {
    series.push({
      type: "scatter",
      name: "_bandBreachUp",
      data: state.events.breachUp,
      symbol: "pin",
      // 2x the bounce marker's 8px height — the nail visibly pierces the edge
      symbolSize: [10, 16],
      symbolRotate: 180, // default pin tip points down; breachUp nails UP through the roof edge
      itemStyle: { color: STREAK_HIGH_ACCENT_COLOR },
      z: 13,
      tooltip: { show: false },
    });
  }
  if (state.events.breachDn.length > 0) {
    series.push({
      type: "scatter",
      name: "_bandBreachDn",
      data: state.events.breachDn,
      symbol: "pin",
      // 2x the bounce marker's 8px height — the nail visibly pierces the edge
      symbolSize: [10, 16],
      symbolRotate: 0, // tip points down, nailing DOWN through the floor edge
      itemStyle: { color: FLOOR_COLOR },
      z: 13,
      tooltip: { show: false },
    });
  }
}

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
