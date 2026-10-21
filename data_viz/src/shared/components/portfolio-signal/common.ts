/**
 * portfolio-signal/common — helpers shared by the portfolio tier's two
 * surfaces (PortfolioSurface reads the portfolio-tier tables, WHAT THE
 * PORTFOLIO TOOK; CellRulesSurface reads the signal-tier tables, each
 * member's own evidence). Pure helpers only.
 */
import type { PortfolioSignalGroup } from "@/lib/api-client";

/** The pipeline's portfolio families run the default INDEX scope —
 *  their rows (composite portfolio codes) carry the index stamp. */
export const DEFAULT_SEC_TYPE = "index";

/** The portfolio_type display words (decoded labels in, words out). */
const PORTFOLIO_TYPE_LABEL: Record<string, string> = {
  neg_corr_hedge_pair: "Negative-Corr Hedge Pair",
};

export const portfolioTypeLabel = (t: string): string =>
  PORTFOLIO_TYPE_LABEL[t] ?? t;

/** The two leg codes of a composite 'A~B' pair id. */
export const pairLegs = (code: string): [string, string] => {
  const [a, b] = code.split("~");
  return [a ?? code, b ?? ""];
};

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

/** The coordinated sub types' display order (entries first, then the
 *  exits — matches the rule_params.subs keys / the member cells' own
 *  sub types). The 2026-10-21 dedup: hp_harvest is the z15/z20
 *  harvest pair merged and hp_entry_wash the per-window washout trio
 *  merged (the bar / window is a candidate, not an identity); the
 *  observational hp_regime_flip is retired — hp_entry_rho50 is the
 *  hedgeable turn-on's one record. */
export const SUB_ORDER = [
  "hp_entry_legs_rsi",
  "hp_entry_wash120_mkt",
  "hp_entry_wash",
  "hp_entry_rho50",
  "hp_entry_catchup_reclaim60",
  "hp_mom_strong",
  "hp_exit_mkt_high",
  "hp_catchup_rsi_hi",
  "hp_mom_lag",
  "hp_harvest",
  "hp_stop_regime",
];

export const subOrderRank = (sub: string): number =>
  (SUB_ORDER.indexOf(sub) + 1) || 99;

/** The PORTFOLIO-EVENT class of one coordinated sub rule — the
 *  history panel's main-row vocabulary (the cells hide inside the row
 *  expansion). Entries buy the pair, the staged exits sell it
 *  (harvest trims · the catch-up · the regime stop), the momentum
 *  tilt shifts the book toward the strong leg. */
export type PortfolioEventClass = "Entry" | "Exit" | "Tilt";

const SUB_EVENT_CLASS: Record<string, PortfolioEventClass> = {
  hp_entry_legs_rsi: "Entry",
  hp_entry_wash120_mkt: "Entry",
  hp_entry_wash: "Entry",
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
export type PairResponseRole = "pair" | "strong" | "laggard" | null;

export function pairResponseRole(response: unknown): PairResponseRole {
  switch (response) {
    case "inverse_vol_pair_portfolio":
      return "pair";
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
  strong: "response: the strong leg — sign(ratio_z) picks it same day",
  laggard: "response: the laggard leg — sign(ratio_z) picks it same day",
};

/** One sub rule's event class; prefix fallback for future hp_entry
 *  graduates, null = unclassified (the raw sub stays in the tooltip). */
export const subEventClass = (sub: string): PortfolioEventClass | null =>
  SUB_EVENT_CLASS[sub] ?? (sub.startsWith("hp_entry") ? "Entry" : null);

/** The classes' display order (entries before exits before the
 *  coordination extras). */
const EVENT_CLASS_ORDER: PortfolioEventClass[] = [
  "Entry", "Exit", "Tilt",
];

/** One event day's class words: the day's records' classes deduped in
 *  display order ("Entry", "Entry · Tilt"). */
export function subEventClasses(subs: ReadonlyArray<string>): string[] {
  const set = new Set<PortfolioEventClass>();
  for (const s of subs) {
    const c = subEventClass(s);
    if (c != null) set.add(c);
  }
  return EVENT_CLASS_ORDER.filter((c) => set.has(c));
}

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
