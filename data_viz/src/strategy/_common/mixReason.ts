/**
 * Mixed-mode signal_reason parser (shared).
 *
 * signal_reason prefixes:
 * - "__MIX__<json>__<human-readable>" — mixed mode. The JSON carries
 *   per-algo contributions so any tooltip (the action marker tooltip,
 *   the DecisionTable's structured breakdown) can render one row per
 *   family.
 * - "__ADHOC__<json>__<human-readable>" — the singleton strategy's
 *   self-proposed ad-hoc action (the position/profit-aware trim). The
 *   JSON is the plan-time evidence (lots / cap / pnl / r5 / sell).
 *
 * Binary mode (no prefix) is returned as-is.
 *
 * The upstream builders are AlgoSignalCollector.build_signal_reason and
 * _comb/adhoc.py wrap_reason_fn (strategy/factors_and_algos/).
 */

export interface MixContribution {
  a: string;     // abbreviation (bb/macd/ma)
  algo: string;  // full algo name
  w: number;     // weight (0-1)
  c: number;     // raw signal_confidence (signed, -100..100)
  n: number;     // net contribution = w * c (signed)
}

export interface MixData {
  algos: MixContribution[];
  blended: number;  // sum of contributions = blended signal
  netted: boolean;  // some algos BUY, some SELL on the same bar
  side: string;     // BUY or SELL (the netted side)
}

export interface AdhocData {
  k: string;    // action kind ("trim")
  lots: number; // position size at plan time (total_qty / 100)
  cap: number;  // the profit-aware cap that was breached (lots)
  pnl: number;  // mark-to-market standing (total_pnl / capital_deployed)
  r5: number;   // trailing small-rise return that armed the trim
  sell: number; // planned sell fraction (0-1)
}

const MIX_PREFIX = "__MIX__";
const ADHOC_PREFIX = "__ADHOC__";

/** Split "<prefix><json>__<human>" into the raw JSON string + human tail. */
function splitPrefixed(reason: string, prefix: string): {
  jsonStr: string;
  human: string;
} | null {
  const rest = reason.slice(prefix.length);
  const endIdx = rest.indexOf("__");
  if (endIdx < 0) {
    return null;
  }
  return { jsonStr: rest.slice(0, endIdx), human: rest.slice(endIdx + 2) };
}

export function parseMixReason(reason: string | null | undefined): {
  mix: MixData | null;
  adhoc: AdhocData | null;
  human: string;
} {
  if (!reason) {
    return { mix: null, adhoc: null, human: reason ?? "" };
  }
  if (reason.startsWith(MIX_PREFIX)) {
    const split = splitPrefixed(reason, MIX_PREFIX);
    if (!split) {
      return { mix: null, adhoc: null, human: reason };
    }
    try {
      const mix = JSON.parse(split.jsonStr) as MixData;
      return { mix, adhoc: null, human: split.human };
    } catch {
      return { mix: null, adhoc: null, human: split.human };
    }
  }
  if (reason.startsWith(ADHOC_PREFIX)) {
    const split = splitPrefixed(reason, ADHOC_PREFIX);
    if (!split) {
      return { mix: null, adhoc: null, human: reason };
    }
    try {
      const adhoc = JSON.parse(split.jsonStr) as AdhocData;
      return { mix: null, adhoc, human: split.human };
    } catch {
      return { mix: null, adhoc: null, human: split.human };
    }
  }
  return { mix: null, adhoc: null, human: reason };
}

/** Strip the __MIX__ / __ADHOC__ JSON payload, keeping the human-readable tail. */
export function stripMixPrefix(reason: string | null | undefined): string {
  return parseMixReason(reason).human;
}
