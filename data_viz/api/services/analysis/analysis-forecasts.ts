/**
 * Forecast buckets (analysis_forecasts schema) — serves the Recent
 * Movements page's second plot (migrated off the MA-Spread panel): a
 * config→result table beneath the trend chart.
 *
 *  getForecastTable(secType, code, kind, month?)
 *    kind = "mov_rsi" → analysis_forecasts.mov_rsi ⋈ forecast_results
 *      one row per (stat_month, rsi_window, side, pct) bucket: bucket keys
 *      + is_market_hyped + the linked forecast_results columns (mean +
 *      std-dev forward changes at the next-day/5d/20d/60d horizons;
 *      close-based max/min forward changes + the best-to-worst n-day
 *      outcome ratio max_low_change_ratio at the 5d/20d/60d horizons;
 *      per-horizon >1% reversal probabilities).
 *    kind = "mov_std" → analysis_forecasts.mov_std ⋈ forecast_results
 *      one row per (stat_month, ma_window, k, side, is_market_hyped)
 *      Bollinger-breach bucket.
 *    kind = "mov_gap" → analysis_forecasts.mov_gap ⋈ forecast_results
 *      one row per (stat_month, gap_window, side, pct, is_market_hyped)
 *      N-day price-return extreme-percentile bucket.
 *    kind = "mov_pairs" → analysis_forecasts.mov_pairs ⋈ forecast_results
 *      one row per (stat_month, pair_window, side,
 *      is_market_hyped) MA-pair cross (golden / death cross) bucket —
 *      the EXISTING analysis.mov_ave_spreads_detail ma5_vs_ma{pair_window}
 *      relative-MA spread changing sign (top = cross up, bottom = cross
 *      down; fast leg fixed ma5).
 *    kind = "mov_pairs_ema" → analysis_forecasts.mov_pairs_ema ⋈
 *      forecast_results — the EMA sibling of mov_pairs: one row per
 *      (stat_month, pair_window, side, is_market_hyped)
 *      cross bucket on the EXISTING
 *      analysis.mov_ave_spreads_detail_ema ema6_vs_ema{pair_window}
 *      relative-EMA spread (fast leg fixed ema6).
 *    kind = "px_vol" → analysis_forecasts.px_vol_state ⋈ forecast_results
 *      one row per (stat_month, px_speed, vol_state, is_market_hyped)
 *      σ-standardized price-speed × z-scored log amount-LEVEL state cell (NO cooldown
 *      — state buckets admit every qualifying day), additionally carrying
 *      the cell's mean_t / mean_z state magnitudes from the linked
 *      forecast_results.config JSONB.
 *    kind = "margin_ratio" → analysis_forecasts.margin_ratio_state ⋈
 *      forecast_results — one row per (stat_month, ratio_state,
 *      is_market_hyped) margin-buy intensity (融资买入额/成交额 ratio)
 *      z-score state cell (NO cooldown; etf + stock only), additionally
 *      carrying the cell's mean_ratio / mean_z state magnitudes from the
 *      linked forecast_results.config JSONB.
 *    kind = "high_low_streaks" → analysis_forecasts.high_low_streaks ⋈
 *      forecast_results — one row per (stat_month, band_period,
 *      pct_type, side, is_market_hyped) MA-Spread High/Low streak
 *      bucket: every band-break excursion streak of
 *      analysis.mov_ave_high_low_pct_streaks audited at its MEAN-MID
 *      anchor day (the ((day_count-1)//2 + 1)-th trading day of the
 *      span; an 8-day streak anchors its 4th day — EX-POST anchor, so
 *      signal months lag by a resolve window; NO cooldown — one
 *      trigger per streak), additionally carrying the bucket's
 *      streak-length context (mean / min / max day_count) from the
 *      linked forecast_results.config JSONB.
 *    kind = "pe" → analysis_forecasts.pe_state ⋈
 *      forecast_results — one row per (stat_month, val_state,
 *      is_market_hyped) PE z-state bucket over the raw pe series of
 *      analysis.pe. PE is LOWER the better: high/vhigh (expensive)
 *      states are bearish (side top), vlow/low (cheap) bullish (side
 *      bottom), carried on the row's side column; additionally
 *      carrying the cell's mean_metric (raw PE ratio) / mean_z state
 *      magnitudes from the linked forecast_results.config JSONB.
 *    kind = "dividend" → analysis_forecasts.dividend_state ⋈
 *      forecast_results — one row per (stat_month, val_state,
 *      is_market_hyped) dividend-yield z-state bucket over the
 *      trailing-12m D/P series of analysis.dividends. The yield is
 *      HIGHER the better (the REVERSE of the pe mapping): high/vhigh
 *      (cheap, well-supported) states are bullish (side bottom),
 *      vlow/low bearish (side top); additionally carrying the cell's
 *      mean_metric (fractional D/P) / mean_z state magnitudes from the
 *      linked forecast_results.config JSONB.
 *
 *  forecast_results is now NORMALIZED (1 row per forecast_id × period) —
 *  one forecast bucket has 4 period rows (next/5d/20d/60d). This service
 *  uses GROUP BY + conditional aggregation to pivot back to the wide
 *  format the UI consumes (1 row per bucket, period-suffixed columns).
 *
 *  The motivation tables carry code alone as their partition key
 *  (2026-09 shape: PK (code, forecast_id) on HASH (code) partitions) —
 *  every query here resolves the bucket's (sec_type, stat_month,
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
 *  When `month` ("YYYY-MM-DD") is given it is a START month — all rows with
 *  stat_month >= month are returned; when omitted, ALL stat_months of the
 *  code are returned. The response also carries `months` — every distinct
 *  stat_month available for the code (DESC) — so the UI can render the
 *  month tick-filter.
 *
 *  Each row also carries `in_signals` — TRUE when the bucket already
 *  produced signal day(s) in analysis_signals.signals. signals has no
 *  forecast_id column; a signal links to its bucket by config equality
 *  (params JSONB: window / side / pct|k) + the signal date
 *  falling inside the bucket's stat_month, so the flag is a parameterized
 *  EXISTS on that natural key (hype split is NOT matched — signals are
 *  not hype-separated).
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
 *  row per bucket: sec_type / code / stat_month / bucket family — the
 *  ONLY table storing the identity since the motivation tables went
 *  forecast_id-keyed). Returns null when the id is unknown; `kind`
 *  maps the motivation table to the ForecastTable family that renders
 *  it (null for opp_pair_state — industry pairs have no table here).
 */
import { queryRows, formatDate, toNum } from "../../lib/db.js";
import { codeVariants } from "../../lib/classify-etf.js";
import type { QueryResultRow } from "pg";
import type {
  ForecastIdentityResponse,
  ForecastKind,
  ForecastResponse,
  ForecastTriggerDatesResponse,
  HighLowStreaksForecastRow,
  MarginRatioForecastRow,
  MovGapForecastRow,
  MovPairsEmaForecastRow,
  MovPairsForecastRow,
  MovRsiForecastRow,
  MovStdForecastRow,
  PeForecastRow,
  DividendForecastRow,
  PxVolForecastRow,
} from "../../../shared/types.js";

const VALID_KINDS: ReadonlySet<string> = new Set([
  "mov_rsi", "mov_std", "mov_gap", "mov_pairs", "mov_pairs_ema",
  "px_vol", "margin_ratio", "high_low_streaks", "pe", "dividend",
]);

// ---- Pivot fragments: 4 periods × consolidated cols → period-suffixed col names ----
// forecast_results is normalized (forecast_id, period) → these fragments
// pivot it back to the wide format the UI consumes. NULLs for period='next'
// on max/min/max_low_change_ratio are handled naturally by CASE WHEN.

const PERIODS: ReadonlyArray<{ period: string; suffix: string; hasMM: boolean }> = [
  { period: "next", suffix: "next",   hasMM: false },
  { period: "5d",   suffix: "5d",     hasMM: true  },
  { period: "20d",  suffix: "20d",    hasMM: true  },
  { period: "60d",  suffix: "60d",    hasMM: true  },
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
    // reverse_prob → reverse_prob / reverse_prob_5d / ...
    const revAlias = suffix === "next" ? "reverse_prob" : `reverse_prob_${suffix}`;
    parts.push(`MAX(CASE WHEN f.period = '${period}' THEN f.reverse_prob END)::float8 AS ${revAlias}`);
    if (hasMM) {
      parts.push(`MAX(CASE WHEN f.period = '${period}' THEN f.max_change END)::float8 AS max_${suffix}_change`);
      parts.push(`MAX(CASE WHEN f.period = '${period}' THEN f.min_change END)::float8 AS min_${suffix}_change`);
      parts.push(`MAX(CASE WHEN f.period = '${period}' THEN f.max_low_change_ratio END)::float8 AS max_low_change_ratio_${suffix}`);
    }
  }
  return parts.join(",\n  ");
}

const PIVOT_COLS = buildPivotCols();

/** Parameterized EXISTS linking a forecast bucket to its signal day(s) in
 *  analysis_signals.signals: same code / sec_type / signal family, the
 *  bucket config inside the signal's params JSONB (numeric casts — JSON
 *  renders 2.0 while float8::text gives "2"), and the signal date inside
 *  the bucket's snapshot month. stat_month is the month-END label
 * (2025-03-31 = March), so the month window is [date_trunc(month),
 *  month_end] — NOT [stat_month, stat_month + 1 month), which would
 *  match the NEXT month's signals. The mov_std variant also matches k
 *  (signals are emitted only at the detection k, so buckets with other k
 *  stay unticked). hype is NOT matched — signals are not hype-separated. */
const IN_SIGNALS_RSI = `
  EXISTS (
    SELECT 1 FROM analysis_signals.signals s
    WHERE s.code = i.code
      AND s.sec_type = i.sec_type
      AND s.signal_type = 'mov_rsi'
      AND (s.params->>'rsi_window')::numeric = m.rsi_window
      AND (s.params->>'pct')::numeric = m.pct
      AND (s.params->>'side') = m.side
      AND s.date >= date_trunc('month', i.stat_month)::date
      AND s.date <= i.stat_month
  )`;

const IN_SIGNALS_STD = `
  EXISTS (
    SELECT 1 FROM analysis_signals.signals s
    WHERE s.code = i.code
      AND s.sec_type = i.sec_type
      AND s.signal_type = 'mov_std'
      AND (s.params->>'ma_window')::numeric = m.ma_window
      AND (s.params->>'k')::numeric = m.k
      AND (s.params->>'side') = m.side
      AND s.date >= date_trunc('month', i.stat_month)::date
      AND s.date <= i.stat_month
  )`;

const IN_SIGNALS_GAP = `
  EXISTS (
    SELECT 1 FROM analysis_signals.signals s
    WHERE s.code = i.code
      AND s.sec_type = i.sec_type
      AND s.signal_type = 'mov_gap'
      AND (s.params->>'gap_window')::numeric = m.gap_window
      AND (s.params->>'pct')::numeric = m.pct
      AND (s.params->>'side') = m.side
      AND s.date >= date_trunc('month', i.stat_month)::date
      AND s.date <= i.stat_month
  )`;

/** mov_pairs variant: cross-event buckets, so the natural
 *  key is pair_window + side (no pct/k — the trigger is
 *  the ma5_vs_ma{W} spread's sign flip). Stays unticked until a
 *  mov_pairs signals engine exists. hype is NOT matched — signals are
 *  not hype-separated. */
const IN_SIGNALS_MOV_PAIRS = `
  EXISTS (
    SELECT 1 FROM analysis_signals.signals s
    WHERE s.code = i.code
      AND s.sec_type = i.sec_type
      AND s.signal_type = 'mov_pairs'
      AND (s.params->>'pair_window')::numeric = m.pair_window
      AND (s.params->>'side') = m.side
      AND s.date >= date_trunc('month', i.stat_month)::date
      AND s.date <= i.stat_month
  )`;

/** mov_pairs_ema variant — the EMA sibling of IN_SIGNALS_MOV_PAIRS (same
 *  natural key; signal_type 'mov_pairs_ema'). Stays unticked until an
 *  EMA-pair signals engine exists. hype is NOT matched — signals are
 *  not hype-separated. */
const IN_SIGNALS_MOV_PAIRS_EMA = `
  EXISTS (
    SELECT 1 FROM analysis_signals.signals s
    WHERE s.code = i.code
      AND s.sec_type = i.sec_type
      AND s.signal_type = 'mov_pairs_ema'
      AND (s.params->>'pair_window')::numeric = m.pair_window
      AND (s.params->>'side') = m.side
      AND s.date >= date_trunc('month', i.stat_month)::date
      AND s.date <= i.stat_month
  )`;

/** px_vol variant: state cells have no cooldown and constant (recorded)
 *  thresholds, so the natural key is px_speed + vol_state + side (all
 *  text in params). flat rows never emit signals and stay unticked.
 *  hype is NOT matched — signals are not hype-separated. */
const IN_SIGNALS_PX_VOL = `
  EXISTS (
    SELECT 1 FROM analysis_signals.signals s
    WHERE s.code = i.code
      AND s.sec_type = i.sec_type
      AND s.signal_type = 'px_vol'
      AND (s.params->>'px_speed') = m.px_speed
      AND (s.params->>'vol_state') = m.vol_state
      AND (s.params->>'side') = m.side
      AND s.date >= date_trunc('month', i.stat_month)::date
      AND s.date <= i.stat_month
  )`;

/** margin_ratio variant: state cells have no cooldown and constant
 *  (recorded) z bars, so the natural key is ratio_state + side (all text
 *  in params). mid / no_buy rows never emit signals and stay unticked.
 *  hype is NOT matched — signals are not hype-separated. */
const IN_SIGNALS_MARGIN_RATIO = `
  EXISTS (
    SELECT 1 FROM analysis_signals.signals s
    WHERE s.code = i.code
      AND s.sec_type = i.sec_type
      AND s.signal_type = 'margin_ratio'
      AND (s.params->>'ratio_state') = m.ratio_state
      AND (s.params->>'side') = m.side
      AND s.date >= date_trunc('month', i.stat_month)::date
      AND s.date <= i.stat_month
  )`;

/** high_low_streaks variant: one trigger per streak (NO cooldown), so
 *  the natural key is band_period + pct_type + side (numeric + text in
 *  params; the signal's sub_type p{band_period}_{pct_type} mirrors the
 *  bucket keys). Signals lag the buckets by the EX-POST resolve window
 *  (a month's anchors are final ~2 months later), so recent months
 *  legitimately stay unticked. hype is NOT matched — signals are not
 *  hype-separated. */
const IN_SIGNALS_HIGH_LOW_STREAKS = `
  EXISTS (
    SELECT 1 FROM analysis_signals.signals s
    WHERE s.code = i.code
      AND s.sec_type = i.sec_type
      AND s.signal_type = 'high_low_streaks'
      AND (s.params->>'band_period')::numeric = m.band_period
      AND (s.params->>'pct_type')::numeric = m.pct_type
      AND (s.params->>'side') = m.side
      AND s.date >= date_trunc('month', i.stat_month)::date
      AND s.date <= i.stat_month
  )`;

/** pe variant: streak-merged state cells have constant (recorded) z
 *  bars, so the natural key is val_state + side (all text in params).
 *  mid rows never carry a directional claim and the family has no
 *  signals engine yet — rows stay unticked until one exists. hype is
 *  NOT matched — signals are not hype-separated. */
const IN_SIGNALS_PE = `
  EXISTS (
    SELECT 1 FROM analysis_signals.signals s
    WHERE s.code = i.code
      AND s.sec_type = i.sec_type
      AND s.signal_type = 'pe'
      AND (s.params->>'val_state') = m.val_state
      AND (s.params->>'side') = m.side
      AND s.date >= date_trunc('month', i.stat_month)::date
      AND s.date <= i.stat_month
  )`;

/** dividend variant — the yield sibling of IN_SIGNALS_PE (same natural
 *  key; signal_type 'dividend'). Stays unticked until a dividend
 *  signals engine exists. hype is NOT matched — signals are not
 *  hype-separated. */
const IN_SIGNALS_DIVIDEND = `
  EXISTS (
    SELECT 1 FROM analysis_signals.signals s
    WHERE s.code = i.code
      AND s.sec_type = i.sec_type
      AND s.signal_type = 'dividend'
      AND (s.params->>'val_state') = m.val_state
      AND (s.params->>'side') = m.side
      AND s.date >= date_trunc('month', i.stat_month)::date
      AND s.date <= i.stat_month
  )`;

interface DbGapRow extends QueryResultRow {
  forecast_id: number | string;
  stat_month: Date | string;
  gap_window: number;
  side: string;
  pct: number;
  is_market_hyped: boolean;
  in_signals: boolean;
  [k: string]: unknown;
}

interface DbPairsRow extends QueryResultRow {
  forecast_id: number | string;
  stat_month: Date | string;
  pair_window: number;
  side: string;
  is_market_hyped: boolean;
  in_signals: boolean;
  [k: string]: unknown;
}

interface DbRsiRow extends QueryResultRow {
  forecast_id: number | string;
  stat_month: Date | string;
  rsi_window: number;
  side: string;
  pct: number;
  is_market_hyped: boolean;
  in_signals: boolean;
  [k: string]: unknown;
}

interface DbStdRow extends QueryResultRow {
  forecast_id: number | string;
  stat_month: Date | string;
  ma_window: number;
  k: number;
  side: string;
  is_market_hyped: boolean;
  in_signals: boolean;
  [k: string]: unknown;
}

interface DbPxVolRow extends QueryResultRow {
  forecast_id: number | string;
  stat_month: Date | string;
  px_speed: string;
  vol_state: string;
  side: string;
  is_market_hyped: boolean;
  in_signals: boolean;
  mean_t: number | null;
  mean_z: number | null;
  [k: string]: unknown;
}

interface DbMarginRatioRow extends QueryResultRow {
  forecast_id: number | string;
  stat_month: Date | string;
  ratio_state: string;
  side: string;
  is_market_hyped: boolean;
  in_signals: boolean;
  mean_ratio: number | null;
  mean_z: number | null;
  [k: string]: unknown;
}

interface DbHighLowStreaksRow extends QueryResultRow {
  forecast_id: number | string;
  stat_month: Date | string;
  band_period: number;
  pct_type: number;
  side: string;
  is_market_hyped: boolean;
  in_signals: boolean;
  mean_day_count: number | null;
  min_day_count: number | null;
  max_day_count: number | null;
  [k: string]: unknown;
}

interface DbValStateRow extends QueryResultRow {
  forecast_id: number | string;
  stat_month: Date | string;
  val_state: string;
  side: string;
  is_market_hyped: boolean;
  in_signals: boolean;
  mean_metric: number | null;
  mean_z: number | null;
  [k: string]: unknown;
}

/** px_vol state magnitudes live in the linked forecast_results.config
 *  JSONB (duplicated across all 4 period rows per forecast_id) — the
 *  MIN(text) trick casts to text (PG can MIN text, not jsonb), MINs,
 *  and casts back to jsonb for ->> access. */
const CONFIG_PX_VOL_COLS = `
  NULLIF(MIN(f.config::text)::jsonb->>'mean_t', '')::float8 AS mean_t,
  NULLIF(MIN(f.config::text)::jsonb->>'mean_z', '')::float8 AS mean_z
`;

/** margin_ratio state magnitudes live in the linked forecast_results.config
 *  JSONB (duplicated across all 4 period rows per forecast_id) — same
 *  MIN(text) trick as CONFIG_PX_VOL_COLS above. */
const CONFIG_MARGIN_RATIO_COLS = `
  NULLIF(MIN(f.config::text)::jsonb->>'mean_ratio', '')::float8 AS mean_ratio,
  NULLIF(MIN(f.config::text)::jsonb->>'mean_z', '')::float8 AS mean_z
`;

/** high_low_streaks streak-length context lives in the linked
 *  forecast_results.config JSONB (duplicated across all 4 period rows
 *  per forecast_id) — same MIN(text) trick as CONFIG_PX_VOL_COLS. */
const CONFIG_HIGH_LOW_STREAKS_COLS = `
  NULLIF(MIN(f.config::text)::jsonb->>'mean_day_count', '')::float8 AS mean_day_count,
  NULLIF(MIN(f.config::text)::jsonb->>'min_day_count', '')::float8 AS min_day_count,
  NULLIF(MIN(f.config::text)::jsonb->>'max_day_count', '')::float8 AS max_day_count
`;

/** pe_state / dividend_state state magnitudes live in the linked
 *  forecast_results.config JSONB (duplicated across all 4 period rows
 *  per forecast_id) — same MIN(text) trick as CONFIG_PX_VOL_COLS.
 *  mean_metric is per family: raw PE ratio for pe rows, fractional D/P
 *  for dividend rows (the UI renders per kind). */
const CONFIG_VAL_STATE_COLS = `
  NULLIF(MIN(f.config::text)::jsonb->>'mean_metric', '')::float8 AS mean_metric,
  NULLIF(MIN(f.config::text)::jsonb->>'mean_z', '')::float8 AS mean_z
`;

// ---- Column-to-field mapping (wide format — matches ForecastResultCols) ----
// The API response shape is unchanged from the old wide table: one row
// per bucket, all periods as period-suffixed columns.

function mapRsiRow(r: DbRsiRow): MovRsiForecastRow {
  return {
    forecast_id: Number(r.forecast_id),
    stat_month: formatDate(r.stat_month),
    rsi_window: r.rsi_window,
    side: r.side as MovRsiForecastRow["side"],
    pct: r.pct,
    is_market_hyped: r.is_market_hyped === true,
    in_signals: r.in_signals === true,
    ave_next_change: toNum(r.ave_next_change),
    ave_next_5d_change: toNum(r.ave_next_5d_change),
    ave_next_20d_change: toNum(r.ave_next_20d_change),
    ave_next_60d_change: toNum(r.ave_next_60d_change),
    std_next_change: toNum(r.std_next_change),
    std_next_5d_change: toNum(r.std_next_5d_change),
    std_next_20d_change: toNum(r.std_next_20d_change),
    std_next_60d_change: toNum(r.std_next_60d_change),
    max_5d_change: toNum(r.max_5d_change),
    max_20d_change: toNum(r.max_20d_change),
    max_60d_change: toNum(r.max_60d_change),
    min_5d_change: toNum(r.min_5d_change),
    min_20d_change: toNum(r.min_20d_change),
    min_60d_change: toNum(r.min_60d_change),
    max_low_change_ratio_5d: toNum(r.max_low_change_ratio_5d),
    max_low_change_ratio_20d: toNum(r.max_low_change_ratio_20d),
    max_low_change_ratio_60d: toNum(r.max_low_change_ratio_60d),
    reverse_prob: toNum(r.reverse_prob),
    reverse_prob_5d: toNum(r.reverse_prob_5d),
    reverse_prob_20d: toNum(r.reverse_prob_20d),
    reverse_prob_60d: toNum(r.reverse_prob_60d),
    occurrence_count_next: toNum(r.occurrence_count_next),
    occurrence_count_5d: toNum(r.occurrence_count_5d),
    occurrence_count_20d: toNum(r.occurrence_count_20d),
    occurrence_count_60d: toNum(r.occurrence_count_60d),
  };
}

function mapGapRow(r: DbGapRow): MovGapForecastRow {
  return {
    forecast_id: Number(r.forecast_id),
    stat_month: formatDate(r.stat_month),
    gap_window: r.gap_window,
    side: r.side as MovGapForecastRow["side"],
    pct: r.pct,
    is_market_hyped: r.is_market_hyped === true,
    in_signals: r.in_signals === true,
    ave_next_change: toNum(r.ave_next_change),
    ave_next_5d_change: toNum(r.ave_next_5d_change),
    ave_next_20d_change: toNum(r.ave_next_20d_change),
    ave_next_60d_change: toNum(r.ave_next_60d_change),
    std_next_change: toNum(r.std_next_change),
    std_next_5d_change: toNum(r.std_next_5d_change),
    std_next_20d_change: toNum(r.std_next_20d_change),
    std_next_60d_change: toNum(r.std_next_60d_change),
    max_5d_change: toNum(r.max_5d_change),
    max_20d_change: toNum(r.max_20d_change),
    max_60d_change: toNum(r.max_60d_change),
    min_5d_change: toNum(r.min_5d_change),
    min_20d_change: toNum(r.min_20d_change),
    min_60d_change: toNum(r.min_60d_change),
    max_low_change_ratio_5d: toNum(r.max_low_change_ratio_5d),
    max_low_change_ratio_20d: toNum(r.max_low_change_ratio_20d),
    max_low_change_ratio_60d: toNum(r.max_low_change_ratio_60d),
    reverse_prob: toNum(r.reverse_prob),
    reverse_prob_5d: toNum(r.reverse_prob_5d),
    reverse_prob_20d: toNum(r.reverse_prob_20d),
    reverse_prob_60d: toNum(r.reverse_prob_60d),
    occurrence_count_next: toNum(r.occurrence_count_next),
    occurrence_count_5d: toNum(r.occurrence_count_5d),
    occurrence_count_20d: toNum(r.occurrence_count_20d),
    occurrence_count_60d: toNum(r.occurrence_count_60d),
  };
}

/** One mov_pairs bucket row → API shape (config cols + the pivoted
 *  forecast_results columns). */
function mapPairsRow(r: DbPairsRow): MovPairsForecastRow {
  return {
    forecast_id: Number(r.forecast_id),
    stat_month: formatDate(r.stat_month),
    pair_window: r.pair_window,
    side: r.side as MovPairsForecastRow["side"],
    is_market_hyped: r.is_market_hyped === true,
    in_signals: r.in_signals === true,
    ave_next_change: toNum(r.ave_next_change),
    ave_next_5d_change: toNum(r.ave_next_5d_change),
    ave_next_20d_change: toNum(r.ave_next_20d_change),
    ave_next_60d_change: toNum(r.ave_next_60d_change),
    std_next_change: toNum(r.std_next_change),
    std_next_5d_change: toNum(r.std_next_5d_change),
    std_next_20d_change: toNum(r.std_next_20d_change),
    std_next_60d_change: toNum(r.std_next_60d_change),
    max_5d_change: toNum(r.max_5d_change),
    max_20d_change: toNum(r.max_20d_change),
    max_60d_change: toNum(r.max_60d_change),
    min_5d_change: toNum(r.min_5d_change),
    min_20d_change: toNum(r.min_20d_change),
    min_60d_change: toNum(r.min_60d_change),
    max_low_change_ratio_5d: toNum(r.max_low_change_ratio_5d),
    max_low_change_ratio_20d: toNum(r.max_low_change_ratio_20d),
    max_low_change_ratio_60d: toNum(r.max_low_change_ratio_60d),
    reverse_prob: toNum(r.reverse_prob),
    reverse_prob_5d: toNum(r.reverse_prob_5d),
    reverse_prob_20d: toNum(r.reverse_prob_20d),
    reverse_prob_60d: toNum(r.reverse_prob_60d),
    occurrence_count_next: toNum(r.occurrence_count_next),
    occurrence_count_5d: toNum(r.occurrence_count_5d),
    occurrence_count_20d: toNum(r.occurrence_count_20d),
    occurrence_count_60d: toNum(r.occurrence_count_60d),
  };
}

function mapStdRow(r: DbStdRow): MovStdForecastRow {
  const base: MovStdForecastRow = {
    forecast_id: Number(r.forecast_id),
    stat_month: formatDate(r.stat_month),
    ma_window: r.ma_window,
    k: toNum(r.k) ?? 0,
    side: r.side as MovStdForecastRow["side"],
    is_market_hyped: r.is_market_hyped === true,
    in_signals: r.in_signals === true,
    ave_next_change: toNum(r.ave_next_change),
    ave_next_5d_change: toNum(r.ave_next_5d_change),
    ave_next_20d_change: toNum(r.ave_next_20d_change),
    ave_next_60d_change: toNum(r.ave_next_60d_change),
    std_next_change: toNum(r.std_next_change),
    std_next_5d_change: toNum(r.std_next_5d_change),
    std_next_20d_change: toNum(r.std_next_20d_change),
    std_next_60d_change: toNum(r.std_next_60d_change),
    max_5d_change: toNum(r.max_5d_change),
    max_20d_change: toNum(r.max_20d_change),
    max_60d_change: toNum(r.max_60d_change),
    min_5d_change: toNum(r.min_5d_change),
    min_20d_change: toNum(r.min_20d_change),
    min_60d_change: toNum(r.min_60d_change),
    max_low_change_ratio_5d: toNum(r.max_low_change_ratio_5d),
    max_low_change_ratio_20d: toNum(r.max_low_change_ratio_20d),
    max_low_change_ratio_60d: toNum(r.max_low_change_ratio_60d),
    reverse_prob: toNum(r.reverse_prob),
    reverse_prob_5d: toNum(r.reverse_prob_5d),
    reverse_prob_20d: toNum(r.reverse_prob_20d),
    reverse_prob_60d: toNum(r.reverse_prob_60d),
    occurrence_count_next: toNum(r.occurrence_count_next),
    occurrence_count_5d: toNum(r.occurrence_count_5d),
    occurrence_count_20d: toNum(r.occurrence_count_20d),
    occurrence_count_60d: toNum(r.occurrence_count_60d),
  };
  return base;
}

function mapPxVolRow(r: DbPxVolRow): PxVolForecastRow {
  return {
    forecast_id: Number(r.forecast_id),
    stat_month: formatDate(r.stat_month),
    px_speed: r.px_speed as PxVolForecastRow["px_speed"],
    vol_state: r.vol_state as PxVolForecastRow["vol_state"],
    side: r.side as PxVolForecastRow["side"],
    is_market_hyped: r.is_market_hyped === true,
    in_signals: r.in_signals === true,
    mean_t: toNum(r.mean_t),
    mean_z: toNum(r.mean_z),
    ave_next_change: toNum(r.ave_next_change),
    ave_next_5d_change: toNum(r.ave_next_5d_change),
    ave_next_20d_change: toNum(r.ave_next_20d_change),
    ave_next_60d_change: toNum(r.ave_next_60d_change),
    std_next_change: toNum(r.std_next_change),
    std_next_5d_change: toNum(r.std_next_5d_change),
    std_next_20d_change: toNum(r.std_next_20d_change),
    std_next_60d_change: toNum(r.std_next_60d_change),
    max_5d_change: toNum(r.max_5d_change),
    max_20d_change: toNum(r.max_20d_change),
    max_60d_change: toNum(r.max_60d_change),
    min_5d_change: toNum(r.min_5d_change),
    min_20d_change: toNum(r.min_20d_change),
    min_60d_change: toNum(r.min_60d_change),
    max_low_change_ratio_5d: toNum(r.max_low_change_ratio_5d),
    max_low_change_ratio_20d: toNum(r.max_low_change_ratio_20d),
    max_low_change_ratio_60d: toNum(r.max_low_change_ratio_60d),
    reverse_prob: toNum(r.reverse_prob),
    reverse_prob_5d: toNum(r.reverse_prob_5d),
    reverse_prob_20d: toNum(r.reverse_prob_20d),
    reverse_prob_60d: toNum(r.reverse_prob_60d),
    occurrence_count_next: toNum(r.occurrence_count_next),
    occurrence_count_5d: toNum(r.occurrence_count_5d),
    occurrence_count_20d: toNum(r.occurrence_count_20d),
    occurrence_count_60d: toNum(r.occurrence_count_60d),
  };
}

function mapMarginRatioRow(r: DbMarginRatioRow): MarginRatioForecastRow {
  return {
    forecast_id: Number(r.forecast_id),
    stat_month: formatDate(r.stat_month),
    ratio_state: r.ratio_state as MarginRatioForecastRow["ratio_state"],
    side: r.side as MarginRatioForecastRow["side"],
    is_market_hyped: r.is_market_hyped === true,
    in_signals: r.in_signals === true,
    mean_ratio: toNum(r.mean_ratio),
    mean_z: toNum(r.mean_z),
    ave_next_change: toNum(r.ave_next_change),
    ave_next_5d_change: toNum(r.ave_next_5d_change),
    ave_next_20d_change: toNum(r.ave_next_20d_change),
    ave_next_60d_change: toNum(r.ave_next_60d_change),
    std_next_change: toNum(r.std_next_change),
    std_next_5d_change: toNum(r.std_next_5d_change),
    std_next_20d_change: toNum(r.std_next_20d_change),
    std_next_60d_change: toNum(r.std_next_60d_change),
    max_5d_change: toNum(r.max_5d_change),
    max_20d_change: toNum(r.max_20d_change),
    max_60d_change: toNum(r.max_60d_change),
    min_5d_change: toNum(r.min_5d_change),
    min_20d_change: toNum(r.min_20d_change),
    min_60d_change: toNum(r.min_60d_change),
    max_low_change_ratio_5d: toNum(r.max_low_change_ratio_5d),
    max_low_change_ratio_20d: toNum(r.max_low_change_ratio_20d),
    max_low_change_ratio_60d: toNum(r.max_low_change_ratio_60d),
    reverse_prob: toNum(r.reverse_prob),
    reverse_prob_5d: toNum(r.reverse_prob_5d),
    reverse_prob_20d: toNum(r.reverse_prob_20d),
    reverse_prob_60d: toNum(r.reverse_prob_60d),
    occurrence_count_next: toNum(r.occurrence_count_next),
    occurrence_count_5d: toNum(r.occurrence_count_5d),
    occurrence_count_20d: toNum(r.occurrence_count_20d),
    occurrence_count_60d: toNum(r.occurrence_count_60d),
  };
}

/** One high_low_streaks bucket row → API shape (config cols + the
 *  bucket's streak-length context + the pivoted forecast_results
 *  columns). */
function mapHighLowStreaksRow(r: DbHighLowStreaksRow): HighLowStreaksForecastRow {
  return {
    forecast_id: Number(r.forecast_id),
    stat_month: formatDate(r.stat_month),
    band_period: r.band_period,
    pct_type: r.pct_type,
    side: r.side as HighLowStreaksForecastRow["side"],
    is_market_hyped: r.is_market_hyped === true,
    in_signals: r.in_signals === true,
    mean_day_count: toNum(r.mean_day_count),
    min_day_count: toNum(r.min_day_count),
    max_day_count: toNum(r.max_day_count),
    ave_next_change: toNum(r.ave_next_change),
    ave_next_5d_change: toNum(r.ave_next_5d_change),
    ave_next_20d_change: toNum(r.ave_next_20d_change),
    ave_next_60d_change: toNum(r.ave_next_60d_change),
    std_next_change: toNum(r.std_next_change),
    std_next_5d_change: toNum(r.std_next_5d_change),
    std_next_20d_change: toNum(r.std_next_20d_change),
    std_next_60d_change: toNum(r.std_next_60d_change),
    max_5d_change: toNum(r.max_5d_change),
    max_20d_change: toNum(r.max_20d_change),
    max_60d_change: toNum(r.max_60d_change),
    min_5d_change: toNum(r.min_5d_change),
    min_20d_change: toNum(r.min_20d_change),
    min_60d_change: toNum(r.min_60d_change),
    max_low_change_ratio_5d: toNum(r.max_low_change_ratio_5d),
    max_low_change_ratio_20d: toNum(r.max_low_change_ratio_20d),
    max_low_change_ratio_60d: toNum(r.max_low_change_ratio_60d),
    reverse_prob: toNum(r.reverse_prob),
    reverse_prob_5d: toNum(r.reverse_prob_5d),
    reverse_prob_20d: toNum(r.reverse_prob_20d),
    reverse_prob_60d: toNum(r.reverse_prob_60d),
    occurrence_count_next: toNum(r.occurrence_count_next),
    occurrence_count_5d: toNum(r.occurrence_count_5d),
    occurrence_count_20d: toNum(r.occurrence_count_20d),
    occurrence_count_60d: toNum(r.occurrence_count_60d),
  };
}

/** One pe / dividend bucket row → API shape (config cols + the pivoted
 *  forecast_results columns). The two families share the row shape —
 *  only the source table + signal family differ. */
function mapValStateRow(r: DbValStateRow): PeForecastRow | DividendForecastRow {
  return {
    forecast_id: Number(r.forecast_id),
    stat_month: formatDate(r.stat_month),
    val_state: r.val_state as PeForecastRow["val_state"],
    side: r.side as PeForecastRow["side"],
    is_market_hyped: r.is_market_hyped === true,
    in_signals: r.in_signals === true,
    mean_metric: toNum(r.mean_metric),
    mean_z: toNum(r.mean_z),
    ave_next_change: toNum(r.ave_next_change),
    ave_next_5d_change: toNum(r.ave_next_5d_change),
    ave_next_20d_change: toNum(r.ave_next_20d_change),
    ave_next_60d_change: toNum(r.ave_next_60d_change),
    std_next_change: toNum(r.std_next_change),
    std_next_5d_change: toNum(r.std_next_5d_change),
    std_next_20d_change: toNum(r.std_next_20d_change),
    std_next_60d_change: toNum(r.std_next_60d_change),
    max_5d_change: toNum(r.max_5d_change),
    max_20d_change: toNum(r.max_20d_change),
    max_60d_change: toNum(r.max_60d_change),
    min_5d_change: toNum(r.min_5d_change),
    min_20d_change: toNum(r.min_20d_change),
    min_60d_change: toNum(r.min_60d_change),
    max_low_change_ratio_5d: toNum(r.max_low_change_ratio_5d),
    max_low_change_ratio_20d: toNum(r.max_low_change_ratio_20d),
    max_low_change_ratio_60d: toNum(r.max_low_change_ratio_60d),
    reverse_prob: toNum(r.reverse_prob),
    reverse_prob_5d: toNum(r.reverse_prob_5d),
    reverse_prob_20d: toNum(r.reverse_prob_20d),
    reverse_prob_60d: toNum(r.reverse_prob_60d),
    occurrence_count_next: toNum(r.occurrence_count_next),
    occurrence_count_5d: toNum(r.occurrence_count_5d),
    occurrence_count_20d: toNum(r.occurrence_count_20d),
    occurrence_count_60d: toNum(r.occurrence_count_60d),
  };
}

export async function getForecastTable(
  secType: string | undefined,
  code: string | null,
  kind: string | undefined,
  month: string | null,
): Promise<ForecastResponse> {
  if (!code) throw new Error("Missing 'code' parameter");
  const st = (secType ?? "").trim().toLowerCase();
  if (!["etf", "index", "stock"].includes(st)) {
    throw new Error(`Invalid sec_type: ${secType}. Expected 'etf', 'index', or 'stock'.`);
  }
  const k = (kind ?? "").trim().toLowerCase();
  if (!VALID_KINDS.has(k)) {
    throw new Error(
      `Invalid kind: ${kind}. Expected 'mov_rsi', 'mov_std', 'mov_gap', 'mov_pairs', 'mov_pairs_ema', 'px_vol', 'margin_ratio', 'high_low_streaks', 'pe', or 'dividend'.`,
    );
  }
  const m = (month ?? "").trim() || null;

  // Code-format tolerance: the caller may pass a bare ("000001") or
  // suffixed ("000001.SZ") code, while forecast_identities / motivation
  // tables store index codes BARE and etf/stock codes SUFFIXED. Matching
  // `code = ANY(variants)` (same convention as every other service) keeps
  // the (code, …) PK-prefix index scans usable for both input forms — a
  // bare equality against a suffixed stock code returns 0 rows and the
  // Recent Movements table renders its "no analysis_forecasts rows" empty
  // state even though the buckets exist.
  const variants = codeVariants(code);

  // Bucket table per kind (px_vol / margin_ratio / pe / dividend
  // motivation tables are the *_state tables). The values double as the
  // forecast_identities `bucket` discriminator: the motivation tables
  // carry code alone as their partition key (2026-09 (code, forecast_id)
  // shape), so every query below resolves (sec_type, code, bucket,
  // stat_month) through forecast_identities and joins the motivation
  // table by forecast_id, with the m.code = ANY($2) predicate pruning it
  // to the code's partition.
  const TABLES: Record<string, string> = {
    mov_rsi: "mov_rsi",
    mov_std: "mov_std",
    mov_gap: "mov_gap",
    mov_pairs: "mov_pairs",
    mov_pairs_ema: "mov_pairs_ema",
    px_vol: "px_vol_state",
    margin_ratio: "margin_ratio_state",
    high_low_streaks: "high_low_streaks",
    pe: "pe_state",
    dividend: "dividend_state",
  };

  const monthsRows = await queryRows<{ stat_month: Date | string }>(
    `SELECT DISTINCT stat_month
     FROM analysis_forecasts.forecast_identities
     WHERE sec_type = $1 AND code = ANY($2::text[]) AND bucket = $3
     ORDER BY stat_month DESC`,
    [st, variants, TABLES[k]],
  );
  const months = monthsRows.map((r) => formatDate(r.stat_month));

  if (k === "mov_rsi") {
    const rows = await queryRows<DbRsiRow>(
      `
      SELECT m.forecast_id,
             i.stat_month,
             m.rsi_window,
             m.side,
             m.pct,
             m.is_market_hyped,
             ${IN_SIGNALS_RSI} AS in_signals,
             ${PIVOT_COLS}
      FROM analysis_forecasts.forecast_identities i
      JOIN analysis_forecasts.mov_rsi m
        ON m.forecast_id = i.forecast_id
      JOIN analysis_forecasts.forecast_results f
        ON f.forecast_id = m.forecast_id
      WHERE m.code = ANY($2::text[]) AND i.sec_type = $1 AND i.code = ANY($2::text[]) AND i.bucket = 'mov_rsi'
        ${m ? "AND i.stat_month >= $3::date" : ""}
      GROUP BY i.code, i.sec_type, m.forecast_id, i.stat_month, m.rsi_window, m.side, m.pct,
               m.is_market_hyped
      ORDER BY i.stat_month DESC, m.rsi_window ASC, m.side ASC, m.pct ASC,
               m.is_market_hyped ASC
      `,
      m ? [st, variants, m] : [st, variants],
    );
    const mapped = rows.map(mapRsiRow);
    return { kind: "mov_rsi", code, sec_type: st, months, rows: mapped, enable_filters: true };
  }

  if (k === "mov_gap") {
    const rows = await queryRows<DbGapRow>(
      `
      SELECT m.forecast_id,
             i.stat_month,
             m.gap_window,
             m.side,
             m.pct,
             m.is_market_hyped,
             ${IN_SIGNALS_GAP} AS in_signals,
             ${PIVOT_COLS}
      FROM analysis_forecasts.forecast_identities i
      JOIN analysis_forecasts.mov_gap m
        ON m.forecast_id = i.forecast_id
      JOIN analysis_forecasts.forecast_results f
        ON f.forecast_id = m.forecast_id
      WHERE m.code = ANY($2::text[]) AND i.sec_type = $1 AND i.code = ANY($2::text[]) AND i.bucket = 'mov_gap'
        ${m ? "AND i.stat_month >= $3::date" : ""}
      GROUP BY i.code, i.sec_type, m.forecast_id, i.stat_month, m.gap_window, m.side, m.pct,
               m.is_market_hyped
      ORDER BY i.stat_month DESC, m.gap_window ASC, m.side ASC, m.pct ASC,
               m.is_market_hyped ASC
      `,
      m ? [st, variants, m] : [st, variants],
    );
    const mapped = rows.map(mapGapRow);
    return { kind: "mov_gap", code, sec_type: st, months, rows: mapped, enable_filters: true };
  }

  if (k === "mov_pairs") {
    const rows = await queryRows<DbPairsRow>(
      `
      SELECT m.forecast_id,
             i.stat_month,
             m.pair_window,
             m.side,
             m.is_market_hyped,
             ${IN_SIGNALS_MOV_PAIRS} AS in_signals,
             ${PIVOT_COLS}
      FROM analysis_forecasts.forecast_identities i
      JOIN analysis_forecasts.mov_pairs m
        ON m.forecast_id = i.forecast_id
      JOIN analysis_forecasts.forecast_results f
        ON f.forecast_id = m.forecast_id
      WHERE m.code = ANY($2::text[]) AND i.sec_type = $1 AND i.code = ANY($2::text[]) AND i.bucket = 'mov_pairs'
        ${m ? "AND i.stat_month >= $3::date" : ""}
      GROUP BY i.code, i.sec_type, m.forecast_id, i.stat_month, m.pair_window, m.side,
               m.is_market_hyped
      ORDER BY i.stat_month DESC, m.pair_window ASC, m.side ASC,
               m.is_market_hyped ASC
      `,
      m ? [st, variants, m] : [st, variants],
    );
    const mapped = rows.map(mapPairsRow);
    return { kind: "mov_pairs", code, sec_type: st, months, rows: mapped, enable_filters: true };
  }

  if (k === "mov_pairs_ema") {
    // Identical row shape as the MA family (mapPairsRow is structurally
    // compatible) — only the source table + signal_type differ.
    const rows = await queryRows<DbPairsRow>(
      `
      SELECT m.forecast_id,
             i.stat_month,
             m.pair_window,
             m.side,
             m.is_market_hyped,
             ${IN_SIGNALS_MOV_PAIRS_EMA} AS in_signals,
             ${PIVOT_COLS}
      FROM analysis_forecasts.forecast_identities i
      JOIN analysis_forecasts.mov_pairs_ema m
        ON m.forecast_id = i.forecast_id
      JOIN analysis_forecasts.forecast_results f
        ON f.forecast_id = m.forecast_id
      WHERE m.code = ANY($2::text[]) AND i.sec_type = $1 AND i.code = ANY($2::text[]) AND i.bucket = 'mov_pairs_ema'
        ${m ? "AND i.stat_month >= $3::date" : ""}
      GROUP BY i.code, i.sec_type, m.forecast_id, i.stat_month, m.pair_window, m.side,
               m.is_market_hyped
      ORDER BY i.stat_month DESC, m.pair_window ASC, m.side ASC,
               m.is_market_hyped ASC
      `,
      m ? [st, variants, m] : [st, variants],
    );
    const mapped: MovPairsEmaForecastRow[] = rows.map(mapPairsRow);
    return { kind: "mov_pairs_ema", code, sec_type: st, months, rows: mapped, enable_filters: true };
  }

  if (k === "px_vol") {
    const rows = await queryRows<DbPxVolRow>(
      `
      SELECT m.forecast_id,
             i.stat_month,
             m.px_speed,
             m.vol_state,
             m.side,
             m.is_market_hyped,
             ${IN_SIGNALS_PX_VOL} AS in_signals,
             ${CONFIG_PX_VOL_COLS},
             ${PIVOT_COLS}
      FROM analysis_forecasts.forecast_identities i
      JOIN analysis_forecasts.px_vol_state m
        ON m.forecast_id = i.forecast_id
      JOIN analysis_forecasts.forecast_results f
        ON f.forecast_id = m.forecast_id
      WHERE m.code = ANY($2::text[]) AND i.sec_type = $1 AND i.code = ANY($2::text[]) AND i.bucket = 'px_vol_state'
        ${m ? "AND i.stat_month >= $3::date" : ""}
      GROUP BY i.code, i.sec_type, m.forecast_id, i.stat_month, m.px_speed, m.vol_state, m.side,
               m.is_market_hyped
      ORDER BY i.stat_month DESC,
               CASE m.px_speed WHEN 'sharp_up' THEN 1 WHEN 'slow_up' THEN 2
                               WHEN 'flat' THEN 3 WHEN 'slow_dn' THEN 4
                               ELSE 5 END ASC,
               CASE m.vol_state WHEN 'heavy' THEN 1 WHEN 'normal' THEN 2
                                ELSE 3 END ASC,
               m.is_market_hyped ASC
      `,
      m ? [st, variants, m] : [st, variants],
    );
    const mapped = rows.map(mapPxVolRow);
    return { kind: "px_vol", code, sec_type: st, months, rows: mapped, enable_filters: true };
  }

  if (k === "margin_ratio") {
    const rows = await queryRows<DbMarginRatioRow>(
      `
      SELECT m.forecast_id,
             i.stat_month,
             m.ratio_state,
             m.side,
             m.is_market_hyped,
             ${IN_SIGNALS_MARGIN_RATIO} AS in_signals,
             ${CONFIG_MARGIN_RATIO_COLS},
             ${PIVOT_COLS}
      FROM analysis_forecasts.forecast_identities i
      JOIN analysis_forecasts.margin_ratio_state m
        ON m.forecast_id = i.forecast_id
      JOIN analysis_forecasts.forecast_results f
        ON f.forecast_id = m.forecast_id
      WHERE m.code = ANY($2::text[]) AND i.sec_type = $1 AND i.code = ANY($2::text[]) AND i.bucket = 'margin_ratio_state'
        ${m ? "AND i.stat_month >= $3::date" : ""}
      GROUP BY i.code, i.sec_type, m.forecast_id, i.stat_month, m.ratio_state, m.side,
               m.is_market_hyped
      ORDER BY i.stat_month DESC,
               CASE m.ratio_state WHEN 'vlow' THEN 1 WHEN 'low' THEN 2
                                  WHEN 'mid' THEN 3 WHEN 'high' THEN 4
                                  WHEN 'vhigh' THEN 5 ELSE 6 END ASC,
               m.is_market_hyped ASC
      `,
      m ? [st, variants, m] : [st, variants],
    );
    const mapped = rows.map(mapMarginRatioRow);
    return { kind: "margin_ratio", code, sec_type: st, months, rows: mapped, enable_filters: true };
  }

  if (k === "pe") {
    // State ordering runs low → high z within each state family
    // (the reversed side semantics are carried per row).
    const rows = await queryRows<DbValStateRow>(
      `
      SELECT m.forecast_id,
             i.stat_month,
             m.val_state,
             m.side,
             m.is_market_hyped,
             ${IN_SIGNALS_PE} AS in_signals,
             ${CONFIG_VAL_STATE_COLS},
             ${PIVOT_COLS}
      FROM analysis_forecasts.forecast_identities i
      JOIN analysis_forecasts.pe_state m
        ON m.forecast_id = i.forecast_id
      JOIN analysis_forecasts.forecast_results f
        ON f.forecast_id = m.forecast_id
      WHERE m.code = ANY($2::text[]) AND i.sec_type = $1 AND i.code = ANY($2::text[]) AND i.bucket = 'pe_state'
        ${m ? "AND i.stat_month >= $3::date" : ""}
      GROUP BY i.code, i.sec_type, m.forecast_id, i.stat_month, m.val_state, m.side,
               m.is_market_hyped
      ORDER BY i.stat_month DESC,
               CASE m.val_state WHEN 'vlow' THEN 1 WHEN 'low' THEN 2
                                WHEN 'mid' THEN 3 WHEN 'high' THEN 4
                                ELSE 5 END ASC,
               m.is_market_hyped ASC
      `,
      m ? [st, variants, m] : [st, variants],
    );
    const mapped = rows.map(mapValStateRow) as PeForecastRow[];
    return { kind: "pe", code, sec_type: st, months, rows: mapped, enable_filters: true };
  }

  if (k === "dividend") {
    const rows = await queryRows<DbValStateRow>(
      `
      SELECT m.forecast_id,
             i.stat_month,
             m.val_state,
             m.side,
             m.is_market_hyped,
             ${IN_SIGNALS_DIVIDEND} AS in_signals,
             ${CONFIG_VAL_STATE_COLS},
             ${PIVOT_COLS}
      FROM analysis_forecasts.forecast_identities i
      JOIN analysis_forecasts.dividend_state m
        ON m.forecast_id = i.forecast_id
      JOIN analysis_forecasts.forecast_results f
        ON f.forecast_id = m.forecast_id
      WHERE m.code = ANY($2::text[]) AND i.sec_type = $1 AND i.code = ANY($2::text[]) AND i.bucket = 'dividend_state'
        ${m ? "AND i.stat_month >= $3::date" : ""}
      GROUP BY i.code, i.sec_type, m.forecast_id, i.stat_month, m.val_state, m.side,
               m.is_market_hyped
      ORDER BY i.stat_month DESC,
               CASE m.val_state WHEN 'vlow' THEN 1 WHEN 'low' THEN 2
                                WHEN 'mid' THEN 3 WHEN 'high' THEN 4
                                ELSE 5 END ASC,
               m.is_market_hyped ASC
      `,
      m ? [st, variants, m] : [st, variants],
    );
    const mapped = rows.map(mapValStateRow) as DividendForecastRow[];
    return { kind: "dividend", code, sec_type: st, months, rows: mapped, enable_filters: true };
  }

  if (k === "high_low_streaks") {
    const rows = await queryRows<DbHighLowStreaksRow>(
      `
      SELECT m.forecast_id,
             i.stat_month,
             m.band_period,
             m.pct_type,
             m.side,
             m.is_market_hyped,
             ${IN_SIGNALS_HIGH_LOW_STREAKS} AS in_signals,
             ${CONFIG_HIGH_LOW_STREAKS_COLS},
             ${PIVOT_COLS}
      FROM analysis_forecasts.forecast_identities i
      JOIN analysis_forecasts.high_low_streaks m
        ON m.forecast_id = i.forecast_id
      JOIN analysis_forecasts.forecast_results f
        ON f.forecast_id = m.forecast_id
      WHERE m.code = ANY($2::text[]) AND i.sec_type = $1 AND i.code = ANY($2::text[]) AND i.bucket = 'high_low_streaks'
        ${m ? "AND i.stat_month >= $3::date" : ""}
      GROUP BY i.code, i.sec_type, m.forecast_id, i.stat_month, m.band_period, m.pct_type,
               m.side, m.is_market_hyped
      ORDER BY i.stat_month DESC, m.band_period ASC, m.pct_type ASC,
               m.side ASC, m.is_market_hyped ASC
      `,
      m ? [st, variants, m] : [st, variants],
    );
    const mapped = rows.map(mapHighLowStreaksRow);
    return { kind: "high_low_streaks", code, sec_type: st, months, rows: mapped, enable_filters: true };
  }

  // kind === "mov_std"
  const rows = await queryRows<DbStdRow>(
    `
    SELECT m.forecast_id,
           i.stat_month,
           m.ma_window,
           m.k::float8 AS k,
           m.side,
           m.is_market_hyped,
           ${IN_SIGNALS_STD} AS in_signals,
           ${PIVOT_COLS}
    FROM analysis_forecasts.forecast_identities i
    JOIN analysis_forecasts.mov_std m
      ON m.forecast_id = i.forecast_id
    JOIN analysis_forecasts.forecast_results f
      ON f.forecast_id = m.forecast_id
    WHERE m.code = ANY($2::text[]) AND i.sec_type = $1 AND i.code = ANY($2::text[]) AND i.bucket = 'mov_std'
      ${m ? "AND i.stat_month >= $3::date" : ""}
    GROUP BY i.code, i.sec_type, m.forecast_id, i.stat_month, m.ma_window, m.k, m.side,
             m.is_market_hyped
    ORDER BY i.stat_month DESC, m.ma_window ASC, m.k ASC, m.side ASC,
             m.is_market_hyped ASC
    `,
    m ? [st, variants, m] : [st, variants],
  );
  const mapped = rows.map(mapStdRow);
  return { kind: "mov_std", code, sec_type: st, months, rows: mapped, enable_filters: true };
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
 *  count 0 / state families) map to null per period. */
export async function getForecastTriggerDates(
  forecastId: number,
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
     WHERE forecast_id = $1`,
    [forecastId],
  );
  const periods: ForecastTriggerDatesResponse["periods"] = {
    next: null,
    "5d": null,
    "20d": null,
    "60d": null,
  };
  const streaks: ForecastTriggerDatesResponse["streaks"] = {
    next: null,
    "5d": null,
    "20d": null,
    "60d": null,
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
 *  that renders it. The four *_state tables map to the px_vol /
 *  margin_ratio / pe / dividend kinds; opp_pair_state has no table in
 *  this UI. */
const BUCKET_KIND: Record<string, ForecastKind> = {
  mov_rsi: "mov_rsi",
  mov_std: "mov_std",
  mov_gap: "mov_gap",
  mov_pairs: "mov_pairs",
  mov_pairs_ema: "mov_pairs_ema",
  px_vol_state: "px_vol",
  margin_ratio_state: "margin_ratio",
  high_low_streaks: "high_low_streaks",
  pe_state: "pe",
  dividend_state: "dividend",
};

/** One forecast_id → its shared-PK registry row
 *  (analysis_forecasts.forecast_identities): sec_type / code /
 *  stat_month / bucket family + the bucket's mean streak length per
 *  merged signal (streak_signal_days; code is the DROPPING industry for
 *  opp_pair rows — their forecast-target pair_industry_id lives on the
 *  opp_pair_state motivation row). Null when the id is not registered.
 *  Powers the UI's search-by-forecast_id jump: no probing of the nine
 *  motivation tables. */
export async function getForecastIdentity(
  forecastId: number,
): Promise<ForecastIdentityResponse | null> {
  const rows = await queryRows<{
    forecast_id: number | string;
    sec_type: string;
    code: string;
    stat_month: Date | string;
    bucket: string;
    streak_signal_days: number | string | null;
    lookback_period: string;
  }>(
    `SELECT forecast_id, sec_type, code, stat_month, bucket,
            streak_signal_days, lookback_period
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
    stat_month: formatDate(r.stat_month),
    bucket: r.bucket as ForecastIdentityResponse["bucket"],
    kind: BUCKET_KIND[r.bucket] ?? null,
    streak_signal_days: toNum(r.streak_signal_days),
    lookback_period: r.lookback_period,
  };
}
