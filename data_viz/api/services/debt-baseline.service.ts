/**
 * Debt Baseline service — queries stats.v_debt_baseline view with date-range
 * filtering pushed down to the database (index-driven WHERE clause).
 *
 * The view joins all debt sub-tables (debt_identity + debt_omo + debt_repo +
 * debt_outright_repo + debt_mlf + debt_shibor + debt_treasury) so each query
 * returns a single row per trading day with all metrics.
 */
import { query, queryRows, toDateParam, formatDate, toNum } from "../lib/db.js";
import type { QueryResultRow } from "pg";
import type { DebtBaselineRow, DebtBaselineResponse } from "../../shared/types.js";

export interface DebtBaselineQuery {
  start_date?: string;
  end_date?: string;
}

interface DbDebtRow extends QueryResultRow {
  date: Date | string;
  omo_rate: number | null;
  omo_quantity: number | null;
  omo_tenor_days: number | null;
  omo_tenor_label: string | null;
  repo_start_quantity: number | null;
  repo_end_quantity: number | null;
  repo_net_injection: number | null;
  repo_cumulative: number | null;
  outright_repo_marker: number | null;
  outright_repo_quantity: number | null;
  outright_repo_tenor_days: number | null;
  outright_repo_tenor_label: string | null;
  outright_repo_serial: string | null;
  mlf_marker: number | null;
  mlf_quantity: number | null;
  mlf_tenor_days: number | null;
  mlf_tenor_label: string | null;
  mlf_serial: string | null;
  shibor_o_n: number | null;
  shibor_1w: number | null;
  shibor_1m: number | null;
  shibor_3m: number | null;
  shibor_6m: number | null;
  shibor_1y: number | null;
  cb_1y: number | null;
  cb_5y: number | null;
  cb_10y: number | null;
  cb_30y: number | null;
}

function transformRow(r: DbDebtRow): DebtBaselineRow {
  return {
    date: formatDate(r.date),
    omo_rate: toNum(r.omo_rate),
    omo_quantity: toNum(r.omo_quantity),
    omo_tenor_days: toNum(r.omo_tenor_days),
    omo_tenor_label: r.omo_tenor_label ?? "",
    repo_start_quantity: toNum(r.repo_start_quantity) ?? 0,
    repo_end_quantity: toNum(r.repo_end_quantity) ?? 0,
    repo_net_injection: toNum(r.repo_net_injection) ?? 0,
    repo_cumulative: toNum(r.repo_cumulative) ?? 0,
    outright_repo_marker: (toNum(r.outright_repo_marker) ?? 0) as 0 | 1,
    outright_repo_quantity: toNum(r.outright_repo_quantity),
    outright_repo_tenor_days: toNum(r.outright_repo_tenor_days),
    outright_repo_tenor_label: r.outright_repo_tenor_label ?? "",
    outright_repo_serial: r.outright_repo_serial ?? "",
    mlf_marker: (toNum(r.mlf_marker) ?? 0) as 0 | 1,
    mlf_quantity: toNum(r.mlf_quantity),
    mlf_tenor_days: toNum(r.mlf_tenor_days),
    mlf_tenor_label: r.mlf_tenor_label ?? "",
    mlf_serial: r.mlf_serial ?? "",
    shibor_o_n: toNum(r.shibor_o_n),
    shibor_1w: toNum(r.shibor_1w),
    shibor_1m: toNum(r.shibor_1m),
    shibor_3m: toNum(r.shibor_3m),
    shibor_6m: toNum(r.shibor_6m),
    shibor_1y: toNum(r.shibor_1y),
    cb_1y: toNum(r.cb_1y),
    cb_5y: toNum(r.cb_5y),
    cb_10y: toNum(r.cb_10y),
    cb_30y: toNum(r.cb_30y),
  };
}

// Columns selected from v_debt_baseline — kept in sync with transformRow.
const SELECT_COLUMNS = `
  date,
  omo_rate, omo_quantity, omo_tenor_days, omo_tenor_label,
  repo_start_quantity, repo_end_quantity, repo_net_injection, repo_cumulative,
  outright_repo_marker, outright_repo_quantity, outright_repo_tenor_days,
  outright_repo_tenor_label, outright_repo_serial,
  mlf_marker, mlf_quantity, mlf_tenor_days, mlf_tenor_label, mlf_serial,
  shibor_o_n, shibor_1w, shibor_1m, shibor_3m, shibor_6m, shibor_1y,
  cb_1y, cb_5y, cb_10y, cb_30y
`;

export async function getDebtBaseline(
  query_in: DebtBaselineQuery,
): Promise<DebtBaselineResponse> {
  const params: unknown[] = [];
  const where: string[] = [];
  let i = 1;

  const startDate = toDateParam(query_in.start_date);
  const endDate = toDateParam(query_in.end_date);

  if (startDate) {
    where.push(`date >= $${i++}::date`);
    params.push(startDate);
  }
  if (endDate) {
    where.push(`date <= $${i++}::date`);
    params.push(endDate);
  }

  const whereClause = where.length > 0 ? `WHERE ${where.join(" AND ")}` : "";

  // Fetch sorted rows
  const sql = `
    SELECT ${SELECT_COLUMNS}
    FROM stats.v_debt_baseline
    ${whereClause}
    ORDER BY date ASC
  `;
  const rows = await queryRows<DbDebtRow>(sql, params);

  if (rows.length === 0) {
    return { dates: [], rows: [], minDate: "", maxDate: "" };
  }

  // Also fetch global min/max dates for the full table (not just filtered)
  // so the frontend can show the complete date range.
  const minMax = await query<{ min_date: Date; max_date: Date }>(
    `SELECT MIN(date) AS min_date, MAX(date) AS max_date FROM stats.v_debt_baseline`,
  );
  const minDate = formatDate(minMax.rows[0]?.min_date);
  const maxDate = formatDate(minMax.rows[0]?.max_date);

  const transformed = rows.map(transformRow);
  const dates = transformed.map((r) => r.date);

  return { dates, rows: transformed, minDate, maxDate };
}
