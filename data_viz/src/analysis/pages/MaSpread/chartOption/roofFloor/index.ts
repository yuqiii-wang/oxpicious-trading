/**
 * Roof/Floor Trendlines group (the row's period buttons + the "shade"
 * toggle) — window-field picking, the rolling-envelope/trendline math,
 * the extreme↔2nd-extreme shade zones, the band event markers, the
 * group state builder, the series, and the tooltip rows.
 */
export {
  ROOF_COLOR,
  FLOOR_COLOR,
  OHLC_SHADE_TOP_FILL,
  OHLC_SHADE_BOTTOM_FILL,
  OHLC_SHADE_TOP_NAME,
  OHLC_SHADE_BOTTOM_NAME,
} from "./colors";
export {
  EMPTY_ROOF_FLOOR_WIN,
  pickRoofFloorWinFields,
  type RoofFloorWinFields,
} from "./winFields";
export { trendlineSegment, rollingExtremes } from "./envelope";
export { buildExtremeShades, type ExtremeShades } from "./shades";
export {
  readBandEvents,
  type BandEventItems,
} from "./eventMarkers";
export {
  buildRoofFloorState,
  type RoofFloorState,
  type RoofFloorAnchorItem,
  type AnchorMarkLine,
} from "./state";
export {
  appendRoofFloorShadeSeries,
  appendRoofFloorTrendSeries,
} from "./series";
export {
  changeSinceLowName,
  changeSinceHighName,
  appendChangeSinceSeries,
  buildChangeSinceRightAxis,
  changeSinceMaxAbsOf,
} from "./changeSince";
export { appendRoofFloorRows } from "./tooltipRows";
