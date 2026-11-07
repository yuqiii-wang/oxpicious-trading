/**
 * Cache version service — returns the latest (MAX) date for each major data
 * source in a single query.  Used by the frontend to decide whether cached
 * data is stale (DB has newer rows than what the UI currently holds).
 *
 * A single round-trip fetches MAX(date) for all 6 sources:
 *   debt, etf_margin, index_daily, options, sec_composition, stock_daily
 */
import { formatDate } from "../lib/db.js";
import { querySqlView } from "../lib/sql-views.js";
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
  // The SELECT lives in the sql view
  // data_viz/api/services/cache/latest_dates.
  const rows = await querySqlView<DbLatestDatesRow>(
    "data_viz/api/services/cache/latest_dates",
  );
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
