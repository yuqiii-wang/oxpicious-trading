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
//  • history(sec_type, code) — EVERY live_signals row of one code (newest
//    first) behind the row-expansion panel: the history-signals table +
//    the buy/sell markers on the code-trend chart.
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

/** Every live_signals row of ONE code (newest first, confidence DESC within
 *  a day) — the row-expansion panel's history table + trend-chart markers.
 *  Optional signal_type / signal_sub_type narrow the history to one signal
 *  family; omitted = all of the code's signals. */
export async function fetchTradingSignalHistory(
  secType: string | null | undefined,
  code: string,
  signalType?: string | null,
  signalSubType?: string | null,
): Promise<TradingSignalRow[]> {
  const st = assertSecType(secType);
  const c = (code ?? "").trim();
  if (!c) {
    throw Object.assign(new Error("code is required"), { status: 400 });
  }
  const conditions = ["s.sec_type = $1", "s.code = $2"];
  const params: unknown[] = [st, c];
  if (signalType) {
    params.push(signalType.trim());
    conditions.push(`s.signal_type = $${params.length}`);
  }
  if (signalSubType) {
    params.push(signalSubType.trim());
    conditions.push(`s.signal_sub_type = $${params.length}`);
  }
  const rows = await queryRows<TradingSignalRow & QueryResultRow>(
    `SELECT s.code,
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
            s.is_day_close_trigger,
            s.regime_state
     FROM live.live_signals s
     LEFT JOIN LATERAL (
       SELECT i.name FROM ${IDENTITY_TABLE[st]!} i
       WHERE i.code = s.code
       ORDER BY i.date DESC LIMIT 1
     ) n ON TRUE
     WHERE ${conditions.join(" AND ")}
     ORDER BY s.date DESC, s.time DESC, s.confidence DESC
     LIMIT 1000`,
    params,
  );
  return rows;
}
