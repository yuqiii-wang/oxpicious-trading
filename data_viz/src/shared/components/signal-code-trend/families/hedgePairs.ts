/**
 * families/hedgePairs — the hedge_pairs family (signal_type
 * "hedge_pairs"): the PORTFOLIO family's pair-native trio rules
 * (fresh 2026-10-24, the typical negative-correlation solution): two
 * offset-opposite leg curves + the broad-market curve, all three in
 * their own RSI capitulation tails the same day — the co-wash BUY of
 * the inverse-vol long-long pair, plus the blow-off mirror as the
 * equity-asymmetry check. Codes are composite 'A~B' pair ids.
 * recordLabel carries the SHORT word the portfolio cell surface
 * renders instead of the raw hp_* literal (the full literal rides the
 * chip's tooltip). The 2026-10-24-prior sub inventory (hp_entry_wash /
 * hp_mom_* / hp_harvest / ... 5800-5827) stays registered for
 * old-history decode; it is no longer produced.
 */
import type { SignalFamily } from "./index";

/** The sub type's short display word (raw literal when unmapped). */
const RECORD_WORDS: Record<string, string> = {
  hp_entry_trio: "trio·wash",
  hp_exit_trio: "trio·blowoff",
  // the SPREAD one-buy-one-sell sub (2026-10-25): the pair's long-short
  // spread curve (leg A long / leg B short, no market curve) in its own
  // RSI bottom tail — BUY the laggard leg A, SELL the leader leg B;
  // hp_exit_spread is the measured asymmetry mirror (no writer), the
  // two hp_spread_*_leg labels are the event's per-ROLE member records
  hp_entry_spread: "spread·conv",
  hp_exit_spread: "spread·mirror",
  hp_spread_buy_leg: "spread·buy leg",
  hp_spread_sell_leg: "spread·sell leg",
  // the per-ROLE witness records (2026-10-25: market / leg A / leg B
  // each their own sub type; the shared 5830 label decodes old rows)
  hp_rsi_witness: "rsi·witness",
  hp_rsi_witness_mkt: "rsi·witness mkt",
  hp_rsi_witness_leg_a: "rsi·witness legA",
  hp_rsi_witness_leg_b: "rsi·witness legB",
  // the retired vocabulary (old history rows only)
  hp_entry_legs_rsi: "legs·rsi",
  hp_entry_rsi_legs: "rsi·legs",
  hp_entry_wash: "wash",
  hp_entry_wash_rsi: "wash·rsi",
  hp_entry_wash_mkt1: "wash·mkt1",
  hp_entry_wash120_mkt: "wash·120+mkt",
  hp_harvest: "harvest",
  hp_mom_strong: "mom·strong",
  hp_mom_lag: "mom·lag",
  hp_stop_regime: "stop·ρ",
  hp_entry_rho50: "hedge·ρ50",
  hp_entry_catchup_reclaim60: "reclaim·60",
  hp_exit_mkt_high: "exit·mkt hi",
  hp_catchup_rsi_hi: "catchup·rsi hi",
};

export const hedgePairsFamily: SignalFamily = {
  label: "Hedge Pairs",
  recordLabel: (subType) => RECORD_WORDS[subType] ?? null,
};
