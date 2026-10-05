/**
 * Per-window OHLC extrema field picking for the Roof/Floor Trendlines
 * row — the roof/floor window buttons (60…1275d) each read their own
 * suffixed columns off a MovAveSpreadOhlcRow.
 */
import type { MovAveSpreadOhlcRow } from "@shared/types";

/** Per-window OHLC extrema fields picked from a MovAveSpreadOhlcRow. */
export interface RoofFloorWinFields {
  open: number | null;
  high: number | null;
  highDate: string | null;
  high2nd: number | null;
  high2ndDate: string | null;
  highSlope: number | null;
  low: number | null;
  lowDate: string | null;
  low2nd: number | null;
  low2ndDate: string | null;
  lowSlope: number | null;
  /** close minus the LAST high anchor's value (the later-dated of the
   *  window's two high anchors — the 2nd whenever present, else the
   *  top); sign = direction since it. */
  changeSinceLastHigh: number | null;
  /** Trading days between `date` and that last high anchor date. */
  daysSinceLastHigh: number | null;
  /** close minus the LAST low anchor's value (the mirror on the low
   *  side); sign = direction since it. */
  changeSinceLastLow: number | null;
  /** Trading days between `date` and that last low anchor date. */
  daysSinceLastLow: number | null;
}

export const EMPTY_ROOF_FLOOR_WIN: RoofFloorWinFields = {
  open: null,
  high: null,
  highDate: null,
  high2nd: null,
  high2ndDate: null,
  highSlope: null,
  low: null,
  lowDate: null,
  low2nd: null,
  low2ndDate: null,
  lowSlope: null,
  changeSinceLastHigh: null,
  daysSinceLastHigh: null,
  changeSinceLastLow: null,
  daysSinceLastLow: null,
};

/** Pick the 15 OHLC extrema fields for window `w` from one extrema row. */
export function pickRoofFloorWinFields(r: MovAveSpreadOhlcRow, w: number): RoofFloorWinFields {
  switch (w) {
    case 60:
      return {
        open: r.open_60d, high: r.high_60d, highDate: r.high_date_60d,
        high2nd: r.high_2nd_60d, high2ndDate: r.high_2nd_date_60d,
        highSlope: r.high_line_slope_60d,
        low: r.low_60d, lowDate: r.low_date_60d,
        low2nd: r.low_2nd_60d, low2ndDate: r.low_2nd_date_60d,
        lowSlope: r.low_line_slope_60d,
        changeSinceLastHigh: r.change_since_last_high_60d,
        daysSinceLastHigh: r.days_since_last_high_60d,
        changeSinceLastLow: r.change_since_last_low_60d,
        daysSinceLastLow: r.days_since_last_low_60d,
      };
    case 120:
      return {
        open: r.open_120d, high: r.high_120d, highDate: r.high_date_120d,
        high2nd: r.high_2nd_120d, high2ndDate: r.high_2nd_date_120d,
        highSlope: r.high_line_slope_120d,
        low: r.low_120d, lowDate: r.low_date_120d,
        low2nd: r.low_2nd_120d, low2ndDate: r.low_2nd_date_120d,
        lowSlope: r.low_line_slope_120d,
        changeSinceLastHigh: r.change_since_last_high_120d,
        daysSinceLastHigh: r.days_since_last_high_120d,
        changeSinceLastLow: r.change_since_last_low_120d,
        daysSinceLastLow: r.days_since_last_low_120d,
      };
    case 255:
      return {
        open: r.open_255d, high: r.high_255d, highDate: r.high_date_255d,
        high2nd: r.high_2nd_255d, high2ndDate: r.high_2nd_date_255d,
        highSlope: r.high_line_slope_255d,
        low: r.low_255d, lowDate: r.low_date_255d,
        low2nd: r.low_2nd_255d, low2ndDate: r.low_2nd_date_255d,
        lowSlope: r.low_line_slope_255d,
        changeSinceLastHigh: r.change_since_last_high_255d,
        daysSinceLastHigh: r.days_since_last_high_255d,
        changeSinceLastLow: r.change_since_last_low_255d,
        daysSinceLastLow: r.days_since_last_low_255d,
      };
    case 500:
      return {
        open: r.open_500d, high: r.high_500d, highDate: r.high_date_500d,
        high2nd: r.high_2nd_500d, high2ndDate: r.high_2nd_date_500d,
        highSlope: r.high_line_slope_500d,
        low: r.low_500d, lowDate: r.low_date_500d,
        low2nd: r.low_2nd_500d, low2ndDate: r.low_2nd_date_500d,
        lowSlope: r.low_line_slope_500d,
        changeSinceLastHigh: r.change_since_last_high_500d,
        daysSinceLastHigh: r.days_since_last_high_500d,
        changeSinceLastLow: r.change_since_last_low_500d,
        daysSinceLastLow: r.days_since_last_low_500d,
      };
    case 750:
      return {
        open: r.open_750d, high: r.high_750d, highDate: r.high_date_750d,
        high2nd: r.high_2nd_750d, high2ndDate: r.high_2nd_date_750d,
        highSlope: r.high_line_slope_750d,
        low: r.low_750d, lowDate: r.low_date_750d,
        low2nd: r.low_2nd_750d, low2ndDate: r.low_2nd_date_750d,
        lowSlope: r.low_line_slope_750d,
        changeSinceLastHigh: r.change_since_last_high_750d,
        daysSinceLastHigh: r.days_since_last_high_750d,
        changeSinceLastLow: r.change_since_last_low_750d,
        daysSinceLastLow: r.days_since_last_low_750d,
      };
    case 1275:
      return {
        open: r.open_1275d, high: r.high_1275d, highDate: r.high_date_1275d,
        high2nd: r.high_2nd_1275d, high2ndDate: r.high_2nd_date_1275d,
        highSlope: r.high_line_slope_1275d,
        low: r.low_1275d, lowDate: r.low_date_1275d,
        low2nd: r.low_2nd_1275d, low2ndDate: r.low_2nd_date_1275d,
        lowSlope: r.low_line_slope_1275d,
        changeSinceLastHigh: r.change_since_last_high_1275d,
        daysSinceLastHigh: r.days_since_last_high_1275d,
        changeSinceLastLow: r.change_since_last_low_1275d,
        daysSinceLastLow: r.days_since_last_low_1275d,
      };
    default:
      return EMPTY_ROOF_FLOOR_WIN;
  }
}
