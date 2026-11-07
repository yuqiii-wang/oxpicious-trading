/**
 * MA-Spread roof/floor extras groups — the "rfest" group (the roof/floor
 * LEVEL-ESTIMATE pivot from analysis.mov_ave_spreads_roof_floor_estimates)
 * and the "rfaudit" group (the rebound-audit events from
 * analysis.mov_ave_spreads_roof_floor_rebounds).
 * Split out of the former monolithic mov-ave-spreads.ts.
 */
import { formatDate, toNum } from "@api/lib/db";
import type { QueryResultRow } from "pg";
import {
  ROOF_FLOOR_ESTIMATE_METHODS,
  ROOF_FLOOR_ESTIMATE_WINDOWS,
} from "@shared/types";
import type {
  MovAveSpreadRoofFloorEstimateRow,
  MovAveSpreadRoofFloorRebound,
  RfReboundOutcome,
  RoofFloorSide,
} from "@shared/types";
import { LABEL_CODE } from "@api/services/label-codes";

/** The "rfest" group's query — the roof/floor LEVEL-ESTIMATE pivot —
 *  analysis.mov_ave_spreads_roof_floor_estimates is LONG (one row per
 *  (code, date, period, method)); pivoted back to one row per date with
 *  roof_{method}_{W}d / floor_{method}_{W}d / breached_{method}_{W}d
 *  fields (method bound via its label_dim code in
 *  LABEL_CODE.roof_floor_method), driven off the detail rows' dates so the
 *  response is index-aligned with the chart. The SELECT lives in the sql
 *  view
 *  data_viz/api/services/analysis/mov-ave-spreads/roof-floor/estimates_pivot
 *  — the two fragments below are passed to it as fragment args. */

/** Fragment: the est CTE's per-(method, window) FILTER aggregates. */
export function rfEstimatePivotSql(): string {
  return ROOF_FLOOR_ESTIMATE_WINDOWS.flatMap((p) =>
    ROOF_FLOOR_ESTIMATE_METHODS.flatMap((m) => {
      const code = LABEL_CODE.roof_floor_method[m];
      return [
        `(MAX(e.roof_estimate) FILTER (WHERE e.period = ${p} AND e.method = ${code}))::float8 AS roof_${m}_${p}d`,
        `(MAX(e.floor_estimate) FILTER (WHERE e.period = ${p} AND e.method = ${code}))::float8 AS floor_${m}_${p}d`,
        `(BOOL_OR(e.is_recently_breached) FILTER (WHERE e.period = ${p} AND e.method = ${code})) AS breached_${m}_${p}d`,
      ];
    }),
  ).join(",\n        ");
}

/** Fragment: the final SELECT's est.<field> list (same order as the pivot). */
export function rfEstimateSelectSql(): string {
  return ROOF_FLOOR_ESTIMATE_WINDOWS.flatMap((p) =>
    ROOF_FLOOR_ESTIMATE_METHODS.flatMap((m) => [
      `est.roof_${m}_${p}d`,
      `est.floor_${m}_${p}d`,
      `est.breached_${m}_${p}d`,
    ]),
  ).join(",\n      ");
}

/** One pivoted estimate row as pg hands it over (float8 casts in the SQL;
 *  NULLs as null). */
export interface DbRfEstimateRow extends QueryResultRow {
  date: Date | string;
  [column: string]: unknown;
}

/** Map one pivoted estimate row to the per-date API shape (all method ×
 *  window level + breach-flag fields; toNum passes NULL through as null,
 *  the boolean flags keep pg's boolean | null). */
export function toRfEstimateRow(r: DbRfEstimateRow): MovAveSpreadRoofFloorEstimateRow {
  const fields: Record<string, number | boolean | null> = {};
  for (const p of ROOF_FLOOR_ESTIMATE_WINDOWS) {
    for (const m of ROOF_FLOOR_ESTIMATE_METHODS) {
      fields[`roof_${m}_${p}d`] = toNum(r[`roof_${m}_${p}d`]);
      fields[`floor_${m}_${p}d`] = toNum(r[`floor_${m}_${p}d`]);
      fields[`breached_${m}_${p}d`] =
        (r[`breached_${m}_${p}d`] as boolean | null | undefined) ?? null;
    }
  }
  return {
    date: formatDate(r.date),
    ...fields,
  } as MovAveSpreadRoofFloorEstimateRow;
}

/** The "rfaudit" group's query: one code's rebound-audit events
 *  (analysis.mov_ave_spreads_roof_floor_rebounds), side + outcome decoded
 *  via label_dim at this boundary (labels never ride frames). The SELECT
 *  lives in the sql view
 *  data_viz/api/services/analysis/mov-ave-spreads/roof-floor/rebounds
 *  (the side/outcome decode fragments passed as fragment args by
 *  extras.ts). */

/** One rebound-audit event row as pg hands it over (side/outcome arrive
 *  decoded to labels; NUMERICs cast float8; booleans real bools). */
export interface DbRfReboundRow {
  event_date: Date | string;
  side: string;
  period: number;
  level: number | null;
  outcome: string;
  rebound_date: Date | string | null;
  retest_date: Date | string | null;
  days_to_retest: number | null;
  rebound_pct: number | null;
  bounce_pct: number | null;
  break_depth_pct: number | null;
  days_to_break: number | null;
  clean_break: boolean | null;
  later_break: boolean | null;
  retest_episodes: number | null;
}

const RF_OUTCOMES: ReadonlySet<string> = new Set([
  "no_rebound", "never_retested", "held", "broken", "unresolved",
]);

/** Map one audit row to the API shape ('top' side label → "roof",
 *  'bottom' → "floor"; unknown outcome labels are dropped — a future
 *  rebuild with extended vocabularies degrades, never breaks). */
export function toRfRebound(r: DbRfReboundRow): MovAveSpreadRoofFloorRebound | null {
  if (!RF_OUTCOMES.has(r.outcome)) return null;
  const side: RoofFloorSide = r.side === "top" ? "roof" : "floor";
  const outcome = r.outcome as RfReboundOutcome;
  const d = (v: Date | string | null): string | null =>
    v == null ? null : formatDate(v);
  return {
    eventDate: formatDate(r.event_date),
    side,
    period: r.period,
    level: toNum(r.level) ?? 0,
    outcome,
    reboundDate: d(r.rebound_date),
    retestDate: d(r.retest_date),
    daysToRetest: toNum(r.days_to_retest),
    reboundPct: toNum(r.rebound_pct),
    bouncePct: toNum(r.bounce_pct),
    breakDepthPct: toNum(r.break_depth_pct),
    daysToBreak: toNum(r.days_to_break),
    cleanBreak: r.clean_break,
    laterBreak: r.later_break,
    retestEpisodes: toNum(r.retest_episodes),
  };
}
