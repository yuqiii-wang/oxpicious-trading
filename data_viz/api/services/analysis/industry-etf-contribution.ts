/**
 * Industry ETF Contribution — multi-ETF price series (1st plot) + per-ETF
 * contribution bars (2nd+ plots) for the "ETF Contribution" view on the
 * Market Beta page.
 *
 * Mirrors the benchmark-price-chart + industry-benchmark-attribution pattern,
 * but replaces benchmark-indices with ETFs as the unit of analysis:
 *   • 1st plot: multi-ETF daily close series (frontend rebases each ETF to 100
 *     at its own first date, cascading so later ETFs start at the mean).
 *   • 2nd+ plots: per-industry bar chart — each bar = one ETF's trading amount
 *     + % share of the industry total.
 *
 * Source tables:
 *   stats.etf_basic_stats.close             (ETF daily close)
 *   stats.etf_liquidity_margin.trading_amount (ETF daily turnover, yuan)
 *   stats.sec_classification                (ETF→parent_index_code→industry_id)
 *   analysis.industry_etf_contribution      (industry aggregate, pool_size='all')
 */
import { queryRows, formatDate, toNum } from "../../lib/db.js";
import { renderSqlView, querySqlView, sqlFrag } from "@api/lib/sql-views";
import type { QueryResultRow } from "pg";
import type {
  IndustryEtfPriceSeriesResponse,
  IndustryEtfPriceSeriesEntry,
  IndustryEtfContributionBarsResponse,
  IndustryEtfContributionBarRow,
} from "../../../shared/types.js";

// ----------------------------------------------------------------------------
//  getIndustryEtfPriceSeries — returns the daily close series for ALL ETFs
//  tracking member indices of the user-selected industries. Drives the 1st
//  plot in "ETF Contribution" mode: a multi-line chart where each line is one
//  ETF, rebased to 100 at its own first available date (cascading rebasing
//  handled client-side). The chart is clickable to pick the as-of date for
//  the per-industry bar charts below.
//
//  ETF discovery: stats.sec_classification WHERE type='etf' AND
//  parent_index_code IN (member indices of selected industries where
//  type='index').
//  Source: stats.etf_basic_stats (close) LEFT JOIN stats.etf_adjustment
//  (adj_close) — price = COALESCE(adj_close, close) so split / dividend
//  deliveries don't show up as phantom large drops. Joined with etf_identity
//  for name.
// ----------------------------------------------------------------------------

interface DbEtfPriceRow extends QueryResultRow {
  etf_code: string;
  etf_name: string | null;
  parent_index_code: string;
  industry_id: string;
  industry_label: string | null;
  date: Date | string;
  close: number | null;
  trading_amount: number | null;
}

export async function getIndustryEtfPriceSeries(
  rawIndustryIds: string[],
): Promise<IndustryEtfPriceSeriesResponse> {
  const industryIds = (rawIndustryIds ?? [])
    .map((s) => (s ?? "").trim())
    .filter((s) => s.length > 0);
  if (industryIds.length === 0) {
    throw new Error("Missing 'industry_ids' parameter");
  }

  // Fetch all ETF rows (code, name, parent_index, industry, date, close) for
  // ETFs tracking member indices of the selected industries. Ordered by
  // etf_code then date so the frontend can group-by ETF and process each
  // ETF's series in chronological order. Price uses COALESCE(adj_close, close)
  // so share splits / dividend deliveries don't appear as large drops. The
  // SELECT lives in the sql view
  // data_viz/api/services/analysis/industry-etf-contribution/etf_price_series.
  const rows = await querySqlView<DbEtfPriceRow>(
    "data_viz/api/services/analysis/industry-etf-contribution/etf_price_series",
    { industry_ids: industryIds },
  );

  // Group rows by etf_code into IndustryEtfPriceSeriesEntry[].
  const entryMap = new Map<string, IndustryEtfPriceSeriesEntry>();
  for (const r of rows) {
    let entry = entryMap.get(r.etf_code);
    if (!entry) {
      entry = {
        etf_code: r.etf_code,
        etf_name: r.etf_name ?? r.etf_code,
        parent_index_code: r.parent_index_code,
        industry_id: r.industry_id,
        industry_label: r.industry_label ?? r.industry_id,
        rows: [],
      };
      entryMap.set(r.etf_code, entry);
    }
    entry.rows.push({
      date: formatDate(r.date),
      close: toNum(r.close),
      trading_amount: toNum(r.trading_amount),
    });
  }

  return {
    industry_ids: industryIds,
    etfs: Array.from(entryMap.values()),
  };
}

// ----------------------------------------------------------------------------
//  getIndustryEtfContributionBars — returns one row per ETF for a given
//  (industry_id, date). Each row carries the ETF's trading_amount (capital
//  flow) and fractional daily return. Also returns the industry aggregate
//  from analysis.industry_etf_contribution (pool_size='all') for context.
//
//  Drives the 2nd+ plots in "ETF Contribution" mode: one bar chart per
//  selected industry, each bar = one ETF.
//
//  Source: stats.etf_liquidity_margin (trading_amount), stats.etf_basic_stats
//  (close for return computation), stats.sec_classification (ETF→industry
//  linkage), analysis.industry_etf_contribution (industry aggregate).
// ----------------------------------------------------------------------------

interface DbEtfBarRow extends QueryResultRow {
  etf_code: string;
  etf_name: string | null;
  parent_index_code: string;
  trading_amount: number | null;
  etf_return: number | null;
  date: Date | string;
  industry_label: string | null;
  industry_etf_trading_amount: number | null;
  industry_etf_trading_amount_ma5: number | null;
  industry_etf_trading_amount_ma20: number | null;
}

export async function getIndustryEtfContributionBars(
  rawIndustryId: string,
  rawDate?: string | null,
): Promise<IndustryEtfContributionBarsResponse> {
  const industryId = (rawIndustryId ?? "").trim();
  if (!industryId) {
    throw new Error("Missing 'industry_id' parameter");
  }

  // Target date: user-provided, or the latest date in
  // analysis.industry_etf_contribution for this industry. The SELECT lives
  // in the sql view
  // data_viz/api/services/analysis/industry-etf-contribution/contribution_bars.
  // The date placeholder is a fragment because the original SQL numbered
  // it as the LAST pg param while it appears FIRST in the text; the
  // params array is bound here ([industry_id, target_date]).
  const { sql } = renderSqlView(
    "data_viz/api/services/analysis/industry-etf-contribution/contribution_bars",
    { industry_id: industryId, target_date_param: sqlFrag("$2::date") },
  );
  const rows = await queryRows<DbEtfBarRow>(sql, [industryId, rawDate ?? null]);

  const etfs: IndustryEtfContributionBarRow[] = rows.map((r) => ({
    etf_code: r.etf_code,
    etf_name: r.etf_name ?? r.etf_code,
    parent_index_code: r.parent_index_code,
    trading_amount: toNum(r.trading_amount),
    etf_return: toNum(r.etf_return),
  }));

  return {
    industry_id: industryId,
    industry_label: rows[0]?.industry_label ?? industryId,
    date: rows[0] ? formatDate(rows[0].date) : "",
    industry_etf_trading_amount: rows[0] ? toNum(rows[0].industry_etf_trading_amount) : null,
    industry_etf_trading_amount_ma5: rows[0] ? toNum(rows[0].industry_etf_trading_amount_ma5) : null,
    industry_etf_trading_amount_ma20: rows[0] ? toNum(rows[0].industry_etf_trading_amount_ma20) : null,
    etfs,
  };
}
