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
import { labelDecodeSql, LABEL_CODE } from "./label-codes.js";
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
  /** The event's population VARIANT: FALSE = the plain record, TRUE =
   *  the OHLCV-boost twin (the SAME trigger, confidence trust-weighted
   *  by the day-0 ohlcv score). The history panel's OHLCV toggle picks
   *  which variant renders. Live-tier rows carry no twins (always
   *  FALSE). */
  is_ohlcv_boosted: boolean;
  /** The event's episode-PASS attribution (the rsi analysis layer's
   *  side-mirrored is_typical flag at the day's own window): TRUE = the
   *  typical pass (buy: the isolated first touch; sell: the re-test),
   *  FALSE = the re-entry pass (flicker re-crossings / continuations /
   *  isolated breakouts — the noisy population). The history panel's
   *  pass toggle filters on it. A live tick winning a day inherits the
   *  history anchor's stamp; null = no stamped anchor for the day
   *  (pre-migration rows, live-only days). */
  is_typical: boolean | null;
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
  // the stored sec_type / signal_type columns are SMALLINT label codes
  // (the label-code contract) — the filter carries the code, the response
  // decodes back to the labels the UI speaks
  const rows = await queryRows<TradingSignalConfigRow & QueryResultRow>(
    `SELECT ${labelDecodeSql("signal_type", "t.signal_type")} AS signal_type,
            t.signal_sub_type, count(*)::int AS n_configs
     FROM analysis_signals.signal_thresholds t
     WHERE t.sec_type = ${LABEL_CODE.sec_type[st]} AND t.is_active
     GROUP BY t.signal_type, t.signal_sub_type
     ORDER BY t.signal_type, t.signal_sub_type`,
    [],
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
 *  DESC — BOTH population variants per event (is_ohlcv_boosted FALSE
 *  plain / TRUE the w-scaled boost twin; the OHLCV toggle picks) + the
 *  event's episode-pass attribute (is_typical — the pass toggle
 *  filters; a live tick winning a day inherits its history anchor's
 *  stamp, null when no stamped anchor exists). Two source roles, joined
 *  per (config, day, VARIANT):
 *    • analysis_signals.history_signals — the day's close-trigger
 *      anchors (the analysis run's 15:00 records),
 *    • live.live_signals — the day's monitor breaches + day-close
 *      observations (no variants: always the plain population).
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
     ),
     recent AS (
        -- THE MONITOR SLICE — live rows carry no population variants
        -- (no boost twins, no pass stamp).
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
               s.regime_state,
               FALSE         AS is_ohlcv_boosted,
               NULL::boolean AS is_typical
        FROM live.live_signals s
        WHERE s.sec_type = $1 AND s.date = $2::date
     ),
     history AS (
        -- THE ANALYSIS ANCHORS — the day's close-trigger records, BOTH
        -- variants (is_ohlcv_boosted) + the day's episode-pass
        -- attribute (is_typical, NULL pre-stamp).
        SELECT s.code,
               ${labelDecodeSql("sec_type", "s.sec_type")} AS sec_type,
               ${labelDecodeSql("signal_type", "s.signal_type")} AS signal_type,
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
               s.regime_state,
               s.is_ohlcv_boosted,
               s.is_typical
        FROM analysis_signals.history_signals s
        WHERE s.sec_type = ${LABEL_CODE.sec_type[st]}
          AND s.date = $2::date
     ),
     combined AS (
        SELECT h.* FROM history h
        UNION ALL
        SELECT r.* FROM recent r
     ),
     deduped AS (
        -- One record per (config, day, VARIANT): the boost twin is the
        -- same event with w-scaled confidence — it survives beside its
        -- plain record (the OHLCV toggle picks). Within a variant, a
        -- live tick wins over the history anchor (earliest first —
        -- state families re-flag on every qualifying tick while the
        -- breach persists); a stronger confidence breaks remaining
        -- ties. The EVENT's pass stamp rides the partition (bool_or —
        -- one event carries exactly one stamp): a live tick winning a
        -- day inherits its history anchor's pass.
        SELECT d.*,
               bool_or(d.is_typical) OVER (
                   PARTITION BY d.signal_type, d.signal_sub_type, d.date,
                                d.is_ohlcv_boosted
               ) AS event_is_typical,
               row_number() OVER (
                   PARTITION BY d.signal_type, d.signal_sub_type, d.date,
                                d.is_ohlcv_boosted
                   ORDER BY d.is_day_close_trigger, d.time,
                            d.confidence DESC
               ) AS rn
        FROM combined d
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
            r.is_ohlcv_boosted,
            r.event_is_typical AS is_typical,
            (SELECT count(DISTINCT ls.date)::int
             FROM live.live_signals ls
             WHERE ls.sec_type = r.sec_type
               AND ls.code = r.code
               AND ls.date <= $2::date
               AND ls.date > COALESCE(
                     (SELECT win_start FROM win), '1900-01-01'::date)
            ) AS count_20d
     FROM deduped r
     LEFT JOIN LATERAL (
       SELECT i.name FROM ${idt} i
       WHERE i.code = r.code
       ORDER BY i.date DESC LIMIT 1
     ) n ON TRUE
     WHERE r.rn = 1
     ORDER BY r.confidence DESC, r.time DESC, r.code, r.signal_type,
              r.signal_sub_type`,
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
                WHERE sec_type = ${LABEL_CODE.sec_type[st]} AND is_active)
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
 *  Two source roles, joined per (config, day, VARIANT) with the live tick
 *  winning:
 *    • analysis_signals.history_signals — the PLOT BULK: the multi-year
 *      event record (a year-end snapshot owns its calendar year, the
 *      rolling latest owns [Jan 1, its key]; 15:00 day-close anchors),
 *      BOTH variants per event (is_ohlcv_boosted FALSE plain / TRUE the
 *      w-scaled boost twin — the UI's OHLCV toggle picks) + the day's
 *      episode-pass attribute (is_typical TRUE typical / FALSE re-entry /
 *      NULL unclassified — the UI's pass toggle filters),
 *    • live.live_signals — the RECENT 20-TRADING-DAY SLICE ONLY: the
 *      recorded breaches inside (win_start, anchor]. Older days belong to
 *      the history record — the union never re-scans them. Live rows
 *      carry no variants (always the plain population).
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
  // The TWO scans' predicates: live.live_signals keeps its TEXT
  // sec_type / signal_type columns (the params carry the labels), while
  // analysis_signals.history_signals stores the SMALLINT label codes —
  // its own predicates carry the codes (validated map lookups, not user
  // input) and its SELECT decodes back to the labels the union speaks.
  // An unknown family label filters to nothing (never a fuzzy match).
  const secCode = LABEL_CODE.sec_type[st];
  const sigCode = signalType
    ? LABEL_CODE.signal_type[signalType.trim()]
    : undefined;
  if (signalType && sigCode === undefined) {
    return { signals: [], recent_window_start: null };
  }
  const predicates = ["s.sec_type = $1", "s.code = $2"];
  const histPredicates = ["s.sec_type = " + secCode, "s.code = $2"];
  const params: unknown[] = [st, c];
  if (signalType) {
    params.push(signalType.trim());
    predicates.push(`s.signal_type = $${params.length}`);
    histPredicates.push(`s.signal_type = ${sigCode}`);
  }
  if (signalSubType) {
    params.push(signalSubType.trim());
    predicates.push(`s.signal_sub_type = $${params.length}`);
    histPredicates.push(`s.signal_sub_type = $${params.length}`);
  }
  const where = predicates.join(" AND ");
  const histWhere = histPredicates.join(" AND ");
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
        -- re-scans live's back catalog. Live rows carry no population
        -- variants (no boost twins, no pass stamp).
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
               s.regime_state,
               FALSE        AS is_ohlcv_boosted,
               NULL::boolean AS is_typical
        FROM live.live_signals s
        WHERE ${where}
          AND s.date > (SELECT win_start FROM win)
          AND s.date <= ${anchorExpr}
     ),
     history AS (
        -- THE PLOT BULK — the multi-year record, every year the strategy
        -- tier registered (15:00 day-close anchors). BOTH population
        -- variants x the day's pass attribute: is_ohlcv_boosted FALSE =
        -- the plain record, TRUE = the boost twin (the same events with
        -- w-scaled confidence — the overlay-style OHLCV toggle picks);
        -- is_typical TRUE = the typical pass, FALSE = the re-entry pass
        -- (the overlay-style pass toggle filters), NULL = unclassified
        -- (rows written before the stamp). The UI filters client-side.
        SELECT s.code,
               ${labelDecodeSql("sec_type", "s.sec_type")} AS sec_type,
               ${labelDecodeSql("signal_type", "s.signal_type")} AS signal_type,
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
               s.regime_state,
               s.is_ohlcv_boosted,
               s.is_typical
        FROM analysis_signals.history_signals s
        WHERE ${histWhere}
     ),
     combined AS (
        SELECT h.* FROM history h
        UNION ALL
        SELECT r.* FROM recent r
     ),
     deduped AS (
        -- One record per (config, day, VARIANT): the boost twin is the
        -- same event with w-scaled confidence — it must survive beside
        -- its plain record (the OHLCV toggle picks). Within a variant,
        -- a live tick wins over the history anchor (earliest first —
        -- state families re-flag on every qualifying tick while the
        -- breach persists; live has no twins, so the TRUE partition is
        -- history-only); a stronger confidence breaks remaining ties
        -- (the retired day-close mirror rows). The EVENT's pass stamp
        -- rides the partition (max — one event carries exactly one
        -- stamp): a live tick winning a day inherits its history
        -- anchor's pass, so the pass toggle still sees the day.
        SELECT d.*,
               bool_or(d.is_typical) OVER (
                   PARTITION BY d.signal_type, d.signal_sub_type, d.date,
                                d.is_ohlcv_boosted
               ) AS event_is_typical,
               row_number() OVER (
                   PARTITION BY d.signal_type, d.signal_sub_type, d.date,
                                d.is_ohlcv_boosted
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
            r.is_ohlcv_boosted,
            r.event_is_typical AS is_typical,
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
 *  at its LATEST snapshot per key): curve(s) = mu_center +
 *  interp(fit_curve, s) over the Gaussian depth score s = Phi^-1(1-q)
 *  — the pooled episode-weighted isotonic shape, drawn only within
 *  [s_lo, s_hi]; tail_bars maps tail pct ("1".."25") to the window's
 *  RSI bar (the s -> RSI-value mapping). */
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
  /** The cell's population VARIANT (PK member): FALSE = the plain
   *  fit, TRUE = the OHLCV-boost twin (the same in-band days, every
   *  statistic weight-blended by episode x day-0 ohlcv score). The
   *  curve overlay's toggle picks which variant to render. */
  is_ohlcv_boosted: boolean;
  /** FALSE if not usually observed for a signal, e.g., RSI re-entry
   *  is registered; TRUE if typical, e.g., RSI isolated ups/downs —
   *  the cell's episode-PASS population (PK member): TRUE = the
   *  first-touch passes' days (each episode's first tail day AND its
   *  gap-1 continuations), FALSE = the re-entry passes' days. The
   *  curve overlay's toggle picks which population to render. */
  is_typical: boolean;
  /** The cell's in-band day count over the window (the fit's raw
   *  sample — every tail-episode day, registrations + continuations). */
  n: number;
  /** The cell's EPISODE count (the effective sample: each episode's
   *  days carry weight 1/episode_size, summing to exactly 1). */
  n_episodes: number;
  /** y-bar: the cell's sign-aligned blended mean forward change at its
   *  own depth center (fraction; > 0 = the reversal worked). */
  mu_center: number;
  /** s-bar: the cell's own mean depth score (never a universal constant). */
  s_center: number;
  /** The pooled (index class, side, regime) WEIGHTED ISOTONIC shape
   *  as {"s": offset} at the 25 1%-band-center knot depths — the PAVA
   *  values are monotone, so linear interpolation between the knots is
   *  a CONTINUOUS MONOTONE curve. curve(s) = mu_center +
   *  interp(fit_curve, s). Significance-gated: all-zero offsets (flat
   *  at mu_center) when the pool's |fit_t| < 2. */
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
   *  (1..25, the deeper edge pct) -> episode-weighted mean
   *  sign-aligned response of the days whose q falls in
   *  ((k-1)%, k%] (absent = empty band) — the measured shape beside
   *  the isotonic fit (the deep bands are measured since the
   *  2026-10-06 episode population). */
  band_curve: Record<string, number> | null;
  /** Sign-aligned mean forward change at each horizon (fraction, NULL
   *  when no trigger carries the leg) — the path behind the
   *  0.10/0.15/0.50/0.15/0.10 blend's next/3d/5d/10d/20d legs. */
  mu_1d: number | null;
  mu_3d: number | null;
  mu_5d: number | null;
  mu_10d: number | null;
  mu_20d: number | null;
  /** Tail pct ("1" | "2" | "3" | "5" | "10") -> the window's interpolated
   *  RSI bar for this (code, rsi_window, side). */
  tail_bars: Record<string, number>;
}

/** The code's LATEST depth-response curves — BOTH boost variants x
 *  BOTH episode-pass populations per cell (is_ohlcv_boosted FALSE
 *  plain / TRUE boost twin; is_typical TRUE typical / FALSE re-entry;
 *  the overlay's toggles filter client-side) — one row per rsi_window
 *  x side x regime_state x population x variant at the newest
 *  snapshot. */
export async function fetchSignalCurves(
  secType: string | null | undefined,
  code: string,
): Promise<SignalCurveRow[]> {
  const st = assertSecType(secType);
  const c = (code ?? "").trim();
  if (!c) {
    throw Object.assign(new Error("code is required"), { status: 400 });
  }
  // the stored sec_type / signal_type columns are SMALLINT label codes
  // (the label-code contract) — the filter carries the code, the response
  // decodes back to the labels the UI speaks. The LATEST-snapshot
  // boundary is PER FAMILY (max end_date within the row's own
  // signal_type): the families key their snapshots on DIFFERENT data
  // horizons (mov_rsi the latest RSI date, bollinger the latest price
  // date), so a global max would hide the family whose newest snapshot
  // is older.
  return queryRows<SignalCurveRow & QueryResultRow>(
    `SELECT s.code,
            ${labelDecodeSql("sec_type", "s.sec_type")} AS sec_type,
            ${labelDecodeSql("signal_type", "s.signal_type")} AS signal_type,
            s.audit_window::int AS rsi_window,
            ${labelDecodeSql("side", "s.side")} AS side,
            ${labelDecodeSql("regime", "s.regime_state")} AS regime_state,
            to_char(s.start_date, 'YYYY-MM-DD') AS start_date,
            to_char(s.end_date, 'YYYY-MM-DD') AS end_date,
            s.is_ohlcv_boosted,
            s.is_typical,
            s.n::int AS n,
            s.n_episodes::int AS n_episodes,
            s.mu_center::float8 AS mu_center,
            s.s_center::float8 AS s_center,
            s.fit_curve,
            s.fit_rho::float8 AS fit_rho,
            s.fit_t::float8 AS fit_t,
            s.s_lo::float8 AS s_lo,
            s.s_hi::float8 AS s_hi,
            s.band_curve,
            s.mu_1d::float8 AS mu_1d,
            s.mu_3d::float8 AS mu_3d,
            s.mu_5d::float8 AS mu_5d,
            s.mu_10d::float8 AS mu_10d,
            s.mu_20d::float8 AS mu_20d,
            s.tail_bars
     FROM analysis_signals.signal_curves s
     WHERE s.sec_type = ${LABEL_CODE.sec_type[st]} AND s.code = $1
       AND s.end_date = (
           SELECT max(c2.end_date) FROM analysis_signals.signal_curves c2
           WHERE c2.sec_type = ${LABEL_CODE.sec_type[st]}
             AND c2.code = $1
             AND c2.signal_type = s.signal_type
       )
     ORDER BY s.audit_window, s.side, s.regime_state`,
    [c],
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
            ${labelDecodeSql("sec_type", "t.sec_type")} AS sec_type,
            ${labelDecodeSql("signal_type", "t.signal_type")} AS signal_type,
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
     WHERE t.sec_type = ${LABEL_CODE.sec_type[st]} AND t.code = $1
       AND t.is_active
     ORDER BY t.signal_sub_type, t.side, t.regime_state`,
    [c],
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
    // analysis.mov_ave_rsi stores its stats.label_dim 'sec_type' code
    [LABEL_CODE.sec_type[st], c],
  );
}
