/**
 * Roof/floor REBOUND AUDIT aggregation — the "rfaudit" extras group
 * (analysis.mov_ave_spreads_roof_floor_rebounds) folded into per-(side,
 * period) readout stats for the Roof/Floor Trendlines section: how many
 * recorded extremes the code has, how many REBOUNDED (frequency) and by
 * how much (amplitude), and — once rebounded — how the level fared on
 * retest (held/broken, bounce/depth amplitudes, durability flags).
 *
 * The events are a small per-code array (tens of rows), so the panel
 * aggregates client-side per selected window; the same builder serves
 * any period scope (pass null to pool all periods).
 */
import type {
  MovAveSpreadRoofFloorRebound,
  RfReboundOutcome,
  RoofFloorSide,
} from "@shared/types";

/** The per-(side, period) audit funnel + amplitudes. */
export interface ReboundAuditStats {
  side: RoofFloorSide;
  period: number | null;
  /** All recorded-extreme events (the rebound-frequency denominator). */
  nEvents: number;
  /** Events that rebounded ≥5% within 60 trading days. */
  nRebounded: number;
  /** Rebound frequency (nRebounded / nEvents); null when no events. */
  reboundPct: number | null;
  /** Mean rebound amplitude (rebound-completion close vs level, |%|). */
  avgReboundPct: number | null;
  /** Rebounded events whose ±1% zone was re-entered (held + broken +
   *  unresolved). */
  nRetested: number;
  nHeld: number;
  nBroken: number;
  /** Held share of the retested events (null when none retested). */
  heldPct: number | null;
  /** Mean bounce amplitude after a held retest (%). */
  avgBouncePct: number | null;
  /** Mean break depth after a broken retest (%). */
  avgDepthPct: number | null;
  /** Held levels that were eventually exceeded on a later retest. */
  nHeldLaterBroken: number;
  /** Broken levels still beyond the level 20d later (role flip). */
  nCleanBreaks: number;
}

const EMPTY: Omit<ReboundAuditStats, "side" | "period"> = {
  nEvents: 0,
  nRebounded: 0,
  reboundPct: null,
  avgReboundPct: null,
  nRetested: 0,
  nHeld: 0,
  nBroken: 0,
  heldPct: null,
  avgBouncePct: null,
  avgDepthPct: null,
  nHeldLaterBroken: 0,
  nCleanBreaks: 0,
};

/** Aggregate the audit funnel + amplitudes for one side and period
 *  scope (null period pools every period). */
export function aggregateReboundAudit(
  events: MovAveSpreadRoofFloorRebound[],
  period: number | null,
  side: RoofFloorSide,
): ReboundAuditStats {
  const rows = events.filter(
    (e) => e.side === side && (period == null || e.period === period),
  );
  const out: ReboundAuditStats = { ...EMPTY, side, period };
  if (rows.length === 0) return out;
  out.nEvents = rows.length;

  const rebounded = rows.filter((e) => e.outcome !== ("no_rebound" as RfReboundOutcome));
  out.nRebounded = rebounded.length;
  out.reboundPct = (100 * rebounded.length) / rows.length;
  const rebAmps = rebounded
    .map((e) => e.reboundPct)
    .filter((v): v is number => v != null);
  if (rebAmps.length > 0) {
    out.avgReboundPct =
      (100 * rebAmps.reduce((a, v) => a + Math.abs(v), 0)) / rebAmps.length;
  }

  const retested = rebounded.filter(
    (e) => e.outcome === "held" || e.outcome === "broken" || e.outcome === "unresolved",
  );
  out.nRetested = retested.length;
  out.nHeld = retested.filter((e) => e.outcome === "held").length;
  out.nBroken = retested.filter((e) => e.outcome === "broken").length;
  out.nHeldLaterBroken = retested.filter(
    (e) => e.outcome === "held" && e.laterBreak === true,
  ).length;
  out.nCleanBreaks = retested.filter(
    (e) => e.outcome === "broken" && e.cleanBreak === true,
  ).length;
  if (retested.length > 0) {
    out.heldPct = (100 * out.nHeld) / retested.length;
  }
  const bounces = retested
    .filter((e) => e.outcome === "held")
    .map((e) => e.bouncePct)
    .filter((v): v is number => v != null);
  if (bounces.length > 0) {
    out.avgBouncePct = (100 * bounces.reduce((a, v) => a + v, 0)) / bounces.length;
  }
  const depths = retested
    .filter((e) => e.outcome === "broken")
    .map((e) => e.breakDepthPct)
    .filter((v): v is number => v != null);
  if (depths.length > 0) {
    out.avgDepthPct = (100 * depths.reduce((a, v) => a + v, 0)) / depths.length;
  }
  return out;
}
