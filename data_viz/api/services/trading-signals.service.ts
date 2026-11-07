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
import { querySqlView, renderSqlView, sqlFrag } from "../lib/sql-views.js";
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
   *  is the only cohort. The is_ohlcv_boosted / is_pe_boosted variant
   *  columns shared the same fate 2026-11-01: the variant axes were
   *  unlinked from the PKs and the emitters — every event IS the
   *  plain record.) */
  is_day_close_trigger: boolean;
  /** The breach bar's DATE market regime — the stats.market_regimes
   *  day label (calm/hot/panic/quiet). Context, not a gate: the breach
   *  still fires; the label matches the strategy's own regime split. */
  regime_state: string;
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
  // decodes back to the labels the UI speaks. The SELECT lives in the sql
  // view data_viz/api/services/trading-signals/signal_configs.
  const rows = await querySqlView<TradingSignalConfigRow & QueryResultRow>(
    "data_viz/api/services/trading-signals/signal_configs",
    {
      signal_type_decode: sqlFrag(
        labelDecodeSql("signal_type", "t.signal_type")),
      signal_sub_type_decode: sqlFrag(
        labelDecodeSql("signal_sub_type", "t.signal_sub_type")),
      sec_type_code: sqlFrag(String(LABEL_CODE.sec_type[st])),
      options_wall_code: sqlFrag(String(LABEL_CODE.signal_type.options_wall)),
      options_vix_code: sqlFrag(String(LABEL_CODE.signal_type.options_vix)),
      wait_code: sqlFrag(String(LABEL_CODE.action.wait)),
    },
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
 *  DESC — one row per event (the retired is_ohlcv_boosted / is_pe_boosted
 *  variant axes left the schema 2026-11-01 — every event IS the plain
 *  record)
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
  // The SELECT lives in the sql view
  // data_viz/api/services/trading-signals/triggered_day.
  const rows = await querySqlView<TradingSignalRow & QueryResultRow>(
    "data_viz/api/services/trading-signals/triggered_day",
    {
      sec_type: st,
      date,
      basic_stats_table: sqlFrag(BASIC_STATS_TABLE[st]!),
      hist_sec_type_decode: sqlFrag(labelDecodeSql("sec_type", "s.sec_type")),
      hist_signal_type_decode: sqlFrag(
        labelDecodeSql("signal_type", "s.signal_type")),
      hist_signal_sub_type_decode: sqlFrag(
        labelDecodeSql("signal_sub_type", "s.signal_sub_type")),
      wait_code: sqlFrag(String(LABEL_CODE.action.wait)),
      hist_sec_type_code: sqlFrag(String(LABEL_CODE.sec_type[st])),
      hist_options_wall_code: sqlFrag(
        String(LABEL_CODE.signal_type.options_wall)),
      hist_options_vix_code: sqlFrag(
        String(LABEL_CODE.signal_type.options_vix)),
      action_decode: sqlFrag(labelDecodeSql("action", "r.action")),
      regime_decode: sqlFrag(labelDecodeSql("regime", "r.regime_state")),
      identity_table: sqlFrag(idt),
    },
  );
  return rows;
}

/** Dates present in live_signals for a sec_type (roster, newest first). */
export async function fetchTradingSignalDates(
  secType: string | null | undefined,
): Promise<string[]> {
  const st = assertSecType(secType);
  // The SELECT lives in the sql view
  // data_viz/api/services/trading-signals/available_dates.
  const rows = await querySqlView<{ date: string } & QueryResultRow>(
    "data_viz/api/services/trading-signals/available_dates",
    {
      sec_type: st,
      basic_stats_table: sqlFrag(BASIC_STATS_TABLE[st]!),
    },
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
  // The SELECT lives in the sql view
  // data_viz/api/services/trading-signals/compute_eligible.
  const rows = await querySqlView<{ eligible: boolean } & QueryResultRow>(
    "data_viz/api/services/trading-signals/compute_eligible",
    {
      date,
      sec_type_code: sqlFrag(String(LABEL_CODE.sec_type[st])),
      basic_stats_table: sqlFrag(BASIC_STATS_TABLE[st]!),
    },
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
 *      one population per event (the is_ohlcv_boosted / is_pe_boosted
 *      variant axes left the schema 2026-11-01) + the day's episode-pass
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
  // The SELECT lives in the sql view
  // data_viz/api/services/trading-signals/history — the per-tier
  // predicates, the anchor, the table names and the label decodes ride
  // fragments; all params stay caller-assembled (the live tier keeps its
  // TEXT-label params $1.. while the history tier's label-code literals
  // ride inside histWhere).
  const rows = await queryRows<TradingSignalRow & QueryResultRow>(
    renderSqlView("data_viz/api/services/trading-signals/history", {
      basic_stats_table: sqlFrag(`${BASIC_STATS_TABLE[st]!}`),
      identity_table: sqlFrag(`${IDENTITY_TABLE[st]!}`),
      where_clause: sqlFrag(where),
      hist_where_clause: sqlFrag(histWhere),
      anchor_expr: sqlFrag(anchorExpr),
      sec_type_decode: sqlFrag(labelDecodeSql("sec_type", "s.sec_type")),
      signal_type_decode: sqlFrag(labelDecodeSql("signal_type", "s.signal_type")),
      signal_sub_type_decode: sqlFrag(labelDecodeSql("signal_sub_type", "s.signal_sub_type")),
      action_decode: sqlFrag(labelDecodeSql("action", "r.action")),
      regime_decode: sqlFrag(labelDecodeSql("regime", "r.regime_state")),
      action_wait: sqlFrag(`${LABEL_CODE.action.wait}`),
      name_code: sqlFrag("$2"),
    }).sql,
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
  // response's rule_params shape. The SELECT lives in the sql view
  // data_viz/api/services/trading-signals/signal_rules.
  const rows = await querySqlView<
    SignalRuleRow & QueryResultRow & PerformanceSnapshotRow
  >(
    "data_viz/api/services/trading-signals/signal_rules",
    {
      code: c,
      sec_type_decode: sqlFrag(labelDecodeSql("sec_type", "r.sec_type")),
      signal_type_decode: sqlFrag(
        labelDecodeSql("signal_type", "r.signal_type")),
      signal_sub_type_decode: sqlFrag(
        labelDecodeSql("signal_sub_type", "r.signal_sub_type")),
      trend_decode: sqlFrag(labelDecodeSql("trend", "r.trend")),
      regime_decode: sqlFrag(labelDecodeSql("regime", "r.regime_state")),
      action_decode: sqlFrag(labelDecodeSql("action", "r.action")),
      perf_join: sqlFrag(LATEST_PERFORMANCE_JOIN),
      sec_type_code: sqlFrag(String(LABEL_CODE.sec_type[st])),
    },
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
  // The SELECT lives in the sql view
  // data_viz/api/services/trading-signals/portfolio_roster (the WHERE
  // predicate list rides as a fragment — literal label codes only).
  return querySqlView<SignalPortfolioRow & QueryResultRow>(
    "data_viz/api/services/trading-signals/portfolio_roster",
    {
      portfolio_type_decode: sqlFrag(
        labelDecodeSql("portfolio_type", "p.portfolio_type")),
      portfolio_sub_type_decode: sqlFrag(
        labelDecodeSql("portfolio_sub_type", "p.portfolio_sub_type")),
      sec_predicates: sqlFrag(predicates.join(" AND ")),
    },
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
  // ONE ROW PER PORTFOLIO RULE (code x portfolio_sub_type — the
  // 2026-10-25 split: a pair carrying both rules returns TWO rows).
  // The SELECT lives in the sql view
  // data_viz/api/services/trading-signals/portfolio_groups.
  const groups = await querySqlView<
    { code: string; portfolio_sub_type: string;
       latest_event: string | null; latest_built: string } &
    QueryResultRow
  >(
    "data_viz/api/services/trading-signals/portfolio_groups",
    {
      portfolio_sub_type_decode: sqlFrag(
        labelDecodeSql("portfolio_sub_type", "p.portfolio_sub_type")),
      sec_type_code: sqlFrag(String(LABEL_CODE.sec_type[st])),
      portfolio_type_code: sqlFrag(
        String(LABEL_CODE.portfolio_type.neg_corr_hedge_pair)),
      codes: cleaned,
    },
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
  // joined newest-date-first, per sub). The SELECT lives in the sql
  // view data_viz/api/services/trading-signals/portfolio_rules.
  const portRows = await querySqlView<
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
    "data_viz/api/services/trading-signals/portfolio_rules",
    {
      sec_type_code: sqlFrag(String(LABEL_CODE.sec_type[st])),
      portfolio_type_code: sqlFrag(
        String(LABEL_CODE.portfolio_type.neg_corr_hedge_pair)),
      codes: matched,
    },
  );
  // the FULL rule rows: the matched portfolios' OWN rows (the
  // composite orchestrating cells — code = the pair id) UNION the
  // rules' member ids expanded (the MAPPED REGISTERED rsi cells at the
  // market + leg codes, riding the is_in_portfolio TRUE tier); each
  // cell's latest performance snapshot laterally joins and
  // mergeRulePerformance folds it back in (the 2026-10-19 split). The
  // SELECT lives in the sql view
  // data_viz/api/services/trading-signals/signal_rules_by_ids.
  const allIds = portRows.flatMap((s) =>
    s.signal_rule_ids.map(Number));
  const rules = await querySqlView<
    SignalRuleRow & QueryResultRow & PerformanceSnapshotRow
  >(
    "data_viz/api/services/trading-signals/signal_rules_by_ids",
    {
      sec_type_decode: sqlFrag(labelDecodeSql("sec_type", "r.sec_type")),
      signal_type_decode: sqlFrag(
        labelDecodeSql("signal_type", "r.signal_type")),
      signal_sub_type_decode: sqlFrag(
        labelDecodeSql("signal_sub_type", "r.signal_sub_type")),
      trend_decode: sqlFrag(labelDecodeSql("trend", "r.trend")),
      regime_decode: sqlFrag(labelDecodeSql("regime", "r.regime_state")),
      action_decode: sqlFrag(labelDecodeSql("action", "r.action")),
      perf_join: sqlFrag(LATEST_PERFORMANCE_JOIN),
      sec_type_code: sqlFrag(String(LABEL_CODE.sec_type[st])),
      codes: matched,
      rule_ids: allIds,
    },
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
  // The common (sec_type, code) predicates live in the view's fixed text;
  // the optional family predicates ride as a fragment suffix (literal
  // label codes only — no pg params inside the fragment).
  const familyPredicates: string[] = [];
  if (signalType != null && signalType.trim() !== "") {
    const sigCode = LABEL_CODE.signal_type[signalType.trim()];
    if (sigCode === undefined) return [];
    familyPredicates.push(`h.signal_type = ${sigCode}`);
  }
  if (signalSubType != null && signalSubType.trim() !== "") {
    const subCode = LABEL_CODE.signal_sub_type[signalSubType.trim()];
    if (subCode === undefined) return [];
    familyPredicates.push(`h.signal_sub_type = ${subCode}`);
  }
  // The SELECT lives in the sql view
  // data_viz/api/services/trading-signals/portfolio_history.
  const rows = await querySqlView<
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
    "data_viz/api/services/trading-signals/portfolio_history",
    {
      code: c,
      sec_type_code: sqlFrag(String(LABEL_CODE.sec_type[st])),
      family_predicates: sqlFrag(
        familyPredicates.map((p) => ` AND ${p}`).join("")),
      portfolio_type_decode: sqlFrag(
        labelDecodeSql("portfolio_type", "h.portfolio_type")),
      portfolio_sub_type_decode: sqlFrag(
        labelDecodeSql("portfolio_sub_type", "h.portfolio_sub_type")),
      signal_type_decode: sqlFrag(
        labelDecodeSql("signal_type", "h.signal_type")),
      signal_sub_type_decode: sqlFrag(
        labelDecodeSql("signal_sub_type", "h.signal_sub_type")),
      action_decode: sqlFrag(labelDecodeSql("action", "h.action")),
      regime_decode: sqlFrag(labelDecodeSql("regime", "h.regime_state")),
    },
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
    // The SELECT lives in the sql view
    // data_viz/api/services/trading-signals/portfolio_cell_signals.
    const cellRows = await querySqlView<
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
      "data_viz/api/services/trading-signals/portfolio_cell_signals",
      {
        cell_keys: JSON.stringify([...keySet].map((s) => JSON.parse(s))),
        action_decode: sqlFrag(labelDecodeSql("action", "h.action")),
        sec_type_code: sqlFrag(String(LABEL_CODE.sec_type[st])),
      },
    );
    for (const cr of cellRows) {
      fetched.set(
        JSON.stringify({
          code: cr.code,
          signal_type: cr.signal_type,
          signal_sub_type: cr.signal_sub_type,
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
  // The SELECT lives in the sql view
  // data_viz/api/services/trading-signals/trend_spans.
  const rows = await querySqlView<TrendSpanRow & QueryResultRow>(
    "data_viz/api/services/trading-signals/trend_spans",
    {
      sec_type_code: LABEL_CODE.sec_type[st],
      code: c,
      window_days: days,
      trend_decode: sqlFrag(labelDecodeSql("trend", "trend")),
    },
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
