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
   *  (the rules-era scale: the cell's measured base_pct amplified by
   *  depth_factor over the normalized breach depth, capped — the
   *  identical formula the history writer applies). The
   *  SignalActionChip input (its 1..100 ramp clamps above 100 bp, the
   *  ForecastTable treatment). */
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
  /** TRUE = the condition FIRED and the owning strategy's gate
   *  explicitly DECLINED it (options_wall's VRP-rejected put breaches
   *  — the 2026-10-12 design): the WAIT record. The
   *  UI renders it as the grey diamond marker / a neutral wait
   *  chip, never a buy/sell triangle. FALSE = the actionable record.
   *  History rows DERIVE it from the action code (action = wait
   *  — the history table's is_rejection column was dropped
   *  2026-10-14); live-tier rows carry no rejections (always FALSE). */
  is_rejection: boolean;
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

/** DISTINCT (signal_type, signal_sub_type) of the sec_type's RULE CELLS
 *  — the page's signal menu (default = all). The rules tier is
 *  current-state (no is_active): a family enters the menu wherever it
 *  carries at least one gate-passing cell for the sec_type. */
export async function fetchTradingSignalConfigs(
  secType: string | null | undefined,
): Promise<TradingSignalConfigRow[]> {
  const st = assertSecType(secType);
  // the stored sec_type / signal_type columns are SMALLINT label codes
  // (the label-code contract) — the filter carries the code, the response
  // decodes back to the labels the UI speaks
  const rows = await queryRows<TradingSignalConfigRow & QueryResultRow>(
    `SELECT ${labelDecodeSql("signal_type", "t.signal_type")} AS signal_type,
            ${labelDecodeSql("signal_sub_type", "t.signal_sub_type")} AS signal_sub_type,
            count(*)::int AS n_configs
     FROM analysis_signals.signal_rules t
     WHERE t.sec_type = ${LABEL_CODE.sec_type[st]}
       -- the options families live on their OWN page (/analysis/
       -- signals/options) — the shared config menu stays the three
       -- general families (mov_rsi / bollinger / ma_cross)
       AND t.signal_type <> ${LABEL_CODE.signal_type.options_wall}
       AND t.signal_type <> ${LABEL_CODE.signal_type.options_vix}
       -- wait rows are not trading yet (the VRP twin / the
       -- sample-starved cells, 2026-10-16): the menu contract stays
       -- "at least one GATE-PASSING cell"
       AND t.action <> ${LABEL_CODE.action.wait}
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
 *  DESC — one row per event (is_ohlcv_boosted is the retired boost-twin
 *  marker: FALSE everywhere since the 2026-10-16 retirement — the
 *  boost's tuned confidence lives on signal_rules.ohlcv_boost_conf)
 *  + the event's episode-pass attribute (is_typical; no toggle filters
 *  on it any more; a live tick winning a day inherits its history
 *  anchor's stamp, null when no stamped anchor exists). Two source
 *  roles, joined per (config, day):
 *    • analysis_signals.history_signals — the day's close-trigger
 *      anchors (the analysis run's 15:00 records),
 *    • live.live_signals — the day's monitor breaches + day-close
 *      observations.
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
               NULL::boolean AS is_typical,
               FALSE         AS is_rejection
        FROM live.live_signals s
        WHERE s.sec_type = $1 AND s.date = $2::date
          -- the options families live on their own page (see
          -- fetchTradingSignalConfigs) — the shared day view excludes
          -- them
          AND s.signal_type <> 'options_wall'
          AND s.signal_type <> 'options_vix'
     ),
     history AS (
        -- THE ANALYSIS ANCHORS — the day's close-trigger records (one
        -- population since the boost twins' 2026-10-16 retirement) + the
        -- day's episode-pass attribute (is_typical, NULL pre-stamp). The
        -- rules-era table stores the crossed cell bar as signal_reference
        -- — the legacy response contract (excess / threshold) is DERIVED
        -- here so the live UNION and every consumer keep their shape.
        SELECT s.code,
               ${labelDecodeSql("sec_type", "s.sec_type")} AS sec_type,
               ${labelDecodeSql("signal_type", "s.signal_type")} AS signal_type,
               ${labelDecodeSql("signal_sub_type", "s.signal_sub_type")} AS signal_sub_type,
               s.date,
               s.time,
               s.action,
               (s.signal - s.signal_reference)::float8 AS signal_excess,
               ((s.signal - s.signal_reference)
                / NULLIF(abs(s.signal_reference), 0) * 100)::float8
                                            AS signal_excess_pct,
               s.signal::float8               AS signal,
               s.signal_reference::float8     AS signal_threshold,
               s.confidence,
               (s.confidence::float8 / 100.0) AS confidence_pct,
               s.is_day_close_trigger,
               s.regime_state,
               s.is_ohlcv_boosted,
               s.is_typical,
               -- the is_rejection column was dropped 2026-10-14 — the
               -- grey-diamond wait event IS the action code now
               (s.action = ${LABEL_CODE.action.wait}) AS is_rejection
        FROM analysis_signals.history_signals s
        WHERE s.sec_type = ${LABEL_CODE.sec_type[st]}
          AND s.date = $2::date
          AND s.signal_type <> ${LABEL_CODE.signal_type.options_wall}
          AND s.signal_type <> ${LABEL_CODE.signal_type.options_vix}
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
        -- day inherits its history anchor's pass. is_rejection rides
        -- the partition too — a rejected NO-ACTION record never
        -- collapses into (or masks) its actionable twin.
        SELECT d.*,
               bool_or(d.is_typical) OVER (
                   PARTITION BY d.signal_type, d.signal_sub_type, d.date,
                                d.is_ohlcv_boosted, d.is_rejection
               ) AS event_is_typical,
               row_number() OVER (
                   PARTITION BY d.signal_type, d.signal_sub_type, d.date,
                                d.is_ohlcv_boosted, d.is_rejection
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
            r.is_rejection,
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
 *  TRUE iff the sec_type carries RULE CELLS (a live signal_rules scope
 *  to replay) AND the date carries daily closes (an evaluation basis:
 *  intraday-last-bar replay when the intraday table covers the date,
 *  official daily close otherwise — the python-side fallback). */
export async function isTradingSignalComputeEligible(
  secType: string | null | undefined,
  date: string,
): Promise<boolean> {
  const st = assertSecType(secType);
  const rows = await queryRows<{ eligible: boolean } & QueryResultRow>(
    `SELECT (
        EXISTS (SELECT 1 FROM analysis_signals.signal_rules
                WHERE sec_type = ${LABEL_CODE.sec_type[st]})
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
 *      one population per event since the boost twins' retirement
 *      (is_ohlcv_boosted FALSE everywhere; the boost's tuned confidence
 *      lives on signal_rules.ohlcv_boost_conf) + the day's episode-pass
 *      attribute (is_typical TRUE typical / FALSE re-entry / NULL
 *      unclassified — no toggle filters on it any more),
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
  } else {
    // The GENERAL (family-unfiltered) view is the shared signals UI —
    // the options families live on their OWN page (/analysis/signals/
    // options), fetched with an explicit signal_type: here they are
    // EXCLUDED so the two surfaces never overlap (live keeps TEXT
    // labels, history the SMALLINT codes).
    predicates.push(
      `s.signal_type <> 'options_wall' AND s.signal_type <> 'options_vix'`,
    );
    histPredicates.push(
      `s.signal_type <> ${LABEL_CODE.signal_type.options_wall}`,
    );
    histPredicates.push(
      `s.signal_type <> ${LABEL_CODE.signal_type.options_vix}`,
    );
  }
  if (signalSubType) {
    params.push(signalSubType.trim());
    predicates.push(`s.signal_sub_type = $${params.length}`);
    // the stored history column is the SMALLINT code — map the label
    // param through the mirror (unknown label filters to nothing,
    // same as the family label above)
    histPredicates.push(
      `s.signal_sub_type = ${LABEL_CODE.signal_sub_type[signalSubType.trim()] ?? -1}`,
    );
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
               NULL::boolean AS is_typical,
               FALSE        AS is_rejection
        FROM live.live_signals s
        WHERE ${where}
          AND s.date > (SELECT win_start FROM win)
          AND s.date <= ${anchorExpr}
     ),
     history AS (
        -- THE PLOT BULK — the multi-year record replayed from the rules
        -- tier (15:00 day-close anchors), one population per event since
        -- the boost twins' retirement (is_ohlcv_boosted FALSE everywhere
        -- — the boost's tuned confidence lives on signal_rules.
        -- ohlcv_boost_conf); is_typical TRUE = the typical pass, FALSE =
        -- the re-entry pass, NULL = the family does not classify passes
        -- (no toggle filters on it any more). The rules-era table stores
        -- the crossed cell bar as signal_reference — the legacy response
        -- contract (excess / threshold) is DERIVED here so the union
        -- keeps its shape.
        SELECT s.code,
               ${labelDecodeSql("sec_type", "s.sec_type")} AS sec_type,
               ${labelDecodeSql("signal_type", "s.signal_type")} AS signal_type,
               ${labelDecodeSql("signal_sub_type", "s.signal_sub_type")} AS signal_sub_type,
               s.date,
               s.time,
               s.action,
               (s.signal - s.signal_reference)::float8 AS signal_excess,
               ((s.signal - s.signal_reference)
                / NULLIF(abs(s.signal_reference), 0) * 100)::float8
                                            AS signal_excess_pct,
               s.signal::float8               AS signal,
               s.signal_reference::float8     AS signal_threshold,
               s.confidence,
               (s.confidence::float8 / 100.0) AS confidence_pct,
               s.is_day_close_trigger,
               s.regime_state,
               s.is_ohlcv_boosted,
               s.is_typical,
               -- the is_rejection column was dropped 2026-10-14 — the
               -- grey-diamond wait event IS the action code now
               (s.action = ${LABEL_CODE.action.wait}) AS is_rejection
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
        -- is_rejection rides the partition too — a rejected NO-ACTION
        -- record never collapses into its actionable twin.
        SELECT d.*,
               bool_or(d.is_typical) OVER (
                   PARTITION BY d.signal_type, d.signal_sub_type, d.date,
                                d.is_ohlcv_boosted, d.is_rejection
               ) AS event_is_typical,
               row_number() OVER (
                   PARTITION BY d.signal_type, d.signal_sub_type, d.date,
                                d.is_ohlcv_boosted, d.is_rejection
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
            r.is_rejection,
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
//  Signal RULES / RSI series — the Recent Movements code-trend's rules
//  panel + the RSI value lines. (The curve/threshold tier was RETIRED
//  with its tables 2026-10-14; signal_rules replaces it.)
// ---------------------------------------------------------------------------

/** One RULE CELL row of the code (analysis_signals.signal_rules — the
 *  rules tier, 2026-10-15): the self-adaptive prepared parameters the
 *  engine measured for THIS (code, config, trend, regime, action) cell
 *  on the code's own history, plus the family knowledge document read
 *  from the repo's static template (the DB stores no document).
 *  The history table is populated by REPLAYING these cells. */
export interface SignalRuleRow {
  code: string;
  sec_type: string;
  signal_type: string;
  signal_sub_type: string;
  /** top / bottom / rise / fall / all / unknown — the code's own trend
   *  classification in force for the cell (the per-PK asymmetry axis),
   *  decoded from the label code. */
  trend: string;
  /** calm / hot / panic / quiet / all (pooled) — decoded. */
  regime_state: string;
  /** buy / sell / wait — decoded. */
  action: string;
  /** The per-cell GATE VERDICT (denormalized rule_params.gate.passed):
   *  TRUE iff the cell's measured future returns cleared its side's
   *  dir_ave bar at the sample floor (buy >= 0.25% @ n >= 3 / sell >=
   *  0% @ n >= 2). FALSE = observational (the starved wait watchlist /
   *  the ungated VRP twin) — negative-future-return evidence is
   *  discouraged: it never validates, never trades, never fires live. */
  is_valid: boolean;
  /** The engine entry that runs the rule (the RuleEntry subclass name
   *  under analysis/analysis_signals/engines/signal_families/). */
  rule_entry: string;
  /** The SELF-ADAPTIVE prepared parameters: the breach bar def, the
   *  episode merge K, the gate numbers, the measured evidence
   *  (n_episodes, dir_ave_pct, dir_std_pct, t_stat, hit_rate), the
   *  confidence scale and — on trading rows — the POOLED EVIDENCE
   *  block (the (sub_type, action) pooled n / t-stat / hit, the
   *  per-horizon decay curve, the expanding-year legs; the two-tier
   *  gate's statistical layer, 2026-10-16). */
  rule_params: Record<string, unknown>;
  /** The ohlcv boost's trust-weighted confidence BASE for the cell
   *  (pct — the same scale as rule_params.confidence.base_pct):
   *  sum(w*y)/sum(w) over the cell's measurable trigger days. NULL =
   *  the side boosted nothing (ship gates failed / family not opted
   *  in) — an honest absence, never a gate. */
  ohlcv_boost_conf: number | null;
  /** The family knowledge document (static.analysis_signal_docs —
   *  the knowledge seeded from database/sql/static/
   *  analysis_signal_docs/, joined per family). */
  document: Record<string, unknown>;
  created_at: string;
}

/** The code's rule cells — every (sub signal, trend, regime, action)
 *  cell the engines measured and shipped, one row each. */
export async function fetchSignalRules(
  secType: string | null | undefined,
  code: string,
): Promise<SignalRuleRow[]> {
  const st = assertSecType(secType);
  const c = (code ?? "").trim();
  if (!c) {
    throw Object.assign(new Error("code is required"), { status: 400 });
  }
  // the stored identity columns are SMALLINT label codes (the
  // label-code contract) — the filter carries the code, the response
  // decodes back to the labels the UI speaks; the two JSONB payloads
  // arrive as parsed objects (node-pg's jsonb parser). The family
  // document joins from static.analysis_signal_docs (one row per family —
  // byte-identical across a family's cells, so it lives there, not on
  // signal_rules); an unseeded family serves an empty document (the
  // rules panel degrades to the params digest)
  return queryRows<SignalRuleRow & QueryResultRow>(
    `SELECT r.code,
            ${labelDecodeSql("sec_type", "r.sec_type")} AS sec_type,
            ${labelDecodeSql("signal_type", "r.signal_type")} AS signal_type,
            ${labelDecodeSql("signal_sub_type", "r.signal_sub_type")} AS signal_sub_type,
            ${labelDecodeSql("trend", "r.trend")} AS trend,
            ${labelDecodeSql("regime", "r.regime_state")} AS regime_state,
            ${labelDecodeSql("action", "r.action")} AS action,
            r.is_valid,
            r.rule_entry,
            r.rule_params,
            r.ohlcv_boost_conf,
            COALESCE(d.document, '{}'::jsonb) AS document,
            to_char(r.created_at, 'YYYY-MM-DD HH24:MI') AS created_at
     FROM analysis_signals.signal_rules r
     LEFT JOIN static.analysis_signal_docs d
            ON d.signal_type = r.signal_type
     WHERE r.sec_type = ${LABEL_CODE.sec_type[st]} AND r.code = $1
     ORDER BY r.signal_type, r.signal_sub_type, r.trend,
              r.regime_state, r.action`,
    [c],
  );
}

// ----------------------------------------------------------------------------
//  fetchTrendSpans — the TREND spans of one (sec_type, code): contiguous
//  same-trend runs computed over analysis.trend_days (the
//  per-(code, date) trend classification trends.attach_trend stamps
//  on every family's prepared frame; written by the analysis_signals
//  pipeline). Gaps-and-islands in SQL (the store is small and always
//  fresh — no materialized spans table needed, unlike the regime spans'
//  stats.market_regime_spans precedent). The shared CodeTrendChart's
//  Trend toggle shades the spans per trend (top/bottom/rise/fall;
//  unknown is the unshaded background, the calm precedent). Default
//  window: the last 3 years (window_days param) — payload stays light.
// ----------------------------------------------------------------------------
export interface TrendSpanRow {
  trend: string;
  start_date: string;
  end_date: string;
  span_days: number;
}

export interface TrendSpansResponse {
  sec_type: string;
  code: string;
  window_days: number;
  spans: Record<string, TrendSpanRow[]>;
}

export async function fetchTrendSpans(
  secType: string | null | undefined,
  code: string,
  windowDays: number | null | undefined,
): Promise<TrendSpansResponse> {
  const st = assertSecType(secType);
  const c = (code ?? "").trim();
  if (!c) {
    throw Object.assign(new Error("code is required"), { status: 400 });
  }
  const days = Math.min(Math.max(Math.round(windowDays ?? 1095), 90), 3650);
  const rows = await queryRows<TrendSpanRow & QueryResultRow>(
    `WITH d AS (
       SELECT date, trend,
              CASE WHEN trend = lag(trend) OVER (ORDER BY date)
                   THEN 0 ELSE 1 END AS ch
       FROM analysis.trend_days
       WHERE sec_type = $1 AND code = $2
         AND date >= CURRENT_DATE - make_interval(days => $3)
     ), g AS (
       SELECT trend, date,
              sum(ch) OVER (ORDER BY date) AS grp
       FROM d
     )
     SELECT ${labelDecodeSql("trend", "trend")} AS trend,
            to_char(min(date), 'YYYY-MM-DD') AS start_date,
            to_char(max(date), 'YYYY-MM-DD') AS end_date,
            count(*)::int AS span_days
     FROM g
     GROUP BY trend, grp
     ORDER BY trend, start_date`,
    [LABEL_CODE.sec_type[st], c, days],
  );
  const spans: Record<string, TrendSpanRow[]> = {};
  for (const r of rows) {
    (spans[r.trend] ??= []).push({
      trend: r.trend,
      start_date: r.start_date,
      end_date: r.end_date,
      span_days: r.span_days,
    });
  }
  return { sec_type: st, code: c, window_days: days, spans };
}
