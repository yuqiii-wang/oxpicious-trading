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
    `s.signal_sub_type = ${subTypeSql}`,
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
): Promise<ForecastResponse> {
  const base = await getForecastTableBase(secType, code, kind, date);
  await attachDelayLadders(base.rows);
  return {
    ...base,
    data_start: await fetchDataStart(base.sec_type, codeVariants(code)),
  };
}

/**
 * The code's earliest ACTUAL trading day in the pipeline's own bars
 * source (stats.{sec_type}_basic_stats — the same table and the same
 * estimated-close exclusion the forecast fetch uses), so the UI can
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
       AND close IS NOT NULL
       AND COALESCE(is_close_estimated, FALSE) = FALSE`,
    [variants],
  );
  const v = rows[0]?.min_date;
  return v == null ? null : formatDate(v);
}

/**
 * Attach each row's delay ladder — the bucket's delay 0..5
 * forecast_results rows (the 2026-09-21 incremental anchors; the row's
 * own pivoted columns are the delay-0 stats), one generic keyed query
 * over the rows' forecast_ids. Each rung carries the delay's FULL
 * per-horizon forecast (ave / std / max / min / ave_close / occurrence
 * per period — the row-click expansion renders it) plus
 * the blended numbers the ladder cell reads. All means are
 * SIGN-ALIGNED (top → −ave, bottom → +; the gate's dir_ave convention;
 * std and ave_close stay raw — a dispersion and a price level do not
 * align), and `decay_stop` is the highest delay whose aligned blended
 * mean is still strictly below the previous rung's and positive — past
 * it the reversal edge has decayed and the ladder "stops".
 *
 * `best_delay` is the rung with the HIGHEST aligned blended mean (the
 * delay's weighted return) — set only when that max is > 0 (a
 * favorable edge); the UI highlights exactly that rung and its
 * expansion row.
 */
async function attachDelayLadders(rows: ForecastResponse["rows"]): Promise<void> {
  if (!rows.length) return;
  // forecast_id normalization: pg returns int8 as STRING, the row mappers
  // expose it as NUMBER — key both sides by Number() (ids here are far
  // below Number.MAX_SAFE_INTEGER).
  const ids = [...new Set(rows.map((r) => Number(r.forecast_id)))];
  const ladders = await queryRows<{
    forecast_id: number;
    delay: number;
    period: string;
    ave_change: number | null;
    std_change: number | null;
    max_change: number | null;
    min_change: number | null;
    ave_close: number | null;
    occurrence_count: string | number | null;
  }>(
    `SELECT forecast_id, delay, period,
            ave_change::float8 AS ave_change,
            std_change::float8 AS std_change,
            max_change::float8 AS max_change,
            min_change::float8 AS min_change,
            ave_close::float8 AS ave_close,
            occurrence_count
     FROM analysis_forecasts.forecast_results
     WHERE forecast_id = ANY($1::bigint[])`,
    [ids],
  );
  /** Per-delay accumulators — one record per (forecast_id × period)
   *  row, keyed by the delay axis (period strings as stored). */
  interface RungAcc {
    ave: Record<string, number | null>;
    std: Record<string, number | null>;
    mx: Record<string, number | null>;
    mn: Record<string, number | null>;
    close: Record<string, number | null>;
    n: Record<string, number | null>;
  }
  const byId = new Map<number, Map<number, RungAcc>>();
  for (const r of ladders) {
    let perDelay = byId.get(Number(r.forecast_id));
    if (!perDelay) {
      perDelay = new Map();
      byId.set(Number(r.forecast_id), perDelay);
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
  for (const row of rows) {
    const perDelay = byId.get(Number(row.forecast_id));
    if (!perDelay?.size) {
      row.delay_ladder = null;
      row.decay_stop = null;
      row.best_delay = null;
      continue;
    }
    const side = row.side;
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
    row.delay_ladder = rungs;
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
    row.decay_stop = stop;
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
    row.best_delay = best;
  }
}

async function getForecastTableBase(
  secType: string | undefined,
  code: string | null,
  kind: string | undefined,
  m: string | null,
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

  // Bucket table per kind (margin_ratio / pe / dividend
  // motivation tables are the *_state tables). The values double as the
  // forecast_identities `bucket` discriminator: the motivation tables
  // carry code alone as their partition key (2026-09 (code, forecast_id)
  // shape), so every query below resolves (sec_type, code, bucket,
  // stat_date) through forecast_identities and joins the motivation
  // table by forecast_id, with the m.code = ANY($2) predicate pruning it
  // to the code's partition.
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

  const dateRows = await queryRows<{ stat_date: Date | string }>(
    `SELECT DISTINCT stat_date
     FROM analysis_forecasts.forecast_identities
     WHERE sec_type = $1 AND code = ANY($2::text[]) AND bucket = $3
     ORDER BY stat_date DESC`,
    [st, variants, TABLES[k]],
  );
  const statDates = dateRows.map((r) => formatDate(r.stat_date));

  if (k === "mov_rsi") {
    const rows = await queryRows<DbRsiRow>(
      `
      SELECT m.forecast_id,
             i.stat_date,
             i.delayed_signal_days,
             m.rsi_window,
             m.side,
             m.pct,
             m.regime_state,
             w.weight::float8 AS regime_weight,
             ${inSignals("mov_rsi", "'rsi' || m.rsi_window || '_' || m.pct::text || 'pct'", "(m.pct = 1)")},
             ${PIVOT_COLS}
      FROM analysis_forecasts.forecast_identities i
      JOIN analysis_forecasts.mov_rsi m
        ON m.forecast_id = i.forecast_id
      JOIN analysis_forecasts.forecast_results f
        ON f.forecast_id = m.forecast_id AND f.delay = 0
      LEFT JOIN analysis_forecasts.regime_weights w
        ON w.stat_date = i.stat_date AND w.sec_type = i.sec_type
       AND w.code = i.code AND w.family = 'mov_rsi'
       AND w.regime = m.regime_state
      WHERE m.code = ANY($2::text[]) AND i.sec_type = $1 AND i.code = ANY($2::text[]) AND i.bucket = 'mov_rsi'
        ${m ? "AND i.stat_date >= $3::date" : ""}
      GROUP BY i.code, i.sec_type, m.forecast_id, i.stat_date, i.delayed_signal_days, m.rsi_window, m.side, m.pct,
               m.regime_state,
w.weight
      ORDER BY i.stat_date DESC, m.rsi_window ASC, m.side ASC, m.pct ASC,
               m.regime_state ASC
      `,
      m ? [st, variants, m] : [st, variants],
    );
    const mapped = rows.map(mapRsiRow);
    return { kind: "mov_rsi", code, sec_type: st, stat_dates: statDates, rows: mapped, enable_filters: true };
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
             ${inSignals("mov_pairs", "(CASE WHEN m.fast_leg = 'price' THEN 'pxpair' ELSE 'pair' END) || m.pair_window")},
             ${PIVOT_COLS}
      FROM analysis_forecasts.forecast_identities i
      JOIN analysis_forecasts.mov_pairs m
        ON m.forecast_id = i.forecast_id
      JOIN analysis_forecasts.forecast_results f
        ON f.forecast_id = m.forecast_id AND f.delay = 0
      LEFT JOIN analysis_forecasts.regime_weights w
        ON w.stat_date = i.stat_date AND w.sec_type = i.sec_type
       AND w.code = i.code AND w.family = 'mov_pairs'
       AND w.regime = m.regime_state
      WHERE m.code = ANY($2::text[]) AND i.sec_type = $1 AND i.code = ANY($2::text[]) AND i.bucket = 'mov_pairs'
        ${m ? "AND i.stat_date >= $3::date" : ""}
      GROUP BY i.code, i.sec_type, m.forecast_id, i.stat_date, i.delayed_signal_days, m.fast_leg, m.pair_window, m.side,
               m.regime_state,
w.weight
      ORDER BY i.stat_date DESC, m.fast_leg ASC, m.pair_window ASC, m.side ASC,
               m.regime_state ASC
      `,
      m ? [st, variants, m] : [st, variants],
    );
    const mapped = rows.map(mapPairsRow);
    return { kind: "mov_pairs", code, sec_type: st, stat_dates: statDates, rows: mapped, enable_filters: true };
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
      LEFT JOIN analysis_forecasts.regime_weights w
        ON w.stat_date = i.stat_date AND w.sec_type = i.sec_type
       AND w.code = i.code AND w.family = 'mov_pairs_ema'
       AND w.regime = m.regime_state
      WHERE m.code = ANY($2::text[]) AND i.sec_type = $1 AND i.code = ANY($2::text[]) AND i.bucket = 'mov_pairs_ema'
        ${m ? "AND i.stat_date >= $3::date" : ""}
      GROUP BY i.code, i.sec_type, m.forecast_id, i.stat_date, i.delayed_signal_days, m.fast_leg, m.pair_window, m.side,
               m.regime_state,
w.weight
      ORDER BY i.stat_date DESC, m.fast_leg ASC, m.pair_window ASC, m.side ASC,
               m.regime_state ASC
      `,
      m ? [st, variants, m] : [st, variants],
    );
    const mapped = rows.map(mapPairsRow) as MovPairsEmaForecastRow[];
    return { kind: "mov_pairs_ema", code, sec_type: st, stat_dates: statDates, rows: mapped, enable_filters: true };
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
      LEFT JOIN analysis_forecasts.regime_weights w
        ON w.stat_date = i.stat_date AND w.sec_type = i.sec_type
       AND w.code = i.code AND w.family = 'margin_ratio_state'
       AND w.regime = m.regime_state
      WHERE m.code = ANY($2::text[]) AND i.sec_type = $1 AND i.code = ANY($2::text[]) AND i.bucket = 'margin_ratio_state'
        ${m ? "AND i.stat_date >= $3::date" : ""}
      GROUP BY i.code, i.sec_type, m.forecast_id, i.stat_date, i.delayed_signal_days, m.ratio_state, m.side,
               m.regime_state,
w.weight
      ORDER BY i.stat_date DESC,
               CASE m.ratio_state WHEN 'vlow' THEN 1 WHEN 'low' THEN 2
                                  WHEN 'mid' THEN 3 WHEN 'high' THEN 4
                                  WHEN 'vhigh' THEN 5 ELSE 6 END ASC,
               m.regime_state ASC
      `,
      m ? [st, variants, m] : [st, variants],
    );
    const mapped = rows.map(mapMarginRatioRow);
    return { kind: "margin_ratio", code, sec_type: st, stat_dates: statDates, rows: mapped, enable_filters: true };
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
      LEFT JOIN analysis_forecasts.regime_weights w
        ON w.stat_date = i.stat_date AND w.sec_type = i.sec_type
       AND w.code = i.code AND w.family = 'pe_state'
       AND w.regime = m.regime_state
      WHERE m.code = ANY($2::text[]) AND i.sec_type = $1 AND i.code = ANY($2::text[]) AND i.bucket = 'pe_state'
        ${m ? "AND i.stat_date >= $3::date" : ""}
      GROUP BY i.code, i.sec_type, m.forecast_id, i.stat_date, i.delayed_signal_days, m.side, m.pct,
               m.regime_state,
w.weight
      ORDER BY i.stat_date DESC, m.side ASC, m.pct ASC,
               m.regime_state ASC
      `,
      m ? [st, variants, m] : [st, variants],
    );
    const mapped = rows.map(mapValPctRow) as PeForecastRow[];
    return { kind: "pe", code, sec_type: st, stat_dates: statDates, rows: mapped, enable_filters: true };
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
      LEFT JOIN analysis_forecasts.regime_weights w
        ON w.stat_date = i.stat_date AND w.sec_type = i.sec_type
       AND w.code = i.code AND w.family = 'dividend_state'
       AND w.regime = m.regime_state
      WHERE m.code = ANY($2::text[]) AND i.sec_type = $1 AND i.code = ANY($2::text[]) AND i.bucket = 'dividend_state'
        ${m ? "AND i.stat_date >= $3::date" : ""}
      GROUP BY i.code, i.sec_type, m.forecast_id, i.stat_date, i.delayed_signal_days, m.side, m.pct,
               m.regime_state,
w.weight
      ORDER BY i.stat_date DESC, m.side ASC, m.pct ASC,
               m.regime_state ASC
      `,
      m ? [st, variants, m] : [st, variants],
    );
    const mapped = rows.map(mapValPctRow) as DividendForecastRow[];
    return { kind: "dividend", code, sec_type: st, stat_dates: statDates, rows: mapped, enable_filters: true };
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
      LEFT JOIN analysis_forecasts.regime_weights w
        ON w.stat_date = i.stat_date AND w.sec_type = i.sec_type
       AND w.code = i.code AND w.family = 'high_low_streaks'
       AND w.regime = m.regime_state
      WHERE m.code = ANY($2::text[]) AND i.sec_type = $1 AND i.code = ANY($2::text[]) AND i.bucket = 'high_low_streaks'
        ${m ? "AND i.stat_date >= $3::date" : ""}
      GROUP BY i.code, i.sec_type, m.forecast_id, i.stat_date, i.delayed_signal_days, m.band_period, m.pct_type,
               m.side, m.regime_state,
w.weight
      ORDER BY i.stat_date DESC, m.band_period ASC, m.pct_type ASC,
               m.side ASC, m.regime_state ASC
      `,
      m ? [st, variants, m] : [st, variants],
    );
    const mapped = rows.map(mapHighLowStreaksRow);
    return { kind: "high_low_streaks", code, sec_type: st, stat_dates: statDates, rows: mapped, enable_filters: true };
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
    LEFT JOIN analysis_forecasts.regime_weights w
      ON w.stat_date = i.stat_date AND w.sec_type = i.sec_type
     AND w.code = i.code AND w.family = 'mov_std'
     AND w.regime = m.regime_state
    WHERE m.code = ANY($2::text[]) AND i.sec_type = $1 AND i.code = ANY($2::text[]) AND i.bucket = 'mov_std'
      ${m ? "AND i.stat_date >= $3::date" : ""}
    GROUP BY i.code, i.sec_type, m.forecast_id, i.stat_date, i.delayed_signal_days, m.ma_window, m.k, m.side,
             m.regime_state,
             w.weight
    ORDER BY i.stat_date DESC, m.ma_window ASC, m.k ASC, m.side ASC,
             m.regime_state ASC
    `,
    m ? [st, variants, m] : [st, variants],
  );
  const mapped = rows.map(mapStdRow);
  return { kind: "mov_std", code, sec_type: st, stat_dates: statDates, rows: mapped, enable_filters: true };
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
): Promise<ForecastTriggerDatesResponse> {
  const rows = await queryRows<{
    period: string;
    trigger_dates: Array<Date | string> | null;
    streak_starts: Array<Date | string> | null;
    streak_ends: Array<Date | string> | null;
    streak_days: number[] | null;
  }>(
    `SELECT period, trigger_dates, streak_starts, streak_ends, streak_days
     FROM analysis_forecasts.forecast_results
     WHERE forecast_id = $1 AND delay = $2`,
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
