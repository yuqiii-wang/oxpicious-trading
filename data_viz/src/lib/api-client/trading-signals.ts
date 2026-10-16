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
  /** The event's RETIRED population-variant marker: the ohlcv-boost
   *  twins were retired 2026-10-16 (the boost's tuned confidence lives
   *  on signal_rules.ohlcv_boost_conf, shown in the rules panel) — the
   *  column reads FALSE on every row. */
  is_ohlcv_boosted: boolean;
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
  /** The gate the cell passed: dir_ave minimum (pct) + sample floor +
   *  whether the population is observational (ungated, e.g. the
   *  options_wall VRP-fail twin). */
  gate?: {
    metric?: string;
    min_pct?: number | null;
    sample_floor?: number | null;
    passed?: boolean;
    ungated?: boolean;
  };
  /** The measured evidence on the cell's trigger days. */
  measure?: {
    n_episodes?: number;
    dir_ave_pct?: number;
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
   *  wall_dist_pct/corr-slope interactions carry their own keys. */
  bar_def?: Record<string, unknown>;
  /** Family extras (window, kind, side policy, VRP gate def...). */
  [key: string]: unknown;
}

/** The family knowledge document (static.analysis_signal_docs —
 *  seeded from database/sql/static/analysis_signal_docs/, joined per
 *  family). Only the fields the rules panel reads are typed; the rest
 *  rides as unknown. */
export interface SignalRuleDocument {
  name?: string;
  description?: string;
  condition?: string;
  /** The per-sub-signal knowledge the expanded rule row shows. */
  sub_signals?: Record<
    string,
    {
      meaning?: string;
      trigger?: string;
      action?: Record<string, string>;
      params?: Record<string, unknown>;
      [key: string]: unknown;
    }
  >;
  principles?: Record<string, string>;
  [key: string]: unknown;
}

/** One RULE CELL (analysis_signals.signal_rules) — the self-adaptive
 *  rule the engine measured for THIS (code, config, trend, regime,
 *  action); the history events are populated by REPLAYING it. */
export interface SignalRule {
  code: string;
  sec_type: string;
  signal_type: string;
  signal_sub_type: string;
  /** top / bottom / rise / fall / flat — the code's own trend
   *  classification in force for the cell. */
  trend: string;
  /** calm / hot / panic / quiet / all (pooled). */
  regime_state: string;
  /** buy / sell / wait. */
  action: string;
  /** The per-cell GATE VERDICT (denormalized rule_params.gate.passed):
   *  true iff the cell's measured future returns cleared its side's
   *  dir_ave bar at the sample floor. false = observational (wait
   *  watchlist / ungated twin) — negative future returns are
   *  discouraged: never valid, never trades, never fires live. */
  is_valid: boolean;
  /** The engine entry class that runs the rule. */
  rule_entry: string;
  rule_params: SignalRuleParams;
  /** The ohlcv boost's trust-weighted confidence BASE for the cell
   *  (pct — the same scale as rule_params.confidence.base_pct):
   *  sum(w*y)/sum(w) over the cell's measurable trigger days. NULL =
   *  the side boosted nothing (ship gates failed / family not opted
   *  in) — an honest absence, never a gate. */
  ohlcv_boost_conf: number | null;
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
