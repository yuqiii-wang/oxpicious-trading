/**
 * Slopes/Curvatures group (the header switch's "slope"/"curv"
 * positions) — the band-offset scale, the offset-right-axis series +
 * axis, and the tooltip readout row.
 */
export {
  DERIV_ZERO_POS,
  derivMaxAbsOf,
  toDerivBand,
  derivBandToOriginal,
} from "./scale";
export { appendDerivTrendSeries, buildDerivRightAxis } from "./series";
export { appendDerivTrendRows } from "./tooltipRows";
