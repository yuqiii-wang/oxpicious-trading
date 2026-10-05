// ---------------------------------------------------------------------------
//  Trading Signals service — DB reads behind the Live Data → Trading Signals
//  page (analysis scheme only for now).
//
//  • activeConfigs(sec_type) — the signal-type / sub-type menu: DISTINCT
//    (signal_type, signal_sub_type) from analysis_signals.signals where
//    is_active (the current threshold set).
//  • triggered(sec_type, date) — one day's rows from live.live_signals,
//    ordered by confidence DESC (the page's main list).
//  • availableDates(sec_type) — the date-selector roster (newest first):
//    dates present in live_signals UNION the newest close dates of the
//    sec_type's basic_stats baseline — the selector stays populated even
//    when no live breach has been recorded yet (every recent trading day
//    is selectable; empty days are computed on demand by the route).
//  • computeEligible(sec_type, date) — can the on-demand as-of evaluation
//    run for the date? (ACTIVE strategies exist AND the date has daily
//    closes.)
//  • history(sec_type, code[, date]) — EVERY signal event of one code
//    (newest first) behind the row-expansion panel. Two source roles:
//    analysis_signals.history_signals carries the PLOT (the multi-year
//    record, every year); live.live_signals contributes ONLY the anchor's
//    last 20 trading days (the recent slice, its ticks preferred there).
//    The response also carries the window boundary so the UI can scope
//    its history TABLE to the recent window while the PLOT keeps every
//    marker.
// ---------------------------------------------------------------------------
import { queryRows, formatDate } from "./db.service.js";
import { labelDecodeSql } from "./label-codes.js";
import type { QueryResultRow } from "pg";

/** One menu entry: a signal family + window (e.g. mov_rsi / rsi14). */
export interface TradingSignalConfigRow {
  signal_type: string;
  signal_sub_type: string;
  n_configs: number;
}

/** One triggered breach record (live.live_signals row). */
export interface TradingSignalRow {
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
   *  (ROUND(10000 × signal_thresholds.confidence_base × scale(|excess|))
   *  — the on-bar sign-aligned dir_ave amplified by the row's scale
   *  model over the breach depth). The SignalActionChip input (its
   *  1..100 ramp clamps above 100 bp, the ForecastTable treatment). */
  confidence: number;
  /** The same confidence as the EXPECTED-MOVE PERCENT (confidence / 100,
   *  e.g. 0.87 = 0.87%) — the display number (text cells + tooltips). */
  confidence_pct: number;
  /** (The confidence_boost cohort add-on fields were RETIRED with
   *  the forecast layer 2026-10-05 — the plain rsi-pct population
   *  is the only cohort.) */
  is_day_close_trigger: boolean;
  /** The breach bar's DATE market regime — the stats.market_regimes
   *  day label (calm/hot/panic/quiet). Context, not a gate: the breach
   *  still fires; the label matches the strategy's own regime split. */
  regime_state: string;
  /** The code's SIGNAL-DAY count over the LAST 20 TRADING DAYS ending on
   *  `date`: one day = one entry — the day's highest-confidence row —
   *  counted once however many rows it has (signal types, intraday
   *  re-checks, day-close). The main table's "20d count" column. */
  count_20d: number;
}

const VALID_SEC_TYPES = new Set(["index", "etf", "stock"]);

function assertSecType(secType: string | null | undefined): string {
  const st = (secType ?? "").trim();
  if (!VALID_SEC_TYPES.has(st)) {
    throw Object.assign(
      new Error(`invalid sec_type: ${secType} (expected index|etf|stock)`),
      { status: 400 },
    );
  }
  return st;
}

/** DISTINCT (signal_type, signal_sub_type) of the ACTIVE configs for a
 *  sec_type — the page's signal menu (default = all). */
export async function fetchTradingSignalConfigs(
  secType: string | null | undefined,
): Promise<TradingSignalConfigRow[]> {
  const st = assertSecType(secType);
  const rows = await queryRows<TradingSignalConfigRow & QueryResultRow>(
    `SELECT signal_type, signal_sub_type, count(*)::int AS n_configs
     FROM analysis_signals.signal_thresholds
     WHERE sec_type = $1 AND is_active
     GROUP BY signal_type, signal_sub_type
     ORDER BY signal_type, signal_sub_type`,
    [st],
  );
  return rows;
}

/** sec_type → stats identity table that carries (code, date, name).
 *  Matches the per-sec lookup pattern used by index/etf/stock baseline
 *  services (e.g. `stats.index_identity (code, date DESC) INCLUDE (name)`). */
const IDENTITY_TABLE: Record<string, string> = {
  index: "stats.index_identity",
  etf: "stats.etf_identity",
  stock: "stats.stock_identity",
};

/** sec_type → daily-close baseline (the date-selector roster source and
 *  the on-demand evaluation's daily-close fallback basis). */
const BASIC_STATS_TABLE: Record<string, string> = {
  index: "stats.index_basic_stats",
  etf: "stats.etf_basic_stats",
  stock: "stats.stock_basic_stats",
};

/** One day's triggered signals for a sec_type, confidence DESC then time
 *  DESC. `date` is 'YYYY-MM-DD' (the UI pre-resolves biz today).
 *  Joins the sec_type's identity table (latest row per code) to resolve
 *  the display name; falls back to NULL when no identity row exists.
 *  Each row also carries count_20d — the code's SIGNAL-DAY count over the
 *  last 20 TRADING DAYS ending on `date` (window anchored on the sec_type's
 *  daily-close baseline): each day enters once through its highest-
 *  confidence row, all signal types. */
export async function fetchTriggeredSignals(
  secType: string | null | undefined,
  date: string,
): Promise<TradingSignalRow[]> {
  const st = assertSecType(secType);
  const idt = IDENTITY_TABLE[st]!;
  const rows = await queryRows<TradingSignalRow & QueryResultRow>(
    `WITH win AS (
        -- 20th-most-recent DISTINCT close date <= the anchor:
        -- (win_start, anchor] spans exactly the last 20 trading days.
        -- DISTINCT: the baseline is per-(code, date) — one date has many
        -- rows, and OFFSET over raw rows would land less than a day back.
        SELECT DISTINCT date AS win_start
        FROM ${BASIC_STATS_TABLE[st]!}
        WHERE date <= $2::date AND close IS NOT NULL
        ORDER BY win_start DESC
        OFFSET 19 LIMIT 1
     )
     SELECT s.code,
            n.name                       AS code_name,
            s.sec_type,
            s.signal_type,
            s.signal_sub_type,
            to_char(s.date, 'YYYY-MM-DD') AS date,
            to_char(s.time, 'HH24:MI')    AS time,
            ${labelDecodeSql("action", "s.action")} AS action,
            s.signal_excess::float8       AS signal_excess,
            s.signal_excess_pct::float8    AS signal_excess_pct,
            s.signal::float8              AS signal,
            s.signal_threshold::float8     AS signal_threshold,
            s.confidence,
            (s.confidence::float8 / 100.0) AS confidence_pct,
            s.is_day_close_trigger,
            ${labelDecodeSql("regime", "s.regime_state")} AS regime_state,
            (SELECT count(DISTINCT ls.date)::int
             FROM live.live_signals ls
             WHERE ls.sec_type = s.sec_type
               AND ls.code = s.code
               AND ls.date <= $2::date
               AND ls.date > COALESCE(
                     (SELECT win_start FROM win), '1900-01-01'::date)
            ) AS count_20d
     FROM live.live_signals s
     LEFT JOIN LATERAL (
       SELECT i.name FROM ${idt} i
       WHERE i.code = s.code
       ORDER BY i.date DESC LIMIT 1
     ) n ON TRUE
     WHERE s.sec_type = $1 AND s.date = $2::date
     ORDER BY s.confidence DESC, s.time DESC, s.code, s.signal_type,
              s.signal_sub_type`,
    [st, date],
  );
  return rows;
}

/** Dates present in live_signals for a sec_type (roster, newest first). */
export async function fetchTradingSignalDates(
  secType: string | null | undefined,
): Promise<string[]> {
  const st = assertSecType(secType);
  const rows = await queryRows<{ date: string } & QueryResultRow>(
    `SELECT date FROM (
        SELECT date FROM live.live_signals WHERE sec_type = $1
        UNION
        SELECT date FROM (
            SELECT DISTINCT date FROM ${BASIC_STATS_TABLE[st]!}
            WHERE close IS NOT NULL
            ORDER BY date DESC LIMIT 120
        ) roster
     ) u
     ORDER BY date DESC
     LIMIT 120`,
    [st],
  );
  return rows.map((r) => formatDate(r.date));
}

/** Can the on-demand as-of evaluation run for (sec_type, date)?
 *  TRUE iff the sec_type has ACTIVE signal strategies (a threshold set
 *  to breach) AND the date carries daily closes (an evaluation basis:
 *  intraday-last-bar replay when the intraday table covers the date,
 *  official daily close otherwise — the python-side fallback). */
export async function isTradingSignalComputeEligible(
  secType: string | null | undefined,
  date: string,
): Promise<boolean> {
  const st = assertSecType(secType);
  const rows = await queryRows<{ eligible: boolean } & QueryResultRow>(
    `SELECT (
        EXISTS (SELECT 1 FROM analysis_signals.signal_thresholds
                WHERE sec_type = $1 AND is_active)
        AND EXISTS (SELECT 1 FROM ${BASIC_STATS_TABLE[st]!}
                    WHERE date = $2::date AND close IS NOT NULL)
     ) AS eligible`,
    [st, date],
  );
  return rows[0]?.eligible === true;
}

/** The history endpoint's result: every signal event of the code PLUS the
 *  recent-window boundary the UI scopes its history TABLE to (the PLOT
 *  renders all rows as markers). */
export interface TradingSignalHistoryResult {
  signals: TradingSignalRow[];
  /** 'YYYY-MM-DD' — the 20th-most-recent distinct close date BEFORE the
   *  anchor's window: signal rows with date > recent_window_start (and
   *  <= the anchor) are the anchor's last 20 trading days — the same
   *  window the main table's count_20d counts over. null when the
   *  sec_type has fewer than 20 baseline closes (the table then shows
   *  everything there is). */
  recent_window_start: string | null;
}

/** Every signal event of ONE code (newest first, confidence DESC within
 *  a day) — the row-expansion panel's history table + trend-chart markers.
 *  Two source roles, joined per (config, day) with the live tick winning:
 *    • analysis_signals.history_signals — the PLOT BULK: the multi-year
 *      event record (a year-end snapshot owns its calendar year, the
 *      rolling latest owns [Jan 1, its key]; 15:00 day-close anchors),
 *    • live.live_signals — the RECENT 20-TRADING-DAY SLICE ONLY: the
 *      recorded breaches inside (win_start, anchor]. Older days belong to
 *      the history record — the union never re-scans them.
 *  Optional signal_type / signal_sub_type narrow both sources to one
 *  signal family. `date` ('YYYY-MM-DD', the viewed day) anchors the
 *  window; omitted = the newest baseline close. */
export async function fetchTradingSignalHistory(
  secType: string | null | undefined,
  code: string,
  signalType?: string | null,
  signalSubType?: string | null,
  date?: string | null,
): Promise<TradingSignalHistoryResult> {
  const st = assertSecType(secType);
  const c = (code ?? "").trim();
  if (!c) {
    throw Object.assign(new Error("code is required"), { status: 400 });
  }
  // Shared predicate — applied inside BOTH scans so the optional family
  // filters prune rows before the union/dedup.
  const predicates = ["s.sec_type = $1", "s.code = $2"];
  const params: unknown[] = [st, c];
  if (signalType) {
    params.push(signalType.trim());
    predicates.push(`s.signal_type = $${params.length}`);
  }
  if (signalSubType) {
    params.push(signalSubType.trim());
    predicates.push(`s.signal_sub_type = $${params.length}`);
  }
  const where = predicates.join(" AND ");
  // The window anchor: the viewed day when given (ISO-guarded), else the
  // newest baseline close ('infinity' upper bound). The win CTE walks back
  // 20 DISTINCT close dates from it — the count_20d window, verbatim.
  const anchor = date && /^\d{4}-\d{2}-\d{2}$/.test(date.trim())
    ? date.trim() : null;
  if (anchor) params.push(anchor);
  const anchorExpr = anchor
    ? `$${params.length}::date`
    : `'infinity'::date`;
  const rows = await queryRows<TradingSignalRow & QueryResultRow>(
    `WITH win AS (
        -- 20th-most-recent DISTINCT close date before the anchor:
        -- (win_start, anchor] spans exactly the last 20 trading days.
        SELECT win_start
        FROM (
            SELECT DISTINCT date AS win_start
            FROM ${BASIC_STATS_TABLE[st]!}
            WHERE date <= ${anchorExpr} AND close IS NOT NULL
            ORDER BY win_start DESC
            OFFSET 19 LIMIT 1
        ) w
     ),
     recent AS (
        -- THE 20d SLICE — live tier ONLY: recorded breaches inside the
        -- anchor's last 20 trading days (the count_20d window). The live
        -- table contributes nothing outside this window — older days
        -- belong to the history record below, so the union never
        -- re-scans live's back catalog.
        SELECT s.code,
               s.sec_type,
               s.signal_type,
               s.signal_sub_type,
               s.date,
               s.time,
               s.action,
               s.signal_excess::float8        AS signal_excess,
               s.signal_excess_pct::float8    AS signal_excess_pct,
               s.signal::float8               AS signal,
               s.signal_threshold::float8     AS signal_threshold,
               s.confidence,
               (s.confidence::float8 / 100.0) AS confidence_pct,
               s.is_day_close_trigger,
               s.regime_state
        FROM live.live_signals s
        WHERE ${where}
          AND s.date > (SELECT win_start FROM win)
          AND s.date <= ${anchorExpr}
     ),
     history AS (
        -- THE PLOT BULK — the multi-year record, every year the strategy
        -- tier registered (15:00 day-close anchors).
        SELECT s.code,
               s.sec_type,
               s.signal_type,
               s.signal_sub_type,
               s.date,
               s.time,
               s.action,
               s.signal_excess::float8        AS signal_excess,
               s.signal_excess_pct::float8    AS signal_excess_pct,
               s.signal::float8               AS signal,
               s.signal_threshold::float8     AS signal_threshold,
               s.confidence,
               (s.confidence::float8 / 100.0) AS confidence_pct,
               s.is_day_close_trigger,
               s.regime_state
        FROM analysis_signals.history_signals s
        WHERE ${where}
     ),
     combined AS (
        SELECT h.* FROM history h
        UNION ALL
        SELECT r.* FROM recent r
     ),
     deduped AS (
        -- One record per (config, day). Inside the window a live tick
        -- wins over the history anchor (earliest first — state families
        -- re-flag on every qualifying tick while the breach persists);
        -- outside it history stands alone. A stronger confidence breaks
        -- remaining ties (the retired day-close mirror rows).
        SELECT d.*,
               row_number() OVER (
                   PARTITION BY d.signal_type, d.signal_sub_type, d.date
                   ORDER BY d.is_day_close_trigger, d.time,
                            d.confidence DESC
               ) AS rn
        FROM combined d
     ),
     name AS (
        SELECT i.name FROM ${IDENTITY_TABLE[st]!} i
        WHERE i.code = $2
        ORDER BY i.date DESC LIMIT 1
     )
     SELECT r.code,
            n.name                       AS code_name,
            r.sec_type,
            r.signal_type,
            r.signal_sub_type,
            to_char(r.date, 'YYYY-MM-DD') AS date,
            to_char(r.time, 'HH24:MI')    AS time,
            ${labelDecodeSql("action", "r.action")} AS action,
            r.signal_excess,
            r.signal_excess_pct,
            r.signal,
            r.signal_threshold,
            r.confidence,
            r.confidence_pct,
            r.is_day_close_trigger,
            ${labelDecodeSql("regime", "r.regime_state")} AS regime_state,
            (SELECT to_char(win_start, 'YYYY-MM-DD') FROM win)
                AS recent_window_start
     FROM deduped r
     LEFT JOIN name n ON TRUE
     WHERE r.rn = 1
     ORDER BY r.date DESC, r.time DESC, r.confidence DESC
     LIMIT 2000`,
    params,
  );
  // The window boundary rides every row (one scalar subquery) — peel it
  // off the first row and strip it so `signals` stays pure event rows.
  const recent_window_start = rows[0]?.recent_window_start ?? null;
  const signals = rows.map((r) => {
    const { recent_window_start: win, ...rest } = r;
    void win;
    return rest;
  });
  return { signals, recent_window_start };
}

// ---------------------------------------------------------------------------
//  Signal CURVES / THRESHOLDS / RSI series — the Recent Movements
//  code-trend's threshold-vs-curve overlay panel.
// ---------------------------------------------------------------------------

/** One fitted depth-response curve row (analysis_signals.signal_curves
 *  at its LATEST snapshot per key): mu(s) = mu_center + slope *
 *  (s - s_center) over the Gaussian depth score s = Phi^-1(1 - q);
 *  tail_bars maps tail pct ("1".."10") to the window's RSI bar (the
 *  s -> RSI-value mapping). */
export interface SignalCurveRow {
  code: string;
  sec_type: string;
  signal_type: string;
  rsi_window: number;
  /** 'top' (sell) | 'bottom' (buy) — decoded from the label code. */
  side: string;
  /** calm / hot / panic / quiet — decoded from the label code. */
  regime_state: string;
  start_date: string;
  end_date: string;
  /** The cell's delay-0 trigger count over the window (the fit's sample). */
  n: number;
  /** y-bar: the cell's sign-aligned blended mean forward change at its
   *  own depth center (fraction; > 0 = the reversal worked). */
  mu_center: number;
  /** s-bar: the cell's own mean depth score (never a universal constant). */
  s_center: number;
  /** b: dmu/ds — the pooled (side, regime) OLS slope (buy side >= 0). */
  slope: number;
  /** Tail pct ("1" | "2" | "3" | "5" | "10") -> the window's interpolated
   *  RSI bar for this (code, rsi_window, side). */
  tail_bars: Record<string, number>;
}

/** The code's LATEST depth-response curves (one row per rsi_window x
 *  side x regime_state at the newest snapshot) — the curve overlay's
 *  data. */
export async function fetchSignalCurves(
  secType: string | null | undefined,
  code: string,
): Promise<SignalCurveRow[]> {
  const st = assertSecType(secType);
  const c = (code ?? "").trim();
  if (!c) {
    throw Object.assign(new Error("code is required"), { status: 400 });
  }
  return queryRows<SignalCurveRow & QueryResultRow>(
    `SELECT s.code,
            s.sec_type,
            s.signal_type,
            s.rsi_window::int AS rsi_window,
            ${labelDecodeSql("side", "s.side")} AS side,
            ${labelDecodeSql("regime", "s.regime_state")} AS regime_state,
            to_char(s.start_date, 'YYYY-MM-DD') AS start_date,
            to_char(s.end_date, 'YYYY-MM-DD') AS end_date,
            s.n::int AS n,
            s.mu_center::float8 AS mu_center,
            s.s_center::float8 AS s_center,
            s.slope::float8 AS slope,
            s.tail_bars
     FROM analysis_signals.signal_curves s
     WHERE s.sec_type = $1 AND s.code = $2
       AND s.end_date = (
           SELECT max(c2.end_date) FROM analysis_signals.signal_curves c2
           WHERE c2.sec_type = $1 AND c2.code = $2
       )
     ORDER BY s.rsi_window, s.side, s.regime_state`,
    [st, c],
  );
}

/** One ACTIVE threshold row of the code (analysis_signals.
 *  signal_thresholds WHERE is_active) — the threshold overlay's bars
 *  in the RSI's own space. */
export interface SignalThresholdRow {
  code: string;
  sec_type: string;
  signal_type: string;
  signal_sub_type: string;
  /** 'top' (sell) | 'bottom' (buy) — decoded from the label code. */
  side: string;
  /** calm / hot / panic / quiet — decoded from the label code. */
  regime_state: string;
  action: string;
  /** The breach bar in the RSI's own space (null when unmeasured). */
  signal_threshold: number | null;
  /** The on-bar sign-aligned confidence base (fraction). */
  confidence_base: number | null;
  scale_method: string | null;
  scale_base: number | null;
  scale_factor: number | null;
  start_date: string;
  end_date: string;
}

/** The code's ACTIVE signal thresholds (the current threshold set —
 *  each config's latest end_date row). */
export async function fetchSignalThresholds(
  secType: string | null | undefined,
  code: string,
): Promise<SignalThresholdRow[]> {
  const st = assertSecType(secType);
  const c = (code ?? "").trim();
  if (!c) {
    throw Object.assign(new Error("code is required"), { status: 400 });
  }
  return queryRows<SignalThresholdRow & QueryResultRow>(
    `SELECT t.code,
            t.sec_type,
            t.signal_type,
            t.signal_sub_type,
            ${labelDecodeSql("side", "t.side")} AS side,
            ${labelDecodeSql("regime", "t.regime_state")} AS regime_state,
            ${labelDecodeSql("action", "t.action")} AS action,
            t.signal_threshold::float8 AS signal_threshold,
            t.confidence_base::float8 AS confidence_base,
            t.scale_method,
            t.scale_base::float8 AS scale_base,
            t.scale_factor::float8 AS scale_factor,
            to_char(t.start_date, 'YYYY-MM-DD') AS start_date,
            to_char(t.end_date, 'YYYY-MM-DD') AS end_date
     FROM analysis_signals.signal_thresholds t
     WHERE t.sec_type = $1 AND t.code = $2 AND t.is_active
     ORDER BY t.signal_sub_type, t.side, t.regime_state`,
    [st, c],
  );
}

/** One RSI row of the code's series (analysis.mov_ave_rsi — one column
 *  per RSI window; the mov_ave_spread rsi step's fixed vocabulary
 *  3/6/10/14/20/60). */
export interface SignalRsiRow {
  date: string;
  rsi_3: number | null;
  rsi_6: number | null;
  rsi_10: number | null;
  rsi_14: number | null;
  rsi_20: number | null;
  rsi_60: number | null;
}

const RSI_SERIES_COLUMNS = "r.rsi_3days::float8 AS rsi_3, r.rsi_6days::float8 AS rsi_6, r.rsi_10days::float8 AS rsi_10, r.rsi_14days::float8 AS rsi_14, r.rsi_20days::float8 AS rsi_20, r.rsi_60days::float8 AS rsi_60" as const;

/** The code's FULL daily RSI series (every window column, ascending) —
 *  the threshold overlay's lines. */
export async function fetchSignalRsiSeries(
  secType: string | null | undefined,
  code: string,
): Promise<SignalRsiRow[]> {
  const st = assertSecType(secType);
  const c = (code ?? "").trim();
  if (!c) {
    throw Object.assign(new Error("code is required"), { status: 400 });
  }
  return queryRows<SignalRsiRow & QueryResultRow>(
    `SELECT to_char(r.date, 'YYYY-MM-DD') AS date,
            ${RSI_SERIES_COLUMNS}
     FROM analysis.mov_ave_rsi r
     WHERE r.sec_type = $1 AND r.code = $2
     ORDER BY r.date ASC`,
    [st, c],
  );
}
