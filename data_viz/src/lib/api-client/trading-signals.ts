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
   *  (ROUND(10000 × signal_thresholds.confidence_base ×
   *  scale(|excess|)) — the on-bar dir_ave amplified by the row's
   *  scale model over the breach depth). The SignalActionChip input
   *  (its 1..100 ramp clamps above 100 bp, the ForecastTable
   *  treatment). */
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
  /** The event's population VARIANT: FALSE = the plain record, TRUE =
   *  the OHLCV-boost twin (the SAME trigger, confidence trust-weighted
   *  by the day-0 ohlcv score). The history panel's OHLCV toggle picks
   *  which variant renders. Live-tier rows carry no twins (FALSE). */
  is_ohlcv_boosted: boolean;
  /** The trigger day's episode-PASS attribution: TRUE = the typical
   *  pass (buy: the isolated first touch; sell: the re-test), FALSE =
   *  the re-entry pass (flicker re-crossings / continuations /
   *  isolated breakouts — the noisy population). The history panel's
   *  pass toggle filters on it. A live tick winning a day inherits the
   *  history anchor's stamp; null = no stamped anchor for the day
   *  (pre-migration rows, live-only days). */
  is_typical: boolean | null;
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
//  Signal CURVES / THRESHOLDS / RSI series — the Recent Movements
//  code-trend's threshold-vs-curve overlay panel.
// ---------------------------------------------------------------------------

/** One fitted depth-response curve (analysis_signals.signal_curves at
 *  its LATEST snapshot): curve(s) = mu_center + interp(fit_curve, s)
 *  over the Gaussian depth score s = Phi^-1(1 - q) of the day's tail
 *  probability — the pooled episode-weighted isotonic shape over EVERY
 *  tail-episode day (registrations + continuations, each day weighted
 *  1/episode_size); tail_bars maps tail pct ("1".."25") to the
 *  window's RSI bar (the s -> RSI-value mapping). */
export interface SignalCurve {
  code: string;
  sec_type: string;
  signal_type: string;
  rsi_window: number;
  /** "top" (sell) | "bottom" (buy). */
  side: string;
  /** calm / hot / panic / quiet. */
  regime_state: string;
  start_date: string;
  end_date: string;
  /** The cell's population VARIANT (PK member): FALSE = the plain
   *  fit, TRUE = the OHLCV-boost twin (the same in-band days, every
   *  statistic weight-blended by episode x day-0 ohlcv score). The
   *  overlay's toggle picks which variant to render. */
  is_ohlcv_boosted: boolean;
  /** FALSE if not usually observed for a signal, e.g., RSI re-entry
   *  is registered; TRUE if typical, e.g., RSI isolated ups/downs —
   *  the cell's episode-PASS population (PK member): TRUE = the
   *  first-touch passes' days (each episode's first tail day AND its
   *  gap-1 continuations), FALSE = the re-entry passes' days. The
   *  overlay's toggle picks which population to render. */
  is_typical: boolean;
  /** The cell's in-band day count over the window (the fit's raw
   *  sample). */
  n: number;
  /** The cell's EPISODE count (the effective sample: each episode's
   *  days carry weight 1/episode_size, summing to exactly 1). */
  n_episodes: number;
  /** y-bar: the cell's episode-weighted sign-aligned blended mean
   *  forward change at its own depth center (fraction; > 0 = the
   *  reversal worked). */
  mu_center: number;
  /** s-bar: the cell's own mean depth score. */
  s_center: number;
  /** The pooled (index class, side, regime) WEIGHTED ISOTONIC shape
   *  as {"s": offset} at the 25 1%-band-center knot depths — the PAVA
   *  values are monotone, so linear interpolation between the knots
   *  (what this client does) is a CONTINUOUS MONOTONE curve.
   *  curve(s) = mu_center + interp(fit_curve, s). Significance-gated:
   *  all-zero offsets (flat at mu_center) when the pool's |fit_t| < 2. */
  fit_curve: Record<string, number>;
  /** The pooled weighted Spearman rho(s, y) — the shape's direction
   *  and strength (gate input). */
  fit_rho: number;
  /** The cluster-adjusted significance t over the pool's EPISODE
   *  count (fit_curve is flat exactly when |fit_t| < 2). */
  fit_t: number;
  /** The cell's shallowest MEASURED depth score — draw the fit only
   *  within [s_lo, s_hi] (no extrapolation). */
  s_lo: number;
  /** The cell's deepest MEASURED depth score. */
  s_hi: number;
  /** The cell's EMPIRICAL depth response at 1% stride: band key
   *  (1..25, the deeper edge pct) -> mean sign-aligned response of
   *  the triggers whose q falls in ((k-1)%, k%] (absent = empty
   *  band) — the shape the linear mu(s) fit summarizes. */
  band_curve: Record<string, number> | null;
  /** Sign-aligned mean forward change at each horizon (fraction, null
   *  when no trigger carries the leg) — the path behind the
   *  0.10/0.15/0.50/0.15/0.10 blend's next/3d/5d/10d/20d legs. */
  mu_1d: number | null;
  mu_3d: number | null;
  mu_5d: number | null;
  mu_10d: number | null;
  mu_20d: number | null;
  /** Tail pct ("1" | "2" | "3" | "5" | "10") -> the window's RSI bar. */
  tail_bars: Record<string, number>;
}

export interface SignalCurvesResponse {
  sec_type: string;
  code: string;
  /** The curves' snapshot key (newest end_date; null = no curves). */
  end_date: string | null;
  curves: SignalCurve[];
}

/** The code's LATEST depth-response curves — the CURVE overlay's data. */
export function fetchSignalCurves(
  secType: string,
  code: string,
): Promise<SignalCurvesResponse> {
  return fetchJson<SignalCurvesResponse>(
    `/api/live-data/trading-signals/curves?sec_type=${encodeURIComponent(secType)}&code=${encodeURIComponent(code)}`,
  );
}

/** One ACTIVE threshold row of the code — the bar in the RSI's own
 *  space (the THRESHOLD overlay's lines). */
export interface SignalThreshold {
  code: string;
  sec_type: string;
  signal_type: string;
  signal_sub_type: string;
  /** "top" (sell) | "bottom" (buy). */
  side: string;
  /** calm / hot / panic / quiet. */
  regime_state: string;
  action: string;
  signal_threshold: number | null;
  confidence_base: number | null;
  scale_method: string | null;
  scale_base: number | null;
  scale_factor: number | null;
  start_date: string;
  end_date: string;
}

export interface SignalThresholdsResponse {
  sec_type: string;
  code: string;
  thresholds: SignalThreshold[];
}

/** The code's ACTIVE signal thresholds — the THRESHOLD overlay's data. */
export function fetchSignalThresholds(
  secType: string,
  code: string,
): Promise<SignalThresholdsResponse> {
  return fetchJson<SignalThresholdsResponse>(
    `/api/live-data/trading-signals/thresholds?sec_type=${encodeURIComponent(secType)}&code=${encodeURIComponent(code)}`,
  );
}

/** One daily RSI row (every mov_ave_rsi window column). */
export interface SignalRsiRow {
  date: string;
  rsi_3: number | null;
  rsi_6: number | null;
  rsi_10: number | null;
  rsi_14: number | null;
  rsi_20: number | null;
  rsi_60: number | null;
}

export interface SignalRsiResponse {
  sec_type: string;
  code: string;
  rows: SignalRsiRow[];
}

/** The code's FULL daily RSI series — the threshold overlay's lines. */
export function fetchSignalRsiSeries(
  secType: string,
  code: string,
): Promise<SignalRsiResponse> {
  return fetchJson<SignalRsiResponse>(
    `/api/live-data/trading-signals/rsi?sec_type=${encodeURIComponent(secType)}&code=${encodeURIComponent(code)}`,
  );
}
