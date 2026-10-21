/**
 * families/hpRsi — the hp_rsi family (signal_type "hp_rsi"): the
 * hedge_pairs family's RSI-SOURCED ORCHESTRATION layer (2026-10-18
 * two-layer merge). The SOURCE is the components' existing
 * Short-Term-Tech rsi signal rules (REAL mov_rsi rows at the market +
 * leg codes); this layer orchestrates them into the portfolio
 * composites at the pair codes — the three-component capitulation
 * entry (hp_entry_legs_rsi), the market-high exit (hp_exit_mkt_high),
 * the laggard's catch-up (hp_catchup_rsi_hi), and the wash trio
 * (hp_entry_wash120_mkt) — and every row's portfolio group references
 * the sourced rule ids it coordinates. Codes are composite 'A~B' pair
 * ids. recordLabel carries the SHORT word the portfolio cell surface
 * renders instead of the raw hp_* literal (the full literal rides the
 * chip's tooltip).
 */
import type { SignalFamily } from "./index";

/** The sub type's short display word (raw literal when unmapped). */
const RECORD_WORDS: Record<string, string> = {
  hp_entry_legs_rsi: "trio·rsi",
  hp_entry_wash120_mkt: "wash·120+mkt",
  hp_exit_mkt_high: "exit·mkt hi",
  hp_catchup_rsi_hi: "catchup·rsi hi",
};

export const hpRsiFamily: SignalFamily = {
  label: "Hedge Pairs RSI",
  recordLabel: (subType) => RECORD_WORDS[subType] ?? null,
};
