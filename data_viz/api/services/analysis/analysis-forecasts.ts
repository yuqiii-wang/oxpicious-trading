/**
 * Forecast buckets (analysis_forecasts schema) — serves the Recent
 * Movements page's second plot (migrated off the MA-Spread panel): a
 * config→result table beneath the trend chart.
 *
 *  getForecastTable(secType, code, kind, date?)
 *    kind = "mov_rsi" → analysis_forecasts.mov_rsi ⋈ forecast_results
 *      one row per (stat_date, rsi_window, side, pct) bucket: bucket keys
 *      + regime_state + the linked forecast_results columns (mean +
 *      std-dev forward changes at the next-day/5d/20d horizons;
 *      close-based max/min forward changes at the 5d/20d horizons;
 *      per-horizon >1% reversal probabilities).
 *    kind = "mov_std" → analysis_forecasts.mov_std ⋈ forecast_results
 *      one row per (stat_date, ma_window, k, side, regime_state)
 *      Bollinger-breach bucket.
 *    kind = "mov_pairs" → analysis_forecasts.mov_pairs ⋈ forecast_results
 *      one row per (stat_date, fast_leg, pair_window, side,
 *      regime_state) MA-pair cross (golden / death cross) bucket —
 *      the EXISTING analysis.mov_ave_spreads_detail ma5_vs_ma{pair_window}
 *      (fast_leg "ma5") or price_vs_ma{pair_window} (fast_leg "price",
 *      the close price) relative-MA spread changing sign (top = cross
 *      up, bottom = cross down).
 *    kind = "mov_pairs_ema" → analysis_forecasts.mov_pairs_ema ⋈
 *      forecast_results — the EMA sibling of mov_pairs: one row per
 *      (stat_date, fast_leg, pair_window, side, regime_state)
 *      cross bucket on the EXISTING
 *      analysis.mov_ave_spreads_detail_ema ema6_vs_ema{pair_window}
 *      (fast_leg "ema6") or price_vs_ema{pair_window} (fast_leg
 *      "price", the close price) relative-EMA spread.
 *    kind = "margin_ratio" → analysis_forecasts.margin_ratio_state ⋈
 *      forecast_results — one row per (stat_date, ratio_state,
 *      regime_state) margin-buy intensity (融资买入额/成交额 ratio)
 *      z-score state cell (NO cooldown; etf + stock only), additionally
 *      carrying the cell's mean_ratio / mean_z state magnitudes from the
 *      linked forecast_results.config JSONB.
 *    kind = "high_low_streaks" → analysis_forecasts.high_low_streaks ⋈
 *      forecast_results — one row per (stat_date, band_period,
 *      pct_type, side, regime_state) MA-Spread High/Low streak
 *      bucket: every band-break excursion streak of
 *      analysis.mov_ave_high_low_pct_streaks audited at its MEAN-MID
 *      anchor day (the ((day_count-1)//2 + 1)-th trading day of the
 *      span; an 8-day streak anchors its 4th day — EX-POST anchor, so
 *      signal months lag by a resolve window; NO cooldown — one
 *      trigger per streak), additionally carrying the bucket's
 *      streak-length context (mean / min / max day_count) from the
 *      linked forecast_results.config JSONB.
 *    kind = "pe" → analysis_forecasts.pe_state ⋈
 *      forecast_results — one row per (stat_date, side, pct,
 *      regime_state) PE extreme-percentile bucket over the raw pe
 *      series of analysis.pe (the mov_rsi pct convention). PE is LOWER
 *      the better: the top-pct% (expensive) days are bearish (side
 *      top), the bottom-pct% (cheap) days bullish (side bottom).
 *    kind = "dividend" → analysis_forecasts.dividend_state ⋈
 *      forecast_results — one row per (stat_date, side, pct,
 *      regime_state) dividend-yield extreme-percentile bucket over
 *      the trailing-12m D/P series of analysis.dividends. The yield is
 *      HIGHER the better (the REVERSE of the pe mapping): the top-pct%
 *      (cheap, well-supported) days are bullish (side bottom), the
 *      bottom-pct% days bearish (side top).
 *
 *  forecast_results is now NORMALIZED (1 row per forecast_id × period) —
 *  one forecast bucket has 3 period rows (next/5d/20d) plus the blended
 *  'mixed' row. This service
 *  uses GROUP BY + conditional aggregation to pivot back to the wide
 *  format the UI consumes (1 row per bucket, period-suffixed columns).
 *
 *  The motivation tables carry code alone as their partition key
 *  (2026-09 shape: PK (code, forecast_id) on HASH (code) partitions) —
 *  every query here resolves the bucket's (sec_type, stat_date,
 *  bucket family) through analysis_forecasts.forecast_identities (the
 *  identity registry, written 1:1 with each motivation row) and joins
 *  the motivation table by forecast_id; the m.code = $2 predicate
 *  prunes the motivation side to the code's single partition.
 *
 *  The underlying indicator values are NOT stored in the mov tables —
 *  rsi_{W}days lives in analysis.mov_ave_rsi and ma/std in
 *  analysis.mov_ave_spreads_detail + stats.*_tech_stats; this endpoint
 *  only surfaces the bucket config + motivation + result columns.
 *
 *  When `date` ("YYYY-MM-DD") is given it is a START date — all rows with
 *  stat_date >= date are returned; when omitted, ALL stat_dates of the
 *  code are returned (the ANNUAL grid since 2026-09-22: one snapshot per
 *  completed year-end plus the running year). The response also carries
 *  `stat_dates` — every distinct stat_date available for the code (DESC)
 *  — so the UI can render the snapshot tick-filter.
 *
 *  Each row also carries `signal_action` + `signal_confidence` — the
 *  bucket's registered signal strategy's OWN action ("buy"/"sell") and
 *  its confidence (the chosen entry rung's sign-aligned dir_ave — the
 *  expected favorable blended move): non-null when
 *  the bucket's own (code × stat_date × config × side × regime split)
 *  emission exists in analysis_signals.signal_strategies — i.e. the
 *  bucket's MIXED forecast_results row (the FIXED-weight blend of the
 *  three horizon rows: 5d 0.65 / next 0.25 / 20d 0.10)
 *  passed the gates the analysis_signals layer applies
 *  (sign-aligned blended mean reversal > 0.75%, then the final
 *  SignalQuality gate — see the inSignals() helper). The strategies layer
 *  emits exactly those buckets — each regime split registering on its
 *  own gate pass — so the action means "this bucket IS a signal strategy
 *  over its forecast period", matched to the row's own regime split
 *  (a ● row carries the action iff its split's strategy exists). NULL
 *  when the bucket never registered.
 *
 *  getForecastTriggerDates(forecastId) — one bucket's 4 period rows'
 *  trigger_dates DATE[] (the calendar dates behind occurrence_count;
 *  array length == the row's occurrence count, NULL for pre-rebuild
 *  rows), so the UI can mark on the code's trend chart exactly which
 *  dates a clicked forecast row was computed over. Served separately
 *  (tiny per-bucket payload) instead of bloating the table response.
 *
 *  getForecastIdentity(forecastId) — resolves a forecast_id against
 *  analysis_forecasts.forecast_identities, the identity registry (one
 *  row per bucket: sec_type / code / stat_date / bucket family — the
 *  ONLY table storing the identity since the motivation tables went
 *  forecast_id-keyed). Returns null when the id is unknown; `kind`
 *  maps the motivation table to the ForecastTable family that renders
 *  it (null for retired families that have no table here).
 */
import { queryRows, formatDate, toNum } from "../../lib/db.js";
import { codeVariants } from "../../lib/classify-etf.js";
import type { QueryResultRow } from "pg";
import type {
  DelayRungHorizon,
  ForecastIdentityResponse,
  ForecastKind,
  ForecastLadderEntry,
  ForecastLaddersResponse,
  ForecastResponse,
  ForecastTriggerDatesResponse,
  HighLowStreaksForecastRow,
  MarginRatioForecastRow,
  MovPairsEmaForecastRow,
  MovPairsForecastRow,
  MovRsiForecastRow,
  MovStdForecastRow,
  PeForecastRow,
  DividendForecastRow,
  MarketRegime,
  OhlcvPopulation,
} from "../../../shared/types.js";

const VALID_KINDS: ReadonlySet<string> = new Set([
  "mov_rsi", "mov_std", "mov_pairs", "mov_pairs_ema",
  "margin_ratio", "high_low_streaks", "pe", "dividend",
]);

// ---- Pivot fragments: 3 periods × consolidated cols → period-suffixed col names ----
// forecast_results is normalized (forecast_id, period) → these fragments
// pivot it back to the wide format the UI consumes. NULLs for period='next'
// on max/min are handled naturally by CASE WHEN. ave_close (the mean
// PERIOD-END close) pivots with max/min — the 5d/20d horizons only (the
// UI shows it in those horizon groups; the mixed row never carries it).

const PERIODS: ReadonlyArray<{ period: string; suffix: string; hasMM: boolean }> = [
  { period: "next", suffix: "next",   hasMM: false },
  { period: "5d",   suffix: "5d",     hasMM: true  },
  { period: "20d",  suffix: "20d",    hasMM: true  },
];

// Build the conditional-aggregation pivot fragment dynamically so the
// column name scheme stays consistent with the old wide table.
function buildPivotCols(): string {
  const parts: string[] = [];
  for (const { period, suffix, hasMM } of PERIODS) {
    // ave_change → ave_next_change / ave_next_5d_change / ...
    const aveAlias = suffix === "next" ? "ave_next_change" : `ave_next_${suffix}_change`;
    parts.push(`MAX(CASE WHEN f.period = '${period}' THEN f.ave_change END)::float8 AS ${aveAlias}`);
    // std_change → std_next_change / std_next_5d_change / ...
    const stdAlias = suffix === "next" ? "std_next_change" : `std_next_${suffix}_change`;
    parts.push(`MAX(CASE WHEN f.period = '${period}' THEN f.std_change END)::float8 AS ${stdAlias}`);
    // occurrence_count → occurrence_count_next / occurrence_count_5d / ...
    const occAlias = suffix === "next" ? "occurrence_count_next" : `occurrence_count_${suffix}`;
    parts.push(`MAX(CASE WHEN f.period = '${period}' THEN f.occurrence_count END)::float8 AS ${occAlias}`);
    if (hasMM) {
      parts.push(`MAX(CASE WHEN f.period = '${period}' THEN f.max_change END)::float8 AS max_${suffix}_change`);
      parts.push(`MAX(CASE WHEN f.period = '${period}' THEN f.min_change END)::float8 AS min_${suffix}_change`);
      parts.push(`MAX(CASE WHEN f.period = '${period}' THEN f.ave_close END)::float8 AS ave_close_${suffix}`);
    }
  }
  return parts.join(",\n  ");
}

const PIVOT_COLS = buildPivotCols();

/**
 * The bucket's registered-strategy fragments — scalar subqueries into
 * the STRATEGIES layer (analysis_signals.signal_strategies), emitted as
 * TWO columns: ``signal_action`` — the strategy's OWN action
 * ("buy"/"sell") — and ``signal_confidence`` — the chosen entry rung's
 * sign-aligned dir_ave (the expected favorable blended move). Both
 * NULL when this exact bucket (its config, side and
 * stat_date) was NOT emitted as a signal strategy, i.e. its MIXED
 * forecast_results row (the FIXED-weight blend of the three horizon
 * rows: 5d 0.65 / next 0.25 / 20d 0.10) did not clear the gates the
 * analysis_signals layer applies (the plain gate: sign-aligned blended
 * mean reversal > 0.75%, then the final SignalQuality gate). The join
 * keys on the strategies layer's
 * STORED side column (no CASE derivation) and on end_date = stat_date
 * (one snapshot owns each forecast period). Each query joins the
 * forecast row's OWN regime split (s.regime_state = m.regime_state):
 * each split registers on its own gate pass, so a ● row carries the
 * action iff its split's strategy exists. Families without engines
 * (pe / dividend / …) never register — their columns stay NULL.
 *
 * COHORT-AWARE (the 2026-09-28 separate-cohort registration): each
 * identity joins ITS OWN population cohort's strategy row — the plain
 * parent the bare sub_type, a derived child (ohlcv_boost /
 * ohlcv_selection) its @boost / @selection suffixed sub_type
 * (PK-distinct). Children gate, register and rank independently with
 * their own confidence, so their forecast rows wear their OWN chip
 * values (02_is_active keeps the cohorts out of the live tier only —
 * the analysis table is exactly where the cohort diagnostics belong).
 */
function inSignals(
  signalType: string,
  subTypeSql: string,
  emittedOnly = "",
): string {
  const where = [
    emittedOnly,
    "s.sec_type = i.sec_type",
    "s.code = i.code",
    `s.signal_type = '${signalType}'`,
    // the cohort suffix — NULL (parent) → bare sub_type, a derived
    // child its @boost / @selection (mirrors mov_rsi's SUB_TYPE_SUFFIX)
    `s.signal_sub_type = ${subTypeSql} || COALESCE(CASE i.ohlcv_type WHEN 'ohlcv_boost' THEN '@boost' WHEN 'ohlcv_selection' THEN '@selection' END, '')`,
    "s.side = m.side",
    "s.regime_state = m.regime_state",
    "s.end_date = i.stat_date",
  ].filter(Boolean).join("\n      AND ");
  return [
    `(SELECT s.action\n     FROM analysis_signals.signal_strategies s\n     WHERE ${where}) AS signal_action`,
    `(SELECT s.confidence::float8\n     FROM analysis_signals.signal_strategies s\n     WHERE ${where}) AS signal_confidence`,
  ].join(",\n");
}

interface DbPairsRow extends QueryResultRow {
  forecast_id: number | string;
  stat_date: Date | string;
  delayed_signal_days: number | null;
  fast_leg: string;
  pair_window: number;
  side: string;
  regime_state: string;
  regime_weight: number | null;
  signal_action: string | null;
  signal_confidence: number | null;
  [k: string]: unknown;
}

interface DbRsiRow extends QueryResultRow {
  forecast_id: number | string;
  stat_date: Date | string;
  delayed_signal_days: number | null;
  ohlcv_type: string | null;
  rsi_window: number;
  side: string;
  pct: number;
  regime_state: string;
  regime_weight: number | null;
  signal_action: string | null;
  signal_confidence: number | null;
  [k: string]: unknown;
}

interface DbStdRow extends QueryResultRow {
  forecast_id: number | string;
  stat_date: Date | string;
  delayed_signal_days: number | null;
  ma_window: number;
  k: number;
  side: string;
  regime_state: string;
  regime_weight: number | null;
  signal_action: string | null;
  signal_confidence: number | null;
  [k: string]: unknown;
}

interface DbMarginRatioRow extends QueryResultRow {
  forecast_id: number | string;
  stat_date: Date | string;
  delayed_signal_days: number | null;
  ratio_state: string;
  side: string;
  regime_state: string;
  regime_weight: number | null;
  signal_action: string | null;
  signal_confidence: number | null;
  mean_ratio: number | null;
  mean_z: number | null;
  [k: string]: unknown;
}

interface DbHighLowStreaksRow extends QueryResultRow {
  forecast_id: number | string;
  stat_date: Date | string;
  delayed_signal_days: number | null;
  band_period: number;
  pct_type: number;
  side: string;
  regime_state: string;
  regime_weight: number | null;
  signal_action: string | null;
  signal_confidence: number | null;
  mean_day_count: number | null;
  min_day_count: number | null;
  max_day_count: number | null;
  [k: string]: unknown;
}

interface DbValPctRow extends QueryResultRow {
  forecast_id: number | string;
  stat_date: Date | string;
  delayed_signal_days: number | null;
  side: string;
  pct: number;
  regime_state: string;
  regime_weight: number | null;
  signal_action: string | null;
  signal_confidence: number | null;
  [k: string]: unknown;
}

/** margin_ratio state magnitudes live in the linked forecast_results.config
 *  JSONB (duplicated across all 3 period rows per forecast_id) — the
 *  MIN(text) trick casts to text (PG can MIN text, not jsonb), MINs,
 *  and casts back to jsonb for ->> access. */
const CONFIG_MARGIN_RATIO_COLS = `
  NULLIF(MIN(f.config::text)::jsonb->>'mean_ratio', '')::float8 AS mean_ratio,
  NULLIF(MIN(f.config::text)::jsonb->>'mean_z', '')::float8 AS mean_z
`;

/** high_low_streaks streak-length context lives in the linked
 *  forecast_results.config JSONB (duplicated across all 4 period rows
 *  per forecast_id) — same MIN(text) trick as CONFIG_MARGIN_RATIO_COLS. */
const CONFIG_HIGH_LOW_STREAKS_COLS = `
  NULLIF(MIN(f.config::text)::jsonb->>'mean_day_count', '')::float8 AS mean_day_count,
  NULLIF(MIN(f.config::text)::jsonb->>'min_day_count', '')::float8 AS min_day_count,
  NULLIF(MIN(f.config::text)::jsonb->>'max_day_count', '')::float8 AS max_day_count
`;

// ---- Column-to-field mapping (wide format — matches ForecastResultCols) ----
// The API response shape is unchanged from the old wide table: one row
// per bucket, all periods as period-suffixed columns.

function mapRsiRow(r: DbRsiRow): MovRsiForecastRow {
  return {
    forecast_id: Number(r.forecast_id),
    stat_date: formatDate(r.stat_date),
    delayed_signal_days: toNum(r.delayed_signal_days),
    ohlcv_type: (r.ohlcv_type ?? null) as MovRsiForecastRow["ohlcv_type"],
    rsi_window: r.rsi_window,
    side: r.side as MovRsiForecastRow["side"],
    pct: r.pct,
    regime_state: r.regime_state as MarketRegime,
    regime_weight: r.regime_weight ?? null,
    signal_action: (r.signal_action as "buy" | "sell" | null) ?? null,
    signal_confidence: toNum(r.signal_confidence),
    ave_next_change: toNum(r.ave_next_change),
    ave_next_5d_change: toNum(r.ave_next_5d_change),
    ave_next_20d_change: toNum(r.ave_next_20d_change),
    std_next_change: toNum(r.std_next_change),
    std_next_5d_change: toNum(r.std_next_5d_change),
    std_next_20d_change: toNum(r.std_next_20d_change),
    max_5d_change: toNum(r.max_5d_change),
    max_20d_change: toNum(r.max_20d_change),
    min_5d_change: toNum(r.min_5d_change),
    min_20d_change: toNum(r.min_20d_change),
    ave_close_5d: toNum(r.ave_close_5d),
    ave_close_20d: toNum(r.ave_close_20d),
    occurrence_count_next: toNum(r.occurrence_count_next),
    occurrence_count_5d: toNum(r.occurrence_count_5d),
    occurrence_count_20d: toNum(r.occurrence_count_20d),
  };
}

/** One mov_pairs bucket row → API shape (config cols + the pivoted
 *  forecast_results columns). */
function mapPairsRow(r: DbPairsRow): MovPairsForecastRow {
  return {
    forecast_id: Number(r.forecast_id),
    stat_date: formatDate(r.stat_date),
    delayed_signal_days: toNum(r.delayed_signal_days),
    fast_leg: r.fast_leg as MovPairsForecastRow["fast_leg"],
    pair_window: r.pair_window,
    side: r.side as MovPairsForecastRow["side"],
    regime_state: r.regime_state as MarketRegime,
    regime_weight: r.regime_weight ?? null,
    signal_action: (r.signal_action as "buy" | "sell" | null) ?? null,
    signal_confidence: toNum(r.signal_confidence),
    ave_next_change: toNum(r.ave_next_change),
    ave_next_5d_change: toNum(r.ave_next_5d_change),
    ave_next_20d_change: toNum(r.ave_next_20d_change),
    std_next_change: toNum(r.std_next_change),
    std_next_5d_change: toNum(r.std_next_5d_change),
    std_next_20d_change: toNum(r.std_next_20d_change),
    max_5d_change: toNum(r.max_5d_change),
    max_20d_change: toNum(r.max_20d_change),
    min_5d_change: toNum(r.min_5d_change),
    min_20d_change: toNum(r.min_20d_change),
    ave_close_5d: toNum(r.ave_close_5d),
    ave_close_20d: toNum(r.ave_close_20d),
    occurrence_count_next: toNum(r.occurrence_count_next),
    occurrence_count_5d: toNum(r.occurrence_count_5d),
    occurrence_count_20d: toNum(r.occurrence_count_20d),
  };
}

function mapStdRow(r: DbStdRow): MovStdForecastRow {
  const base: MovStdForecastRow = {
    forecast_id: Number(r.forecast_id),
    stat_date: formatDate(r.stat_date),
    delayed_signal_days: toNum(r.delayed_signal_days),
    ma_window: r.ma_window,
    k: toNum(r.k) ?? 0,
    side: r.side as MovStdForecastRow["side"],
    regime_state: r.regime_state as MarketRegime,
    regime_weight: r.regime_weight ?? null,
    signal_action: (r.signal_action as "buy" | "sell" | null) ?? null,
    signal_confidence: toNum(r.signal_confidence),
    ave_next_change: toNum(r.ave_next_change),
    ave_next_5d_change: toNum(r.ave_next_5d_change),
    ave_next_20d_change: toNum(r.ave_next_20d_change),
    std_next_change: toNum(r.std_next_change),
    std_next_5d_change: toNum(r.std_next_5d_change),
    std_next_20d_change: toNum(r.std_next_20d_change),
    max_5d_change: toNum(r.max_5d_change),
    max_20d_change: toNum(r.max_20d_change),
    min_5d_change: toNum(r.min_5d_change),
    min_20d_change: toNum(r.min_20d_change),
    ave_close_5d: toNum(r.ave_close_5d),
    ave_close_20d: toNum(r.ave_close_20d),
    occurrence_count_next: toNum(r.occurrence_count_next),
    occurrence_count_5d: toNum(r.occurrence_count_5d),
    occurrence_count_20d: toNum(r.occurrence_count_20d),
  };
  return base;
}

function mapMarginRatioRow(r: DbMarginRatioRow): MarginRatioForecastRow {
  return {
    forecast_id: Number(r.forecast_id),
    stat_date: formatDate(r.stat_date),
    delayed_signal_days: toNum(r.delayed_signal_days),
    ratio_state: r.ratio_state as MarginRatioForecastRow["ratio_state"],
    side: r.side as MarginRatioForecastRow["side"],
    regime_state: r.regime_state as MarketRegime,
    regime_weight: r.regime_weight ?? null,
    signal_action: (r.signal_action as "buy" | "sell" | null) ?? null,
    signal_confidence: toNum(r.signal_confidence),
    mean_ratio: toNum(r.mean_ratio),
    mean_z: toNum(r.mean_z),
    ave_next_change: toNum(r.ave_next_change),
    ave_next_5d_change: toNum(r.ave_next_5d_change),
    ave_next_20d_change: toNum(r.ave_next_20d_change),
    std_next_change: toNum(r.std_next_change),
    std_next_5d_change: toNum(r.std_next_5d_change),
    std_next_20d_change: toNum(r.std_next_20d_change),
    max_5d_change: toNum(r.max_5d_change),
    max_20d_change: toNum(r.max_20d_change),
    min_5d_change: toNum(r.min_5d_change),
    min_20d_change: toNum(r.min_20d_change),
    ave_close_5d: toNum(r.ave_close_5d),
    ave_close_20d: toNum(r.ave_close_20d),
    occurrence_count_next: toNum(r.occurrence_count_next),
    occurrence_count_5d: toNum(r.occurrence_count_5d),
    occurrence_count_20d: toNum(r.occurrence_count_20d),
  };
}

/** One high_low_streaks bucket row → API shape (config cols + the
 *  bucket's streak-length context + the pivoted forecast_results
 *  columns). */
function mapHighLowStreaksRow(r: DbHighLowStreaksRow): HighLowStreaksForecastRow {
  return {
    forecast_id: Number(r.forecast_id),
    stat_date: formatDate(r.stat_date),
    delayed_signal_days: toNum(r.delayed_signal_days),
    band_period: r.band_period,
    pct_type: r.pct_type,
    side: r.side as HighLowStreaksForecastRow["side"],
    regime_state: r.regime_state as MarketRegime,
    regime_weight: r.regime_weight ?? null,
    signal_action: (r.signal_action as "buy" | "sell" | null) ?? null,
    signal_confidence: toNum(r.signal_confidence),
    mean_day_count: toNum(r.mean_day_count),
    min_day_count: toNum(r.min_day_count),
    max_day_count: toNum(r.max_day_count),
    ave_next_change: toNum(r.ave_next_change),
    ave_next_5d_change: toNum(r.ave_next_5d_change),
    ave_next_20d_change: toNum(r.ave_next_20d_change),
    std_next_change: toNum(r.std_next_change),
    std_next_5d_change: toNum(r.std_next_5d_change),
    std_next_20d_change: toNum(r.std_next_20d_change),
    max_5d_change: toNum(r.max_5d_change),
    max_20d_change: toNum(r.max_20d_change),
    min_5d_change: toNum(r.min_5d_change),
    min_20d_change: toNum(r.min_20d_change),
    ave_close_5d: toNum(r.ave_close_5d),
    ave_close_20d: toNum(r.ave_close_20d),
    occurrence_count_next: toNum(r.occurrence_count_next),
    occurrence_count_5d: toNum(r.occurrence_count_5d),
    occurrence_count_20d: toNum(r.occurrence_count_20d),
  };
}

/** One pe / dividend bucket row → API shape (the pivoted
 *  forecast_results columns). The two families share the row shape —
 *  only the source table + signal family differ. */
function mapValPctRow(r: DbValPctRow): PeForecastRow | DividendForecastRow {
  return {
    forecast_id: Number(r.forecast_id),
    stat_date: formatDate(r.stat_date),
    delayed_signal_days: toNum(r.delayed_signal_days),
    side: r.side as PeForecastRow["side"],
    pct: r.pct,
    regime_state: r.regime_state as MarketRegime,
    regime_weight: r.regime_weight ?? null,
    signal_action: (r.signal_action as "buy" | "sell" | null) ?? null,
    signal_confidence: toNum(r.signal_confidence),
    ave_next_change: toNum(r.ave_next_change),
    ave_next_5d_change: toNum(r.ave_next_5d_change),
    ave_next_20d_change: toNum(r.ave_next_20d_change),
    std_next_change: toNum(r.std_next_change),
    std_next_5d_change: toNum(r.std_next_5d_change),
    std_next_20d_change: toNum(r.std_next_20d_change),
    max_5d_change: toNum(r.max_5d_change),
    max_20d_change: toNum(r.max_20d_change),
    min_5d_change: toNum(r.min_5d_change),
    min_20d_change: toNum(r.min_20d_change),
    ave_close_5d: toNum(r.ave_close_5d),
    ave_close_20d: toNum(r.ave_close_20d),
    occurrence_count_next: toNum(r.occurrence_count_next),
    occurrence_count_5d: toNum(r.occurrence_count_5d),
    occurrence_count_20d: toNum(r.occurrence_count_20d),
  };
}

export async function getForecastTable(
  secType: string | undefined,
  code: string | null,
  kind: string | undefined,
  date: string | null,
  population: OhlcvPopulation | null = null,
  snapshot: string | null = null,
): Promise<ForecastResponse> {
  // `population` optionally scopes the read to ONE derived population
  // (mov_rsi only today): 'gap_boost' the pattern-weighted child
  // (analyze.analysis_forecasts.ohlc_boost), 'gap_selection' the
  // rule-based selection child (ohlc_selection), 'vol_boost' /
  // 'vol_selection' the vol×amt amount-state children (vol_boost /
  // vol_selection) — each its own related forecast_id. The DEFAULT
  // (null) is UNSCOPED: every population's rows come back, each
  // carrying its own ohlcv_type verdict (other kinds have only the
  // parent population — their rows are all NULL). forecast_id is
  // population-exact, so the unscoped reads cannot mix populations;
  // an explicit scope still reaches EVERY results read behind the
  // table (the base rows, the delay ladders).
  //
  // `snapshot` ("latest" | "YYYY-MM-DD") optionally narrows the rows
  // to exactly ONE stat_date — the UI's FIRST load asks for the
  // snapshot its end-month filter starts on (the LATEST), so the
  // table paints over ~1/7 of the rows and the remaining snapshots
  // load on demand when the filter moves off it. The DELAY LADDERS
  // are NOT part of this response anymore — they ride the separate
  // /forecast-ladders endpoint (getForecastLadders), fetched
  // alongside and merged client-side, so the table renders before
  // the ladder payload parses (2026-09-30 progressive split).
  //
  // Validate BEFORE any read fires: the legs below run in PARALLEL
  // (the pre-2026-09-30 chain ran them sequentially — the response
  // waited on the SUM, whose dominant term was the ladder read's
  // cold 15s/42s), and only a validated (sec_type, kind) names real
  // tables for every leg.
  const st = (secType ?? "").trim().toLowerCase();
  if (!["etf", "index", "stock"].includes(st)) {
    throw new Error(`Invalid sec_type: ${secType}. Expected 'etf', 'index', or 'stock'.`);
  }
  const k = (kind ?? "").trim().toLowerCase();
  if (!VALID_KINDS.has(k)) {
    throw new Error(
      `Invalid kind: ${kind}. Expected 'mov_rsi', 'mov_std', 'mov_pairs', 'mov_pairs_ema', 'margin_ratio', 'high_low_streaks', 'pe', or 'dividend'.`,
    );
  }
  if (!code) throw new Error("Missing 'code' parameter");
  const bucket = TABLES[k];
  const variants = codeVariants(code);
  const [base, improvByKey, dataStart] = await Promise.all([
    getForecastTableBase(secType, code, kind, date, population, snapshot),
    // The IMPROV stat rides each derived child's OWN row (every
    // population separately — the 2026-09-27 shape: no parent-row
    // attachment; parent "none" rows render the dash) — mov_rsi only.
    bucket === "mov_rsi"
      ? buildRsiImprovIndex(st, variants)
      : Promise.resolve(null),
    fetchDataStart(st, variants),
  ]);
  if (improvByKey != null) {
    applyRsiImprov(base.rows as MovRsiForecastRow[], improvByKey);
  }
  return { ...base, data_start: dataStart };
}

/** The code's delay ladders alone — the OTHER half of the split
 *  forecast-table response (see getForecastTable's snapshot note).
 *  Same scope params as the base read so the two payloads cover the
 *  same rows; returns the forecast_id-keyed ladder entries as an
 *  array the UI merges into its base rows when they land. */
export async function getForecastLadders(
  secType: string | undefined,
  code: string | null,
  kind: string | undefined,
  date: string | null,
  population: OhlcvPopulation | null = null,
  snapshot: string | null = null,
): Promise<ForecastLaddersResponse> {
  const st = (secType ?? "").trim().toLowerCase();
  if (!["etf", "index", "stock"].includes(st)) {
    throw new Error(`Invalid sec_type: ${secType}. Expected 'etf', 'index', or 'stock'.`);
  }
  const k = (kind ?? "").trim().toLowerCase();
  if (!VALID_KINDS.has(k)) {
    throw new Error(
      `Invalid kind: ${kind}. Expected 'mov_rsi', 'mov_std', 'mov_pairs', 'mov_pairs_ema', 'margin_ratio', 'high_low_streaks', 'pe', or 'dividend'.`,
    );
  }
  if (!code) throw new Error("Missing 'code' parameter");
  const index = await buildLadderIndex(
    st, TABLES[k], codeVariants(code), date, population, snapshot,
  );
  const ladders: ForecastLadderEntry[] = [];
  for (const [forecast_id, entry] of index) {
    ladders.push({
      forecast_id,
      rungs: entry.rungs,
      decay_stop: entry.decay_stop,
      best_delay: entry.best_delay,
    });
  }
  return { kind: k as ForecastKind, code, sec_type: st, ladders };
}

/** Attach each derived-child row's IMPROV — how much its population
 *  improves the bucket's expected reversal over the parent's, per
 *  horizon (improv_ave_{next,5d,20d} / improv_n_{next,5d,20d}):
 *
 *    improv = sign-align(child's delay-0 mean) − sign-align(parent's
 *             delay-0 mean)      (top → −ave, bottom → +ave — the
 *                                  dir_ave convention, so POSITIVE
 *                                  always reads "the derived
 *                                  population is better than the
 *                                  plain bucket")
 *
 *  Applied to EVERY derived child's OWN rows separately (each
 *  population its own forecast_id under the same bucket identity) —
 *  the parent ("none") rows carry null and the UI renders the muted
 *  dash (there is nothing for the plain population to improve on).
 *  One query reads the delay-0 horizon rows of ALL populations for
 *  the code's bucket identities (the response's rows may be
 *  population-scoped, so the parent's means cannot be read off the
 *  response alone); improv_n carries the CHILD's trigger-day count.
 *  Split into buildRsiImprovIndex (the query — runs in parallel with
 *  the response's other reads) + applyRsiImprov (the per-row merge). */
async function buildRsiImprovIndex(
  secType: string,
  variants: string[],
): Promise<Map<string, RsiImprovEntry>> {
  const stats = await queryRows<{
    stat_date: Date | string;
    rsi_window: number;
    side: string;
    pct: number;
    regime_state: string;
    ohlcv_type: string | null;
    period: string;
    ave: number | null;
    n: number | null;
  }>(
    `SELECT i.stat_date, m.rsi_window, m.side, m.pct, m.regime_state,
            i.ohlcv_type,
            f.period,
            f.ave_change::float8 AS ave,
            f.occurrence_count AS n
     FROM analysis_forecasts.forecast_identities i
     JOIN analysis_forecasts.mov_rsi m
       ON m.forecast_id = i.forecast_id
     JOIN analysis_forecasts.forecast_results f
       ON f.forecast_id = i.forecast_id AND f.delay = 0
     WHERE i.sec_type = $1 AND i.code = ANY($2::text[])
       AND i.bucket = 'mov_rsi'
       -- the m.code twin of the i.code predicate: without it the planner
       -- cannot prune mov_rsi (HASH (code) partitions, PK (code,
       -- forecast_id)) and parallel-seq-scans ALL 16 partitions; with it
       -- the read touches the code's single partition (the base query's
       -- shape). m.code = i.code holds for every registered bucket.
       AND m.code = ANY($2::text[])`,
    [secType, variants],
  );
  type PeriodStat = { ave: number | null; n: number | null };
  type PopStat = Record<"next" | "5d" | "20d", PeriodStat>;
  const blank = (): PopStat => ({
    next: { ave: null, n: null },
    "5d": { ave: null, n: null },
    "20d": { ave: null, n: null },
  });
  // bucket identity → per-population delay-0 stats ("none" = the
  // parent + the two children — each child relates to its identity's
  // parent: the selection a strict subset, the boost the same days)
  type PopEntry = Record<"none" | OhlcvPopulation, PopStat>;
  const blankEntry = (): PopEntry => ({
    none: blank(), ohlcv_selection: blank(), ohlcv_boost: blank(),
  });
  const byKey = new Map<string, PopEntry>();
  for (const s of stats) {
    if (s.period !== "next" && s.period !== "5d" && s.period !== "20d") continue;
    const key = [formatDate(s.stat_date), s.rsi_window, s.side, s.pct,
                 s.regime_state].join("|");
    let e = byKey.get(key);
    if (!e) {
      e = blankEntry();
      byKey.set(key, e);
    }
    const pop = (s.ohlcv_type === "ohlcv_selection"
                 || s.ohlcv_type === "ohlcv_boost")
      ? e[s.ohlcv_type] : e.none;
    pop[s.period as "next" | "5d" | "20d"] = { ave: toNum(s.ave), n: toNum(s.n) };
  }
  return byKey;
}

/** One bucket identity's per-population delay-0 stats — the improv
 *  input (see buildRsiImprovIndex). */
type RsiImprovEntry = Record<
  "none" | OhlcvPopulation,
  Record<"next" | "5d" | "20d", { ave: number | null; n: number | null }>
>;

function applyRsiImprov(
  rows: MovRsiForecastRow[],
  byKey: Map<string, RsiImprovEntry>,
): void {
  const align = (side: string, v: number | null): number | null =>
    v == null ? null : side === "top" ? -v : v;
  const isChild = (t: string | null): t is OhlcvPopulation =>
    t === "ohlcv_selection" || t === "ohlcv_boost";
  for (const r of rows) {
    if (!isChild(r.ohlcv_type)) {
      // the plain parent population — nothing to improve on: dash
      r.improv_ave_next = null;
      r.improv_ave_5d = null;
      r.improv_ave_20d = null;
      r.improv_n_next = null;
      r.improv_n_5d = null;
      r.improv_n_20d = null;
      continue;
    }
    const e = byKey.get([r.stat_date, r.rsi_window, r.side, r.pct,
                         r.regime_state].join("|"));
    const child = e?.[r.ohlcv_type];
    const parent = e?.none;
    for (const [h, fld] of [
      ["next", "improv_ave_next"], ["5d", "improv_ave_5d"],
      ["20d", "improv_ave_20d"],
    ] as const) {
      const c = align(r.side, child?.[h].ave ?? null);
      const p = align(r.side, parent?.[h].ave ?? null);
      r[fld] = c != null && p != null ? c - p : null;
    }
    r.improv_n_next = child?.next.n ?? null;
    r.improv_n_5d = child?.["5d"].n ?? null;
    r.improv_n_20d = child?.["20d"].n ?? null;
  }
}

/**
 * The code's earliest ACTUAL trading day in the pipeline's own bars
 * source (stats.{sec_type}_basic_stats — the same table and the same
 * close-not-null exclusion the forecast fetch uses), so the UI can
 * label the stats window by the data that actually exists instead of
 * the nominal 10y lookback (a 10y window over a series starting 2020
 * reads "2020 → 2026", not "2016 → 2026"). Null when the code has no
 * bars. `sec_type` is already allowlist-validated by the caller.
 */
async function fetchDataStart(
  secType: string,
  variants: string[],
): Promise<string | null> {
  const rows = await queryRows<{ min_date: Date | string | null }>(
    `SELECT MIN(date) AS min_date
     FROM stats.${secType}_basic_stats
     WHERE code = ANY($1::text[])
       AND close IS NOT NULL`,
    [variants],
  );
  const v = rows[0]?.min_date;
  return v == null ? null : formatDate(v);
}

/**
 * Build each bucket's delay ladder — the bucket's delay 0..5
 * forecast_results rows (the 2026-09-21 incremental anchors; the row's
 * own pivoted columns are the delay-0 stats), returned as a
 * forecast_id-keyed index so getForecastTable attaches them after its
 * parallel reads settle. Each rung carries the delay's FULL per-horizon
 * forecast (ave / std / max / min / ave_close / occurrence per period —
 * the row-click expansion renders it) plus the blended numbers the
 * ladder cell reads. All means are SIGN-ALIGNED (top → −ave, bottom →
 * +; the gate's dir_ave convention; std and ave_close stay raw — a
 * dispersion and a price level do not align), and `decay_stop` is the
 * highest delay whose aligned blended mean is still strictly below the
 * previous rung's and positive — past it the reversal edge has decayed
 * and the ladder "stops".
 *
 * `best_delay` is the rung with the HIGHEST aligned blended mean (the
 * delay's weighted return) — set only when that max is > 0 (a favorable
 * edge); the UI highlights exactly that rung and its expansion row.
 */
/** The identities-registry population predicate, shared by every
 *  results read behind the table: an explicit `population` request
 *  scopes the read to exactly that derived population; the DEFAULT
 *  (null) is UNSCOPED — mov_rsi returns all five populations (parent
 *  + the four children, each its own forecast_id since the 2026-09-26
 *  identity migration; other kinds have only the parent),
 *  distinguished per row by its ohlcv_type verdict. forecast_id is
 *  population-exact, so the unscoped reads cannot mix populations.
 *  (The value is validated to the OhlcvPopulation union at the route
 *  before it reaches this string interpolation.) */
function populationPredicateSql(population: OhlcvPopulation | null): string {
  if (!population) return "";
  return `
       AND i.ohlcv_type = '${population}'`;
}

/** kind → forecast_identities.bucket (the motivation-table name;
 *  margin_ratio / pe / dividend motivation tables are the *_state
 *  tables). The values double as the identities `bucket`
 *  discriminator: the motivation tables carry code alone as their
 *  partition key (2026-09 (code, forecast_id) shape), so the queries
 *  below resolve (sec_type, code, bucket, stat_date) through
 *  forecast_identities and join the motivation table by forecast_id,
 *  with the m.code = ANY($2) predicate pruning it to the code's
 *  partition. Shared by getForecastTableBase's queries and the
 *  ladders read. */
const TABLES: Record<string, string> = {
  mov_rsi: "mov_rsi",
  mov_std: "mov_std",
  mov_pairs: "mov_pairs",
  mov_pairs_ema: "mov_pairs_ema",
  margin_ratio: "margin_ratio_state",
  high_low_streaks: "high_low_streaks",
  pe: "pe_state",
  dividend: "dividend_state",
};

/** One fully-computed ladder per bucket — what every response row of
 *  that forecast_id wears. */
interface LadderEntry {
  rungs: NonNullable<ForecastResponse["rows"][number]["delay_ladder"]>;
  decay_stop: number | null;
  best_delay: number | null;
}

/** THE LADDER READ IS CHUNKED (2026-09-30): a single
 *  `forecast_id = ANY(<all the code's ids>)` read of the ~18.7M-row,
 *  16-way HASH-partitioned forecast_results lets the planner assume
 *  EVERY id may live in EVERY partition (per-partition n_distinct
 *  statistics cannot see the hash distribution), inflating each
 *  partition's row estimate ~15x past the bitmap→seq-scan crossover —
 *  past ~2,000 ids partitions flip to parallel SEQ SCANS of the whole
 *  table (measured 15s on the primary / 42s on the replica, cold — the
 *  Recent Movements slow-load). The fix reads the code's ids + sides
 *  first (one partition-pruned identities⋈motivation query, ~10ms),
 *  then pulls the results rows in ≤800-id chunks — every chunk plans
 *  as PK bitmap index scans (~0.15s total for the heaviest code's
 *  3,069 ids) — firing the chunks in PARALLEL (the pg pool's max
 *  naturally bounds concurrency). The id list is inlined into the SQL
 *  as a bigint[] ARRAY literal, NOT a $n param: node-pg's extended
 *  protocol hides a parameter's value from the planner, whose
 *  value-blind ScalarArrayOp estimate then cannot use the PK prefix at
 *  all. The ids are BIGINTs read from our own identities registry one
 *  query earlier — integers only, no injection surface (the same
 *  rationale as the bucket-name interpolation). */
const LADDER_ID_CHUNK = 800;

/** One raw ladder read row off forecast_results (the chunk query's
 *  shape). */
interface LadderDbRow {
  forecast_id: number | string;
  delay: number;
  period: string;
  ave_change: number | null;
  std_change: number | null;
  max_change: number | null;
  min_change: number | null;
  ave_close: number | null;
  occurrence_count: string | number | null;
}

/** The code's bucket ids + their sides (the sign-alignment input) — the
 *  identities⋈motivation drive query, pruned to the code's single
 *  partition by the m.code twin of the i.code predicate (the same shape
 *  every other read behind this service uses; m.code = i.code holds for
 *  every registered bucket). The optional date/population/snapshot scope
 *  keeps the id set exactly the response rows'. */
async function collectLadderIdSides(
  secType: string,
  bucket: string,
  variants: string[],
  date: string | null,
  population: OhlcvPopulation | null,
  snapshot: string | null = null,
): Promise<Array<{ forecast_id: number; side: string }>> {
  // Same snapshot scoping as the base rows read (getForecastTableBase)
  // so the two payloads cover the same rows.
  const snapshotIsDate = snapshot != null && snapshot !== "latest";
  const dateFilter = date
    ? "AND i.stat_date >= $3::date"
    : snapshotIsDate
      ? "AND i.stat_date = $3::date"
      : snapshot === "latest"
        ? `AND i.stat_date = (SELECT MAX(stat_date) FROM analysis_forecasts.forecast_identities WHERE sec_type = $1 AND code = ANY($2::text[]) AND bucket = '${bucket}')`
        : "";
  const params = (date || snapshotIsDate)
    ? [secType, variants, date ?? snapshot]
    : [secType, variants];
  const rows = await queryRows<{ forecast_id: number | string; side: string }>(
    `SELECT i.forecast_id, m.side
     FROM analysis_forecasts.forecast_identities i
     JOIN analysis_forecasts.${bucket} m
       ON m.forecast_id = i.forecast_id
     WHERE i.sec_type = $1 AND i.code = ANY($2::text[])
       AND i.bucket = '${bucket}'
       AND m.code = ANY($2::text[])
       ${dateFilter}${populationPredicateSql(population)}`,
    params,
  );
  return rows.map((r) => ({ forecast_id: Number(r.forecast_id), side: r.side }));
}

/** The forecast_id-keyed ladder index for the code's buckets (empty map
 *  when the code has none). */
async function buildLadderIndex(
  secType: string,
  bucket: string,
  variants: string[],
  date: string | null,
  population: OhlcvPopulation | null,
  snapshot: string | null = null,
): Promise<Map<number, LadderEntry>> {
  const idSides = await collectLadderIdSides(secType, bucket, variants, date, population, snapshot);
  const index = new Map<number, LadderEntry>();
  if (idSides.length === 0) return index;
  const sideById = new Map<number, string>();
  const ids: number[] = [];
  for (const { forecast_id, side } of idSides) {
    if (!Number.isInteger(forecast_id)) continue;
    sideById.set(forecast_id, side);
    ids.push(forecast_id);
  }
  // Fire every chunk at once — the pool's max (10) is the concurrency
  // cap; extra chunk queries simply queue.
  const chunks: number[][] = [];
  for (let i = 0; i < ids.length; i += LADDER_ID_CHUNK) {
    chunks.push(ids.slice(i, i + LADDER_ID_CHUNK));
  }
  const ladderRows = (
    await Promise.all(
      chunks.map((chunk) =>
        queryRows<LadderDbRow>(
          `SELECT r.forecast_id, r.delay, r.period,
                  r.ave_change::float8 AS ave_change,
                  r.std_change::float8 AS std_change,
                  r.max_change::float8 AS max_change,
                  r.min_change::float8 AS min_change,
                  r.ave_close::float8 AS ave_close,
                  r.occurrence_count
           FROM analysis_forecasts.forecast_results r
           WHERE r.forecast_id = ANY(ARRAY[${chunk.join(",")}]::bigint[])`,
        ),
      ),
    )
  ).flat();
  /** Per-delay accumulators — one record per (forecast_id × period)
   * row, keyed by the delay axis (period strings as stored). */
  interface RungAcc {
    ave: Record<string, number | null>;
    std: Record<string, number | null>;
    mx: Record<string, number | null>;
    mn: Record<string, number | null>;
    close: Record<string, number | null>;
    n: Record<string, number | null>;
  }
  const byId = new Map<number, Map<number, RungAcc>>();
  for (const r of ladderRows) {
    const fid = Number(r.forecast_id);
    let perDelay = byId.get(fid);
    if (!perDelay) {
      perDelay = new Map();
      byId.set(fid, perDelay);
    }
    let rung = perDelay.get(r.delay);
    if (!rung) {
      rung = { ave: {}, std: {}, mx: {}, mn: {}, close: {}, n: {} };
      perDelay.set(r.delay, rung);
    }
    rung.ave[r.period] = r.ave_change;
    rung.std[r.period] = r.std_change;
    rung.mx[r.period] = r.max_change;
    rung.mn[r.period] = r.min_change;
    rung.close[r.period] = r.ave_close;
    rung.n[r.period] = r.occurrence_count == null ? null : Number(r.occurrence_count);
  }
  const align = (side: string, v: number | null): number | null =>
    v == null ? null : side === "top" ? -v : v;
  for (const [fid, perDelay] of byId) {
    const side = sideById.get(fid) ?? "bottom";
    const horizonOf = (rung: RungAcc, period: string): DelayRungHorizon => ({
      ave: align(side, rung.ave[period] ?? null),
      std: rung.std[period] ?? null,
      n: rung.n[period] ?? null,
      max: align(side, rung.mx[period] ?? null),
      min: align(side, rung.mn[period] ?? null),
      close: rung.close[period] ?? null,
    });
    const rungs = [...perDelay.entries()]
      .sort(([a], [b]) => a - b)
      .map(([delay, rung]) => ({
        delay,
        n: rung.n["mixed"] ?? 0,
        dir_ave: align(side, rung.ave["mixed"] ?? null),
        dir_ave_next: align(side, rung.ave["next"] ?? null),
        dir_ave_5d: align(side, rung.ave["5d"] ?? null),
        dir_ave_20d: align(side, rung.ave["20d"] ?? null),
        horizons: {
          next: horizonOf(rung, "next"),
          "5d": horizonOf(rung, "5d"),
          "20d": horizonOf(rung, "20d"),
        },
      }));
    let stop: number | null = null;
    if (rungs.length && (rungs[0].dir_ave ?? 0) > 0) {
      stop = rungs[0].delay;
      for (let i = 1; i < rungs.length; i++) {
        const cur = rungs[i].dir_ave;
        const prev = rungs[i - 1].dir_ave;
        if (cur == null || prev == null || cur <= 0 || cur >= prev) break;
        stop = rungs[i].delay;
      }
    }
    // Best rung = the highest sign-aligned blended mean (the delay's
    // weighted return); only a POSITIVE max marks (no favorable edge —
    // no highlight, even when every rung is negative).
    let best: number | null = null;
    let bestV = 0;
    for (const g of rungs) {
      if (g.dir_ave != null && g.dir_ave > bestV) {
        bestV = g.dir_ave;
        best = g.delay;
      }
    }
    index.set(fid, { rungs, decay_stop: stop, best_delay: best });
  }
  return index;
}

async function getForecastTableBase(
  secType: string | undefined,
  code: string | null,
  kind: string | undefined,
  m: string | null,
  population: OhlcvPopulation | null = null,
  snapshot: string | null = null,
): Promise<Omit<ForecastResponse, "data_start">> {
  // (data_start attaches in getForecastTable — one shared lookup.)
  if (!code) throw new Error("Missing 'code' parameter");
  const st = (secType ?? "").trim().toLowerCase();
  if (!["etf", "index", "stock"].includes(st)) {
    throw new Error(`Invalid sec_type: ${secType}. Expected 'etf', 'index', or 'stock'.`);
  }
  const k = (kind ?? "").trim().toLowerCase();
  if (!VALID_KINDS.has(k)) {
    throw new Error(
      `Invalid kind: ${kind}. Expected 'mov_rsi', 'mov_std', 'mov_pairs', 'mov_pairs_ema', 'margin_ratio', 'high_low_streaks', 'pe', or 'dividend'.`,
    );
  }


  // Code-format tolerance: the caller may pass a bare ("000001") or
  // suffixed ("000001.SZ") code, while forecast_identities / motivation
  // tables store index codes BARE and etf/stock codes SUFFIXED. Matching
  // `code = ANY(variants)` (same convention as every other service) keeps
  // the (code, …) PK-prefix index scans usable for both input forms — a
  // bare equality against a suffixed stock code returns 0 rows and the
  // Recent Movements table renders its "no analysis_forecasts rows" empty
  // state even though the buckets exist.
  const variants = codeVariants(code);

  // Snapshot scoping: `m` (a START date, stat_date >= m) and `snapshot`
  // ("latest" | an exact stat_date) are mutually exclusive (validated
  // at the route). "latest" resolves through a scalar MAX() InitPlan —
  // one registry probe, no extra round trip — so the first load reads
  // exactly the snapshot the UI's end-month filter starts on (~1/7 of
  // the rows on the annual grid); the stat_dates list below stays FULL
  // (the filter menu lists every snapshot either way).
  const snapshotIsDate = snapshot != null && snapshot !== "latest";
  const dateFilter = m
    ? "AND i.stat_date >= $3::date"
    : snapshotIsDate
      ? "AND i.stat_date = $3::date"
      : snapshot === "latest"
        ? `AND i.stat_date = (SELECT MAX(stat_date) FROM analysis_forecasts.forecast_identities WHERE sec_type = $1 AND code = ANY($2::text[]) AND bucket = '${TABLES[k]}')`
        : "";
  const rowParams = (m || snapshotIsDate)
    ? [st, variants, m ?? snapshot]
    : [st, variants];

  // The stat_dates registry read and the main rows read are independent —
  // one round trip's time, not two (getForecastTable runs the other reads
  // of the response alongside them in parallel).
  const dateRowsPromise = queryRows<{ stat_date: Date | string }>(
    `SELECT DISTINCT stat_date
     FROM analysis_forecasts.forecast_identities
     WHERE sec_type = $1 AND code = ANY($2::text[]) AND bucket = $3
     ORDER BY stat_date DESC`,
    [st, variants, TABLES[k]],
  );
  const statDatesPromise = dateRowsPromise.then((rs) =>
    rs.map((r) => formatDate(r.stat_date)),
  );

  if (k === "mov_rsi") {
    const rows = await queryRows<DbRsiRow>(
      `
      SELECT m.forecast_id,
             i.stat_date,
             i.delayed_signal_days,
             i.ohlcv_type,
             m.rsi_window,
             m.side,
             m.pct,
             m.regime_state,
             w.weight::float8 AS regime_weight,
             ${inSignals("mov_rsi", "'rsi' || m.rsi_window || '_' || m.pct::text || 'pct'", "(m.pct IN (1, 2, 3))")},
             ${PIVOT_COLS}
      FROM analysis_forecasts.forecast_identities i
      JOIN analysis_forecasts.mov_rsi m
        ON m.forecast_id = i.forecast_id
      JOIN analysis_forecasts.forecast_results f
        ON f.forecast_id = m.forecast_id AND f.delay = 0
        -- the derived-population verdict lives on the identities
        -- registry (the 2026-09-26 identity migration; the 2026-09-27
        -- ohlcv_type consolidation); the default is UNSCOPED (parent +
        -- the four children together, each its own forecast_id) — an
        -- explicit population request scopes the read to one
        -- population${populationPredicateSql(population)}
      LEFT JOIN analysis_forecasts.regime_weights w
        ON w.stat_date = i.stat_date AND w.sec_type = i.sec_type
       AND w.code = i.code AND w.family = 'mov_rsi'
       AND w.regime = m.regime_state
      WHERE m.code = ANY($2::text[]) AND i.sec_type = $1 AND i.code = ANY($2::text[]) AND i.bucket = 'mov_rsi'
        ${dateFilter}
      GROUP BY i.code, i.sec_type, m.forecast_id, i.stat_date, i.delayed_signal_days, i.ohlcv_type, m.rsi_window, m.side, m.pct,
               m.regime_state,
w.weight
      ORDER BY i.stat_date DESC, m.rsi_window ASC, m.side ASC, m.pct ASC,
               m.regime_state ASC, i.ohlcv_type ASC NULLS FIRST
      `,
      rowParams,
    );
    const mapped = rows.map(mapRsiRow);
    return { kind: "mov_rsi", code, sec_type: st, stat_dates: await statDatesPromise, rows: mapped, enable_filters: true };
  }

  if (k === "mov_pairs") {
    const rows = await queryRows<DbPairsRow>(
      `
      SELECT m.forecast_id,
             i.stat_date,
             i.delayed_signal_days,
             m.fast_leg,
             m.pair_window,
             m.side,
             m.regime_state,
             w.weight::float8 AS regime_weight,
             ${inSignals("mov_pairs", "(CASE WHEN m.fast_leg = 'price' THEN 'pxmapair' ELSE 'mapair' END) || m.pair_window")},
             ${PIVOT_COLS}
      FROM analysis_forecasts.forecast_identities i
      JOIN analysis_forecasts.mov_pairs m
        ON m.forecast_id = i.forecast_id
      JOIN analysis_forecasts.forecast_results f
        ON f.forecast_id = m.forecast_id AND f.delay = 0
      -- the derived-population markers live on the identities registry
      -- (the 2026-09-26 identity migration) — parent population only
       AND i.ohlcv_type IS NULL
      LEFT JOIN analysis_forecasts.regime_weights w
        ON w.stat_date = i.stat_date AND w.sec_type = i.sec_type
       AND w.code = i.code AND w.family = 'mov_pairs'
       AND w.regime = m.regime_state
      WHERE m.code = ANY($2::text[]) AND i.sec_type = $1 AND i.code = ANY($2::text[]) AND i.bucket = 'mov_pairs'
        ${dateFilter}
      GROUP BY i.code, i.sec_type, m.forecast_id, i.stat_date, i.delayed_signal_days, i.ohlcv_type, m.fast_leg, m.pair_window, m.side,
               m.regime_state,
w.weight
      ORDER BY i.stat_date DESC, m.fast_leg ASC, m.pair_window ASC, m.side ASC,
               m.regime_state ASC
      `,
      rowParams,
    );
    const mapped = rows.map(mapPairsRow);
    return { kind: "mov_pairs", code, sec_type: st, stat_dates: await statDatesPromise, rows: mapped, enable_filters: true };
  }

  if (k === "mov_pairs_ema") {
    // Identical row shape as the MA family (mapPairsRow is structurally
    // compatible) — only the source table + signal_type differ.
    const rows = await queryRows<DbPairsRow>(
      `
      SELECT m.forecast_id,
             i.stat_date,
             i.delayed_signal_days,
             m.fast_leg,
             m.pair_window,
             m.side,
             m.regime_state,
             w.weight::float8 AS regime_weight,
             ${inSignals("mov_pairs_ema", "(CASE WHEN m.fast_leg = 'price' THEN 'pxemapair' ELSE 'emapair' END) || m.pair_window")},
             ${PIVOT_COLS}
      FROM analysis_forecasts.forecast_identities i
      JOIN analysis_forecasts.mov_pairs_ema m
        ON m.forecast_id = i.forecast_id
      JOIN analysis_forecasts.forecast_results f
        ON f.forecast_id = m.forecast_id AND f.delay = 0
      -- the derived-population markers live on the identities registry
      -- (the 2026-09-26 identity migration) — parent population only
       AND i.ohlcv_type IS NULL
      LEFT JOIN analysis_forecasts.regime_weights w
        ON w.stat_date = i.stat_date AND w.sec_type = i.sec_type
       AND w.code = i.code AND w.family = 'mov_pairs_ema'
       AND w.regime = m.regime_state
      WHERE m.code = ANY($2::text[]) AND i.sec_type = $1 AND i.code = ANY($2::text[]) AND i.bucket = 'mov_pairs_ema'
        ${dateFilter}
      GROUP BY i.code, i.sec_type, m.forecast_id, i.stat_date, i.delayed_signal_days, i.ohlcv_type, m.fast_leg, m.pair_window, m.side,
               m.regime_state,
w.weight
      ORDER BY i.stat_date DESC, m.fast_leg ASC, m.pair_window ASC, m.side ASC,
               m.regime_state ASC
      `,
      rowParams,
    );
    const mapped = rows.map(mapPairsRow) as MovPairsEmaForecastRow[];
    return { kind: "mov_pairs_ema", code, sec_type: st, stat_dates: await statDatesPromise, rows: mapped, enable_filters: true };
  }


  if (k === "margin_ratio") {
    const rows = await queryRows<DbMarginRatioRow>(
      `
      SELECT m.forecast_id,
             i.stat_date,
             i.delayed_signal_days,
             m.ratio_state,
             m.side,
             m.regime_state,
             w.weight::float8 AS regime_weight,
             ${inSignals("margin_ratio", "'ratio_' || m.ratio_state")},
             ${CONFIG_MARGIN_RATIO_COLS},
             ${PIVOT_COLS}
      FROM analysis_forecasts.forecast_identities i
      JOIN analysis_forecasts.margin_ratio_state m
        ON m.forecast_id = i.forecast_id
      JOIN analysis_forecasts.forecast_results f
        ON f.forecast_id = m.forecast_id AND f.delay = 0
      -- the derived-population markers live on the identities registry
      -- (the 2026-09-26 identity migration) — parent population only
       AND i.ohlcv_type IS NULL
      LEFT JOIN analysis_forecasts.regime_weights w
        ON w.stat_date = i.stat_date AND w.sec_type = i.sec_type
       AND w.code = i.code AND w.family = 'margin_ratio_state'
       AND w.regime = m.regime_state
      WHERE m.code = ANY($2::text[]) AND i.sec_type = $1 AND i.code = ANY($2::text[]) AND i.bucket = 'margin_ratio_state'
        ${dateFilter}
      GROUP BY i.code, i.sec_type, m.forecast_id, i.stat_date, i.delayed_signal_days, i.ohlcv_type, m.ratio_state, m.side,
               m.regime_state,
w.weight
      ORDER BY i.stat_date DESC,
               CASE m.ratio_state WHEN 'vlow' THEN 1 WHEN 'low' THEN 2
                                  WHEN 'mid' THEN 3 WHEN 'high' THEN 4
                                  WHEN 'vhigh' THEN 5 ELSE 6 END ASC,
               m.regime_state ASC
      `,
      rowParams,
    );
    const mapped = rows.map(mapMarginRatioRow);
    return { kind: "margin_ratio", code, sec_type: st, stat_dates: await statDatesPromise, rows: mapped, enable_filters: true };
  }

  if (k === "pe") {
    // Side ordering puts the bearish top rows first (the reversed
    // side semantics are carried per row).
    const rows = await queryRows<DbValPctRow>(
      `
      SELECT m.forecast_id,
             i.stat_date,
             i.delayed_signal_days,
             m.side,
             m.pct,
             m.regime_state,
             w.weight::float8 AS regime_weight,
             NULL::text AS signal_action,
             NULL::float8 AS signal_confidence,
             ${PIVOT_COLS}
      FROM analysis_forecasts.forecast_identities i
      JOIN analysis_forecasts.pe_state m
        ON m.forecast_id = i.forecast_id
      JOIN analysis_forecasts.forecast_results f
        ON f.forecast_id = m.forecast_id AND f.delay = 0
      -- the derived-population markers live on the identities registry
      -- (the 2026-09-26 identity migration) — parent population only
       AND i.ohlcv_type IS NULL
      LEFT JOIN analysis_forecasts.regime_weights w
        ON w.stat_date = i.stat_date AND w.sec_type = i.sec_type
       AND w.code = i.code AND w.family = 'pe_state'
       AND w.regime = m.regime_state
      WHERE m.code = ANY($2::text[]) AND i.sec_type = $1 AND i.code = ANY($2::text[]) AND i.bucket = 'pe_state'
        ${dateFilter}
      GROUP BY i.code, i.sec_type, m.forecast_id, i.stat_date, i.delayed_signal_days, i.ohlcv_type, m.side, m.pct,
               m.regime_state,
w.weight
      ORDER BY i.stat_date DESC, m.side ASC, m.pct ASC,
               m.regime_state ASC
      `,
      rowParams,
    );
    const mapped = rows.map(mapValPctRow) as PeForecastRow[];
    return { kind: "pe", code, sec_type: st, stat_dates: await statDatesPromise, rows: mapped, enable_filters: true };
  }

  if (k === "dividend") {
    const rows = await queryRows<DbValPctRow>(
      `
      SELECT m.forecast_id,
             i.stat_date,
             i.delayed_signal_days,
             m.side,
             m.pct,
             m.regime_state,
             w.weight::float8 AS regime_weight,
             NULL::text AS signal_action,
             NULL::float8 AS signal_confidence,
             ${PIVOT_COLS}
      FROM analysis_forecasts.forecast_identities i
      JOIN analysis_forecasts.dividend_state m
        ON m.forecast_id = i.forecast_id
      JOIN analysis_forecasts.forecast_results f
        ON f.forecast_id = m.forecast_id AND f.delay = 0
      -- the derived-population markers live on the identities registry
      -- (the 2026-09-26 identity migration) — parent population only
       AND i.ohlcv_type IS NULL
      LEFT JOIN analysis_forecasts.regime_weights w
        ON w.stat_date = i.stat_date AND w.sec_type = i.sec_type
       AND w.code = i.code AND w.family = 'dividend_state'
       AND w.regime = m.regime_state
      WHERE m.code = ANY($2::text[]) AND i.sec_type = $1 AND i.code = ANY($2::text[]) AND i.bucket = 'dividend_state'
        ${dateFilter}
      GROUP BY i.code, i.sec_type, m.forecast_id, i.stat_date, i.delayed_signal_days, i.ohlcv_type, m.side, m.pct,
               m.regime_state,
w.weight
      ORDER BY i.stat_date DESC, m.side ASC, m.pct ASC,
               m.regime_state ASC
      `,
      rowParams,
    );
    const mapped = rows.map(mapValPctRow) as DividendForecastRow[];
    return { kind: "dividend", code, sec_type: st, stat_dates: await statDatesPromise, rows: mapped, enable_filters: true };
  }

  if (k === "high_low_streaks") {
    const rows = await queryRows<DbHighLowStreaksRow>(
      `
      SELECT m.forecast_id,
             i.stat_date,
             i.delayed_signal_days,
             m.band_period,
             m.pct_type,
             m.side,
             m.regime_state,
             w.weight::float8 AS regime_weight,
             ${inSignals("high_low_streaks", "'p' || m.band_period || '_' || m.pct_type")},
             ${CONFIG_HIGH_LOW_STREAKS_COLS},
             ${PIVOT_COLS}
      FROM analysis_forecasts.forecast_identities i
      JOIN analysis_forecasts.high_low_streaks m
        ON m.forecast_id = i.forecast_id
      JOIN analysis_forecasts.forecast_results f
        ON f.forecast_id = m.forecast_id AND f.delay = 0
      -- the derived-population markers live on the identities registry
      -- (the 2026-09-26 identity migration) — parent population only
       AND i.ohlcv_type IS NULL
      LEFT JOIN analysis_forecasts.regime_weights w
        ON w.stat_date = i.stat_date AND w.sec_type = i.sec_type
       AND w.code = i.code AND w.family = 'high_low_streaks'
       AND w.regime = m.regime_state
      WHERE m.code = ANY($2::text[]) AND i.sec_type = $1 AND i.code = ANY($2::text[]) AND i.bucket = 'high_low_streaks'
        ${dateFilter}
      GROUP BY i.code, i.sec_type, m.forecast_id, i.stat_date, i.delayed_signal_days, i.ohlcv_type, m.band_period, m.pct_type,
               m.side, m.regime_state,
w.weight
      ORDER BY i.stat_date DESC, m.band_period ASC, m.pct_type ASC,
               m.side ASC, m.regime_state ASC
      `,
      rowParams,
    );
    const mapped = rows.map(mapHighLowStreaksRow);
    return { kind: "high_low_streaks", code, sec_type: st, stat_dates: await statDatesPromise, rows: mapped, enable_filters: true };
  }

  // kind === "mov_std"
  const rows = await queryRows<DbStdRow>(
    `
    SELECT m.forecast_id,
           i.stat_date,
           i.delayed_signal_days,
           m.ma_window,
           m.k::float8 AS k,
           m.side,
           m.regime_state,
           w.weight::float8 AS regime_weight,
           ${inSignals("mov_std",
                 "'std' || m.ma_window || '_' || (m.k::float8::text) || 'std'",
                 "(m.ma_window >= 20 AND m.k::float8 >= 2.0)")},
           ${PIVOT_COLS}
    FROM analysis_forecasts.forecast_identities i
    JOIN analysis_forecasts.mov_std m
      ON m.forecast_id = i.forecast_id
    JOIN analysis_forecasts.forecast_results f
      ON f.forecast_id = m.forecast_id AND f.delay = 0
      -- the derived-population markers live on the identities registry
      AND i.ohlcv_type IS NULL
    LEFT JOIN analysis_forecasts.regime_weights w
      ON w.stat_date = i.stat_date AND w.sec_type = i.sec_type
     AND w.code = i.code AND w.family = 'mov_std'
     AND w.regime = m.regime_state
    WHERE m.code = ANY($2::text[]) AND i.sec_type = $1 AND i.code = ANY($2::text[]) AND i.bucket = 'mov_std'
      ${dateFilter}
    GROUP BY i.code, i.sec_type, m.forecast_id, i.stat_date, i.delayed_signal_days, i.ohlcv_type, m.ma_window, m.k, m.side,
             m.regime_state,
             w.weight
    ORDER BY i.stat_date DESC, m.ma_window ASC, m.k ASC, m.side ASC,
             m.regime_state ASC
    `,
    rowParams,
  );
  const mapped = rows.map(mapStdRow);
  return { kind: "mov_std", code, sec_type: st, stat_dates: await statDatesPromise, rows: mapped, enable_filters: true };
}

/** One bucket's per-period trigger dates — the forecast_results rows'
 *  trigger_dates DATE[] (the calendar dates behind each period's
 *  occurrence_count: under the 2026-09 streak-merge these are the
 *  merged signals' MID days) plus the parallel STREAK SPANS — each
 *  signal's qualifying-run [start, end] from the streak_starts /
 *  streak_ends DATE[] columns, with the run's trading-day count
 *  (streak_days BIGINT[], parallel to streak_starts) — so the UI can
 *  shade the streak period dark purple, pinpoint the mid dates and
 *  report the streak length. NULL dates (pre-rebuild rows / occurrence
 *  count 0 / state families) map to null per period. `delay` selects
 *  the anchor-delay rung (default 0 — the row's own pivoted columns;
 *  the table's inline expansion rows pass their rung's delay so the
 *  chart marks THAT rung's trigger days). */
export async function getForecastTriggerDates(
  forecastId: number,
  delay = 0,
  population: OhlcvPopulation | null = null,
): Promise<ForecastTriggerDatesResponse> {
  const rows = await queryRows<{
    period: string;
    trigger_dates: Array<Date | string> | null;
    streak_starts: Array<Date | string> | null;
    streak_ends: Array<Date | string> | null;
    streak_days: number[] | null;
  }>(
    `SELECT f.period, f.trigger_dates, f.streak_starts, f.streak_ends,
            f.streak_days
     FROM analysis_forecasts.forecast_results f
     JOIN analysis_forecasts.forecast_identities i
       ON i.forecast_id = f.forecast_id
     WHERE f.forecast_id = $1 AND f.delay = $2
       -- the derived-population markers live on the identities
       -- registry (the 2026-09-26 identity migration); the flags are
       -- passed from the clicked row's OWN population markers
       -- (unscoped when both false — forecast_id is population-exact)${populationPredicateSql(population)}`,
    [forecastId, delay],
  );
  const periods: ForecastTriggerDatesResponse["periods"] = {
    next: null,
    "5d": null,
    "20d": null,
  };
  const streaks: ForecastTriggerDatesResponse["streaks"] = {
    next: null,
    "5d": null,
    "20d": null,
  };
  for (const r of rows) {
    if (r.period in periods) {
      const key = r.period as keyof typeof periods;
      periods[key] = (r.trigger_dates ?? []).map((d) => formatDate(d));
      if (r.streak_starts != null && r.streak_ends != null) {
        streaks[key] = r.streak_starts.map((start, i) => ({
          start: formatDate(start),
          end: formatDate(r.streak_ends?.[i] ?? start),
          days: r.streak_days?.[i] ?? null,
        }));
      }
    }
  }
  return { forecast_id: forecastId, periods, streaks };
}

/** Motivation table name (identity.bucket) → the ForecastTable family
 *  that renders it. The *_state tables map to the margin_ratio / pe /
 *  dividend kinds. */
const BUCKET_KIND: Record<string, ForecastKind> = {
  mov_rsi: "mov_rsi",
  mov_std: "mov_std",
  mov_pairs: "mov_pairs",
  mov_pairs_ema: "mov_pairs_ema",
  margin_ratio_state: "margin_ratio",
  high_low_streaks: "high_low_streaks",
  pe_state: "pe",
  dividend_state: "dividend",
};

/** One forecast_id → its shared-PK registry row
 *  (analysis_forecasts.forecast_identities): sec_type / code /
 *  stat_date / bucket family + the bucket's mean streak length per
 *  merged signal (streak_signal_days) and mean trigger delay
 *  (delayed_signal_days — the trading days from a signal's first
 *  qualifying day to its mid-anchored actual trigger, capped at 5).
 *  Null when the id is not registered. Powers the UI's
 *  search-by-forecast_id jump: no probing of the eight motivation
 *  tables. */
export async function getForecastIdentity(
  forecastId: number,
): Promise<ForecastIdentityResponse | null> {
  const rows = await queryRows<{
    forecast_id: number | string;
    sec_type: string;
    code: string;
    stat_date: Date | string;
    bucket: string;
    streak_signal_days: number | string | null;
    delayed_signal_days: number | string | null;
    lookback_period: string;
  }>(
    `SELECT forecast_id, sec_type, code, stat_date, bucket,
            streak_signal_days, delayed_signal_days, lookback_period
     FROM analysis_forecasts.forecast_identities
     WHERE forecast_id = $1`,
    [forecastId],
  );
  if (rows.length === 0) return null;
  const r = rows[0];
  return {
    forecast_id: Number(r.forecast_id),
    sec_type: r.sec_type,
    code: r.code,
    stat_date: formatDate(r.stat_date),
    bucket: r.bucket as ForecastIdentityResponse["bucket"],
    kind: BUCKET_KIND[r.bucket] ?? null,
    streak_signal_days: toNum(r.streak_signal_days),
    delayed_signal_days: toNum(r.delayed_signal_days),
    lookback_period: r.lookback_period,
  };
}
