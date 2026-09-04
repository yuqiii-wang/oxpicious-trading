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
  ForecastKind,
  ForecastResponse,
  MovGapForecastRow,
  MovRsiForecastRow,
  MovStdForecastRow,
} from "../../../shared/types.js";

const VALID_KINDS: ReadonlySet<string> = new Set([
  "mov_rsi", "mov_std", "mov_gap",
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

/** Excess-magnitude metrics live in the linked forecast_results.config JSONB
 *  (duplicated across all 4 period rows per forecast_id) — extract them
 *  as float8. Since all 4 periods carry identical config, we cast to text
 *  (PG can MIN text, not jsonb), MIN, and cast back to jsonb for ->> access. */
const CONFIG_EXCESS_COLS = `
  NULLIF(MIN(f.config::text)::jsonb->>'mean_excess_close', '')::float8 AS mean_excess_close,
  NULLIF(MIN(f.config::text)::jsonb->>'mean_excess_max', '')::float8   AS mean_excess_max,
  NULLIF(MIN(f.config::text)::jsonb->>'max_excess_max', '')::float8    AS max_excess_max
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
      `Invalid kind: ${kind}. Expected 'mov_rsi', 'mov_std', or 'mov_gap'.`,
    );
  }
  const m = (month ?? "").trim() || null;

  const monthsRows = await queryRows<{ stat_month: Date | string }>(
    `SELECT DISTINCT stat_month
     FROM analysis_forecasts.${k}
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
    return { kind: "mov_rsi", code, sec_type: st, months, rows: mapped };
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
    return { kind: "mov_gap", code, sec_type: st, months, rows: mapped };
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
  return { kind: "mov_std", code, sec_type: st, months, rows: mapped };
}
