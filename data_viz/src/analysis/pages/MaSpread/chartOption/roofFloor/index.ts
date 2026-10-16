/**
 * Roof/Floor Trendlines group (the row's period buttons) — window-field
 * picking, the rolling-envelope/trendline math, the group state builder,
 * the series, and the tooltip rows.
 */
export {
  ROOF_COLOR,
  FLOOR_COLOR,
} from "./colors";
export {
  EMPTY_ROOF_FLOOR_WIN,
  pickRoofFloorWinFields,
  type RoofFloorWinFields,
} from "./winFields";
export { trendlineSegment, rollingExtremes } from "./envelope";
export {
  buildRoofFloorState,
  type RoofFloorState,
  type RoofFloorAnchorItem,
  type AnchorMarkLine,
} from "./state";
export {
  buildRoofFloorEstimateState,
  type RoofFloorEstimateState,
  type EstimateMarkLine,
} from "./estimate";
export {
  aggregateReboundAudit,
  type ReboundAuditStats,
} from "./reboundAudit";
export {
  buildReboundAuditMarks,
  appendReboundAuditSeries,
  type ReboundAuditMarks,
  type ReboundMarkItem,
} from "./auditMarks";
export {
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
