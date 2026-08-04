/**
 * Industry attribution bar charts - getAllIndustriesAttribution +
 * getMemberIndexAttribution.
 * Extracted from the former analysis.service.ts.
 */
import { queryRows, formatDate, toNum } from "../../lib/db.js";
import type { QueryResultRow } from "pg";
import type {
  AllIndustriesAttributionRow,
  AllIndustriesAttributionResponse,
  MemberIndexAttributionRow,
  MemberIndexAttributionResponse,
} from "../../../shared/types.js";

// ----------------------------------------------------------------------------
//  getAllIndustriesAttribution — returns ALL industries' benchmark_shared_weight
//  for a given (benchmark_code, date). Drives the industry-level bar chart in
//  "Benchmark Attribution" mode: each bar = one industry.
//
//  Source: analysis.industry_attributions (one row per industry per benchmark
//  per date). industry_label + sector_label joined from stats.sec_classification.
//  benchmark_return computed on-the-fly via LATERAL join to stats.index_basic_stats
//  (same pattern as getIndustryBenchmarkAttribution) so the chart can show
//  Contribution = benchmark_return × (benchmark_shared_weight / 100) — the
//  same convention used by Sec Allocation Perf Attribution's fluctuation chart.
// ----------------------------------------------------------------------------
interface DbAllIndustriesAttributionRow extends QueryResultRow {
  industry_id: string;
  industry_label: string | null;
  sector_label: string | null;
  benchmark_shared_weight: number | null;
  industry_shared_weight: number | null;
  benchmark_name: string | null;
  is_broad_market: boolean | null;
  benchmark_return: number | null;
  date: Date | string;
}

export async function getAllIndustriesAttribution(
  rawBenchmarkCode: string,
  rawDate?: string | null,
): Promise<AllIndustriesAttributionResponse> {
  const benchmarkCode = (rawBenchmarkCode ?? "").trim();
  if (!benchmarkCode) throw new Error("Missing 'benchmark_code' parameter");

  const sql = `
    WITH target_date AS (
      SELECT COALESCE(
        $2::date,
        (SELECT MAX(date) FROM analysis.industry_attributions
         WHERE benchmark_code = $1::text)
      ) AS max_date
    )
    SELECT
      ia.industry_id,
      sc.industry_label,
      sc.sector_label,
      ia.benchmark_shared_weight,
      ia.industry_shared_weight,
      ii.name AS benchmark_name,
      sit.is_broad_market,
      ld.max_date AS date,
      CASE
        WHEN ib.close IS NOT NULL AND pb.close IS NOT NULL AND pb.close != 0
        THEN (ib.close - pb.close) / pb.close
        ELSE NULL
      END AS benchmark_return
    FROM analysis.industry_attributions ia
    CROSS JOIN target_date ld
    LEFT JOIN LATERAL (
      SELECT industry_label, sector_label
      FROM stats.sec_classification
      WHERE industry_id = ia.industry_id AND type = 'index'
        AND industry_label IS NOT NULL
      LIMIT 1
    ) sc ON true
    LEFT JOIN LATERAL (
      SELECT DISTINCT ON (code) name
      FROM stats.index_identity
      WHERE code = ia.benchmark_code
      ORDER BY code, date DESC
    ) ii ON true
    LEFT JOIN LATERAL (
      SELECT BOOL_OR(is_broad_market) AS is_broad_market
      FROM stats.sec_index_tags
      WHERE code = ia.benchmark_code
    ) sit ON true
    LEFT JOIN LATERAL (
      SELECT close FROM stats.index_basic_stats
      WHERE code = ia.benchmark_code AND date = ld.max_date
    ) ib ON true
    LEFT JOIN LATERAL (
      SELECT close FROM stats.index_basic_stats
      WHERE code = ia.benchmark_code AND date < ld.max_date
      ORDER BY date DESC LIMIT 1
    ) pb ON true
    WHERE ia.benchmark_code = $1::text
      AND ia.date = ld.max_date
    ORDER BY ia.benchmark_shared_weight DESC NULLS LAST
  `;
  const rows = await queryRows<DbAllIndustriesAttributionRow>(
    sql, [benchmarkCode, rawDate ?? null],
  );

  return {
    benchmark_code: benchmarkCode,
    benchmark_name: rows[0]?.benchmark_name ?? benchmarkCode,
    date: rows[0] ? formatDate(rows[0].date) : "",
    is_broad_market: rows[0]?.is_broad_market == null
      ? null : Boolean(rows[0].is_broad_market),
    benchmark_return: rows[0] ? toNum(rows[0].benchmark_return) : null,
    industries: rows.map((r) => ({
      industry_id: r.industry_id,
      industry_label: r.industry_label ?? r.industry_id,
      sector_label: r.sector_label,
      benchmark_shared_weight: toNum(r.benchmark_shared_weight),
      industry_shared_weight: toNum(r.industry_shared_weight),
      benchmark_return: toNum(r.benchmark_return),
    })),
  };
}

// ----------------------------------------------------------------------------
//  getMemberIndexAttribution — returns all member indices'
//  code_sec_shared_weight for a given (industry_id, benchmark_code, date).
//  Drives the per-industry bar charts in "Benchmark Attribution" mode: each
//  bar = one member index of the selected industry.
//
//  Source: analysis.sec_alloc_perf_attribution (sec_type='index') joined with
//  stats.sec_classification for industry membership + stats.index_identity for
//  index display name.
// ----------------------------------------------------------------------------
interface DbMemberIndexAttributionRow extends QueryResultRow {
  code: string;
  name: string | null;
  code_sec_shared_weight: number | null;
  benchmark_sec_shared_weight: number | null;
  industry_label: string | null;
  benchmark_name: string | null;
  is_broad_market: boolean | null;
  date: Date | string;
}

export async function getMemberIndexAttribution(
  rawIndustryId: string,
  rawBenchmarkCode: string,
  rawDate?: string | null,
): Promise<MemberIndexAttributionResponse> {
  const industryId = (rawIndustryId ?? "").trim();
  const benchmarkCode = (rawBenchmarkCode ?? "").trim();
  if (!industryId) throw new Error("Missing 'industry_id' parameter");
  if (!benchmarkCode) throw new Error("Missing 'benchmark_code' parameter");

  const sql = `
    WITH target_date AS (
      SELECT COALESCE(
        $3::date,
        (SELECT MAX(date) FROM analysis.sec_alloc_perf_attribution
         WHERE benchmark_code = $2::text)
      ) AS max_date
    )
    SELECT
      sa.code,
      ii.name AS name,
      sa.code_sec_shared_weight,
      sa.benchmark_sec_shared_weight,
      sc.industry_label,
      bi.name AS benchmark_name,
      sit.is_broad_market,
      ld.max_date AS date
    FROM analysis.sec_alloc_perf_attribution sa
    JOIN stats.sec_classification sc
      ON sc.code = sa.code AND sc.type = 'index'
      AND sc.industry_id = $1::text
    CROSS JOIN target_date ld
    LEFT JOIN LATERAL (
      SELECT DISTINCT ON (code) name
      FROM stats.index_identity
      WHERE code = sa.code
      ORDER BY code, date DESC
    ) ii ON true
    LEFT JOIN LATERAL (
      SELECT DISTINCT ON (code) name
      FROM stats.index_identity
      WHERE code = sa.benchmark_code
      ORDER BY code, date DESC
    ) bi ON true
    LEFT JOIN LATERAL (
      SELECT BOOL_OR(is_broad_market) AS is_broad_market
      FROM stats.sec_index_tags
      WHERE code = sa.benchmark_code
    ) sit ON true
    WHERE sa.benchmark_code = $2::text
      AND sa.sec_type = 'index'
      AND sa.date = ld.max_date
    ORDER BY sa.code_sec_shared_weight DESC NULLS LAST
  `;
  const rows = await queryRows<DbMemberIndexAttributionRow>(
    sql, [industryId, benchmarkCode, rawDate ?? null],
  );

  return {
    industry_id: industryId,
    industry_label: rows[0]?.industry_label ?? industryId,
    benchmark_code: benchmarkCode,
    benchmark_name: rows[0]?.benchmark_name ?? benchmarkCode,
    date: rows[0] ? formatDate(rows[0].date) : "",
    is_broad_market: rows[0]?.is_broad_market == null
      ? null : Boolean(rows[0].is_broad_market),
    indices: rows.map((r) => ({
      code: r.code,
      name: r.name ?? r.code,
      code_sec_shared_weight: toNum(r.code_sec_shared_weight),
      benchmark_sec_shared_weight: toNum(r.benchmark_sec_shared_weight),
    })),
  };
}
