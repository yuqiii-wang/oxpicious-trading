/**
 * portfolio-signal/common — helpers shared by the portfolio tier's two
 * surfaces (PortfolioSurface reads the portfolio-tier tables, WHAT THE
 * PORTFOLIO TOOK; CellRulesSurface reads the signal-tier tables, each
 * member's own evidence). Pure helpers only.
 */
import type {
  PortfolioEventMemberSignal,
  PortfolioHistorySignal,
  PortfolioSignalGroup,
  TradingSignal,
} from "@/lib/api-client";
import { signalSubWordLoose } from "@/shared/components/signal-code-trend/families";

/** The pipeline's portfolio families run the default INDEX scope —
 *  their rows (composite portfolio codes) carry the index stamp. */
export const DEFAULT_SEC_TYPE = "index";

/** The portfolio_type display words (decoded labels in, words out). */
const PORTFOLIO_TYPE_LABEL: Record<string, string> = {
  neg_corr_hedge_pair: "Negative-Corr Hedge Pair",
};

export const portfolioTypeLabel = (t: string): string =>
  PORTFOLIO_TYPE_LABEL[t] ?? t;

/** The portfolio_sub_type display words — WHICH PORTFOLIO RULE a row
 *  is within its type (the 2026-10-25 split: trio wash / spread, the
 *  rule_params.name vocabulary). */
const PORTFOLIO_SUB_TYPE_LABEL: Record<string, string> = {
  trio_wash: "trio wash",
  spread: "spread",
};

export const portfolioSubLabel = (s: string): string =>
  PORTFOLIO_SUB_TYPE_LABEL[s] ?? s;

/** The two leg codes of a composite 'A~B' pair id. */
export const pairLegs = (code: string): [string, string] => {
  const [a, b] = code.split("~");
  return [a ?? code, b ?? ""];
};

/** The row key of one portfolio history event — the PK shape of
 *  portfolio_history_signals (date + the composite rule key + time).
 *  Shared by the history panel's row identity and the host pages'
 *  chart-marker payload (the clicked-row emphasis keys the chart's
 *  markers through the same value). */
export function portfolioEventRowKey(e: Pick<
  PortfolioHistorySignal, "date" | "signal_type" | "signal_sub_type" | "time"
>): string {
  return `${e.date}|${e.signal_type}|${e.signal_sub_type}|${e.time}`;
}

/** The pair catch-up window in traded days: ONE leg's signal action
 *  commits the pair entry, the OTHER leg's witness should fire within
 *  this window (the entry subs' coordination.witness_window; the
 *  pipeline's design default 5). */
export function pairWitnessWindow(p: PortfolioSignalGroup): number {
  const subs = (p.rule_params?.subs ?? {}) as Record<string, {
    coordination?: { witness_window?: unknown };
  }>;
  for (const spec of Object.values(subs)) {
    const w = spec?.coordination?.witness_window;
    if (typeof w === "number") return w;
  }
  return 5;
}

/** The coordinated sub types' display order (matches the
 *  rule_params.subs keys / the member cells' own sub types). The
 *  fresh typical-solution trio (2026-10-24): the co-wash BUY (both
 *  legs + the broad market in their own RSI capitulation tails the
 *  same day; the blow-off mirror hp_exit_trio stays the measured
 *  asymmetry check, never shipped). The 2026-10-25 spread subs: the
 *  ENTRY (the pair's long-short spread curve in its own RSI bottom
 *  tail — one buy (leg A) one sell (leg B)) and the EXIT (the same
 *  curve at its RSI TOP tail — sell the leader / buy the laggard;
 *  shipped 2026-10-25 evening, the rotation-blow-off side). The
 *  2026-10-22/23 coordination subs stay listed for old-history
 *  decode; they are no longer produced. */
export const SUB_ORDER = [
  "hp_entry_trio",
  "hp_entry_spread",
  "hp_exit_trio",
  "hp_exit_spread",
  "hp_entry_legs_rsi",
  "hp_entry_rsi_legs",
  "hp_entry_wash120_mkt",
  "hp_entry_wash_mkt1",
  "hp_entry_wash",
  "hp_entry_wash_rsi",
];

export const subOrderRank = (sub: string): number =>
  (SUB_ORDER.indexOf(sub) + 1) || 99;

/** One coordinated sub rule's TRIGGER CONDITION — the roster row's
 *  plain-language "when does the portfolio signal fire" line (the
 *  DD / members / evidence detail stays in the row expansion).
 *  Derived from the sub's own coordination spec under
 *  portfolio_signal_rules.rule_params.subs. */
export interface PortfolioTriggerLine {
  /** The sub's short display word ("trio·rsi"). */
  word: string;
  /** The condition the sub's member witnesses coordinate on. */
  text: string;
}

/** The coordination spec's shape over rule_params.subs.<sub>
 *  (engine-written keys — the fresh trio carries tail_pcts + the
 *  witnesses list and NO witness_window, same-day; the retired
 *  coordination subs carried their own scalar keys). */
interface SubSpecView {
  observational?: boolean;
  /** The sub's STORED RULE CONDITION line (2026-10-25, user-directed
   *  "add Rule condition in Rule params ... all UI data must be
   *  sourced from DB") — the engine-written plain-language trigger
   *  sentence the UI parses verbatim; the composed prose per kind
   *  below stays only for rows written before the field existed. */
  condition?: unknown;
  coordination?: {
    kind?: string;
    witnesses?: unknown;
    tail_pcts?: unknown;
    tail_pct?: unknown;
    window?: unknown;
    mkt_tail_pct?: unknown;
    rsi_tail_pct?: unknown;
    witness_window?: unknown;
  };
  /** The sub's CELL-SIGNAL condition records — one per witness curve,
   *  engine-written (payload conditions, no rule rows behind them);
   *  the cell-signals table renders these verbatim. */
  cell_signals?: unknown;
}

/** One stored CELL-SIGNAL condition record under
 *  rule_params.subs.<sub>.cell_signals (engine-written). ONE FLAT
 *  array per sub (2026-10-26, user-directed "there are RSI(10) · 3
 *  witnesses vs RSI(14) · 3 witnesses, remove them"): the trio's
 *  THREE witness curves, the spread subs' two role legs — no per-RSI-
 *  window keying (the mapped registered cells classify by window in
 *  the cell surface instead). ``mapped_signals`` — the registered
 *  mov_rsi sub-type labels this portfolio rule maps at the witness's
 *  OWN curve, action-side aligned (2026-10-25, user-directed "ensure
 *  mapping, ok to duplicate the mappings"); an empty list on rows
 *  predating the field falls back to a code match. */
export interface SubCellSignalSpec {
  sub_type: string;
  code: string;
  role: string;
  condition: string;
  bar: string;
  /** The record's OWN ACTION label word ("buy"/"sell") — engine-written
   *  (2026-10-26, user-directed "since cell signal carry action, should
   *  also show action on UI"); null on rows predating the field. */
  action: string | null;
  mapped_signals: string[];
}

/** The picked sub's stored cell-signal condition records; null when
 *  the portfolio carries no subs spec / the sub is missing / the list
 *  is malformed. The trio's records are KEYED BY RSI WINDOW
 *  (cell_signals.rsi{w} — user-directed "rsi10 and rsi14 should be
 *  part of key field ... for better search"): the window derives from
 *  the group key; the flat legacy array (records carrying their own
 *  window) still parses. No composition — the UI renders these
 *  verbatim (user-directed "all UI data must be sourced from DB"). */
export function subSpecCells(
  p: PortfolioSignalGroup,
  subType?: string | null,
): SubCellSignalSpec[] | null {
  const subs = (p.rule_params?.subs ?? {}) as Record<string, unknown>;
  const key = subType != null && subs[subType] != null
    ? subType : Object.keys(subs)[0];
  if (key == null) return null;
  const raw = (subs[key] as { cell_signals?: unknown })?.cell_signals;
  const out: SubCellSignalSpec[] = [];
  const push = (o: Record<string, unknown>) => {
    if (o != null && typeof o.sub_type === "string"
        && typeof o.code === "string" && typeof o.condition === "string") {
      out.push({
        sub_type: o.sub_type,
        code: o.code,
        role: typeof o.role === "string" ? o.role : "",
        condition: o.condition,
        bar: typeof o.bar === "string" ? o.bar : "",
        action: typeof o.action === "string" && o.action
          ? o.action : null,
        mapped_signals: Array.isArray(o.mapped_signals)
          ? o.mapped_signals.filter(
              (s): s is string => typeof s === "string")
          : [],
      });
    }
  };
  if (Array.isArray(raw)) {
    for (const w of raw) push(w as Record<string, unknown>);
  } else if (raw != null && typeof raw === "object") {
    // the pre-2026-10-26 keyed shape ({rsi10: [...], rsi14: [...]})
    // flattens — the window key is dropped with the grouping
    for (const recs of Object.values(raw)) {
      if (Array.isArray(recs)) {
        for (const r of recs) push(r as Record<string, unknown>);
      }
    }
  }
  return out.length > 0 ? out : null;
}

/** The witnesses list word: ["leg_a","leg_b","market"] →
 *  "leg A + leg B + the broad market"; null when absent/malformed. */
const witnessSubjects = (v: unknown): string | null =>
  Array.isArray(v) && v.length > 0
    ? v
        .map((w) =>
          typeof w === "string" ? (WITNESS_WORDS[w] ?? w) : String(w),
        )
        .join(" + ")
    : null;

const WITNESS_WORDS: Record<string, string> = {
  leg_a: "leg A",
  leg_b: "leg B",
  market: "the broad market",
};

/** The roster's trigger lines, narrowed to ONE coordinated sub rule
 *  when ``subType`` picks one (the host dropdown's focus — the same
 *  pick the expansion / markers / history narrow to); null/undefined =
 *  every sub's line. A picked sub the portfolio lacks yields []. */
export function portfolioTriggerLines(
  p: PortfolioSignalGroup,
  subType?: string | null,
): PortfolioTriggerLine[] {
  const subs = (p.rule_params?.subs ?? {}) as Record<string, SubSpecView>;
  const pctList = (v: unknown): string | null =>
    typeof v === "number" ? `${v}%`
      : Array.isArray(v) && v.length > 0 && v.every((x) => typeof x === "number")
        ? `${v.join("/")}%`
        : null;
  const dayList = (v: unknown): string | null =>
    typeof v === "number" ? `${v}d`
      : Array.isArray(v) && v.length > 0 && v.every((x) => typeof x === "number")
        ? `windows ${v.join("/")}` : null;
  const lines: PortfolioTriggerLine[] = [];
  const labels = Object.keys(subs)
    .filter((s) => subType == null || s === subType)
    .sort(
      (a, b) => subOrderRank(a) - subOrderRank(b) || a.localeCompare(b),
    );
  for (const sub of labels) {
    const spec = subs[sub];
    const c = spec?.coordination;
    // the STORED RULE CONDITION parses verbatim when the engine wrote
    // one (2026-10-25, user-directed "all UI data must be sourced from
    // DB"); the composed prose below stays for rows written before
    // the field existed
    const stored = typeof spec?.condition === "string" && spec.condition
      ? spec.condition : null;
    if (stored == null && c == null) continue;
    if (stored != null) {
      lines.push({
        word: signalSubWordLoose(sub)
          + (spec?.observational === true ? " (obs)" : ""),
        text: stored,
      });
      continue;
    }
    const w = typeof c.witness_window === "number" ? c.witness_window : 5;
    // the trio kinds load their subjects + tails from the spec's own
    // keys (witnesses / tail_pcts); the prose is the fallback shape
    const tails = pctList(c.tail_pcts ?? c.tail_pct);
    const subjects = witnessSubjects(c.witnesses);
    let text: string | null = null;
    if (c.kind === "trio_rsi_tail") {
      text = `${subjects ?? "both legs + the broad market"} in their own RSI capitulation tails the same day${
        tails != null ? ` · tails ${tails}` : ""}`;
    } else if (c.kind === "trio_rsi_tail_mirror") {
      text = `${subjects ?? "all three curves"} at their RSI exhaustion tails the same day — the blow-off mirror${
        tails != null ? ` · tails ${tails}` : ""}`;
    } else if (c.kind === "hp_entry_legs_rsi") {
      const tails = pctList(c.tail_pct);
      text = `market + both legs' RSI capitulation pair within ≤${w}d${
        tails != null ? ` · tails ${tails}` : ""}`;
    } else if (c.kind === "hp_entry_rsi_legs") {
      const tails = pctList(c.tail_pct);
      text = `both legs' RSI capitulation pair within ≤${w}d${
        tails != null ? ` · tails ${tails}` : ""} (market-free)`;
    } else if (c.kind === "hp_entry_wash_rsi") {
      const win = dayList(c.window);
      const rsi = pctList(c.rsi_tail_pct);
      text = `one leg's ${win ?? "W-day"} Donchian-floor breakdown + the other leg's RSI ${
        rsi ?? "3%"} witness within ≤${w}d`;
    } else if (c.kind === "hp_entry_wash_mkt1") {
      const win = dayList(c.window);
      const mkt = pctList(c.mkt_tail_pct);
      text = `one leg's ${win ?? "W-day"} Donchian-floor breakdown + market RSI ${
        mkt ?? "3%"} witness within ≤${w}d`;
    } else if (c.kind === "hp_entry_wash") {
      const win = dayList(c.window);
      const mkt = pctList(c.mkt_tail_pct);
      text = `both legs' ${win ?? "W-day"} Donchian-floor breakdown${
        mkt != null ? ` + market RSI ${mkt} witness` : ""
      } pair within ≤${w}d`;
    }
    if (text == null) continue;
    lines.push({
      word: signalSubWordLoose(sub)
        + (subs[sub]?.observational === true ? " (obs)" : ""),
      text,
    });
  }
  return lines;
}

/** The PORTFOLIO-EVENT class of one coordinated sub rule — the
 *  history panel's main-row vocabulary (the cells hide inside the row
 *  expansion). The shipped vocabulary: the ENTRY subs (the trio
 *  co-wash + the spread bottom convergence, both buy-the-pair) and
 *  the spread TOP-tail EXIT (2026-10-25 evening); the retired exit /
 *  tilt subs still decode from old history rows. */
export type PortfolioEventClass = "Entry" | "Exit" | "Tilt";

const SUB_EVENT_CLASS: Record<string, PortfolioEventClass> = {
  hp_entry_trio: "Entry",
  hp_entry_spread: "Entry",
  hp_exit_trio: "Exit",
  hp_exit_spread: "Exit",
  hp_entry_legs_rsi: "Entry",
  hp_entry_rsi_legs: "Entry",
  hp_entry_wash120_mkt: "Entry",
  hp_entry_wash_mkt1: "Entry",
  hp_entry_wash: "Entry",
  hp_entry_wash_rsi: "Entry",
  hp_entry_rho50: "Entry",
  hp_entry_catchup_reclaim60: "Entry",
  hp_exit_mkt_high: "Exit",
  hp_catchup_rsi_hi: "Exit",
  hp_harvest: "Exit",
  hp_stop_regime: "Exit",
  hp_mom_strong: "Tilt",
  hp_mom_lag: "Tilt",
};

/** The member cell's RESPONSE role (rule_params.response) — what the
 *  cell's trade acts on; PairLegsViz renders it as the legs diagram. */
export type PairResponseRole = "pair" | "spread" | "strong" | "laggard"
  | null;

export function pairResponseRole(response: unknown): PairResponseRole {
  switch (response) {
    case "inverse_vol_pair_portfolio":
      return "pair";
    case "spread_long_short":
      return "spread";
    case "strong_leg":
      return "strong";
    case "laggard_leg":
      return "laggard";
    default:
      return null;
  }
}

/** The response roles' full words (the viz's tooltip). */
export const RESPONSE_ROLE_TEXT: Record<
  Exclude<PairResponseRole, null>,
  string
> = {
  pair: "response: the inverse-vol pair portfolio (both legs)",
  spread: "response: the long-short spread curve (long leg A / short leg B)",
  strong: "response: the strong leg — sign(ratio_z) picks it same day",
  laggard: "response: the laggard leg — sign(ratio_z) picks it same day",
};

/** One sub rule's event class; prefix fallback for future hp_entry
 *  graduates, null = unclassified (the raw sub stays in the tooltip). */
export const subEventClass = (sub: string): PortfolioEventClass | null =>
  SUB_EVENT_CLASS[sub] ?? (sub.startsWith("hp_entry") ? "Entry" : null);

export const fmtPct = (v: number | undefined | null): string =>
  v == null || !Number.isFinite(v) ? "—" : `${v.toFixed(1)}%`;

/** The portfolio's benefit-audit block (the LATEST
 *  history_portfolio_signal_performance snapshot): the max_dd block
 *  audits the PORTFOLIO CONSTRUCTION. */
export interface BenefitAudit {
  as_of: string | null;
  kind: string;
  maxDd: {
    window?: string;
    portfolio_pct: number;
    leg_a_pct: number;
    leg_b_pct: number;
    worst_leg_pct: number;
    mean_leg_pct: number;
    reduction_vs_worst_pct: number;
    reduction_vs_mean_pct: number;
  };
}

export function portfolioAudit(p: PortfolioSignalGroup): BenefitAudit | null {
  const benefit = p.performance?.benefit as
    | { kind?: string; max_dd?: Record<string, number | string> }
    | null
    | undefined;
  const m = benefit?.max_dd;
  if (benefit == null || m == null) return null;
  return {
    as_of: p.performance?.as_of ?? null,
    kind: String(benefit.kind ?? ""),
    maxDd: m as BenefitAudit["maxDd"],
  };
}

/** Map one portfolio-event member record (a witness the triggered
 *  portfolio signal set, or a legacy sourced cell's record) into the
 *  shared records-table row shape — the history panel's expansions
 *  and the rules surface's cell-level-signals section share ONE
 *  mapper. */
export function portfolioMemberToRecord(
  x: Pick<PortfolioEventMemberSignal, "code"
    | "signal_type" | "signal_sub_type" | "action" | "date" | "signal"
    | "signal_reference" | "confidence"> & { time?: string },
): TradingSignal {
  const excess = x.signal - x.signal_reference;
  return {
    code: x.code,
    code_name: null,
    sec_type: "index",
    signal_type: x.signal_type,
    signal_sub_type: x.signal_sub_type,
    date: x.date,
    time: x.time ?? "15:00",
    action: x.action,
    signal_excess: excess,
    signal_excess_pct: x.signal_reference !== 0
      ? (excess / Math.abs(x.signal_reference)) * 100
      : null,
    signal: x.signal,
    signal_threshold: x.signal_reference,
    confidence: x.confidence,
    confidence_pct: x.confidence / 100,
    is_day_close_trigger: true,
    regime_state: "all",
    is_typical: null,
    is_rejection: x.action === "wait",
    count_20d: 0,
  };
}
