/**
 * MA-Spread OHLC extras groups — the "ohlc" group (the FULL 7-window
 * rolling-extrema pivot from analysis.mov_ave_spreads_roof_and_floor_lines,
 * feeding the panel's Roof/Floor Trendlines row) and the "gaps" group (the
 * daily OHLC gap anatomy + pattern block).
 * Split out of the former monolithic mov-ave-spreads.ts.
 */
import { formatDate, toNum } from "@api/lib/db";
import type { QueryResultRow } from "pg";
import type {
  MovAveSpreadOhlcGapDay,
  MovAveSpreadOhlcRow,
} from "@shared/types";
import {
  ROOF_FLOOR_PERIODS,
  ohlcPivotCteSql,
  ohlcPivotSelectSql,
} from "./ohlc-pivot.js";

/** One row of the "ohlc" extras group: the full 7-window rolling extrema
 *  (anchors + dates + line slopes + change/days since the last extreme)
 *  from analysis.mov_ave_spreads_roof_and_floor_lines, pivoted back to the
 *  per-window names MovAveSpreadOhlcRow carries. */
export interface DbExtremaRow extends QueryResultRow {
  date: Date | string;
  [column: string]: unknown;
}

/** SQL for the "ohlc" extras group: the FULL 7-window extrema pivot driven
 *  off the detail rows' dates (index-aligned with the chart response;
 *  sec_type binds through the $1 parameter at call time). */
export function buildOhlcExtrasSql(): string {
  return `
    WITH d AS MATERIALIZED (
      SELECT * FROM analysis.mov_ave_spreads_detail
      WHERE sec_type = $1
        AND code = ANY($2::text[])
    ),
    ${ohlcPivotCteSql(ROOF_FLOOR_PERIODS)}
    SELECT
      d.date,
      ${ohlcPivotSelectSql(ROOF_FLOOR_PERIODS, "")}
    FROM d
    LEFT JOIN ohlc ON ohlc.code = d.code AND ohlc.date = d.date
    ORDER BY d.date ASC
  `;
}

/** Map one DB extrema row to a top-level ohlc row (all 7 windows).
 *  Used to build the "ohlc" extras group — ONE copy per date shared by all
 *  pair series (instead of fanning the extrema out into every pair's rows,
 *  which would multiply the payload by the pair count). */
export function toOhlcExtremaRow(r: DbExtremaRow): MovAveSpreadOhlcRow {
  const d = (v: unknown): string | null =>
    v != null ? formatDate(v) : null;
  return {
    date: formatDate(r.date),
    open_60d: toNum(r.open_60d),
    high_60d: toNum(r.high_60d),
    high_date_60d: d(r.high_date_60d),
    high_2nd_60d: toNum(r.high_2nd_60d),
    high_2nd_date_60d: d(r.high_2nd_date_60d),
    low_60d: toNum(r.low_60d),
    low_date_60d: d(r.low_date_60d),
    low_2nd_60d: toNum(r.low_2nd_60d),
    low_2nd_date_60d: d(r.low_2nd_date_60d),
    open_120d: toNum(r.open_120d),
    high_120d: toNum(r.high_120d),
    high_date_120d: d(r.high_date_120d),
    high_2nd_120d: toNum(r.high_2nd_120d),
    high_2nd_date_120d: d(r.high_2nd_date_120d),
    low_120d: toNum(r.low_120d),
    low_date_120d: d(r.low_date_120d),
    low_2nd_120d: toNum(r.low_2nd_120d),
    low_2nd_date_120d: d(r.low_2nd_date_120d),
    open_255d: toNum(r.open_255d),
    high_255d: toNum(r.high_255d),
    high_date_255d: d(r.high_date_255d),
    high_2nd_255d: toNum(r.high_2nd_255d),
    high_2nd_date_255d: d(r.high_2nd_date_255d),
    low_255d: toNum(r.low_255d),
    low_date_255d: d(r.low_date_255d),
    low_2nd_255d: toNum(r.low_2nd_255d),
    low_2nd_date_255d: d(r.low_2nd_date_255d),
    open_500d: toNum(r.open_500d),
    high_500d: toNum(r.high_500d),
    high_date_500d: d(r.high_date_500d),
    high_2nd_500d: toNum(r.high_2nd_500d),
    high_2nd_date_500d: d(r.high_2nd_date_500d),
    low_500d: toNum(r.low_500d),
    low_date_500d: d(r.low_date_500d),
    low_2nd_500d: toNum(r.low_2nd_500d),
    low_2nd_date_500d: d(r.low_2nd_date_500d),
    open_750d: toNum(r.open_750d),
    high_750d: toNum(r.high_750d),
    high_date_750d: d(r.high_date_750d),
    high_2nd_750d: toNum(r.high_2nd_750d),
    high_2nd_date_750d: d(r.high_2nd_date_750d),
    low_750d: toNum(r.low_750d),
    low_date_750d: d(r.low_date_750d),
    low_2nd_750d: toNum(r.low_2nd_750d),
    low_2nd_date_750d: d(r.low_2nd_date_750d),
    open_1275d: toNum(r.open_1275d),
    high_1275d: toNum(r.high_1275d),
    high_date_1275d: d(r.high_date_1275d),
    high_2nd_1275d: toNum(r.high_2nd_1275d),
    high_2nd_date_1275d: d(r.high_2nd_date_1275d),
    low_1275d: toNum(r.low_1275d),
    low_date_1275d: d(r.low_date_1275d),
    low_2nd_1275d: toNum(r.low_2nd_1275d),
    low_2nd_date_1275d: d(r.low_2nd_date_1275d),
    high_line_slope_60d: toNum(r.high_line_slope_60d),
    low_line_slope_60d: toNum(r.low_line_slope_60d),
    high_line_slope_120d: toNum(r.high_line_slope_120d),
    low_line_slope_120d: toNum(r.low_line_slope_120d),
    high_line_slope_255d: toNum(r.high_line_slope_255d),
    low_line_slope_255d: toNum(r.low_line_slope_255d),
    high_line_slope_500d: toNum(r.high_line_slope_500d),
    low_line_slope_500d: toNum(r.low_line_slope_500d),
    high_line_slope_750d: toNum(r.high_line_slope_750d),
    low_line_slope_750d: toNum(r.low_line_slope_750d),
    high_line_slope_1275d: toNum(r.high_line_slope_1275d),
    low_line_slope_1275d: toNum(r.low_line_slope_1275d),
    change_since_last_high_60d: toNum(r.change_since_last_high_60d),
    days_since_last_high_60d: toNum(r.days_since_last_high_60d),
    change_since_last_low_60d: toNum(r.change_since_last_low_60d),
    days_since_last_low_60d: toNum(r.days_since_last_low_60d),
    change_since_last_high_120d: toNum(r.change_since_last_high_120d),
    days_since_last_high_120d: toNum(r.days_since_last_high_120d),
    change_since_last_low_120d: toNum(r.change_since_last_low_120d),
    days_since_last_low_120d: toNum(r.days_since_last_low_120d),
    change_since_last_high_255d: toNum(r.change_since_last_high_255d),
    days_since_last_high_255d: toNum(r.days_since_last_high_255d),
    change_since_last_low_255d: toNum(r.change_since_last_low_255d),
    days_since_last_low_255d: toNum(r.days_since_last_low_255d),
    change_since_last_high_500d: toNum(r.change_since_last_high_500d),
    days_since_last_high_500d: toNum(r.days_since_last_high_500d),
    change_since_last_low_500d: toNum(r.change_since_last_low_500d),
    days_since_last_low_500d: toNum(r.days_since_last_low_500d),
    change_since_last_high_750d: toNum(r.change_since_last_high_750d),
    days_since_last_high_750d: toNum(r.days_since_last_high_750d),
    change_since_last_low_750d: toNum(r.change_since_last_low_750d),
    days_since_last_low_750d: toNum(r.days_since_last_low_750d),
    change_since_last_high_1275d: toNum(r.change_since_last_high_1275d),
    days_since_last_high_1275d: toNum(r.days_since_last_high_1275d),
    change_since_last_low_1275d: toNum(r.change_since_last_low_1275d),
    days_since_last_low_1275d: toNum(r.days_since_last_low_1275d),
  };
}

/** One analysis.mov_ave_spreads_detail_ohlc_gaps row as pg hands it
 *  over (boolean flags arrive as real bools; int8/numeric through ::float8). */
export interface DbOhlcGapRow {
  date: Date | string;
  gap_open: number | null;
  intra: number | null;
  uw: number | null;
  lw: number | null;
  uw_top10: boolean | null;
  lw_top10: boolean | null;
  pattern: string | null;
}

/** SQL for the "gaps" extras group: one (sec_type, code)'s daily OHLC gap
 *  anatomy + pattern block (analysis.mov_ave_spreads_detail_ohlc_gaps — PK
 *  (code, sec_type, date) on HASH(code) partitions; estimated rows are
 *  excluded at build time). Serves the Top-10% OHLC Days group. */
export function buildOhlcGapsSql(): string {
  return `
    SELECT
      g.date,
      g.gap_open::float8  AS gap_open,
      g.intra::float8     AS intra,
      g.uw::float8        AS uw,
      g.lw::float8        AS lw,
      g.uw_top10,
      g.lw_top10,
      g.pattern
    FROM analysis.mov_ave_spreads_detail_ohlc_gaps g
    WHERE g.sec_type = $1
      AND g.code = ANY($2::text[])
    ORDER BY g.date
  `;
}

/** DB row → API shape (fraction legs to num, booleans as-is, dates
 *  ISO-formatted). */
export function toOhlcGapDay(r: DbOhlcGapRow): MovAveSpreadOhlcGapDay {
  return {
    date: formatDate(r.date),
    gap_open: toNum(r.gap_open),
    intra: toNum(r.intra),
    uw: toNum(r.uw),
    lw: toNum(r.lw),
    uw_top10: r.uw_top10 === true,
    lw_top10: r.lw_top10 === true,
    pattern: r.pattern ?? null,
  };
}
