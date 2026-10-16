/**
 * families/optionsWall — the options_wall family (signal_type
 * "options_wall"): display labels + the sub-type words. The sub-type
 * word carries the side, the breach bar and the no-action mark of the
 * hit-and-bounce configs. Null for unknown literals. (The curve-era
 * axis machinery was retired with the curve overlay 2026-10-15.)
 */
import type { SignalFamily } from "./index";

export function owSubTypeLabel(subType: string): string | null {
  const m = /^ow_(put|call)_(\d+)pct(?:_rej)?$/.exec(subType);
  if (m) {
    const side = m[1] === "put" ? "Put Wall" : "Call Wall";
    const bar = `${Number(m[2]) / 100}%`;
    const rej = subType.endsWith("_rej") ? " · no-action" : "";
    return `${side} ${bar}${rej}`;
  }
  if (
    subType === "ow_put_center_vrp"
    || subType === "ow_put_center_vrp_rej"
  ) {
    const rej = subType.endsWith("_rej") ? " · no-action" : "";
    return `Put Center Hit 1% · VRP${rej}`;
  }
  if (subType === "ow_call_approach_rally") {
    return "Call Wall Approach 1.5% · rally";
  }
  return null;
}

/** The config's wall side — ow_put_* breaches the dominant PUT wall
 *  (the support/floor side), ow_call_* the CALL wall (cap). The RULE
 *  ROW-EXPANSION emphasis target on the options signals page's skew
 *  chart (expanding a wall cell bolds its side's dominant wall zone);
 *  null for unknown literals. */
export function owSubTypeWallSide(subType: string): "put" | "call" | null {
  if (subType.startsWith("ow_put")) return "put";
  if (subType.startsWith("ow_call")) return "call";
  return null;
}

export const optionsWallFamily: SignalFamily = {
  label: "Options Wall",
  recordLabel: owSubTypeLabel,
};
