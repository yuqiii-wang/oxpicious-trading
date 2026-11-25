/**
 * Macro service — queries stats.v_macro_daily with date-range filtering
 * pushed down to the database (index-driven WHERE clause).
 *
 * The view joins macro_identity + macro_basic_stats so each query returns
 * one row per (code, date) — currently the precious-metal family: SGE
 * Au99.99/Ag99.99 spot (CNY) + COMEX GC/SI main continuous (USD), loaded
 * by builds.macro. Units ride macro_identity.unit (never converted).
 */
import { query, queryRows, toDateParam, formatDate, toNum } from "../../lib/db.js";
import { renderSqlView, sqlFrag } from "../../lib/sql-views.js";
import { labelDecodeSql } from "../infra/label-codes.js";
import type { QueryResultRow } from "pg";
import type {
  MacroDailyRow,
  MacroDailyResponse,
} from "../../../shared/types.js";

export interface MacroDailyQuery {
  start_date?: string;
  end_date?: string;
}

interface DbMacroRow extends QueryResultRow {
  date: Date | string;
  code: string;
  name: string;
  metal_type: number | null;
  metal_label: string | null;
  unit: string | null;
  source: string | null;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  change: number | null;
  change_pct: number | null;
  volume: number | null;
}

function transformRow(r: DbMacroRow): MacroDailyRow {
  return {
    date: formatDate(r.date),
    code: r.code ?? "",
    name: r.name ?? "",
    metal_label: r.metal_label ?? "",
    unit: r.unit ?? "",
    source: r.source ?? "",
    open: toNum(r.open),
    high: toNum(r.high),
    low: toNum(r.low),
    close: toNum(r.close),
    change: toNum(r.change),
    change_pct: toNum(r.change_pct),
    volume: toNum(r.volume),
  };
}

// Columns selected from v_macro_daily — kept in sync with transformRow.
// metal_type decodes through the label index (macro_metal domain) — the
// code column rides along for the UI's per-metal grouping.
const SELECT_COLUMNS = `
  date,
  code,
  name,
  metal_type,
  ${labelDecodeSql("macro_metal", "metal_type")} AS metal_label,
  unit,
  source,
  open, high, low, close,
  change, change_pct,
  volume
`;

export async function getMacroDaily(
  query_in: MacroDailyQuery,
): Promise<MacroDailyResponse> {
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

  // Fetch sorted rows — the SELECT lives in the sql view
  // data_viz/api/services/macro/daily (select_columns + where_clause are
  // fragment args; the date params stay positional $1/$2 inside the
  // where_clause fragment).
  const { sql } = renderSqlView("data_viz/api/services/macro/daily", {
    select_columns: sqlFrag(SELECT_COLUMNS),
    where_clause: sqlFrag(whereClause),
  });
  const rows = await queryRows<DbMacroRow>(sql, params);

  if (rows.length === 0) {
    return { dates: [], rows: [], minDate: "", maxDate: "" };
  }

  // Also fetch global min/max dates for the full table (not just filtered)
  // so the frontend can show the complete date range.
  const minMax = await query<{ min_date: Date; max_date: Date }>(
    renderSqlView("data_viz/api/services/macro/date_range").sql,
  );
  const minDate = formatDate(minMax.rows[0]?.min_date);
  const maxDate = formatDate(minMax.rows[0]?.max_date);

  const transformed = rows.map(transformRow);
  // One row per (code, date) — the date axis is the unique sorted set.
  const dates = [...new Set(transformed.map((r) => r.date))].sort();

  return { dates, rows: transformed, minDate, maxDate };
}
