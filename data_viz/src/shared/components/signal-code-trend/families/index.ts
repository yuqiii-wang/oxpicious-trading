/**
 * families — the signal families' display REGISTRY (one file per
 * family: rsi / bollinger / macross / optionsWall / optionsVix /
 * momentum / rfBreak). The
 * rules/history panels and the shared tables consume THIS registry and
 * never name a family themselves — adding a family is one file + one
 * registry entry (the ENGINES-registry pattern the Python signals
 * tier uses).
 *
 * (The registry's curve-era members — the depth-response axis
 * builders, the threshold-bar decodes, the series GROUP model — were
 * retired with the curve/threshold overlay 2026-10-15; what remains is
 * the display vocabulary: the family label and the record's sub-type
 * word.)
 */
import { rsiFamily } from "./rsi";
import { bollingerFamily } from "./bollinger";
import { maCrossFamily } from "./macross";
import { optionsWallFamily } from "./optionsWall";
import { optionsVixFamily } from "./optionsVix";
import { momentumFamily } from "./momentum";
import { rfBreakFamily } from "./rfBreak";

/** One family's display vocabulary. */
export interface SignalFamily {
  /** Display label (the tag selector, record names, the AI advice
   *  buttons). */
  label: string;
  /** The RECORD's display word for one sub_type (ma_cross: the
   *  window-only word; options families: the config word); null = the
   *  raw literal. Default: null. */
  recordLabel?: (subType: string) => string | null;
}

/** signal_type → the family's entry (insertion order = the tag
 *  selector's grouping order). */
export const SIGNAL_FAMILIES: Record<string, SignalFamily> = {
  mov_rsi: rsiFamily,
  bollinger: bollingerFamily,
  ma_cross: maCrossFamily,
  options_wall: optionsWallFamily,
  options_vix: optionsVixFamily,
  momentum: momentumFamily,
  rf_break: rfBreakFamily,
};

/** Display label per signal TAG (signal_type — the indicator family).
 *  New families add a registry entry and surface in the tag selector
 *  automatically (unknown types fall back to the raw code). */
export const SIGNAL_TYPE_LABELS: Record<string, string> = Object
  .fromEntries(
    Object.entries(SIGNAL_FAMILIES).map(([t, f]) => [t, f.label]),
  );

/** The `family · sub` display of one signal record — the family word
 * is the family's display LABEL (never the raw signal_type literal —
 * "Roof/Floor · rf60_med_roof", not "rf_break · …"); families with a
 * recordLabel carry their own sub word (ma_cross: the window-only
 * label, "MA Cross · MA 20"; options_wall: the config word), others
 * keep the raw literal. */
export function signalRecordName(
  signalType: string,
  subType: string,
): string {
  const own = SIGNAL_FAMILIES[signalType]?.recordLabel?.(subType) ?? null;
  const family = SIGNAL_TYPE_LABELS[signalType] ?? signalType;
  return `${family} · ${own ?? subType}`;
}
