/**
 * Cache version service — returns the latest (MAX) date for each major data
 * source in a single query.  Used by the frontend to decide whether cached
 * data is stale (DB has newer rows than what the UI currently holds).
 *
 * A single round-trip fetches MAX(date) for all 6 sources:
 *   debt, etf_margin, index_daily, options, sec_composition, stock_daily
 */
import { queryRows, formatDate } from "../lib/db.js";
import type { QueryResultRow } from "pg";
import type { LatestDatesResponse } from "../../shared/types.js";

interface DbLatestDatesRow extends QueryResultRow {
  debt: Date | string | null;
  etf_margin: Date | string | null;
  index_daily: Date | string | null;
  options: Date | string | null;
  sec_composition: Date | string | null;
  stock_daily: Date | string | null;
  intraday_movements: Date | string | null;
}

/**
 * Fetch MAX(date) across all data sources in one query.
 * Returns "" for any source that is empty or has no rows.
 */
export async function getLatestDates(): Promise<LatestDatesResponse> {
  // MAX(date) is taken from each family's IDENTITY table, not the
  // reconstructed *_daily views: every one of those views is a LEFT-JOIN
  // fan-out whose date column comes from the identity table anyway, so
  // this is row-for-row the same MAX at a fraction of the cost (the view
  // variant joined+sorted ~30M rows across families, ~1.8s per probe).
  const sql = `
    SELECT
      (SELECT MAX(date)          FROM stats.debt_identity)    AS debt,
      (SELECT MAX(date)          FROM stats.etf_identity)     AS etf_margin,
      (SELECT MAX(date)          FROM stats.index_identity)   AS index_daily,
      (SELECT MAX(date)          FROM stats.options_identity) AS options,
      (SELECT MAX(snapshot_date) FROM stats.sec_composition)  AS sec_composition,
      (SELECT MAX(date)          FROM stats.stock_identity)   AS stock_daily,
      (SELECT MAX(date) FROM stats.index_intraday_5min WHERE close IS NOT NULL) AS intraday_movements
  `;
  const rows = await queryRows<DbLatestDatesRow>(sql);
  const r = rows[0];
  return {
    debt:           formatDate(r?.debt),
    etf_margin:     formatDate(r?.etf_margin),
    index_daily: formatDate(r?.index_daily),
    options:        formatDate(r?.options),
    sec_composition: formatDate(r?.sec_composition),
    stock_daily: formatDate(r?.stock_daily),
    intraday_movements: formatDate(r?.intraday_movements),
  };
}
