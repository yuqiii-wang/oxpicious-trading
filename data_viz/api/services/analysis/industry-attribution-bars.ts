/**
 * Industry attribution bar charts - getAllIndustriesAttribution +
 * getMemberIndexAttribution.
 * Extracted from the former analysis.service.ts.
 */
import { queryRows, formatDate, toNum } from "../../lib/db.js";
import { renderSqlView, sqlFrag } from "@api/lib/sql-views";
import type { QueryResultRow } from "pg";
import type {
  AllIndustriesAttributionResponse,
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

  // The SELECT lives in the sql view
  // data_viz/api/services/analysis/industry-attribution-bars/all_industries.
  // The date placeholder is a fragment because the original SQL numbered
  // it as the LAST pg param while it appears FIRST in the text; the
  // params array is bound here ([benchmark_code, target_date]).
  const { sql } = renderSqlView(
    "data_viz/api/services/analysis/industry-attribution-bars/all_industries",
    { benchmark_code: benchmarkCode, target_date_param: sqlFrag("$2::date") },
  );
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
//  Source: stats.cross_stats (sec_type='index' pair grain; former
//  analysis.sec_alloc_perf_attribution) joined with
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

  // The SELECT lives in the sql view
  // data_viz/api/services/analysis/industry-attribution-bars/member_indices.
  // The benchmark_code/date placeholders are fragments because the
  // original SQL numbered params by array order (industry $1, benchmark
  // $2, date $3), which differs from their first-use text order; the
  // params array is bound here ([industry_id, benchmark_code, target_date]).
  const { sql } = renderSqlView(
    "data_viz/api/services/analysis/industry-attribution-bars/member_indices",
    {
      industry_id: industryId,
      benchmark_code_param: sqlFrag("$2::text"),
      target_date_param: sqlFrag("$3::date"),
    },
  );
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
