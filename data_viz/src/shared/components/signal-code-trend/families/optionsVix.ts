/**
 * families/optionsVix — the options_vix family (signal_type
 * "options_vix"): display labels + the sub-type words — ovix_spike
 * the corr<-0.8 & slope panic entry, ovix_spike_hivol the high-vol
 * interaction, ovix_rollover the peak-confirmed turn,
 * ovix_fear_unwind the corr>=-0.5 exit companion (+
 * ovix_deep_hivol / ovix_spike_wide, the 2026-10-14 populate
 * additions; ovix_panic_peak, the 2026-10-15 vol30-space escalation
 * leg). Null for unknown literals. (The curve-era axis
 * machinery was retired with the curve overlay 2026-10-15.)
 */
import type { SignalFamily } from "./index";

export function ovixSubTypeLabel(subType: string): string | null {
  switch (subType) {
    case "ovix_spike":
      return "VIX Spike · corr < -0.8";
    case "ovix_spike_hivol":
      return "VIX Spike · high-vol regime";
    case "ovix_rollover":
      return "VIX Peak Rollover";
    case "ovix_fear_unwind":
      return "Fear Unwind · corr ≥ -0.5 (exit)";
    case "ovix_deep_hivol":
      return "VIX Deep Fear · high-vol regime (no spike)";
    case "ovix_spike_wide":
      return "VIX Spike · corr < -0.75 (wide)";
    case "ovix_panic_peak":
      return "Panic Peak · vol new 120d high +5% & falling";
    default:
      return null;
  }
}

/** The config's corr_20d bar — the RULE ROW-EXPANSION emphasis target
 *  on the vol-index page's corr companion chart (expanding a vix cell
 *  bolds its config's bar): the buy configs share the -0.8 fear bar
 *  except ovix_spike_wide's widened -0.75, the unwind the -0.5 exit
 *  bar; ovix_panic_peak lives on the vol30 value space (no corr bar).
 *  Null for unknown literals. */
export function ovixSubTypeCorrBar(subType: string): number | null {
  if (
    subType === "ovix_spike"
    || subType === "ovix_spike_hivol"
    || subType === "ovix_rollover"
    || subType === "ovix_deep_hivol"
  ) {
    return -0.8;
  }
  if (subType === "ovix_spike_wide") {
    return -0.75;
  }
  if (subType === "ovix_fear_unwind") {
    return -0.5;
  }
  return null;
}

export const optionsVixFamily: SignalFamily = {
  label: "Options VIX",
  recordLabel: ovixSubTypeLabel,
};
