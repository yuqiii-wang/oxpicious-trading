/**
 * MA-Spread OHLC pivot SQL fragments — the long-format roof/floor lines
 * table pivoted to per-window columns for the chart's shared tooltip
 * metrics and the "ohlc" extras group.
 * Split out of the former monolithic mov-ave-spreads.ts.
 */

// analysis.mov_ave_spreads_roof_and_floor_lines (renamed 2026-10 from
// mov_ave_spreads_detail_ohlc) is LONG format — one row per
// (sec_type, code, date, period) with generic *_over_period columns. Every
// consumer pivots it in ONE prefetched CTE (one index range scan — the PK
// (code, sec_type, date, period) keeps a code's rows contiguous) and aliases
// the generic columns to the per-window names the API serves.
// Entries are [db_column, stem] pairs: most columns follow the
// `${stem}_over_period` naming; the change/days_since_last_high|low
// columns carry no suffix, so the db column is spelled out per stem.
export const OHLC_STEMS = [
  ["open_over_period", "open"],
  ["high_over_period", "high"],
  ["high_date_over_period", "high_date"],
  ["low_over_period", "low"],
  ["low_date_over_period", "low_date"],
  ["high_2nd_over_period", "high_2nd"],
  ["high_2nd_date_over_period", "high_2nd_date"],
  ["low_2nd_over_period", "low_2nd"],
  ["low_2nd_date_over_period", "low_2nd_date"],
  ["high_line_slope_over_period", "high_line_slope"],
  ["low_line_slope_over_period", "low_line_slope"],
  ["change_since_last_high", "change_since_last_high"],
  ["days_since_last_high", "days_since_last_high"],
  ["change_since_last_low", "change_since_last_low"],
  ["days_since_last_low", "days_since_last_low"],
] as const;

/** The anchor/slope stems the DEFAULT chart's shared tooltip pivot needs —
 *  the change/days-since-last-high/low columns load only with the "ohlc"
 *  extras group (they belong to the roof/floor readout, which the extras
 *  group feeds). */
export const OHLC_SHARED_STEMS = OHLC_STEMS.filter(
  ([, stem]) => !stem.startsWith("change_since_last") && !stem.startsWith("days_since_last"),
);

/** Windows whose rolling OHLC the DEFAULT chart's shared tooltip metrics
 *  carry — the 4 windows a pair's ma_long can take (ma_long ∈ 20/60/120/255;
 *  the tooltip shows them on SMA pairs matching the window). */
export const OHLC_SHARED_PERIODS = [20, 60, 120, 255] as const;

/** The roof/floor windows the "ohlc" + "bands" extras groups load (the
 *  panel's Roof/Floor Trendlines row): 60…1275 — the 20d is NOT a
 *  roof/floor window (the shared tooltip block keeps its own 20d pivot
 *  for the Price/MA20 pair readout). */
export const ROOF_FLOOR_PERIODS = [60, 120, 255, 500, 750, 1275] as const;

/** WITH-CTE fragment fetching the requested codes' long-format OHLC rows in
 *  one range scan and pivoting them to `{stem}_{p}d` columns per date. */
export function ohlcPivotCteSql(
  periods: readonly number[],
  stems: readonly (readonly [string, string])[] = OHLC_STEMS,
): string {
  const pivot = periods.flatMap((p) =>
    stems.map(
      ([db, stem]) =>
        `MAX(o.${db}) FILTER (WHERE o.period = ${p}) AS ${stem}_${p}d`,
    ),
  ).join(",\n        ");
  return `ohlc_raw AS MATERIALIZED (
      SELECT * FROM analysis.mov_ave_spreads_roof_and_floor_lines
      WHERE sec_type = $1
        AND code = ANY($2::text[])
    ),
    ohlc AS MATERIALIZED (
      SELECT
        o.code,
        o.date,
        ${pivot}
      FROM ohlc_raw o
      GROUP BY o.code, o.date
    )`;
}

/** Final-SELECT fragment reading the pivoted `ohlc` CTE columns, aliased
 *  under `prefix` ("sh_" for the chart's shared tooltip metrics, "" for the
 *  ohlc extras group's MovAveSpreadOhlcRow names). */
export function ohlcPivotSelectSql(
  periods: readonly number[],
  prefix: string,
  stems: readonly (readonly [string, string])[] = OHLC_STEMS,
): string {
  return periods.flatMap((p) =>
    stems.map(([, stem]) => `ohlc.${stem}_${p}d AS ${prefix}${stem}_${p}d`),
  ).join(",\n      ");
}
