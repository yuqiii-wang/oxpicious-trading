/**
 * Forecast buckets (analysis_forecasts schema) — serves the MA-Spread
 * panel's second plot: a config→result table beneath the spread chart.
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
 *      Bollinger-breach bucket, additionally carrying mean_excess_close /
 *      mean_excess_max / max_excess_max.
 *    kind = "mov_gap" → analysis_forecasts.mov_gap ⋈ forecast_results
 *      one row per (stat_month, gap_window, side, pct, is_market_hyped)
 *      N-day price-return extreme-percentile bucket.
 *    kind = "px_vol" → analysis_forecasts.px_vol_state ⋈ forecast_results
 *      one row per (stat_month, px_speed, vol_state, is_market_hyped)
 *      σ-standardized price-speed × z-scored 量比 state cell (NO cooldown
 *      — state buckets admit every qualifying day), additionally carrying
 *      the cell's mean_t / mean_z state magnitudes from the linked
 *      forecast_results.config JSONB.
 *    kind = "margin_ratio" → analysis_forecasts.margin_ratio_state ⋈
 *      forecast_results — one row per (stat_month, ratio_state,
 *      is_market_hyped) margin-buy intensity (融资买入额/成交额 ratio)
 *      z-score state cell (NO cooldown; etf + stock only), additionally
 *      carrying the cell's mean_ratio / mean_z state magnitudes from the
 *      linked forecast_results.config JSONB.
 *
 *  forecast_results is now NORMALIZED (1 row per forecast_id × period) —
 *  one forecast bucket has 4 period rows (next/5d/20d/60d). This service
 *  uses GROUP BY + conditional aggregation to pivot back to the wide
 *  format the UI consumes (1 row per bucket, period-suffixed columns).
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
 *  (params JSONB: window / side / pct|k / cooldown) + the signal date
 *  falling inside the bucket's stat_month, so the flag is a parameterized
 *  EXISTS on that natural key (hype split is NOT matched — signals are
 *  not hype-separated).
 */
import { queryRows, formatDate, toNum } from "../../lib/db.js";
import type { QueryResultRow } from "pg";
import type {
  ForecastResponse,
  MarginRatioForecastRow,
  MovGapForecastRow,
  MovRsiForecastRow,
  MovStdForecastRow,
  PxVolForecastRow,
} from "../../../shared/types.js";

const VALID_KINDS: ReadonlySet<string> = new Set([
  "mov_rsi", "mov_std", "mov_gap", "px_vol", "margin_ratio",
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
 *  the bucket's snapshot month. The mov_std variant also matches k
 *  (signals are emitted only at the detection k, so buckets with other k
 *  stay unticked). hype is NOT matched — signals are not hype-separated. */
const IN_SIGNALS_RSI = `
  EXISTS (
    SELECT 1 FROM analysis_signals.signals s
    WHERE s.code = m.code
      AND s.sec_type = m.sec_type
      AND s.signal_type = 'mov_rsi'
      AND (s.params->>'rsi_window')::numeric = m.rsi_window
      AND (s.params->>'pct')::numeric = m.pct
      AND (s.params->>'side') = m.side
      AND (s.params->>'cooldown_days')::numeric = m.cooldown_days
      AND s.date >= m.stat_month
      AND s.date < m.stat_month + INTERVAL '1 month'
  )`;

const IN_SIGNALS_STD = `
  EXISTS (
    SELECT 1 FROM analysis_signals.signals s
    WHERE s.code = m.code
      AND s.sec_type = m.sec_type
      AND s.signal_type = 'mov_std'
      AND (s.params->>'ma_window')::numeric = m.ma_window
      AND (s.params->>'k')::numeric = m.k
      AND (s.params->>'side') = m.side
      AND (s.params->>'cooldown_days')::numeric = m.cooldown_days
      AND s.date >= m.stat_month
      AND s.date < m.stat_month + INTERVAL '1 month'
  )`;

const IN_SIGNALS_GAP = `
  EXISTS (
    SELECT 1 FROM analysis_signals.signals s
    WHERE s.code = m.code
      AND s.sec_type = m.sec_type
      AND s.signal_type = 'mov_gap'
      AND (s.params->>'gap_window')::numeric = m.gap_window
      AND (s.params->>'pct')::numeric = m.pct
      AND (s.params->>'side') = m.side
      AND (s.params->>'cooldown_days')::numeric = m.cooldown_days
      AND s.date >= m.stat_month
      AND s.date < m.stat_month + INTERVAL '1 month'
  )`;

/** px_vol variant: state cells have no cooldown and constant (recorded)
 *  thresholds, so the natural key is px_speed + vol_state + side (all
 *  text in params). flat rows never emit signals and stay unticked.
 *  hype is NOT matched — signals are not hype-separated. */
const IN_SIGNALS_PX_VOL = `
  EXISTS (
    SELECT 1 FROM analysis_signals.signals s
    WHERE s.code = m.code
      AND s.sec_type = m.sec_type
      AND s.signal_type = 'px_vol'
      AND (s.params->>'px_speed') = m.px_speed
      AND (s.params->>'vol_state') = m.vol_state
      AND (s.params->>'side') = m.side
      AND s.date >= m.stat_month
      AND s.date < m.stat_month + INTERVAL '1 month'
  )`;

/** margin_ratio variant: state cells have no cooldown and constant
 *  (recorded) z bars, so the natural key is ratio_state + side (all text
 *  in params). mid / no_buy rows never emit signals and stay unticked.
 *  hype is NOT matched — signals are not hype-separated. */
const IN_SIGNALS_MARGIN_RATIO = `
  EXISTS (
    SELECT 1 FROM analysis_signals.signals s
    WHERE s.code = m.code
      AND s.sec_type = m.sec_type
      AND s.signal_type = 'margin_ratio'
      AND (s.params->>'ratio_state') = m.ratio_state
      AND (s.params->>'side') = m.side
      AND s.date >= m.stat_month
      AND s.date < m.stat_month + INTERVAL '1 month'
  )`;

interface DbGapRow extends QueryResultRow {
  stat_month: Date | string;
  gap_window: number;
  side: string;
  pct: number;
  cooldown_days: number;
  is_market_hyped: boolean;
  in_signals: boolean;
  [k: string]: unknown;
}

interface DbRsiRow extends QueryResultRow {
  stat_month: Date | string;
  rsi_window: number;
  side: string;
  pct: number;
  cooldown_days: number;
  is_market_hyped: boolean;
  in_signals: boolean;
  [k: string]: unknown;
}

interface DbStdRow extends QueryResultRow {
  stat_month: Date | string;
  ma_window: number;
  k: number;
  side: string;
  cooldown_days: number;
  is_market_hyped: boolean;
  in_signals: boolean;
  mean_excess_close: number | null;
  mean_excess_max: number | null;
  max_excess_max: number | null;
  [k: string]: unknown;
}

interface DbPxVolRow extends QueryResultRow {
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
  stat_month: Date | string;
  ratio_state: string;
  side: string;
  is_market_hyped: boolean;
  in_signals: boolean;
  mean_ratio: number | null;
  mean_z: number | null;
  [k: string]: unknown;
}

/** Excess-magnitude metrics live in the linked forecast_results.config JSONB
 *  (duplicated across all 4 period rows per forecast_id) — extract them
 *  as float8. Since all 4 periods carry identical config, we cast to text
 *  (PG can MIN text, not jsonb), MIN, and cast back to jsonb for ->> access. */
const CONFIG_EXCESS_COLS = `
  NULLIF(MIN(f.config::text)::jsonb->>'mean_excess_close', '')::float8 AS mean_excess_close,
  NULLIF(MIN(f.config::text)::jsonb->>'mean_excess_max', '')::float8   AS mean_excess_max,
  NULLIF(MIN(f.config::text)::jsonb->>'max_excess_max', '')::float8    AS max_excess_max
`;

/** px_vol state magnitudes live in the linked forecast_results.config
 *  JSONB (duplicated across all 4 period rows per forecast_id) — same
 *  MIN(text) trick as CONFIG_EXCESS_COLS above. */
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

// ---- Column-to-field mapping (wide format — matches ForecastResultCols) ----
// The API response shape is unchanged from the old wide table: one row
// per bucket, all periods as period-suffixed columns.

function mapRsiRow(r: DbRsiRow): MovRsiForecastRow {
  return {
    stat_month: formatDate(r.stat_month),
    rsi_window: r.rsi_window,
    side: r.side as MovRsiForecastRow["side"],
    pct: r.pct,
    cooldown_days: r.cooldown_days,
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
    stat_month: formatDate(r.stat_month),
    gap_window: r.gap_window,
    side: r.side as MovGapForecastRow["side"],
    pct: r.pct,
    cooldown_days: r.cooldown_days,
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
    stat_month: formatDate(r.stat_month),
    ma_window: r.ma_window,
    k: toNum(r.k) ?? 0,
    side: r.side as MovStdForecastRow["side"],
    cooldown_days: r.cooldown_days,
    is_market_hyped: r.is_market_hyped === true,
    in_signals: r.in_signals === true,
    mean_excess_close: toNum(r.mean_excess_close),
    mean_excess_max: toNum(r.mean_excess_max),
    max_excess_max: toNum(r.max_excess_max),
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
      `Invalid kind: ${kind}. Expected 'mov_rsi', 'mov_std', 'mov_gap', 'px_vol', or 'margin_ratio'.`,
    );
  }
  const m = (month ?? "").trim() || null;

  // Bucket table per kind (px_vol / margin_ratio motivation tables are
  // the *_state tables).
  const TABLES: Record<string, string> = {
    mov_rsi: "mov_rsi",
    mov_std: "mov_std",
    mov_gap: "mov_gap",
    px_vol: "px_vol_state",
    margin_ratio: "margin_ratio_state",
  };

  const monthsRows = await queryRows<{ stat_month: Date | string }>(
    `SELECT DISTINCT stat_month
     FROM analysis_forecasts.${TABLES[k]}
     WHERE sec_type = $1 AND code = $2
     ORDER BY stat_month DESC`,
    [st, code],
  );
  const months = monthsRows.map((r) => formatDate(r.stat_month));

  if (k === "mov_rsi") {
    const rows = await queryRows<DbRsiRow>(
      `
      SELECT m.stat_month,
             m.rsi_window,
             m.side,
             m.pct,
             m.cooldown_days,
             m.is_market_hyped,
             ${IN_SIGNALS_RSI} AS in_signals,
             ${PIVOT_COLS}
      FROM analysis_forecasts.mov_rsi m
      JOIN analysis_forecasts.forecast_results f
        ON f.forecast_id = m.forecast_id
      WHERE m.sec_type = $1 AND m.code = $2
        ${m ? "AND m.stat_month >= $3::date" : ""}
      GROUP BY m.stat_month, m.rsi_window, m.side, m.pct,
               m.cooldown_days, m.is_market_hyped, m.code, m.sec_type
      ORDER BY m.stat_month DESC, m.rsi_window ASC, m.side ASC, m.pct ASC,
               m.cooldown_days ASC, m.is_market_hyped ASC
      `,
      m ? [st, code, m] : [st, code],
    );
    const mapped = rows.map(mapRsiRow);
    return { kind: "mov_rsi", code, sec_type: st, months, rows: mapped, enable_filters: false };
  }

  if (k === "mov_gap") {
    const rows = await queryRows<DbGapRow>(
      `
      SELECT m.stat_month,
             m.gap_window,
             m.side,
             m.pct,
             m.cooldown_days,
             m.is_market_hyped,
             ${IN_SIGNALS_GAP} AS in_signals,
             ${PIVOT_COLS}
      FROM analysis_forecasts.mov_gap m
      JOIN analysis_forecasts.forecast_results f
        ON f.forecast_id = m.forecast_id
      WHERE m.sec_type = $1 AND m.code = $2
        ${m ? "AND m.stat_month >= $3::date" : ""}
      GROUP BY m.stat_month, m.gap_window, m.side, m.pct,
               m.cooldown_days, m.is_market_hyped, m.code, m.sec_type
      ORDER BY m.stat_month DESC, m.gap_window ASC, m.side ASC, m.pct ASC,
               m.cooldown_days ASC, m.is_market_hyped ASC
      `,
      m ? [st, code, m] : [st, code],
    );
    const mapped = rows.map(mapGapRow);
    return { kind: "mov_gap", code, sec_type: st, months, rows: mapped, enable_filters: false };
  }

  if (k === "px_vol") {
    const rows = await queryRows<DbPxVolRow>(
      `
      SELECT m.stat_month,
             m.px_speed,
             m.vol_state,
             m.side,
             m.is_market_hyped,
             ${IN_SIGNALS_PX_VOL} AS in_signals,
             ${CONFIG_PX_VOL_COLS},
             ${PIVOT_COLS}
      FROM analysis_forecasts.px_vol_state m
      JOIN analysis_forecasts.forecast_results f
        ON f.forecast_id = m.forecast_id
      WHERE m.sec_type = $1 AND m.code = $2
        ${m ? "AND m.stat_month >= $3::date" : ""}
      GROUP BY m.stat_month, m.px_speed, m.vol_state, m.side,
               m.is_market_hyped, m.code, m.sec_type
      ORDER BY m.stat_month DESC,
               CASE m.px_speed WHEN 'sharp_up' THEN 1 WHEN 'slow_up' THEN 2
                               WHEN 'flat' THEN 3 WHEN 'slow_dn' THEN 4
                               ELSE 5 END ASC,
               CASE m.vol_state WHEN 'heavy' THEN 1 WHEN 'normal' THEN 2
                                ELSE 3 END ASC,
               m.is_market_hyped ASC
      `,
      m ? [st, code, m] : [st, code],
    );
    const mapped = rows.map(mapPxVolRow);
    return { kind: "px_vol", code, sec_type: st, months, rows: mapped, enable_filters: false };
  }

  if (k === "margin_ratio") {
    const rows = await queryRows<DbMarginRatioRow>(
      `
      SELECT m.stat_month,
             m.ratio_state,
             m.side,
             m.is_market_hyped,
             ${IN_SIGNALS_MARGIN_RATIO} AS in_signals,
             ${CONFIG_MARGIN_RATIO_COLS},
             ${PIVOT_COLS}
      FROM analysis_forecasts.margin_ratio_state m
      JOIN analysis_forecasts.forecast_results f
        ON f.forecast_id = m.forecast_id
      WHERE m.sec_type = $1 AND m.code = $2
        ${m ? "AND m.stat_month >= $3::date" : ""}
      GROUP BY m.stat_month, m.ratio_state, m.side,
               m.is_market_hyped, m.code, m.sec_type
      ORDER BY m.stat_month DESC,
               CASE m.ratio_state WHEN 'vlow' THEN 1 WHEN 'low' THEN 2
                                  WHEN 'mid' THEN 3 WHEN 'high' THEN 4
                                  WHEN 'vhigh' THEN 5 ELSE 6 END ASC,
               m.is_market_hyped ASC
      `,
      m ? [st, code, m] : [st, code],
    );
    const mapped = rows.map(mapMarginRatioRow);
    return { kind: "margin_ratio", code, sec_type: st, months, rows: mapped, enable_filters: false };
  }

  // kind === "mov_std"
  const rows = await queryRows<DbStdRow>(
    `
    SELECT m.stat_month,
           m.ma_window,
           m.k::float8 AS k,
           m.side,
           m.cooldown_days,
           m.is_market_hyped,
           ${IN_SIGNALS_STD} AS in_signals,
           ${CONFIG_EXCESS_COLS},
           ${PIVOT_COLS}
    FROM analysis_forecasts.mov_std m
    JOIN analysis_forecasts.forecast_results f
      ON f.forecast_id = m.forecast_id
    WHERE m.sec_type = $1 AND m.code = $2
      ${m ? "AND m.stat_month >= $3::date" : ""}
    GROUP BY m.stat_month, m.ma_window, m.k, m.side,
             m.cooldown_days, m.is_market_hyped, m.code, m.sec_type
    ORDER BY m.stat_month DESC, m.ma_window ASC, m.k ASC, m.side ASC,
             m.cooldown_days ASC, m.is_market_hyped ASC
    `,
    m ? [st, code, m] : [st, code],
  );
  const mapped = rows.map(mapStdRow);
  return { kind: "mov_std", code, sec_type: st, months, rows: mapped, enable_filters: false };
}
