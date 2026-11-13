/**
 * Per-window OHLC extrema field picking for the Roof/Floor Trendlines
 * row — the roof/floor window buttons (60…1260d) each read their own
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
    case 252:
      return {
        open: r.open_252d, high: r.high_252d, highDate: r.high_date_252d,
        high2nd: r.high_2nd_252d, high2ndDate: r.high_2nd_date_252d,
        highSlope: r.high_line_slope_252d,
        low: r.low_252d, lowDate: r.low_date_252d,
        low2nd: r.low_2nd_252d, low2ndDate: r.low_2nd_date_252d,
        lowSlope: r.low_line_slope_252d,
        changeSinceLastHigh: r.change_since_last_high_252d,
        daysSinceLastHigh: r.days_since_last_high_252d,
        changeSinceLastLow: r.change_since_last_low_252d,
        daysSinceLastLow: r.days_since_last_low_252d,
      };
    case 504:
      return {
        open: r.open_504d, high: r.high_504d, highDate: r.high_date_504d,
        high2nd: r.high_2nd_504d, high2ndDate: r.high_2nd_date_504d,
        highSlope: r.high_line_slope_504d,
        low: r.low_504d, lowDate: r.low_date_504d,
        low2nd: r.low_2nd_504d, low2ndDate: r.low_2nd_date_504d,
        lowSlope: r.low_line_slope_504d,
        changeSinceLastHigh: r.change_since_last_high_504d,
        daysSinceLastHigh: r.days_since_last_high_504d,
        changeSinceLastLow: r.change_since_last_low_504d,
        daysSinceLastLow: r.days_since_last_low_504d,
      };
    case 756:
      return {
        open: r.open_756d, high: r.high_756d, highDate: r.high_date_756d,
        high2nd: r.high_2nd_756d, high2ndDate: r.high_2nd_date_756d,
        highSlope: r.high_line_slope_756d,
        low: r.low_756d, lowDate: r.low_date_756d,
        low2nd: r.low_2nd_756d, low2ndDate: r.low_2nd_date_756d,
        lowSlope: r.low_line_slope_756d,
        changeSinceLastHigh: r.change_since_last_high_756d,
        daysSinceLastHigh: r.days_since_last_high_756d,
        changeSinceLastLow: r.change_since_last_low_756d,
        daysSinceLastLow: r.days_since_last_low_756d,
      };
    case 1260:
      return {
        open: r.open_1260d, high: r.high_1260d, highDate: r.high_date_1260d,
        high2nd: r.high_2nd_1260d, high2ndDate: r.high_2nd_date_1260d,
        highSlope: r.high_line_slope_1260d,
        low: r.low_1260d, lowDate: r.low_date_1260d,
        low2nd: r.low_2nd_1260d, low2ndDate: r.low_2nd_date_1260d,
        lowSlope: r.low_line_slope_1260d,
        changeSinceLastHigh: r.change_since_last_high_1260d,
        daysSinceLastHigh: r.days_since_last_high_1260d,
        changeSinceLastLow: r.change_since_last_low_1260d,
        daysSinceLastLow: r.days_since_last_low_1260d,
      };
    default:
      return EMPTY_ROOF_FLOOR_WIN;
  }
}
