// ---------------------------------------------------------------------------
//  Live Data — Trading Signals (analysis scheme) client.
//
//  POST /api/live-data/trading-signals/run spawns
//  `python -m live.live_signals --sec-type <csv>` (batch mode: every code
//  with ACTIVE analysis_signals signal configs of the given sec_types;
//  codes without intraday price are skipped server-side). Fired by the
//  Trading Signals page's Refresh button and the once-per-biz-day 13:30
//  scheduler (useTradingSignalsSchedule).
//
//  GET /api/live-data/trading-signals returns ONE day's triggered breach
//  records (confidence DESC) + the date roster + the resolved date
//  (param || Asia/Shanghai biz today — the same "biz today" the live
//  markets pages use).
//
//  GET /api/live-data/trading-signals/configs returns the ACTIVE
//  signal_type / signal_sub_type combos — the page's signal menu
//  (default = all).
//
//  GET /api/live-data/trading-signals/history returns EVERY signal event
//  of one code (newest first): analysis_signals.history_signals carries
//  the multi-year plot bulk, live_signals contributes ONLY the viewed
//  day's last 20 trading days (its ticks preferred in that window), one
//  record per config-day — plus the window boundary: the row-expansion
//  panel scopes its history TABLE to the recent window while the
//  code-trend chart draws every row as a buy/sell marker.
// ---------------------------------------------------------------------------
import { fetchJson } from "./_cache";

/** Response of POST /api/live-data/trading-signals/run. */
export interface TradingSignalsRunResponse {
  success: boolean;
  sec_types?: string[];
  process_id_tag?: string;
  already_running?: boolean;
  stdout_tail?: string;
  stderr_tail?: string;
}

/** One ACTIVE config combo (signal menu entry). */
export interface TradingSignalConfig {
  signal_type: string;
  signal_sub_type: string;
  n_configs: number;
}

export interface TradingSignalConfigsResponse {
  sec_type: string;
  configs: TradingSignalConfig[];
}

/** One signal event record (analysis_signals.history_signals ∪
 *  live.live_signals, deduped to one per config-day). */
export interface TradingSignal {
  code: string;
  /** Display name from stats.<sec>_identity (latest row per code);
   *  null when no identity row is available. */
  code_name: string | null;
  sec_type: string;
  signal_type: string;
  signal_sub_type: string;
  date: string;
  time: string;
  action: string;
  /** signal - signal_threshold; sign = breach direction
   *  (positive = upward/above, negative = downward/below). */
  signal_excess: number;
  /** Unitless breach depth in %: signal_excess / |signal_threshold| * 100.
   *  null when signal_threshold = 0 (guarded by NULLIF). */
  signal_excess_pct: number | null;
  signal: number;
  signal_threshold: number;
  /** The breach's SCALED expected-move confidence in BASIS POINTS
   *  (ROUND(10000 × the firing rule cell's confidence base — the
   *  measured dir_ave — × scale(|excess|)) — the on-bar base amplified
   *  by the cell's depth model over the breach depth). The
   *  SignalActionChip input (its 1..100 ramp clamps above 100 bp). */
  confidence: number;
  /** The same confidence as the EXPECTED-MOVE PERCENT (confidence / 100,
   *  e.g. 0.87 = 0.87%) — the display number (text cells + tooltips). */
  confidence_pct: number;
  /** TRUE = day-close mirror row (analysis run, time 15:00);
   *  FALSE = intraday live-monitor breach. */
  is_day_close_trigger: boolean;
  /** The breach bar's DATE market regime — the stats.market_regimes day
   *  label (calm / hot / panic / quiet); recorded context, never a gate,
   *  and the same split the strategy itself registered under. */
  regime_state: string;
  /** The trigger day's episode-PASS attribution: TRUE = the typical
   *  pass (buy: the isolated first touch; sell: the re-test), FALSE =
   *  the re-entry pass (flicker re-crossings / continuations /
   *  isolated breakouts — the noisy population). Engine-stamped
   *  context (no toggle filters on it since the pass toggle's removal
   *  2026-10-16). A live tick winning a day inherits the history
   *  anchor's stamp; null = no stamped anchor for the day
   *  (pre-migration rows, live-only days). */
  is_typical: boolean | null;
  /** TRUE = the condition FIRED and the owning strategy's gate
   *  explicitly DECLINED it (options_wall's VRP-rejected put breaches
   *  — the 2026-10-12 no-action design): the NO-ACTION record, rendered
   *  as the grey diamond marker / the neutral NO ACTION chip, never a
   *  buy/sell triangle. FALSE = the actionable record. History rows
   *  derive it from the action code (action = wait — the history
   *  table's is_rejection column was dropped 2026-10-14). */
  is_rejection: boolean;
  /** The code's SIGNAL-DAY count over the last 20 TRADING DAYS ending on
   *  `date`: one day = one entry (its highest-confidence row), across all
   *  signal types — the main table's "20d count" column. */
  count_20d: number;
}

export interface TradingSignalsResponse {
  sec_type: string;
  /** The date the rows are for (param || biz today). */
  date: string;
  /** Dates present in live_signals for the sec_type (newest first). */
  available_dates: string[];
  signals: TradingSignal[];
}

/** Trigger one batch run of `python -m live.live_signals --sec-type …`.
 *  Resolves when the run finishes (or is deduped). Never throws. */
export async function runTradingSignals(
  secTypes: ReadonlyArray<string>,
): Promise<TradingSignalsRunResponse> {
  try {
    const res = await fetch("/api/live-data/trading-signals/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sec_types: [...secTypes] }),
    });
    if (!res.ok) {
      return { success: false, stderr_tail: `HTTP ${res.status}` };
    }
    return (await res.json()) as TradingSignalsRunResponse;
  } catch (e) {
    return { success: false, stderr_tail: String(e) };
  }
}

/** Trigger one batch run of `python -m analysis.analysis_signals --live …`:
 *  the analysis-signal pipeline + day-close mirror (every not-yet-recorded
 *  signal day becomes ONE live_signals observation at that day's close,
 *  time 15:00). Used for old-date refreshes, where no intraday data exists.
 *  Resolves when the run finishes (or is deduped). Never throws. */
export async function runTradingSignalsAnalysis(
  secTypes: ReadonlyArray<string>,
): Promise<TradingSignalsRunResponse> {
  try {
    const res = await fetch("/api/live-data/trading-signals/run-analysis", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sec_types: [...secTypes] }),
    });
    if (!res.ok) {
      return { success: false, stderr_tail: `HTTP ${res.status}` };
    }
    return (await res.json()) as TradingSignalsRunResponse;
  } catch (e) {
    return { success: false, stderr_tail: String(e) };
  }
}

/** Running-state of the trading-signals runs (intraday + analysis
 *  day-close) — polled so a page refresh restores the Refresh button's
 *  spinning state. TRUE while ANY of the two tags is running. */
export async function fetchTradingSignalsRunStatus(): Promise<boolean> {
  const res = await fetch(
    "/api/live-data/trading-signals/run/status",
  );
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  }
  const json = (await res.json()) as { status: Record<string, boolean> };
  return Object.values(json.status ?? {}).some(Boolean);
}

/** The ACTIVE signal-type / sub-type menu for a sec_type. */
export function fetchTradingSignalConfigs(
  secType: string,
): Promise<TradingSignalConfigsResponse> {
  return fetchJson<TradingSignalConfigsResponse>(
    `/api/live-data/trading-signals/configs?sec_type=${encodeURIComponent(secType)}`,
  );
}

/** One day's triggered signals (confidence DESC). `date === null` means
 *  biz today (the server resolves Asia/Shanghai today — the same biz day
 *  the live markets pages use). */
export function fetchTradingSignals(
  secType: string,
  date: string | null,
): Promise<TradingSignalsResponse> {
  const params = new URLSearchParams({ sec_type: secType });
  if (date) params.set("date", date);
  return fetchJson<TradingSignalsResponse>(
    `/api/live-data/trading-signals?${params.toString()}`,
  );
}

export interface TradingSignalHistoryResponse {
  sec_type: string;
  code: string;
  /** Echoed filters (null = not narrowed). */
  signal_type: string | null;
  signal_sub_type: string | null;
  /** Echoed window anchor (null = the server anchored on the newest
   *  baseline close). */
  date: string | null;
  /** 'YYYY-MM-DD' — the 20th-most-recent close date before the anchor:
   *  signal rows with date > this are the anchor's last 20 trading days
   *  (the history TABLE's scope; the chart plots every row). null when
   *  the sec_type has fewer than 20 baseline closes. */
  recent_window_start: string | null;
  /** EVERY signal event of the code, newest first (history_signals ∪
   *  live_signals, one per config-day). */
  signals: TradingSignal[];
}

/** One code's FULL signal history (all dates) — drives the expanded row's
 *  history table (scoped to the last 20 trading days ending on `date`,
 *  the viewed day) + the buy/sell markers on its code-trend chart (every
 *  row). Optional signalType / signalSubType narrow the history to one
 *  signal family. */
export function fetchTradingSignalHistory(
  secType: string,
  code: string,
  signalType?: string | null,
  signalSubType?: string | null,
  date?: string | null,
): Promise<TradingSignalHistoryResponse> {
  const params = new URLSearchParams({ sec_type: secType, code });
  if (signalType) params.set("signal_type", signalType);
  if (signalSubType) params.set("signal_sub_type", signalSubType);
  if (date) params.set("date", date);
  return fetchJson<TradingSignalHistoryResponse>(
    `/api/live-data/trading-signals/history?${params.toString()}`,
  );
}

// ---------------------------------------------------------------------------
//  Portfolio HISTORY SIGNALS — the portfolio tier's EVENT history
//  (analysis_signals.portfolio_history_signals, 2026-10-21): one row
//  per coordinated PORTFOLIO event with THE EVENT DATE and the member
//  association — the event's coordinated MEMBER ACTIONS under the
//  member-action invariant (at least TWO actions on at least TWO codes
//  per event, different dates allowed): the sourced component cell
//  events (each with its OWN date — different sub signals on different
//  nearby days; the components' own history is dropped from
//  history_signals, the member records ride the payload), the legs'
//  own witness records, and the pair response legs on the event day.
//  hp_harvest is the measured one-leg exception. Drives the Hedge
//  Pairs page's expandable history rows.
// ---------------------------------------------------------------------------

/** One coordinated member action of a portfolio event: a sourced
 *  component cell's episode-first record, a leg's own witness record
 *  (its own day), or the leg's response action on the event day. */
export interface PortfolioEventMemberSignal {
  code: string;
  signal_type: string;
  signal_sub_type: string;
  action: string;
  /** 'YYYY-MM-DD' — the member's OWN day (a witness up to the window
   *  before the portfolio event's date; a response leg ON it). */
  date: string;
  signal: number;
  signal_reference: number;
  /** Basis points — the member record's own scaled confidence. */
  confidence: number;
}

/** One PORTFOLIO signal event: the coordinated event's own record +
 *  its member actions (>= 2 actions on >= 2 codes per the invariant;
 *  hp_harvest the measured one-leg exception). */
export interface PortfolioHistorySignal {
  code: string;
  portfolio_type: string;
  /** The event's PORTFOLIO RULE, decoded (trio_wash / spread — the
   *  2026-10-25 split). */
  portfolio_sub_type: string;
  signal_type: string;
  signal_sub_type: string;
  /** 'YYYY-MM-DD' — the EVENT date (the day the coordination
   *  completed — every component witness alive). */
  date: string;
  time: string;
  action: string;
  signal: number;
  signal_reference: number;
  signal_excess: number;
  /** Basis points — the composite event's scaled confidence. */
  confidence: number;
  confidence_pct: number;
  regime_state: string;
  is_typical: boolean | null;
  members: PortfolioEventMemberSignal[];
}

export interface PortfolioSignalHistoryResponse {
  sec_type: string;
  code: string;
  /** Echoed filters (null = not narrowed). */
  signal_type: string | null;
  signal_sub_type: string | null;
  events: PortfolioHistorySignal[];
}

/** One portfolio code's FULL coordinated-event history (newest first)
 *  with each event's member cell-level signals. Optional signalType /
 *  signalSubType narrow to one family / sub. */
export function fetchPortfolioSignalHistory(
  secType: string,
  code: string,
  signalType?: string | null,
  signalSubType?: string | null,
): Promise<PortfolioSignalHistoryResponse> {
  const params = new URLSearchParams({ sec_type: secType, code });
  if (signalType) params.set("signal_type", signalType);
  if (signalSubType) params.set("signal_sub_type", signalSubType);
  return fetchJson<PortfolioSignalHistoryResponse>(
    `/api/live-data/trading-signals/portfolio-history?${params.toString()}`,
  );
}

// ---------------------------------------------------------------------------
//  Signal RULES — the Recent Movements code-trend's rules panel.
//  (The curve/threshold tier was RETIRED with its tables 2026-10-14;
//  signal_rules replaces it.)
// ---------------------------------------------------------------------------

/** The rule_params JSONB payload of a rule cell — the SELF-ADAPTIVE
 *  prepared parameters the engine measured on the code's own history
 *  for THIS (code, config, trend, regime, action) cell. */
export interface SignalRuleParams {
  /** e.g. "ma_cross:ma120_gap" — the family:sub identity of the axis. */
  axis?: string;
  /** The episode merge K (out-of-band traded days absorbed as the same
   *  episode). */
  episode_merge_days?: number;
  /** The gate's BAR constants (the stored constant rule_params; since
   *  the 2026-10-20 combined-tier move the VERDICT no longer merges
   *  in — it is the row's is_valid, and the starvation haircut is
   *  recomputed by readers as min(measure.n_episodes / sample_floor,
   *  1)). ungated marks the observational populations (the
   *  options_wall VRP-fail twin) — never a trading row. */
  gate?: {
    metric?: string;
    min_pct?: number | null;
    sample_floor?: number | null;
    ungated?: boolean;
  };
  /** The measured evidence on the cell's trigger days (the latest
   *  performance snapshot's measure block — merged at the API
   *  boundary). */
  measure?: {
    n_episodes?: number;
    dir_ave_pct?: number;
    dir_std_pct?: number | null;
    t_stat?: number | null;
    hit_rate?: number;
  };
  /** The confidence scale: base_pct = the on-bar measured base in
   *  PERCENT (the cell's measured dir_ave), depth_factor x breach
   *  depth (capped) amplifies it. */
  confidence?: {
    base_pct?: number;
    depth_factor?: number;
    depth_cap?: number;
  };
  /** The breach bar DEFINITION (family-specific shape — read
   *  defensively): gap_pct/cross_excess/rolling_quantile/fixed_sigma/
   *  wall_dist_pct/corr-slope interactions carry their own keys.
   *  (The variant-axes stamps pe_boosted / ohlcv_boosted and the
   *  bar_def.pe_gate / bar_def.ohlcv_gate fragments left the rows
   *  with the 2026-11-01 variant-axes unlink.) */
  bar_def?: Record<string, unknown>;
  /** Family extras (window, kind, side policy, VRP gate def...). */
  [key: string]: unknown;
}

/** The family knowledge document (static.signal_rules_docs —
 *  seeded from database/sql/static/signal_rules/, joined per
 *  family). The seed files carry ONLY what the rules panel renders —
 *  the per-sub-signal meaning + trigger (pruned 2026-10-27). */
export interface SignalRuleDocument {
  sub_signals?: Record<
    string,
    {
      meaning?: string;
      trigger?: string;
    }
  >;
}

/** One RULE CELL (analysis_signals.signal_rules) — the self-adaptive
 *  rule the engine measured for THIS (code, config, trend, regime,
 *  action); the history events are populated by REPLAYING it. */
export interface SignalRule {
  code: string;
  sec_type: string;
  signal_type: string;
  signal_sub_type: string;
  /** The rule row's SURROGATE ID — the one-to-one join key
   *  portfolio_signal_rules manages the rule cell through (each rule
   *  id under exactly one portfolio code). Identifies the row
   *  EMISSION: ids churn with the pipeline's wipe-and-reinsert runs. */
  signal_rule_id: number;
  /** top / bottom / rise / fall / flat — the code's own trend
   *  classification in force for the cell. */
  trend: string;
  /** calm / hot / panic / quiet / all (pooled). */
  regime_state: string;
  /** buy / sell / wait. */
  action: string;
  /** The per-cell GATE VERDICT (the unified dir_ave test): true iff
   *  the cell's measured future returns cleared its side's dir_ave
   *  bar. false = observational (the ungated twin) — negative future
   *  returns are discouraged: never valid, never trades, never fires
   *  live. */
  is_valid: boolean;
  /** Whether the cell belongs to the PORTFOLIO tier (a signal_rules PK
   *  member since 2026-10-23): an existing signal rule SOURCED by a
   *  portfolio is a SEPARATE row from the standalone one. true = the
   *  row was stamped by a portfolio family's source layer (the hedge
   *  mov_rsi / rf_break component rows at the market + leg codes —
   *  claimed by an orchestration or not) or is a pair-native member;
   *  false = the standalone families' rows and the orchestrating
   *  composites (the group IS the composite). The cell's own test
   *  stays the unified dir_ave gate; the portfolio tier's combined
   *  audit lives in history_portfolio_signal_performance. */
  is_in_portfolio: boolean;
  /** The engine entry class that runs the rule. */
  rule_entry: string;
  rule_params: SignalRuleParams;
  /** 'YYYY-MM-DD' — the as-of date of the latest performance snapshot
   *  merged onto this row (the frame data horizon the cell was last
   *  measured through); null when no snapshot exists. */
  performance_as_of: string | null;
  document: SignalRuleDocument;
  created_at: string;
}

export interface SignalRulesResponse {
  sec_type: string;
  code: string;
  rules: SignalRule[];
}

/** The code's rule cells — every (sub signal, trend, regime, action)
 *  cell the engines measured and shipped (the RULES panel). */
export function fetchSignalRules(
  secType: string,
  code: string,
): Promise<SignalRulesResponse> {
  const params = new URLSearchParams({ sec_type: secType, code });
  return fetchJson<SignalRulesResponse>(
    `/api/live-data/trading-signals/rules?${params.toString()}`,
  );
}

// ---------------------------------------------------------------------------
//  Signal PORTFOLIOS — the portfolio tier (2026-10-18, refactored to
//  per-sub-type groups): portfolio_signal_rules holds one row per
//  (code, portfolio_type, signal_sub_type) whose signal_rule_ids
//  BIGINT[] carries the group's rule ids. fetchSignalPortfolios is
//  the per-code roster; fetchPortfolioSignals matches portfolios by
//  the requested CODES (the pair types: both 'A~B' legs in the list)
//  and expands the ids to full rule rows — the shared
//  portfolio-signal component's one round trip.
// ---------------------------------------------------------------------------

/** One managed PORTFOLIO code's census (the roster row) — one row per
 *  PORTFOLIO RULE (code x portfolio_sub_type since the 2026-10-25
 *  split: trio wash and spread are separate portfolio signals). */
export interface SignalPortfolio {
  code: string;
  /** The portfolio tier's own vocabulary, decoded (today always
   *  neg_corr_hedge_pair — the two-leg 'A~B' hedge pair). */
  portfolio_type: string;
  /** The PORTFOLIO RULE within the type, decoded (trio_wash /
   *  spread — the 2026-10-25 split's identity axis). */
  portfolio_sub_type: string;
  /** The portfolio rule's rule cells (its whole ids array: the sub's
   *  orchestrating cells + the mapped registered rsi cells). */
  n_rules: number;
  /** The portfolio rule's gate-passed (is_valid) rule cells. */
  n_valid: number;
  /** The rule's coordinated sub types (rule_params.subs keys). */
  n_sub_types: number;
  /** 'YYYY-MM-DD' — the portfolio code's latest replayed event date;
   *  null when its rules have never triggered. */
  latest_event: string | null;
  /** 'YYYY-MM-DD HH24:MI' — the pipeline run that emitted the
   *  portfolio's current rows. */
  latest_built: string;
}

export interface SignalPortfoliosResponse {
  sec_type: string;
  /** Echoed portfolio_type filter (null = all types). */
  portfolio_type: string | null;
  portfolios: SignalPortfolio[];
}

/** The managed PORTFOLIO codes of a sec_type (optionally one
 *  portfolio_type) — one row per code with its census. */
export function fetchSignalPortfolios(
  secType: string,
  portfolioType?: string | null,
): Promise<SignalPortfoliosResponse> {
  const params = new URLSearchParams({ sec_type: secType });
  if (portfolioType) params.set("portfolio_type", portfolioType);
  return fetchJson<SignalPortfoliosResponse>(
    `/api/live-data/trading-signals/portfolios?${params.toString()}`,
  );
}

/** The latest history_portfolio_signal_performance snapshot of one
 *  portfolio — the PORTFOLIO-LEVEL AUDIT (renamed 2026-10-20): WHAT
 *  THE PORTFOLIO TOOK plus the COMBINED STATS of the individual
 *  in-portfolio signals. The hedge type's claim: benefit kind
 *  "max_drawdown_reduction" — max_dd {portfolio_pct, leg_a_pct,
 *  leg_b_pct, worst_leg_pct, mean_leg_pct, reduction_vs_worst_pct,
 *  reduction_vs_mean_pct (percentage points shallower than the legs'
 *  drawdown; >= 0 = the hedge paid)} — the gate VERDICT digest
 *  {metric, min_pp, passed, conf_scale} (conf_scale = the member
 *  benefit-boost scale the passing portfolio's cells take) — the
 *  members' combined stats (pooled: {n_cells, n_episodes,
 *  dir_ave_pct, t_stat, hit_rate} — the member cells' own measures
 *  meta-combined) — and the portfolio's dd lens {min20_pct, p10_pct,
 *  baseline_pct, n} (the orchestrating cells' lenses combined). */
export interface PortfolioBenefitSnapshot {
  as_of: string | null;
  pooled: Record<string, unknown> | null;
  benefit: Record<string, unknown> | null;
  gate: Record<string, unknown> | null;
  dd: Record<string, unknown> | null;
}

/** One matched portfolio rule: its identity, census and its PORTFOLIO
 *  row (2026-10-20's cell-level redesign; the 2026-10-25 SUB SPLIT —
 *  one row per (code, portfolio_type, portfolio_sub_type): trio wash
 *  and spread are SEPARATE portfolio signals, each with its OWN ids
 *  array — the sub's orchestrating cells PLUS the MAPPED REGISTERED
 *  rsi cells (real signal_rules rows riding the is_in_portfolio TRUE
 *  tier, never portfolio rows themselves) — the per-sub coordination
 *  spec, verdict + audit). */
export interface PortfolioSignalGroup {
  code: string;
  portfolio_type: string;
  /** The PORTFOLIO RULE within the type, decoded (trio_wash / spread). */
  portfolio_sub_type: string;
  n_rules: number;
  n_valid: number;
  n_sub_types: number;
  latest_event: string | null;
  latest_built: string;
  /** The portfolio rule's member rule ids (the whole ids array — the
   *  sub's orchestrating cells + the mapped registered rsi cells;
   *  pair with the rules response on (signal_rule_id)). */
  signal_rule_ids: number[];
  /** The portfolio's GATE VERDICT: the audited benefit cleared the
   *  portfolio gate AND at least one orchestrating composite cell's
   *  own entry-rule verdict — the GATING conjunction (members see the
   *  confidence increase only on is_valid portfolios). */
  is_valid: boolean;
  /** The orchestrating RuleEntry subclasses that wrote the portfolio
   *  ('|'-joined). */
  rule_entry: string;
  rule_params: Record<string, unknown>;
  performance: PortfolioBenefitSnapshot | null;
}

export interface PortfolioSignalsResponse {
  sec_type: string;
  /** Echoed requested codes (deduped, trimmed). */
  codes: string[];
  /** The portfolios whose legs sit in the codes (pair convention:
   *  both 'A~B' legs in the list) — empty when none match. */
  portfolios: PortfolioSignalGroup[];
  /** The matched portfolios' FULL rule rows (the SignalRule shape;
   *  rule.code identifies its portfolio — pairs with the
   *  sub_types' signal_rule_ids on (code, signal_rule_id)). */
  rules: SignalRule[];
}

/** The portfolios formed from the given codes (industry scope for
 *  now — the hedge-pair legs are industry ids), with their sub-type
 *  groups and full rule rows. */
export function fetchPortfolioSignals(
  secType: string,
  codes: ReadonlyArray<string>,
): Promise<PortfolioSignalsResponse> {
  const params = new URLSearchParams({
    sec_type: secType,
    codes: codes.join(","),
  });
  return fetchJson<PortfolioSignalsResponse>(
    `/api/live-data/trading-signals/portfolio?${params.toString()}`,
  );
}

/** One contiguous same-trend run (top/bottom/rise/fall/unknown). */
export interface TrendSpan {
  trend: string;
  start_date: string;
  end_date: string;
  span_days: number;
}

export interface TrendSpansResponse {
  sec_type: string;
  code: string;
  window_days: number;
  /** Span lists keyed by trend label (only the trends with spans). */
  spans: Record<string, TrendSpan[]>;
}

/** The code's TREND spans — contiguous same-trend runs over
 *  analysis.trend_days (the rule tier's per-(code, date)
 *  classification: rise/fall the RSI re-entry episodes, top/bottom the
 *  MA255-gap extremes, unknown the unclassified bucket; default window
 *  the last 3y, API cap 10y). The shared CodeTrendChart's Trend toggle
 *  and the MaSpread Trend section fetch these (the regime spans
 *  precedent). */
export function fetchTrendSpans(
  secType: string,
  code: string,
  windowDays?: number,
): Promise<TrendSpansResponse> {
  const params = new URLSearchParams({ sec_type: secType, code });
  if (windowDays != null) params.set("window_days", String(windowDays));
  return fetchJson<TrendSpansResponse>(
    `/api/live-data/trading-signals/trend-spans?${params.toString()}`,
  );
}
