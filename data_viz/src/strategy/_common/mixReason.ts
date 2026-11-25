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

/**
 * Split one decision reason's human text into the tooltip's SUB-ROWS —
 * along the shared reason grammar's own markers, never a blind character
 * wrap:
 *
 *  • trailing `[scale …]` / `[amp …]` bracket disclosures (the oracle's
 *    confidence-scaling / amplification notes) peel off the end, one row
 *    each, brackets stripped;
 *  • the body splits at the depth-0 ` — ` / ` -> ` segment markers — the
 *    phrasing convention of the oracle (`_oracle/algo.py`) and the
 *    analysis-source / macd reason builders — paren-aware, so the
 *    oracle's grading parenthetical stays with its conf clause;
 *  • a segment whose tail is a LONG parenthetical (>= 25 chars, the lead
 *    ending in a word character — `conf 12.2/100 (the oracle's …)`,
 *    `ADHOC TRIM: sell 46% … (lots …)`) breaks it onto its own row; a
 *    short or mid-string group (`swing unknown (frontier pivot)`, the
 *    LAST DAY SELL math) stays inline.
 *
 * A reason carrying none of the markers comes back as ONE row — the
 * caller renders it unchanged (the mix/forecast/LDS voices stay compact).
 */
export function splitReasonRows(reason: string): string[] {
  const notes: string[] = [];
  let body = reason.trim();
  // Trailing bracket notes ([scale …] / [amp …]) — peel from the end.
  for (;;) {
    const m = /(?:^| )\[[^[\]]+\]$/.exec(body);
    if (!m) break;
    notes.unshift(m[0].trim().slice(1, -1));
    body = body.slice(0, m.index).trim();
  }
  // Depth-0 split on the " — " / " -> " segment markers.
  const segs: string[] = [];
  let depth = 0;
  let start = 0;
  for (let i = 0; i < body.length; ) {
    const ch = body[i];
    if (ch === "(") {
      depth += 1;
      i += 1;
    } else if (ch === ")") {
      depth = Math.max(0, depth - 1);
      i += 1;
    } else if (
      depth === 0
      && (body.startsWith(" — ", i) || body.startsWith(" -> ", i))
    ) {
      const seg = body.slice(start, i).trim();
      if (seg) segs.push(seg);
      i += 3;
      start = i;
    } else {
      i += 1;
    }
  }
  const tail = body.slice(start).trim();
  if (tail) segs.push(tail);
  // A segment ending in a long parenthetical breaks it onto its own row.
  const rows: string[] = [];
  for (const seg of segs) {
    const m = /^(.+?[A-Za-z0-9%]) \(([^()]{25,})\)$/.exec(seg);
    if (m) {
      rows.push(m[1], `(${m[2]})`);
    } else {
      rows.push(seg);
    }
  }
  if (rows.length === 0) return [...notes];
  return [...rows, ...notes];
}
