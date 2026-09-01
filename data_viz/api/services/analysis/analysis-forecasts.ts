/**
 * Forecast buckets (analysis_forecasts schema) — serves the MA-Spread
 * panel's second plot: a config→result table beneath the spread chart.
 *
 *  getForecastTable(secType, code, kind, month?)
 *    kind = "mov_rsi" → analysis_forecasts.mov_rsi ⋈ forecast_results
 *      one row per (stat_month, rsi_window, side, pct) bucket: bucket keys
 *      + is_market_hyped + the linked forecast_results columns (mean
 *      forward changes at the next-day/5d/20d/60d horizons; close-based
 *      max/min forward changes + within-window close swing amplitude
 *      max_low_change_ratio at the 5d/20d/60d horizons; per-horizon >1%
 *      reversal probabilities).
 *    kind = "mov_std" → analysis_forecasts.mov_std ⋈ forecast_results
 *      one row per (stat_month, ma_window, k, side, is_market_hyped)
 *      Bollinger-breach bucket, additionally carrying mean_excess_close /
 *      mean_excess_max / max_excess_max.
 *
 *  The underlying indicator values are NOT stored in the mov tables —
 *  rsi_{W}days lives in analysis.mov_ave_rsi and ma/std in
 *  analysis.mov_ave_spreads_detail + stats.*_tech_stats; this endpoint
 *  only surfaces the bucket config + motivation + result columns.
 *
 *  When `month` ("YYYY-MM-DD") is omitted, the rows are the LATEST
 *  stat_month for the code; when given, only that stat_month's rows. The
 *  response also carries `months` — every distinct stat_month available for
 *  the code (DESC) — so the UI can render the month selector.
 */
import { queryRows, formatDate, toNum } from "../../lib/db.js";
import type { QueryResultRow } from "pg";
import type {
  ForecastKind,
  ForecastResponse,
  MovRsiForecastRow,
  MovStdForecastRow,
} from "../../../shared/types.js";

const VALID_KINDS: ReadonlySet<string> = new Set(["mov_rsi", "mov_std"]);

const RESULT_COLS = `
  f.ave_next_change::float8     AS ave_next_change,
  f.ave_next_5d_change::float8  AS ave_next_5d_change,
  f.ave_next_20d_change::float8 AS ave_next_20d_change,
  f.ave_next_60d_change::float8 AS ave_next_60d_change,
  f.max_5d_change::float8  AS max_5d_change,
  f.max_20d_change::float8 AS max_20d_change,
  f.max_60d_change::float8 AS max_60d_change,
  f.min_5d_change::float8  AS min_5d_change,
  f.min_20d_change::float8 AS min_20d_change,
  f.min_60d_change::float8 AS min_60d_change,
  f.max_low_change_ratio_5d::float8  AS max_low_change_ratio_5d,
  f.max_low_change_ratio_20d::float8 AS max_low_change_ratio_20d,
  f.max_low_change_ratio_60d::float8 AS max_low_change_ratio_60d,
  f.reverse_prob::float8     AS reverse_prob,
  f.reverse_prob_5d::float8  AS reverse_prob_5d,
  f.reverse_prob_20d::float8 AS reverse_prob_20d,
  f.reverse_prob_60d::float8 AS reverse_prob_60d,
  f.occurrence_count_next::float8 AS occurrence_count_next,
  f.occurrence_count_5d::float8  AS occurrence_count_5d,
  f.occurrence_count_20d::float8 AS occurrence_count_20d,
  f.occurrence_count_60d::float8 AS occurrence_count_60d
`;

interface DbRsiRow extends QueryResultRow {
  stat_month: Date | string;
  rsi_window: number;
  side: string;
  pct: number;
  is_market_hyped: boolean;
  [k: string]: unknown;
}

interface DbStdRow extends QueryResultRow {
  stat_month: Date | string;
  ma_window: number;
  k: number;
  side: string;
  is_market_hyped: boolean;
  mean_excess_close: number | null;
  mean_excess_max: number | null;
  max_excess_max: number | null;
  [k: string]: unknown;
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
    throw new Error(`Invalid kind: ${kind}. Expected 'mov_rsi' or 'mov_std'.`);
  }
  // Optional stat_month filter ("YYYY-MM-DD" as returned by the months
  // list). Empty → the latest stat_month for the code.
  const m = (month ?? "").trim() || null;

  const monthsRows = await queryRows<{ stat_month: Date | string }>(
    `SELECT DISTINCT stat_month
     FROM analysis_forecasts.${k === "mov_rsi" ? "mov_rsi" : "mov_std"}
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
             m.is_market_hyped,
             ${RESULT_COLS}
      FROM analysis_forecasts.mov_rsi m
      JOIN analysis_forecasts.forecast_results f
        ON f.forecast_id = m.forecast_id
      WHERE m.sec_type = $1 AND m.code = $2
        ${m ? "AND m.stat_month = $3::date" : "AND m.stat_month = (SELECT max(stat_month) FROM analysis_forecasts.mov_rsi WHERE sec_type = $1 AND code = $2)"}
      ORDER BY m.stat_month DESC, m.rsi_window ASC, m.side ASC, m.pct ASC,
               m.is_market_hyped ASC
      `,
      m ? [st, code, m] : [st, code],
    );
    const mapped: MovRsiForecastRow[] = rows.map((r) => ({
      stat_month: formatDate(r.stat_month),
      rsi_window: r.rsi_window,
      side: r.side as MovRsiForecastRow["side"],
      pct: r.pct,
      is_market_hyped: r.is_market_hyped === true,
      ave_next_change: toNum(r.ave_next_change),
      ave_next_5d_change: toNum(r.ave_next_5d_change),
      ave_next_20d_change: toNum(r.ave_next_20d_change),
      ave_next_60d_change: toNum(r.ave_next_60d_change),
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
    }));
    return { kind: "mov_rsi", code, sec_type: st, months, rows: mapped };
  }

  // kind === "mov_std"
  const rows = await queryRows<DbStdRow>(
    `
    SELECT m.stat_month,
           m.ma_window,
           m.k::float8 AS k,
           m.side,
           m.is_market_hyped,
           m.mean_excess_close::float8 AS mean_excess_close,
           m.mean_excess_max::float8   AS mean_excess_max,
           m.max_excess_max::float8    AS max_excess_max,
           ${RESULT_COLS}
    FROM analysis_forecasts.mov_std m
    JOIN analysis_forecasts.forecast_results f
      ON f.forecast_id = m.forecast_id
    WHERE m.sec_type = $1 AND m.code = $2
      ${m ? "AND m.stat_month = $3::date" : "AND m.stat_month = (SELECT max(stat_month) FROM analysis_forecasts.mov_std WHERE sec_type = $1 AND code = $2)"}
    ORDER BY m.stat_month DESC, m.ma_window ASC, m.k ASC, m.side ASC,
             m.is_market_hyped ASC
    `,
    m ? [st, code, m] : [st, code],
  );
  const mapped: MovStdForecastRow[] = rows.map((r) => ({
    stat_month: formatDate(r.stat_month),
    ma_window: r.ma_window,
    k: toNum(r.k) ?? 0,
    side: r.side as MovStdForecastRow["side"],
    is_market_hyped: r.is_market_hyped === true,
    mean_excess_close: toNum(r.mean_excess_close),
    mean_excess_max: toNum(r.mean_excess_max),
    max_excess_max: toNum(r.max_excess_max),
    ave_next_change: toNum(r.ave_next_change),
    ave_next_5d_change: toNum(r.ave_next_5d_change),
    ave_next_20d_change: toNum(r.ave_next_20d_change),
    ave_next_60d_change: toNum(r.ave_next_60d_change),
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
  }));
  return { kind: "mov_std", code, sec_type: st, months, rows: mapped };
}
