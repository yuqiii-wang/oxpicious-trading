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
  /** The strategy's expected-move confidence in BASIS POINTS
   *  (ROUND(10000 × signal_strategies.confidence) — the chosen rung's
   *  sign-aligned dir_ave). The SignalActionChip input (its 1..100 ramp
   *  clamps above 100 bp, the ForecastTable treatment). */
  confidence: number;
  /** The same confidence as the EXPECTED-MOVE PERCENT (confidence / 100,
   *  e.g. 0.87 = 0.87%) — the display number (text cells + tooltips). */
  confidence_pct: number;
  /** The SAME bucket's ohlcv_boost cohort confidence (bp) — the
   *  score-trust-weighted measurement of the same trigger days. Add-on
   *  context shown parenthesized next to the confidence; null when the
   *  cohort never registered / non-mov_rsi family / history arm row. */
  confidence_boost: number | null;
  /** confidence_boost / 100 as the expected-move percent (the
   *  parenthetical add-on's display number). */
  confidence_boost_pct: number | null;
  /** The SAME bucket's ohlcv_selection cohort confidence (bp) — the
   *  favorable-anatomy subset's own dir_ave (bottom side only, the top
   *  side carries no selection child). */
  confidence_selection: number | null;
  /** confidence_selection / 100 as the expected-move percent. */
  confidence_selection_pct: number | null;
  /** TRUE = day-close mirror row (analysis run, time 15:00);
   *  FALSE = intraday live-monitor breach. */
  is_day_close_trigger: boolean;
  /** The breach bar's DATE market regime — the stats.market_regimes day
   *  label (calm / hot / panic / quiet); recorded context, never a gate,
   *  and the same split the strategy itself registered under. */
  regime_state: string;
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

/** Trigger one batch run of `python -m analyze.analysis_signals --live …`:
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
