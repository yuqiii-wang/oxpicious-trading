/**
 * MaSpread button-group ids.
 *
 * The group titles and description texts live in static.docs
 * (`ui.ma-spread.group.<id>` — seeded by
 * database/sql/static/docs/static/10_ui_ma_spread.sql, the source of
 * record); SectionLabel resolves them by id. This module is the stable
 * id union only — a new group means a new doc row + a member here.
 */

/** Union of every MA-Spread button-group id (one doc row per id). */
export type MaSpreadGroupId =
  | "pairsSimple"
  | "pairsEma"
  | "tradingAmt"
  | "rsiTrends"
  | "momentum"
  | "roofFloorWindow"
  | "highLowStreaks"
  | "pxVolStates"
  | "ohlcGaps"
  | "marketRegimes"
  | "trendBands";
