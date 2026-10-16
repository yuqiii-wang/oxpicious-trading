/**
 * families/momentum — the momentum family (signal_type "momentum"):
 * the log-return tail family over analysis.mov_ave_momentum's
 * vol-adaptive lookbacks. The sub types carry no record word of their
 * own (the raw literal — "momshort_2pct" — is the record's word).
 */
import type { SignalFamily } from "./index";

export const momentumFamily: SignalFamily = {
  label: "Momentum",
};
