/**
 * families/hedgePairs — the hedge_pairs family (signal_type
 * "hedge_pairs"): the long–long anti-correlated industry-pair rules
 * over the three curves (two offset-opposite industry composites +
 * the broad-market benchmark) — the pair-native coordination rules:
 * the harvest ratio trim, the momentum tilt, the regime stop, and the
 * washout / hedgeable / reclaim entries. Codes are composite 'A~B'
 * pair ids. recordLabel carries the SHORT word the portfolio cell
 * surface renders instead of the raw hp_* literal (the full literal
 * rides the chip's tooltip).
 */
import type { SignalFamily } from "./index";

/** The sub type's short display word (raw literal when unmapped).
 *  hp_entry_wash is the 2026-10-21 dedup's merged washout entry (the
 *  per-window trio collapsed; the window rides the cell's bar_def). */
const RECORD_WORDS: Record<string, string> = {
  hp_harvest: "harvest",
  hp_mom_strong: "mom·strong",
  hp_mom_lag: "mom·lag",
  hp_stop_regime: "stop·ρ",
  hp_entry_wash: "wash",
  hp_entry_rho50: "hedge·ρ50",
  hp_entry_catchup_reclaim60: "reclaim·60",
};

export const hedgePairsFamily: SignalFamily = {
  label: "Hedge Pairs",
  recordLabel: (subType) => RECORD_WORDS[subType] ?? null,
};
