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
  /** The strategy's expected-move confidence in BASIS POINTS
   *  (ROUND(10000 × signal_strategies.confidence) — the chosen rung's
   *  sign-aligned dir_ave). The SignalActionChip input (its 1..100 ramp
   *  clamps above 100 bp, the ForecastTable treatment). */
  confidence: number;
  /** The same confidence as the EXPECTED-MOVE PERCENT (confidence / 100,
   *  e.g. 0.87 = 0.87%) — the display number (text cells + tooltips). */
  confidence_pct: number;
  /** The SAME bucket's ohlcv_boost cohort confidence (bp, ROUND(10000 ×
   *  the @boost child row's confidence) — the ohlcv-score-trust-weighted
   *  measurement of the same trigger days). ADD-ON context, never a
   *  gate; null = the cohort never registered or the family carries no
   *  ohlcv children (mov_std / the pair-cross families). Live rows
   *  only — null on history arm rows. */
  confidence_boost: number | null;
  /** confidence_boost / 100 as the expected-move PERCENT — the
   *  parenthetical add-on's display number. */
  confidence_boost_pct: number | null;
  /** The SAME bucket's ohlcv_selection cohort confidence (bp — the
   *  favorable-anatomy subset's own dir_ave; bottom side only, the top
   *  side carries no selection child). Same add-on rule as
   *  confidence_boost. */
  confidence_selection: number | null;
  /** confidence_selection / 100 as the expected-move PERCENT. */
  confidence_selection_pct: number | null;
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
     FROM analysis_signals.signal_strategies
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
            s.action,
            s.signal_excess::float8       AS signal_excess,
            s.signal_excess_pct::float8    AS signal_excess_pct,
            s.signal::float8              AS signal,
            s.signal_threshold::float8     AS signal_threshold,
            s.confidence,
            (s.confidence::float8 / 100.0) AS confidence_pct,
            s.confidence_boost,
            (s.confidence_boost::float8 / 100.0) AS confidence_boost_pct,
            s.confidence_selection,
            (s.confidence_selection::float8 / 100.0) AS confidence_selection_pct,
            s.is_day_close_trigger,
            s.regime_state,
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
        EXISTS (SELECT 1 FROM analysis_signals.signal_strategies
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
               s.confidence_boost,
               (s.confidence_boost::float8 / 100.0) AS confidence_boost_pct,
               s.confidence_selection,
               (s.confidence_selection::float8 / 100.0) AS confidence_selection_pct,
               s.is_day_close_trigger,
               s.regime_state
        FROM live.live_signals s
        WHERE ${where}
          AND s.date > (SELECT win_start FROM win)
          AND s.date <= ${anchorExpr}
     ),
     history AS (
        -- THE PLOT BULK — the multi-year record, every year the strategy
        -- tier registered (15:00 day-close anchors). The cohort add-on
        -- confidences exist only on live rows — NULL placeholders keep
        -- the union's column lists aligned (the @boost/@selection
        -- children appear here as their own config rows instead).
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
               NULL::int                      AS confidence_boost,
               NULL::float8                   AS confidence_boost_pct,
               NULL::int                      AS confidence_selection,
               NULL::float8                   AS confidence_selection_pct,
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
            r.action,
            r.signal_excess,
            r.signal_excess_pct,
            r.signal,
            r.signal_threshold,
            r.confidence,
            r.confidence_pct,
            r.is_day_close_trigger,
            r.regime_state,
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
    const { recent_window_start: _win, ...rest } = r;
    return rest;
  });
  return { signals, recent_window_start };
}
