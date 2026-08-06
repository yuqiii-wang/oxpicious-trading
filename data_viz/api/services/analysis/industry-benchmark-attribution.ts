/**
 * Industry-level Benchmark Attribution - reads pre-materialized rows from
 * analysis.industry_attributions.
 * Extracted from the former analysis.service.ts.
 */
import { queryRows, formatDate, toNum } from "../../lib/db.js";
import type { QueryResultRow } from "pg";
import type {
  IndustryBenchmarkAttributionRow,
  IndustryBenchmarkAttributionResponse,
  IndustryAttributionBenchmarkEntry,
} from "../../../shared/types.js";

// ----------------------------------------------------------------------------
//  Industry-level Benchmark Attribution — reads pre-materialized rows from
//  analysis.industry_attributions (PK: date, industry_id, benchmark_code).
//  Each row carries industry_shared_weight (SUM of member indices' overlap
//  with the benchmark — can exceed 100) and benchmark_shared_weight (the
//  benchmark's weight on the UNION of industry member stocks — bounded
//  [0, 100]). benchmark_return is computed on-the-fly via a LATERAL join to
//  stats.index_basic_stats (same pattern as getPerfAttrAttribution).
//
//  Drives the per-industry attribution bar charts (2nd plot onward) on the
//  Industry Sentiments page in "Benchmark Attribution" mode. The 1st plot is
//  the benchmark price chart (clickable to pick a date); each subsequent plot
//  shows the attribution bars for ONE selected industry at the clicked date.
//
//  Source: analysis.industry_attributions (built by
//  analyze.industry_sentiments.attributions — truncate-then-recompute).
// ----------------------------------------------------------------------------
interface DbIndustryBenchmarkAttributionRow extends QueryResultRow {
  benchmark_code: string;
  benchmark_name: string | null;
  date: Date | string;
  industry_shared_weight: number | null;
  benchmark_shared_weight: number | null;
  is_broad_market: boolean | null;
  benchmark_return: number | null;
}

export async function getIndustryBenchmarkAttribution(
  rawIndustryId: string,
  date?: string | null,
): Promise<IndustryBenchmarkAttributionResponse> {
  const industryId = (rawIndustryId ?? "").trim();
  if (!industryId) {
    throw new Error("Missing 'industry_id' parameter");
  }

  // Look up the industry_label from stats.sec_classification.
  const labelRows = await queryRows<{ industry_label: string | null }>(
    `SELECT DISTINCT industry_label
     FROM stats.sec_classification
     WHERE type = 'index' AND industry_id = $1::text`,
    [industryId],
  );
  const industryLabel = labelRows[0]?.industry_label ?? industryId;

  // Query the pre-materialized industry_attributions table for the given
  // industry at the target date (or latest available when date is NULL).
  // benchmark_return is computed on-the-fly via LATERAL join to
  // stats.index_basic_stats (NOT stored in industry_attributions).
  const sql = `
    WITH target_date AS (
      SELECT COALESCE(
        $2::date,
        (SELECT MAX(date) FROM analysis.industry_attributions
         WHERE industry_id = $1::text)
      ) AS max_date
    ),
    bench_rows AS (
      SELECT
        ia.benchmark_code,
        ia.industry_shared_weight,
        ia.benchmark_shared_weight
      FROM analysis.industry_attributions ia
      CROSS JOIN target_date ld
      WHERE ia.industry_id = $1::text
        AND ia.date = ld.max_date
    )
    SELECT
      br.benchmark_code,
      bi.name AS benchmark_name,
      ld.max_date AS date,
      br.industry_shared_weight,
      br.benchmark_shared_weight,
      sit.is_broad_market,
      CASE
        WHEN ib.close IS NOT NULL AND pb.close IS NOT NULL AND pb.close != 0
        THEN (ib.close - pb.close) / pb.close
        ELSE NULL
      END AS benchmark_return
    FROM bench_rows br
    CROSS JOIN target_date ld
    LEFT JOIN LATERAL (
      SELECT DISTINCT ON (code) code, name
      FROM stats.index_identity
      WHERE code = br.benchmark_code
      ORDER BY code, date DESC
    ) bi ON true
    LEFT JOIN LATERAL (
      SELECT close FROM stats.index_basic_stats
      WHERE code = br.benchmark_code AND date = ld.max_date
    ) ib ON true
    LEFT JOIN LATERAL (
      SELECT close FROM stats.index_basic_stats
      WHERE code = br.benchmark_code AND date < ld.max_date
      ORDER BY date DESC LIMIT 1
    ) pb ON true
    LEFT JOIN LATERAL (
      SELECT BOOL_OR(is_broad_market) AS is_broad_market
      FROM stats.sec_index_tags
      WHERE code = br.benchmark_code
    ) sit ON true
    ORDER BY br.benchmark_code
  `;
  const rows = await queryRows<DbIndustryBenchmarkAttributionRow>(
    sql,
    [industryId, date ?? null],
  );

  const benchmarks: IndustryBenchmarkAttributionRow[] = rows.map((r) => {
    const br = toNum(r.benchmark_return);
    return {
      benchmark_code: r.benchmark_code,
      benchmark_name: r.benchmark_name ?? "",
      date: formatDate(r.date),
      industry_shared_weight: toNum(r.industry_shared_weight),
      benchmark_shared_weight: toNum(r.benchmark_shared_weight),
      is_broad_market: r.is_broad_market === null ? null : Boolean(r.is_broad_market),
      benchmark_return: br,
    };
  });

  return {
    industry_id: industryId,
    industry_label: industryLabel,
    latest_date: benchmarks[0]?.date ?? "",
    benchmarks,
  };
}

// ----------------------------------------------------------------------------
//  listIndustryAttributionBenchmarks — returns the distinct BROAD-MARKET
//  benchmark codes that appear in analysis.industry_attributions, enriched
//  with display name (from stats.index_identity) and is_broad_market flag.
//
//  Only 宽基 (broad-market) benchmarks are returned — the dropdown drives
//  the 1st plot (benchmark price chart), which should only offer broad-market
//  indices. Member-index benchmarks (each industry's own indices) are also
//  materialized in industry_attributions but are NOT offered in this
//  dropdown — they appear automatically as bars in the per-industry
//  attribution chart (via getIndustryBenchmarkAttribution).
//
//  Drives the benchmark dropdown on the Industry Sentiments page in
//  "Benchmark Attribution" mode — the selected benchmark's close price series
//  is shown as the 1st plot (clickable to pick a date for the attribution
//  plots below).
// ----------------------------------------------------------------------------
interface DbIndustryAttributionBenchmarkRow extends QueryResultRow {
  benchmark_code: string;
  benchmark_name: string | null;
  is_broad_market: boolean | null;
}

export async function listIndustryAttributionBenchmarks(): Promise<IndustryAttributionBenchmarkEntry[]> {
  const sql = `
    WITH bench_codes AS (
      SELECT DISTINCT ia.benchmark_code
      FROM analysis.industry_attributions ia
      JOIN stats.sec_index_tags sit ON sit.code = ia.benchmark_code
      WHERE sit.is_broad_market = TRUE
    )
    SELECT
      bc.benchmark_code,
      ii.name AS benchmark_name,
      TRUE AS is_broad_market
    FROM bench_codes bc
    LEFT JOIN LATERAL (
      SELECT DISTINCT ON (code) name
      FROM stats.index_identity
      WHERE code = bc.benchmark_code
      ORDER BY code, date DESC
    ) ii ON true
    ORDER BY bc.benchmark_code
  `;
  const rows = await queryRows<DbIndustryAttributionBenchmarkRow>(sql, []);
  return rows.map((r) => ({
    benchmark_code: r.benchmark_code,
    benchmark_name: r.benchmark_name ?? r.benchmark_code,
    is_broad_market: r.is_broad_market === null ? null : Boolean(r.is_broad_market),
  }));
}
