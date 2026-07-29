/**
 * Cache version service — returns the latest (MAX) date for each major data
 * source in a single query.  Used by the frontend to decide whether cached
 * data is stale (DB has newer rows than what the UI currently holds).
 *
 * A single round-trip fetches MAX(date) for all 6 sources:
 *   debt, etf_margin, index_baseline, options, sec_composition, stock_baseline
 */
import { queryRows, formatDate } from "../lib/db.js";
import type { QueryResultRow } from "pg";
import type { LatestDatesResponse } from "../../shared/types.js";

interface DbLatestDatesRow extends QueryResultRow {
  debt: Date | string | null;
  etf_margin: Date | string | null;
  index_baseline: Date | string | null;
  options: Date | string | null;
  sec_composition: Date | string | null;
  stock_baseline: Date | string | null;
}

/**
 * Fetch MAX(date) across all data sources in one query.
 * Returns "" for any source that is empty or has no rows.
 */
export async function getLatestDates(): Promise<LatestDatesResponse> {
  const sql = `
    SELECT
      (SELECT MAX(date)          FROM stats.v_debt_baseline)  AS debt,
      (SELECT MAX(date)          FROM stats.v_etf_margin)     AS etf_margin,
      (SELECT MAX(date)          FROM stats.v_index_baseline) AS index_baseline,
      (SELECT MAX(date)          FROM stats.v_options_quote)  AS options,
      (SELECT MAX(snapshot_date) FROM stats.sec_composition)  AS sec_composition,
      (SELECT MAX(date)          FROM stats.v_stock_baseline) AS stock_baseline
  `;
  const rows = await queryRows<DbLatestDatesRow>(sql);
  const r = rows[0];
  return {
    debt:           formatDate(r?.debt),
    etf_margin:     formatDate(r?.etf_margin),
    index_baseline: formatDate(r?.index_baseline),
    options:        formatDate(r?.options),
    sec_composition: formatDate(r?.sec_composition),
    stock_baseline: formatDate(r?.stock_baseline),
  };
}
