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
import {
  labelDecodeSql,
  LABEL_CODE,
  decodeLabel,
} from "./label-codes.js";
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
  /** The event's PE-CONDITIONING variant (a history_signals PK member
   *  since 2026-10-26): FALSE = the plain population, TRUE = the
   *  PE-gated variant of the same sub type (the rebound family emits
   *  both; the gate rides the cell's rule_params.bar_def.pe_gate).
   *  Live-tier rows carry no variants (always FALSE). */
  is_pe_boosted: boolean;
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
       -- wait rows are observational only since 2026-10-19 (the
       -- ungated VRP twin; the starvation wait era ended — under-floor
       -- cells trade at a reduced confidence): the menu contract stays
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
 *  boost's tuned confidence lives on the performance snapshots)
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
               s.is_pe_boosted,
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
                                d.is_ohlcv_boosted, d.is_pe_boosted,
                                d.is_rejection
               ) AS event_is_typical,
               row_number() OVER (
                   PARTITION BY d.signal_type, d.signal_sub_type, d.date,
                                d.is_ohlcv_boosted, d.is_pe_boosted,
                                d.is_rejection
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
            r.is_pe_boosted,
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
                    WHERE date = $1::date AND close IS NOT NULL)
     ) AS eligible`,
    [date],
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
 *      lives on the performance snapshots) + the day's episode-pass
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
               FALSE        AS is_pe_boosted,
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
        -- the performance snapshots); is_typical TRUE = the typical pass, FALSE =
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
               s.is_pe_boosted,
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
                                d.is_ohlcv_boosted, d.is_pe_boosted,
                                d.is_rejection
               ) AS event_is_typical,
               row_number() OVER (
                   PARTITION BY d.signal_type, d.signal_sub_type, d.date,
                                d.is_ohlcv_boosted, d.is_pe_boosted,
                                d.is_rejection
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
            r.is_pe_boosted,
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
 *  rules tier, 2026-10-15): the CONSTANT rule definition the engine
 *  prepared for THIS (code, config, trend, regime, action) cell, plus
 *  the family knowledge document read from the repo's static template
 *  (the DB stores no document). The MEASURED items — the cell's OWN
 *  evidence measure block — live in
 *  analysis_signals.history_signal_performance as dated daily
 *  snapshots (the 2026-10-19 split, slimmed 2026-10-20: the pooled
 *  tier / gate digest / boost conf / cascade moved to the portfolio
 *  tier); the response re-merges each cell's LATEST snapshot at this
 *  boundary, so the rule_params shape the UI consumes is unchanged.
 *  The history table is populated by REPLAYING these cells. */
export interface SignalRuleRow {
  code: string;
  sec_type: string;
  signal_type: string;
  signal_sub_type: string;
  /** The rule row's SURROGATE ID (signal_rule_id_seq, 2026-10-18) —
   *  the one-to-one join key analysis_signals.portfolio_signal_rules
   *  manages the rule cell through. Identifies the row EMISSION: the
   *  rules tier is current-state (a family's scope is wiped and
   *  re-inserted every pipeline run), so ids churn with it. */
  signal_rule_id: number;
  /** top / bottom / rise / fall / all / unknown — the code's own trend
   *  classification in force for the cell (the per-PK asymmetry axis),
   *  decoded from the label code. */
  trend: string;
  /** calm / hot / panic / quiet / all (pooled) — decoded. */
  regime_state: string;
  /** buy / sell / wait — decoded. */
  action: string;
  /** The per-cell GATE VERDICT (the unified dir_ave test): TRUE iff
   *  the cell's measured future returns cleared its side's dir_ave bar
   *  (buy >= 0.25% / sell >= 0%). The sample floor is NOT part of the
   *  verdict since 2026-10-19 — a cell under its floor trades at the
   *  starvation confidence scale (min(n/floor, 1), recomputed by
   *  readers from the latest snapshot's n_episodes + the rule's
   *  gate.sample_floor). FALSE = observational (the ungated VRP twin)
   *  — negative-future-return evidence is discouraged: it never
   *  validates, never trades, never fires live. */
  is_valid: boolean;
  /** Whether the cell belongs to the PORTFOLIO tier (a signal_rules PK
   *  member since 2026-10-23): an existing signal rule SOURCED by a
   *  portfolio is a SEPARATE row from the standalone one — true = a
   *  portfolio family's source-layer row (the hedge mov_rsi /
   *  rf_break component rows at the market + leg codes, claimed by an
   *  orchestration or not) or a pair-native member; false = the
   *  standalone families' rows and the orchestrating composites (the
   *  group IS the composite). The cell's own test stays the unified
   *  dir_ave gate; the portfolio tier's combined audit lives in
   *  history_portfolio_signal_performance. */
  is_in_portfolio: boolean;
  /** Whether the cell is the PE-CONDITIONED variant of its rule (a
   *  signal_rules PK member since 2026-10-26, the
   *  is_ohlcv_boosted/is_in_portfolio PK-member precedent): the
   *  rebound family emits both variants — true = the candidate masks
   *  are AND-gated by the code's own trailing PE-percentile condition
   *  (rule_params.bar_def.pe_gate carries it); false = the plain,
   *  PE-free population. Every non-PE family reads false. */
  is_pe_boosted: boolean;
  /** The cell's OHLCV-boost VARIANT axis (a signal_rules PK member
   *  since 2026-10-26, mirrored from history_signals where it has
   *  keyed since the boost-twin era): true = the OHLCV-boosted
   *  variant of the rule — a SEPARATE row from the unconstrained one.
   *  Retired as a live variant 2026-10-16 (the twins became the
   *  per-cell trust-weighted measurement): reads false on every row
   *  today, kept for the key's shape. */
  is_ohlcv_boosted: boolean;
  /** The engine entry that runs the rule (the RuleEntry subclass name
   *  under analysis/analysis_signals/engines/signal_families/). */
  rule_entry: string;
  /** The CONSTANT rule definition (bar def, episode merge K, reentry
   *  flags, gate bar constants, confidence scale) MERGED at this
   *  boundary with the latest performance snapshot's measured items —
   *  measure (n_episodes, dir_ave_pct, dir_std_pct, t_stat, hit_rate)
   *  and confidence.base_pct (= the measured dir_ave). */
  rule_params: Record<string, unknown>;
  /** 'YYYY-MM-DD' — the as-of date of the merged performance
   *  snapshot (the frame data horizon the cell was last measured
   *  through); null when no snapshot exists (pre-migration residue). */
  performance_as_of: string | null;
  /** The family knowledge document (static.signal_rules_docs — the
   *  knowledge seeded from database/sql/static/signal_rules/, joined
   *  per family). */
  document: Record<string, unknown>;
  created_at: string;
}

/** The latest performance snapshot's measured columns (the LATERAL
 *  join's payload) — peeled by mergeRulePerformance; never part of the
 *  wire contract (performance_as_of is the one field promoted onto the
 *  row). Slimmed 2026-10-20: the cell's OWN measure block only (the
 *  pooled tier / gate digest / boost conf / dd / cascade live on the
 *  portfolio tier). */
interface PerformanceSnapshotRow {
  perf_date: string | null;
  perf_n_episodes: number | null;
  perf_dir_ave_pct: number | null;
  perf_dir_std_pct: number | null;
  perf_t_stat: number | null;
  perf_hit_rate: number | null;
}

/** The LATERAL fragment joining each rule cell's LATEST
 *  history_signal_performance snapshot (the 2026-10-19 split: the
 *  stored rule_params are constant-only; the measured items are dated
 *  daily snapshots). PK-equi-joined on the full cell key, newest date
 *  first — partition-pruned by the code-leading PK. Alias: `perf`. */
const LATEST_PERFORMANCE_JOIN = `
     LEFT JOIN LATERAL (
        SELECT to_char(p.date, 'YYYY-MM-DD') AS perf_date,
               p.n_episodes        AS perf_n_episodes,
               p.dir_ave_pct       AS perf_dir_ave_pct,
               p.dir_std_pct       AS perf_dir_std_pct,
               p.t_stat            AS perf_t_stat,
               p.hit_rate          AS perf_hit_rate
        FROM analysis_signals.history_signal_performance p
        WHERE p.code = r.code AND p.sec_type = r.sec_type
          AND p.signal_type = r.signal_type
          AND p.signal_sub_type = r.signal_sub_type
          AND p.trend = r.trend
          AND p.regime_state = r.regime_state
          AND p.action = r.action
          AND p.is_pe_boosted = r.is_pe_boosted
          AND p.is_ohlcv_boosted = r.is_ohlcv_boosted
        ORDER BY p.date DESC LIMIT 1
     ) perf ON TRUE`;

/** Merge a cell's latest performance snapshot into its rule row: the
 *  measured items ride rule_params (measure /
 *  confidence.base_pct) — the rules-era response shape the UI
 *  consumes, served from the split store. A row without a snapshot
 *  (pre-migration residue) degrades to constants-only. */
function mergeRulePerformance(
  row: SignalRuleRow & QueryResultRow & PerformanceSnapshotRow,
): SignalRuleRow {
  const params: Record<string, unknown> = {
    ...(row.rule_params ?? {}),
  };
  if (row.perf_n_episodes != null) {
    params.measure = {
      n_episodes: row.perf_n_episodes,
      dir_ave_pct: row.perf_dir_ave_pct,
      dir_std_pct: row.perf_dir_std_pct,
      t_stat: row.perf_t_stat,
      hit_rate: row.perf_hit_rate,
    };
    params.confidence = {
      ...(params.confidence as Record<string, unknown> ?? {}),
      base_pct: row.perf_dir_ave_pct,
    };
  }
  const {
    perf_date, perf_n_episodes, perf_dir_ave_pct, perf_dir_std_pct,
    perf_t_stat, perf_hit_rate,
    ...rest
  } = row;
  void perf_n_episodes; void perf_dir_ave_pct; void perf_dir_std_pct;
  void perf_t_stat; void perf_hit_rate;
  return { ...rest, rule_params: params,
           performance_as_of: perf_date };
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
  // document joins from static.signal_rules_docs (one row per family —
  // byte-identical across a family's cells, so it lives there, not on
  // signal_rules); an unseeded family serves an empty document (the
  // rules panel degrades to the params digest). The latest performance
  // snapshot (the measured half — the 2026-10-19 split) laterally
  // joins per cell and mergeRulePerformance folds it back into the
  // response's rule_params shape.
  const rows = await queryRows<
    SignalRuleRow & QueryResultRow & PerformanceSnapshotRow
  >(
    `SELECT r.code,
            ${labelDecodeSql("sec_type", "r.sec_type")} AS sec_type,
            ${labelDecodeSql("signal_type", "r.signal_type")} AS signal_type,
            ${labelDecodeSql("signal_sub_type", "r.signal_sub_type")} AS signal_sub_type,
            ${labelDecodeSql("trend", "r.trend")} AS trend,
            ${labelDecodeSql("regime", "r.regime_state")} AS regime_state,
            ${labelDecodeSql("action", "r.action")} AS action,
            r.signal_rule_id::float8 AS signal_rule_id,
            r.is_valid,
            r.is_in_portfolio,
            r.is_pe_boosted,
            r.is_ohlcv_boosted,
            r.rule_entry,
            r.rule_params,
            perf.perf_date,
            perf.perf_n_episodes, perf.perf_dir_ave_pct,
            perf.perf_dir_std_pct, perf.perf_t_stat,
            perf.perf_hit_rate,
            COALESCE(d.document, '{}'::jsonb) AS document,
            to_char(r.created_at, 'YYYY-MM-DD HH24:MI') AS created_at
     FROM analysis_signals.signal_rules r
     LEFT JOIN static.signal_rules_docs d
            ON d.signal_type = r.signal_type${LATEST_PERFORMANCE_JOIN}
     WHERE r.sec_type = ${LABEL_CODE.sec_type[st]} AND r.code = $1
     ORDER BY r.signal_type, r.signal_sub_type, r.trend,
              r.regime_state, r.action`,
    [c],
  );
  return rows.map(mergeRulePerformance);
}

// ----------------------------------------------------------------------------
//  fetchSignalPortfolios / fetchPortfolioSignals — the PORTFOLIO tier
//  (2026-10-18; simplified 2026-10-20's cell-level redesign):
//  analysis_signals.portfolio_signal_rules holds ONE ROW PER (code,
//  portfolio_type) whose signal_rule_ids BIGINT[] carries ALL the
//  portfolio's member rule-row surrogate ids ACROSS SUB TYPES — =
//  ANY(ids) finds the portfolio's member cells, exactly the
//  is_in_portfolio cells — and whose rule_params JSONB carries the
//  CONSTANT portfolio construction (legs / market / aim / weighting +
//  the per-sub coordination specs under subs).
//  analysis_signals.history_portfolio_signal_performance (renamed
//  2026-10-20) holds the dated PORTFOLIO AUDIT per portfolio — WHAT
//  THE PORTFOLIO TOOK (the hedge type's claim: max drawdown reduction)
//  AND the COMBINED STATS of the individual in-portfolio signals (the
//  pooled block — the members' own measures meta-combined);
//  fetchPortfolioSignals laterally joins each portfolio's LATEST
//  snapshot into its response.
//  portfolio_type is the tier's OWN vocabulary (0 neg_corr_hedge_pair
//  — the two-leg 'A~B' long–long negatively-correlated hedge pair).
//  fetchSignalPortfolios serves the per-code roster census;
//  fetchPortfolioSignals matches portfolios by the REQUESTED CODES
//  (the pair types: both legs of 'A~B' must sit in the codes list)
//  and expands the ids to full rule rows.
// ----------------------------------------------------------------------------

/** One managed PORTFOLIO code's census (the roster row) — one row per
 *  PORTFOLIO RULE (code x portfolio_sub_type since the 2026-10-25
 *  split: trio wash and spread are separate portfolio signals). */
export interface SignalPortfolioRow {
  code: string;
  /** The portfolio tier's own vocabulary, decoded (today always
   *  neg_corr_hedge_pair — the two-leg 'A~B' hedge pair). */
  portfolio_type: string;
  /** The PORTFOLIO RULE within the type, decoded (static.label_dim
   *  'portfolio_sub_type': trio_wash / spread — the 2026-10-25 split's
   *  identity axis; one roster row per (code, sub)). */
  portfolio_sub_type: string;
  /** The portfolio rule's member rule cells (its whole ids array: the
   *  sub's orchestrating cells + the mapped registered rsi cells). */
  n_rules: number;
  /** The portfolio rule's gate-passed (is_valid) member cells. */
  n_valid: number;
  /** The rule's coordinated sub types (rule_params.subs keys). */
  n_sub_types: number;
  /** 'YYYY-MM-DD' — the portfolio code's latest replayed event date;
   *  null when its rules have never triggered. */
  latest_event: string | null;
  /** 'YYYY-MM-DD HH24:MI' — the pipeline run that emitted the
   *  portfolio's current row. */
  latest_built: string;
}

/** The managed PORTFOLIO codes of a sec_type (optionally narrowed to
 *  one portfolio_type) with their census — one row per code. An
 *  unknown portfolio_type label filters to nothing (never a fuzzy
 *  match). */
export async function fetchSignalPortfolios(
  secType: string | null | undefined,
  portfolioType?: string | null,
): Promise<SignalPortfolioRow[]> {
  const st = assertSecType(secType);
  const predicates = [`p.sec_type = ${LABEL_CODE.sec_type[st]}`];
  if (portfolioType != null && portfolioType.trim() !== "") {
    const ptCode = LABEL_CODE.portfolio_type[portfolioType.trim()];
    if (ptCode === undefined) {
      return [];
    }
    predicates.push(`p.portfolio_type = ${ptCode}`);
  }
  return queryRows<SignalPortfolioRow & QueryResultRow>(
    `SELECT p.code,
            ${labelDecodeSql("portfolio_type", "p.portfolio_type")} AS portfolio_type,
            ${labelDecodeSql("portfolio_sub_type", "p.portfolio_sub_type")} AS portfolio_sub_type,
            cardinality(p.signal_rule_ids)::int AS n_rules,
            (SELECT count(*)::int
             FROM analysis_signals.signal_rules r
             WHERE r.signal_rule_id = ANY(p.signal_rule_ids)
               AND r.is_valid) AS n_valid,
            COALESCE((SELECT count(*)::int
             FROM jsonb_object_keys(p.rule_params->'subs')), 0)
              AS n_sub_types,
            to_char(e.latest_event, 'YYYY-MM-DD') AS latest_event,
            to_char(p.created_at, 'YYYY-MM-DD HH24:MI') AS latest_built
     FROM analysis_signals.portfolio_signal_rules p
     LEFT JOIN LATERAL (
        SELECT max(h.date) AS latest_event
        FROM analysis_signals.history_signals h
        WHERE h.code = p.code AND h.sec_type = p.sec_type
     ) e ON TRUE
     WHERE ${predicates.join(" AND ")}
     ORDER BY p.code, p.portfolio_sub_type`,
    [],
  );
}

/** The latest history_portfolio_signal_performance snapshot of one
 *  portfolio: the as-of date, the COMBINED STATS of the individual
 *  in-portfolio signals (pooled — {n_cells, n_episodes, dir_ave_pct,
 *  t_stat, hit_rate}: the member cells' own measures meta-combined),
 *  the benefit payload (kind max_drawdown_reduction for the hedge
 *  type — max_dd {portfolio_pct, leg_a_pct, leg_b_pct,
 *  worst_leg_pct, mean_leg_pct, reduction_vs_worst_pct,
 *  reduction_vs_mean_pct}), the gate VERDICT digest {metric, min_pp,
 *  passed, conf_scale} (conf_scale = the member benefit-boost scale),
 *  and the portfolio's dd lens {min20_pct, p10_pct, baseline_pct, n}
 *  (the orchestrating cells' lenses combined). */
export interface PortfolioBenefitSnapshot {
  as_of: string | null;
  pooled: Record<string, unknown> | null;
  benefit: Record<string, unknown> | null;
  gate: Record<string, unknown> | null;
  dd: Record<string, unknown> | null;
}

/** One matched portfolio rule: its identity, census and PORTFOLIO row
 *  (2026-10-20's cell-level redesign; the 2026-10-25 SUB SPLIT — one
 *  row per (code, portfolio_type, portfolio_sub_type): trio wash and
 *  spread are SEPARATE portfolio signals, each with its OWN ids array
 *  (the sub's orchestrating cells + the MAPPED registered rsi cells),
 *  per-sub coordination spec, verdict + audit). */
export interface PortfolioSignalGroupRow {
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
  /** The portfolio rule's GATE VERDICT: the audited benefit cleared
   *  the portfolio gate AND at least one orchestrating composite cell's
   *  own entry-rule verdict — the GATING conjunction (members see the
   *  confidence increase only on is_valid portfolios). */
  is_valid: boolean;
  /** The orchestrating RuleEntry subclasses that wrote the portfolio
   *  ('|'-joined). */
  rule_entry: string;
  rule_params: Record<string, unknown>;
  performance: PortfolioBenefitSnapshot | null;
}

export interface PortfolioSignalsResult {
  sec_type: string;
  codes: string[];
  portfolios: PortfolioSignalGroupRow[];
  /** The matched portfolios' FULL rule rows (the same shape as
   *  fetchSignalRules; rule.code identifies its portfolio). */
  rules: SignalRuleRow[];
}

/** The portfolios whose legs sit in ``codes`` (the pair types: BOTH
 *  legs of the 'A~B' composite must be in the list — the neg_corr_
 *  hedge_pair convention), with their portfolio rows and FULL rule
 *  rows expanded from the signal_rule_ids arrays. Industry scope for
 *  now (the hedge-pair legs are industry ids). Feeds the shared
 *  portfolio-signal UI component. */
export async function fetchPortfolioSignals(
  secType: string | null | undefined,
  codes: string[],
): Promise<PortfolioSignalsResult> {
  const st = assertSecType(secType);
  const cleaned = [...new Set(
    (codes ?? []).map((c) => (c ?? "").trim()).filter(Boolean),
  )];
  if (cleaned.length === 0) {
    return { sec_type: st, codes: [], portfolios: [], rules: [] };
  }
  // the PAIR types' leg matching: the composite code is 'A~B' — both
  // split parts must sit in the requested codes. neg_corr_hedge_pair
  // (0) is the only portfolio type today; multi-leg types extend this
  // predicate when they arrive. ONE ROW PER PORTFOLIO RULE (code x
  // portfolio_sub_type — the 2026-10-25 split: a pair carrying both
  // rules returns TWO rows)
  const groups = await queryRows<
    { code: string; portfolio_sub_type: string;
       latest_event: string | null; latest_built: string } &
    QueryResultRow
  >(
    `SELECT p.code,
            ${labelDecodeSql("portfolio_sub_type", "p.portfolio_sub_type")} AS portfolio_sub_type,
            to_char(e.latest_event, 'YYYY-MM-DD') AS latest_event,
            to_char(p.created_at, 'YYYY-MM-DD HH24:MI') AS latest_built
     FROM analysis_signals.portfolio_signal_rules p
     LEFT JOIN LATERAL (
        SELECT max(h.date) AS latest_event
        FROM analysis_signals.history_signals h
        WHERE h.code = p.code AND h.sec_type = p.sec_type
     ) e ON TRUE
     WHERE p.sec_type = ${LABEL_CODE.sec_type[st]}
       AND p.portfolio_type = ${LABEL_CODE.portfolio_type.neg_corr_hedge_pair}
       AND split_part(p.code, '~', 1) = ANY($1::text[])
       AND split_part(p.code, '~', 2) = ANY($1::text[])
     ORDER BY p.code, p.portfolio_sub_type`,
    [cleaned],
  );
  if (groups.length === 0) {
    return { sec_type: st, codes: cleaned, portfolios: [], rules: [] };
  }
  const matched = [...new Set(groups.map((g) => g.code))];
  // the matched portfolio rules' OWN rows (one per code x sub since
  // the split): the whole ids array, the construction + the rule's
  // per-sub coordination spec (rule_params), the rule-level gate
  // verdict + writing entries, and the LATEST
  // history_portfolio_signal_performance snapshot (the rule's audit —
  // what the portfolio took + the members' COMBINED STATS; laterally
  // joined newest-date-first, per sub)
  const portRows = await queryRows<
    { code: string; portfolio_sub_type: number;
       signal_rule_ids: number[];
       is_valid: boolean; rule_entry: string;
       rule_params: Record<string, unknown>;
       pp_as_of: string | null;
       pp_pooled: Record<string, unknown> | null;
       pp_benefit: Record<string, unknown> | null;
       pp_gate: Record<string, unknown> | null;
       pp_dd: Record<string, unknown> | null } & QueryResultRow
  >(
    `SELECT p.code,
            p.portfolio_sub_type,
            p.signal_rule_ids,
            p.is_valid,
            p.rule_entry,
            p.rule_params,
            to_char(pp.pp_date, 'YYYY-MM-DD') AS pp_as_of,
            pp.pp_pooled,
            pp.pp_benefit,
            pp.pp_gate,
            pp.pp_dd
     FROM analysis_signals.portfolio_signal_rules p
     LEFT JOIN LATERAL (
        SELECT q.date AS pp_date, q.pooled AS pp_pooled,
               q.benefit AS pp_benefit, q.gate AS pp_gate,
               q.dd AS pp_dd
        FROM analysis_signals.history_portfolio_signal_performance q
        WHERE q.code = p.code AND q.portfolio_type = p.portfolio_type
          AND q.portfolio_sub_type = p.portfolio_sub_type
        ORDER BY q.date DESC LIMIT 1
     ) pp ON TRUE
     WHERE p.sec_type = ${LABEL_CODE.sec_type[st]}
       AND p.portfolio_type = ${LABEL_CODE.portfolio_type.neg_corr_hedge_pair}
       AND p.code = ANY($1::text[])
     ORDER BY p.code, p.portfolio_sub_type`,
    [matched],
  );
  // the FULL rule rows: the matched portfolios' OWN rows (the
  // composite orchestrating cells — code = the pair id) UNION the
  // rules' member ids expanded (the MAPPED REGISTERED rsi cells at the
  // market + leg codes, riding the is_in_portfolio TRUE tier); each
  // cell's latest performance snapshot laterally joins and
  // mergeRulePerformance folds it back in (the 2026-10-19 split)
  const allIds = portRows.flatMap((s) =>
    s.signal_rule_ids.map(Number));
  const rules = await queryRows<
    SignalRuleRow & QueryResultRow & PerformanceSnapshotRow
  >(
    `SELECT r.code,
            ${labelDecodeSql("sec_type", "r.sec_type")} AS sec_type,
            ${labelDecodeSql("signal_type", "r.signal_type")} AS signal_type,
            ${labelDecodeSql("signal_sub_type", "r.signal_sub_type")} AS signal_sub_type,
            ${labelDecodeSql("trend", "r.trend")} AS trend,
            ${labelDecodeSql("regime", "r.regime_state")} AS regime_state,
            ${labelDecodeSql("action", "r.action")} AS action,
            r.signal_rule_id::float8 AS signal_rule_id,
            r.is_valid,
            r.is_in_portfolio,
            r.is_pe_boosted,
            r.is_ohlcv_boosted,
            r.rule_entry,
            r.rule_params,
            perf.perf_date,
            perf.perf_n_episodes, perf.perf_dir_ave_pct,
            perf.perf_dir_std_pct, perf.perf_t_stat,
            perf.perf_hit_rate,
            COALESCE(d.document, '{}'::jsonb) AS document,
            to_char(r.created_at, 'YYYY-MM-DD HH24:MI') AS created_at
     FROM analysis_signals.signal_rules r
     LEFT JOIN static.signal_rules_docs d
            ON d.signal_type = r.signal_type${LATEST_PERFORMANCE_JOIN}
     WHERE r.sec_type = ${LABEL_CODE.sec_type[st]}
       AND (r.code = ANY($1::text[])
            OR r.signal_rule_id = ANY($2::bigint[]))
     ORDER BY r.code, r.signal_type, r.signal_sub_type, r.trend,
              r.regime_state, r.action`,
    [matched, allIds],
  ).then((rows) => rows.map(mergeRulePerformance));
  // the census per portfolio rule: its OWN members by id (the sub's
  // orchestrating cells + the mapped registered cells; the composite's
  // own row is not a member — the RULE is the composite)
  const byId = new Map(rules.map((r) => [r.signal_rule_id, r]));
  const byKey = new Map(portRows.map((p) =>
    [`${p.code}|${p.portfolio_sub_type}`, p]));
  const portfolios: PortfolioSignalGroupRow[] = groups.map((g) => {
    const p = byKey.get(`${g.code}|${
      LABEL_CODE.portfolio_sub_type[g.portfolio_sub_type] ?? ""}`);
    const ids = (p?.signal_rule_ids ?? []).map(Number);
    return {
      code: g.code,
      portfolio_type: "neg_corr_hedge_pair",
      portfolio_sub_type: g.portfolio_sub_type,
      n_rules: ids.length,
      n_valid: ids.filter((id) => byId.get(id)?.is_valid === true)
        .length,
      n_sub_types: Object.keys(
        (p?.rule_params as { subs?: Record<string, unknown> } | undefined)
          ?.subs ?? {},
      ).length,
      latest_event: g.latest_event,
      latest_built: g.latest_built,
      signal_rule_ids: ids,
      is_valid: p?.is_valid === true,
      rule_entry: p?.rule_entry ?? "",
      rule_params: p?.rule_params ?? {},
      performance: (p != null && (p.pp_benefit != null
                                  || p.pp_as_of != null
                                  || p.pp_gate != null || p.pp_dd != null
                                  || p.pp_pooled != null))
        ? { as_of: p.pp_as_of, pooled: p.pp_pooled,
            benefit: p.pp_benefit, gate: p.pp_gate, dd: p.pp_dd }
        : null,
    };
  });
  return { sec_type: st, codes: cleaned, portfolios, rules };
}

// ----------------------------------------------------------------------------
//  fetchPortfolioSignalHistory — the PORTFOLIO tier's EVENT history
//  (2026-10-21): analysis_signals.portfolio_history_signals holds ONE
//  ROW PER PORTFOLIO EVENT (the composite/pair-native rule cells'
//  replayed triggers at the pair codes) with THE EVENT DATE, the
//  ``member_signals`` JSONB — the coordinated MEMBER ACTIONS the event
//  consists of (the MEMBER-ACTION INVARIANT: at least TWO actions on
//  at least TWO codes per event, different dates allowed; hp_harvest
//  the measured one-leg exception) — AND the ``cell_signals`` JSONB
//  array: each SOURCED member's history_signals PK (carrying the
//  is_in_portfolio tier flag — a PK member since 2026-10-23: the
//  sourced cell events ride the TRUE tier, SEPARATE rows from the
//  standalone families' events under the same vocabulary). The cell-level
//  signals are fetched EXPLICITLY from signal history (2026-10-21,
//  user-directed): the sourced members render the records
//  history_signals holds NOW; the pair-frame facets (the legs'
//  witness/response records — no cell behind them) and any dangling
//  key fall back to the stored event-time snapshots. The source
//  layer's component cell events LIVE in history_signals (the engines'
//  episode-first records, each WITH ITS OWN DATE — different sub
//  signals on different nearby days), so one event hooks the cells of
//  MULTIPLE sub types. Label codes decode at the response boundary.
//  Feeds the portfolio history panel's expandable rows.
// ----------------------------------------------------------------------------

/** One member CELL-LEVEL action record inside a portfolio event's
 *  member association: a sourced component cell event fetched from
 *  signal history (through the event's cell_signals keys), or a
 *  pair-frame facet record (the stored event-time snapshot). */
export interface PortfolioEventMember {
  code: string;
  signal_type: string;
  signal_sub_type: string;
  action: string;
  /** 'YYYY-MM-DD' — the member's OWN trigger day (the component's
   *  episode first day — up to the witness window before the event). */
  date: string;
  signal: number;
  signal_reference: number;
  /** Basis points — the member record's own scaled confidence. */
  confidence: number;
}

/** One PORTFOLIO EVENT (analysis_signals.portfolio_history_signals
 *  row): the coordinated event's own record + the member association
 *  the UI's row expansion renders. */
export interface PortfolioHistorySignalRow {
  code: string;
  portfolio_type: string;
  /** The event's PORTFOLIO RULE, decoded (trio_wash / spread — the
   *  2026-10-25 split; derived exactly from the event's orchestrating
   *  sub on the pre-split migration). */
  portfolio_sub_type: string;
  signal_type: string;
  signal_sub_type: string;
  date: string;
  time: string;
  action: string;
  signal: number;
  signal_reference: number;
  /** signal - signal_reference (the breach-direction sign). */
  signal_excess: number;
  /** Basis points — the composite event's scaled confidence. */
  confidence: number;
  confidence_pct: number;
  regime_state: string;
  is_typical: boolean | null;
  /** The event's coordinated MEMBER ACTIONS (sourced cell events /
   *  leg witnesses at their own days / the pair response legs); the
   *  member-action invariant holds >= 2 on >= 2 codes (hp_harvest the
   *  measured one-leg exception). */
  members: PortfolioEventMember[];
}

/** One portfolio code's FULL event history (newest first): the
 *  coordinated portfolio events with their member cell-level signals.
 *  Optional signal_type / signal_sub_type narrow to one family / sub
 *  (unknown labels filter to nothing, never a fuzzy match). */
export async function fetchPortfolioSignalHistory(
  secType: string | null | undefined,
  code: string,
  signalType?: string | null,
  signalSubType?: string | null,
): Promise<PortfolioHistorySignalRow[]> {
  const st = assertSecType(secType);
  const c = (code ?? "").trim();
  if (!c) {
    throw Object.assign(new Error("code is required"), { status: 400 });
  }
  const predicates = [
    `h.sec_type = ${LABEL_CODE.sec_type[st]}`,
    `h.code = $1`,
  ];
  const params: unknown[] = [c];
  if (signalType != null && signalType.trim() !== "") {
    const sigCode = LABEL_CODE.signal_type[signalType.trim()];
    if (sigCode === undefined) return [];
    predicates.push(`h.signal_type = ${sigCode}`);
  }
  if (signalSubType != null && signalSubType.trim() !== "") {
    const subCode = LABEL_CODE.signal_sub_type[signalSubType.trim()];
    if (subCode === undefined) return [];
    predicates.push(`h.signal_sub_type = ${subCode}`);
  }
  const rows = await queryRows<
    {
      code: string;
      portfolio_type: string;
      portfolio_sub_type: string;
      signal_type: string;
      signal_sub_type: string;
      date: string;
      time: string;
      action: string;
      signal: number;
      signal_reference: number;
      signal_excess: number;
      confidence: number;
      confidence_pct: number;
      regime_state: string;
      is_typical: boolean | null;
      member_signals: Array<Record<string, unknown>>;
      cell_signals: Array<Record<string, unknown>> | null;
    } & QueryResultRow
  >(
    `SELECT h.code,
            ${labelDecodeSql("portfolio_type", "h.portfolio_type")} AS portfolio_type,
            ${labelDecodeSql("portfolio_sub_type", "h.portfolio_sub_type")} AS portfolio_sub_type,
            ${labelDecodeSql("signal_type", "h.signal_type")} AS signal_type,
            ${labelDecodeSql("signal_sub_type", "h.signal_sub_type")} AS signal_sub_type,
            to_char(h.date, 'YYYY-MM-DD') AS date,
            to_char(h.time, 'HH24:MI')    AS time,
            ${labelDecodeSql("action", "h.action")} AS action,
            h.signal::float8               AS signal,
            h.signal_reference::float8     AS signal_reference,
            (h.signal - h.signal_reference)::float8 AS signal_excess,
            h.confidence,
            (h.confidence::float8 / 100.0) AS confidence_pct,
            ${labelDecodeSql("regime", "h.regime_state")} AS regime_state,
            h.is_typical,
            h.member_signals,
            h.cell_signals
     FROM analysis_signals.portfolio_history_signals h
     WHERE ${predicates.join(" AND ")}
     ORDER BY h.date DESC, h.time DESC, h.signal_type, h.signal_sub_type`,
    params,
  );
  // The cell-level signals are fetched EXPLICITLY from signal history
  // (2026-10-21, user-directed): every sourced member's ``cell_signals``
  // key (its history_signals PK) resolves against ONE history query for
  // the whole page, and the fetched row REPLACES the event-time
  // snapshot as the member's authoritative record. The pair-frame
  // facets (the legs' witness/response records — no cell behind them)
  // and any dangling key fall back to the stored member_signals
  // snapshots.
  const keySet = new Set<string>();
  for (const r of rows) {
    for (const k of r.cell_signals ?? []) keySet.add(JSON.stringify(k));
  }
  const fetched = new Map<string, PortfolioEventMember>();
  if (keySet.size > 0) {
    const cellRows = await queryRows<
      {
        code: string;
        signal_type: number;
        signal_sub_type: number;
        action: number;
        date: string;
        time: string;
        signal: number;
        signal_reference: number;
        confidence: number;
        is_in_portfolio: boolean;
      } & QueryResultRow
    >(
      `SELECT h.code,
              h.signal_type, h.signal_sub_type,
              ${labelDecodeSql("action", "h.action")} AS action,
              to_char(h.date, 'YYYY-MM-DD') AS date,
              to_char(h.time, 'HH24:MI')    AS time,
              h.signal::float8              AS signal,
              h.signal_reference::float8    AS signal_reference,
              h.confidence,
              h.is_in_portfolio
       FROM analysis_signals.history_signals h
       JOIN jsonb_to_recordset($1::jsonb) AS k(code text,
              signal_type smallint, signal_sub_type smallint,
              is_ohlcv_boosted boolean, is_in_portfolio boolean,
              date date, time text)
         ON h.code = k.code
        AND h.signal_type = k.signal_type
        AND h.signal_sub_type = k.signal_sub_type
        AND h.is_ohlcv_boosted = k.is_ohlcv_boosted
        AND h.is_in_portfolio = COALESCE(k.is_in_portfolio, false)
        AND h.date = k.date
        AND h.time = k.time::time
       WHERE h.sec_type = ${LABEL_CODE.sec_type[st]}`,
      [JSON.stringify([...keySet].map((s) => JSON.parse(s)))],
    );
    for (const cr of cellRows) {
      fetched.set(
        JSON.stringify({
          code: cr.code,
          signal_type: cr.signal_type,
          signal_sub_type: cr.signal_sub_type,
          is_ohlcv_boosted: false,
          is_in_portfolio: cr.is_in_portfolio === true,
          date: cr.date,
          time: cr.time,
        }),
        {
          code: cr.code,
          signal_type: decodeLabel("signal_type", cr.signal_type)
            ?? String(cr.signal_type),
          signal_sub_type: decodeLabel("signal_sub_type",
            cr.signal_sub_type as number) ?? String(cr.signal_sub_type),
          action: String(cr.action),
          date: cr.date,
          signal: Number(cr.signal ?? 0),
          signal_reference: Number(cr.signal_reference ?? 0),
          confidence: Number(cr.confidence ?? 0),
        },
      );
    }
  }
  const decodedKey = (k: Record<string, unknown>) => JSON.stringify({
    code: k.code, signal_type: k.signal_type,
    signal_sub_type: k.signal_sub_type,
    is_ohlcv_boosted: k.is_ohlcv_boosted ?? false,
    // the sourced cell events ride the is_in_portfolio TRUE tier (a
    // history_signals PK member since 2026-10-23); pre-flag legacy
    // keys read false and only ever matched FALSE-tier rows anyway
    is_in_portfolio: k.is_in_portfolio ?? false,
    date: k.date, time: k.time,
  });
  return rows.map((r) => {
    // the sourced members, in their recorded order, each the record
    // history_signals holds NOW (the stored snapshot as the fallback)
    const members: PortfolioEventMember[] = [];
    const served = new Set<string>();
    for (const k of r.cell_signals ?? []) {
      const dk = decodedKey(k);
      const m = fetched.get(dk);
      if (m) {
        members.push(m);
        served.add(dk);
      }
    }
    // the facets (and any dangling key): the stored event-time records
    // whose identity no fetched cell row covers — a member WITH a cell
    // behind it is a SOURCED record (the TRUE tier), so its identity
    // carries is_in_portfolio true to meet the fetched key's shape; a
    // facet's identity matches nothing and falls through
    for (const m of r.member_signals ?? []) {
      const identity = JSON.stringify({
        code: m.code, signal_type: m.signal_type,
        signal_sub_type: m.signal_sub_type,
        is_ohlcv_boosted: false,
        is_in_portfolio: true,
        date: m.date, time: m.time ?? "15:00",
      });
      if (served.has(identity)) continue;
      members.push({
        code: String(m.code ?? ""),
        signal_type: decodeLabel("signal_type", m.signal_type as number)
          ?? String(m.signal_type),
        // a facet member's sub may be null (no cell behind it) — render
        // the em dash, never the string "null"
        signal_sub_type: decodeLabel("signal_sub_type",
          m.signal_sub_type as number)
          ?? (m.signal_sub_type == null ? "—" : String(m.signal_sub_type)),
        action: decodeLabel("action", m.action as number)
          ?? String(m.action),
        date: String(m.date ?? ""),
        signal: Number(m.signal ?? 0),
        signal_reference: Number(m.signal_reference ?? 0),
        confidence: Number(m.confidence ?? 0),
      });
    }
    return {
      ...r,
      members,
    };
  });
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
