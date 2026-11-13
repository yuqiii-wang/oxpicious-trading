/**
 * Industry Stock Attribution — the per-industry daily pct-change bucket
 * distribution for the "Stock Attribution" view on the Market Beta page
 * (the rise/drop audit: "how many stocks in industry X rose >= 7.5%
 * today").
 *
 * One bar chart per selected industry; each bar = one of the 12
 * 'stock_change_bucket' label_dim buckets (drop tail -> rise tail) with
 * the number of member stocks whose stats.stock_basic_stats.pct_change
 * fell in that bucket on the date. pct_of_industry is computed
 * host-side against the denormalized total_stocks. Also returns the 90
 * most recent available dates for the view's date dropdown.
 *
 * Source table: analysis.industry_stock_change_dist (built by
 * analysis.market_beta.stock_attribution).
 */
import { queryRows, formatDate, toNum } from "../../lib/db.js";
import { renderSqlView, sqlFrag } from "@api/lib/sql-views";
import { labelDecodeSql } from "../infra/label-codes.js";
import type { QueryResultRow } from "pg";
import type {
  IndustryStockAttributionResponse,
  IndustryStockAttributionBucket,
} from "../../../shared/types.js";

interface DbStockDistRow extends QueryResultRow {
  date: Date | string;
  bucket: number | null;
  bucket_label: string | null;
  stock_count: number | null;
  total_stocks: number | null;
  industry_label: string | null;
  available_dates: string[] | null;
}

export async function getIndustryStockAttribution(
  rawIndustryId: string,
  rawDate?: string | null,
): Promise<IndustryStockAttributionResponse> {
  const industryId = (rawIndustryId ?? "").trim();
  if (!industryId) {
    throw new Error("Missing 'industry_id' parameter");
  }

  // Fetch the 12 bucket rows for (industry_id, date) — date NULL falls
  // back to the latest materialized date for the industry. The SELECT
  // lives in the sql view
  // data_viz/api/services/analysis/industry-stock-attribution/dist.
  // The date placeholder is a fragment because the loader numbers VALUE
  // slots by first text use (industry_id -> $1); the service binds the
  // params array itself ([industry_id, target_date]). bucket_label is
  // decoded SQL-side via the label_dim scalar subquery.
  const { sql } = renderSqlView(
    "data_viz/api/services/analysis/industry-stock-attribution/dist",
    {
      industry_id: industryId,
      date_param: sqlFrag("$2::date"),
      bucket_decode: sqlFrag(labelDecodeSql("stock_change_bucket", "r.bucket")),
    },
  );
  const rows = await queryRows<DbStockDistRow>(sql, [industryId, rawDate ?? null]);

  const total = rows.length > 0 ? toNum(rows[0].total_stocks) ?? 0 : 0;
  const buckets: IndustryStockAttributionBucket[] = rows.map((r) => {
    const count = toNum(r.stock_count) ?? 0;
    return {
      bucket: Number(r.bucket ?? 0),
      bucket_label: r.bucket_label ?? String(r.bucket ?? ""),
      stock_count: count,
      pct_of_industry: total > 0 ? count / total : null,
    };
  });

  return {
    industry_id: industryId,
    industry_label: rows[0]?.industry_label ?? industryId,
    date: rows[0] ? formatDate(rows[0].date) : "",
    total_stocks: total,
    buckets,
    available_dates: rows[0]?.available_dates ?? [],
  };
}
