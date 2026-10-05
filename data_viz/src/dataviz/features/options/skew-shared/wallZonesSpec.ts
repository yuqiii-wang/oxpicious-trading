/**
 * Wall/zone light-shade spec — shapes analysis.options_walls rows
 * (wall_type='zone') into the chain-wide dominant wall zone per
 * (date, side) the skew panel's options-bands layer renders.
 *
 * The Options Trend OI Bands chart draws the same walls per selected
 * expiry cohort (see ../options-trend/bandData.buildZoneWalls); this
 * panel has no cohort selector — its corridor pools the WHOLE chain —
 * so the dominant zone per (date, side) is ranked across ALL expiry
 * groups that date, with the same strength rule (strength_score, then
 * wall OI as the tie-break). Strike levels are price-scaled exactly
 * like the corridor (raw units — same scale as the corridor bounds and
 * the moneyness-series spot).
 */
import type { OptionsWallRow } from "@shared/types";
import type { SharedWallZone, SharedWallZones } from "./types";

/** Rank rule shared with the Options Trend zone walls: dominant strength
 *  first, wall OI as the tie-break. */
function rank(row: OptionsWallRow): number {
  return (row.strength_score ?? -1) * 1e9 + (row.wall_oi ?? 0);
}

/** Identity-stable empty result (ui-charts memo rule — never rebuild). */
export const NO_WALL_ZONES: SharedWallZones = {
  call: new Map<string, SharedWallZone>(),
  put: new Map<string, SharedWallZone>(),
};

/**
 * Dominant chain-wide wall zone per (date, side), PRICE-SCALED to the
 * chart's unit (divide by `priceScale` — 1000 for the ETF venues' 厘,
 * 1 for CFFEX index points, same divisor the corridor and spot use).
 */
export function buildChainWallZones(
  rows: OptionsWallRow[],
  priceScale: number,
): SharedWallZones {
  const call = new Map<string, SharedWallZone>();
  const put = new Map<string, SharedWallZone>();
  const rankByDate: Record<"call" | "put", Map<string, number>> = {
    call: new Map<string, number>(),
    put: new Map<string, number>(),
  };
  for (const row of rows) {
    if (row.wall_type !== "zone") continue;
    if (row.wall_low == null || row.wall_high == null || row.wall_center == null) continue;
    if (row.wall_oi == null || row.wall_oi <= 0) continue;
    const isPut = row.option_type === "PUT";
    const target = isPut ? put : call;
    const ranks = isPut ? rankByDate.put : rankByDate.call;
    const curRank = ranks.get(row.date);
    if (curRank != null && rank(row) <= curRank) continue;
    ranks.set(row.date, rank(row));
    target.set(row.date, {
      date: row.date,
      side: row.option_type,
      low: row.wall_low / priceScale,
      high: row.wall_high / priceScale,
      center: row.wall_center / priceScale,
      wallOi: row.wall_oi,
      massShare: row.mass_share,
      gapPct: row.gap_pct,
      state: row.state,
      strength: row.strength_score,
    });
  }
  return { call, put };
}
