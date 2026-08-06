/**
 * Benchmark price chart + industry attribution price series.
 * Extracted from the former analysis.service.ts.
 */
import { queryRows, formatDate, toNum } from "../../lib/db.js";
import type { QueryResultRow } from "pg";
import type {
  BenchmarkPriceChartResponse,
  IndustryAttributionPriceSeriesResponse,
} from "../../../shared/types.js";

// ----------------------------------------------------------------------------
//  getBenchmarkPriceChart — returns the daily close series + fractional daily
//  return for ONE benchmark index. Drives the 1st plot in "Benchmark
//  Attribution" mode: the user clicks a date on this chart to set the as-of
//  date for the per-industry attribution bar charts below.
//
//  Source: stats.index_basic_stats (close). daily_return is computed via a
//  LATERAL self-join (prev-day close) — same pattern used by
//  getPerfAttrAttribution and getIndustryBenchmarkAttribution.
// ----------------------------------------------------------------------------
interface DbBenchmarkPriceRow extends QueryResultRow {
  date: Date | string;
  close: number | null;
  daily_return: number | null;
  trading_amount: number | null;
}

export async function getBenchmarkPriceChart(
  rawCode: string,
): Promise<BenchmarkPriceChartResponse> {
  const code = (rawCode ?? "").trim();
  if (!code) throw new Error("Missing 'code' parameter");

  const [priceRows, nameRows] = await Promise.all([
    queryRows<DbBenchmarkPriceRow>(
      `SELECT
        ib.date,
        ib.close,
        ib.trading_amount,
        CASE
          WHEN pb.close IS NOT NULL AND pb.close != 0
          THEN (ib.close - pb.close) / pb.close
          ELSE NULL
        END AS daily_return
       FROM stats.index_basic_stats ib
       LEFT JOIN LATERAL (
         SELECT close FROM stats.index_basic_stats
         WHERE code = ib.code AND date < ib.date
         ORDER BY date DESC LIMIT 1
       ) pb ON true
       WHERE ib.code = $1::text
       ORDER BY ib.date ASC`,
      [code],
    ),
    queryRows<{ name: string | null }>(
      `SELECT DISTINCT ON (code) code, name FROM stats.index_identity
       WHERE code = $1::text ORDER BY code, date DESC`,
      [code],
    ),
  ]);

  return {
    code,
    name: nameRows[0]?.name ?? "",
    rows: priceRows.map((r) => ({
      date: formatDate(r.date),
      close: toNum(r.close),
      daily_return: toNum(r.daily_return),
      trading_amount: toNum(r.trading_amount),
    })),
  };
}

// ----------------------------------------------------------------------------
//  getIndustryAttributionPriceSeries — returns the benchmark close + the
//  non-this-industry price columns for ONE (industry_id, benchmark_code) pair.
//  Drives the green/red shade overlay on the BenchmarkPriceChart.
//
//  Source: analysis.industry_attributions (LEFT JOIN stats.index_basic_stats
//  for benchmark close). benchmark_rolling is computed via a window function
//  (100 × close / first_value(close)).
//
//  Only broad-market benchmarks have non-NULL non_this_industry_* values
//  (computed by the attributions step). For non-broad benchmarks the rows
//  still contain benchmark_close + benchmark_rolling so the chart can render
//  the benchmark line alone.
//
//  The 5 rolling_Xdays_price columns (5/20/60/255/500) are returned as-is;
//  the frontend dropdown picks which one drives the shade overlay.
// ----------------------------------------------------------------------------
interface DbAttributionPriceRow extends QueryResultRow {
  date: Date | string;
  benchmark_close: number | null;
  benchmark_rolling: number | null;
  non_this_industry_price: number | null;
  non_this_industry_rolling_5days_price: number | null;
  non_this_industry_rolling_20days_price: number | null;
  non_this_industry_rolling_60days_price: number | null;
  non_this_industry_rolling_255days_price: number | null;
  non_this_industry_rolling_500days_price: number | null;
  benchmark_shared_weight: number | null;
  industry_label: string | null;
  benchmark_name: string | null;
  is_broad_market: boolean | null;
}

export async function getIndustryAttributionPriceSeries(
  rawIndustryId: string,
  rawBenchmarkCode: string,
): Promise<IndustryAttributionPriceSeriesResponse> {
  const industryId = (rawIndustryId ?? "").trim();
  const benchmarkCode = (rawBenchmarkCode ?? "").trim();
  if (!industryId) throw new Error("Missing 'industry_id' parameter");
  if (!benchmarkCode) throw new Error("Missing 'benchmark_code' parameter");

  // NOTE: aliases strip the `benchmark_` prefix from the column names so the
  // row keys match the DbAttributionPriceRow interface and the response
  // payload field names. Also uses LATERAL ... LIMIT 1 for industry_label to
  // avoid row multiplication when an industry has multiple member indices
  // in stats.sec_classification.
  const rows = await queryRows<DbAttributionPriceRow>(
    `SELECT
        ia.date,
        ib.close AS benchmark_close,
        -- Rebase benchmark close to 100 at the first date in the partition
        -- so it's comparable to non_this_industry_rolling_*_price (also 100-based).
        100.0 * ib.close / NULLIF(
          FIRST_VALUE(ib.close) OVER (
            PARTITION BY ia.benchmark_code
            ORDER BY ia.date
            ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
          ), 0
        ) AS benchmark_rolling,
        ia.benchmark_non_this_industry_price                    AS non_this_industry_price,
        ia.benchmark_non_this_industry_rolling_5days_price      AS non_this_industry_rolling_5days_price,
        ia.benchmark_non_this_industry_rolling_20days_price      AS non_this_industry_rolling_20days_price,
        ia.benchmark_non_this_industry_rolling_60days_price      AS non_this_industry_rolling_60days_price,
        ia.benchmark_non_this_industry_rolling_255days_price     AS non_this_industry_rolling_255days_price,
        ia.benchmark_non_this_industry_rolling_500days_price    AS non_this_industry_rolling_500days_price,
        ia.benchmark_shared_weight,
        sc.industry_label,
        ii.name AS benchmark_name,
        sit.is_broad_market
     FROM analysis.industry_attributions ia
     LEFT JOIN stats.index_basic_stats ib
        ON ib.code = ia.benchmark_code AND ib.date = ia.date
     LEFT JOIN LATERAL (
        SELECT industry_label
        FROM stats.sec_classification
        WHERE industry_id = ia.industry_id AND type = 'index'
          AND industry_label IS NOT NULL
        LIMIT 1
     ) sc ON true
     LEFT JOIN LATERAL (
        SELECT DISTINCT ON (code) code, name
        FROM stats.index_identity
        WHERE code = ia.benchmark_code
        ORDER BY code, date DESC
     ) ii ON true
     LEFT JOIN LATERAL (
        SELECT is_broad_market
        FROM stats.sec_index_tags
        WHERE code = ia.benchmark_code
        LIMIT 1
     ) sit ON true
     WHERE ia.industry_id = $1::text
       AND ia.benchmark_code = $2::text
     ORDER BY ia.date ASC`,
    [industryId, benchmarkCode],
  );

  return {
    industry_id: industryId,
    industry_label: rows[0]?.industry_label ?? industryId,
    benchmark_code: benchmarkCode,
    benchmark_name: rows[0]?.benchmark_name ?? benchmarkCode,
    is_broad_market: rows[0]?.is_broad_market ?? null,
    rows: rows.map((r) => ({
      date: formatDate(r.date),
      benchmark_close: toNum(r.benchmark_close),
      benchmark_rolling: toNum(r.benchmark_rolling),
      non_this_industry_price: toNum(r.non_this_industry_price),
      non_this_industry_rolling_5days_price: toNum(r.non_this_industry_rolling_5days_price),
      non_this_industry_rolling_20days_price: toNum(r.non_this_industry_rolling_20days_price),
      non_this_industry_rolling_60days_price: toNum(r.non_this_industry_rolling_60days_price),
      non_this_industry_rolling_255days_price: toNum(r.non_this_industry_rolling_255days_price),
      non_this_industry_rolling_500days_price: toNum(r.non_this_industry_rolling_500days_price),
      benchmark_shared_weight: toNum(r.benchmark_shared_weight),
    })),
  };
}
