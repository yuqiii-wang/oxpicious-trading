/**
 * Industry Correlations by Benchmark Offset (composite analysis) —
 * opposite industry correlations audited in
 * analysis_composites.industry_corr_benchmark_offsets.
 *
 * Each industry's MA-W trend (mean_close) is offset by a broad-market
 * benchmark (benchmark MA rebased to the industry's MA level at each
 * window start, then subtracted — the common market factor removed;
 * prices recomputed starting at 100), and the pairwise correlations are
 * audited per 20/60/255-day window next to the RAW (overall) correlation.
 * The table's derived opposite_score_* = (1 − offset_sub_corr) / 2 stays
 * DB-side audit storage only — a pure rescale of the offset correlation,
 * so the API does not expose it.
 *
 * Source: analysis_composites.industry_corr_benchmark_offsets (built by
 * `python -m analysis.analysis_composites`; UI refresh runs it in filtered
 * mode via POST /api/analysis/industry-corr-offsets/run).
 */
import { queryRows, formatDate, toNum } from "../../lib/db.js";
import type { QueryResultRow } from "pg";
import type {
  IndustryCorrOffsetBenchmarksResponse,
  IndustryCorrOffsetIndustriesResponse,
  IndustryCorrOffsetRow,
  IndustryCorrOffsetsResponse,
} from "../../../shared/types.js";
import { fetchIndustryLabels } from "./industry-correlations.js";
import { encodeLabelParam } from "../label-codes.js";

// ----------------------------------------------------------------------------
//  GET rows — one row per (start_date, lexicographic pair, benchmark) for
//  every pair where both endpoints are in the user-selected industry_ids
//  set (either direction of the stored row). Same order convention as
//  analysis.industry_correlations: industry_id < benchmark_industry_id
//  (COLLATE "C" / code-point comparison).
// ----------------------------------------------------------------------------
interface DbIndustryCorrOffsetRow extends QueryResultRow {
  industry_id: string;
  benchmark_industry_id: string;
  start_date: Date | string;
  end_ma20_20d: Date | string | null;
  end_ma60_60d: Date | string | null;
  end_ma255_255d: Date | string | null;
  interval: number;
  overall_corr_ma20_20d: string | number | null;
  overall_corr_ma60_60d: string | number | null;
  overall_corr_ma255_255d: string | number | null;
  offset_sub_corr_ma20_20d: string | number | null;
  offset_sub_corr_ma60_60d: string | number | null;
  offset_sub_corr_ma255_255d: string | number | null;
}

const VALID_INDUSTRY_CORR_POOLS = new Set(["all", "small", "mid", "large"]);
const DEFAULT_BENCHMARK = "000300";

export async function getIndustryCorrOffsets(
  rawIndustryIds: string[],
  rawPoolSize: string,
  rawBenchmark: string,
): Promise<IndustryCorrOffsetsResponse> {
  const industryIds = (rawIndustryIds ?? [])
    .map((s) => (s ?? "").trim())
    .filter((s) => s.length > 0);
  const uniqueIds = Array.from(new Set(industryIds));
  if (uniqueIds.length < 2) {
    throw new Error(
      `Need at least 2 DISTINCT industry_ids (got ${uniqueIds.length}).`,
    );
  }
  // The pair IN-list carries 2 params per pair (PG's hard limit is 65,535)
  // and the row payload grows quadratically with the pair set long before
  // that — cap the selection so a runaway multi-select fails with a clear
  // message instead of a protocol error or a response the UI can't chart.
  const MAX_INDUSTRY_IDS = 100;
  if (uniqueIds.length > MAX_INDUSTRY_IDS) {
    throw new Error(
      `Too many industry_ids (${uniqueIds.length} > ${MAX_INDUSTRY_IDS}) — ` +
        `pair count grows quadratically; narrow the selection.`,
    );
  }
  const poolSize = VALID_INDUSTRY_CORR_POOLS.has(rawPoolSize)
    ? (rawPoolSize as "all" | "small" | "mid" | "large")
    : "all";
  const benchmarkCode = (rawBenchmark ?? "").trim() || DEFAULT_BENCHMARK;

  // (a, b) pairs with a < b lexicographically — matches the stored order
  // convention (see module docstring).
  const pairs: Array<[string, string]> = [];
  for (let i = 0; i < uniqueIds.length; i++) {
    for (let j = i + 1; j < uniqueIds.length; j++) {
      const [x, y] = [uniqueIds[i], uniqueIds[j]];
      pairs.push(x < y ? [x, y] : [y, x]);
    }
  }
  const pairPlaceholders = pairs
    .map((_, i) => `($${i * 2 + 1}::text, $${i * 2 + 2}::text)`)
    .join(", ");
  const pairParams = pairs.flat();

  const sql = `
    WITH cal AS (
      SELECT date, ROW_NUMBER() OVER (ORDER BY date) - 1 AS idx
      FROM (SELECT DISTINCT date FROM stats.industry_basic_stats) s
    )
    SELECT
      t.industry_id,
      t.benchmark_industry_id,
      t.start_date,
      e20.date AS end_ma20_20d,
      e60.date AS end_ma60_60d,
      e255.date AS end_ma255_255d,
      t."interval",
      t.overall_corr_ma20_20d,
      t.overall_corr_ma60_60d,
      t.overall_corr_ma255_255d,
      t.offset_sub_corr_ma20_20d,
      t.offset_sub_corr_ma60_60d,
      t.offset_sub_corr_ma255_255d
    FROM analysis_composites.industry_corr_benchmark_offsets t
    JOIN cal c ON c.date = t.start_date
    LEFT JOIN cal e20 ON e20.idx = c.idx + 19
    LEFT JOIN cal e60 ON e60.idx = c.idx + 59
    LEFT JOIN cal e255 ON e255.idx = c.idx + 254
    WHERE t.benchmark_code = $${pairParams.length + 1}::text
      AND t.pool_size = $${pairParams.length + 2}::smallint
      AND (t.industry_id, t.benchmark_industry_id) IN (${pairPlaceholders})
    ORDER BY t.start_date ASC, t.industry_id, t.benchmark_industry_id
  `;
  // pool_size crosses as its static.label_dim SMALLINT code.
  const params = [
    ...pairParams,
    benchmarkCode,
    encodeLabelParam("pool_size", poolSize),
  ];

  const [rows, labelMap] = await Promise.all([
    queryRows<DbIndustryCorrOffsetRow>(sql, params),
    fetchIndustryLabels(uniqueIds),
  ]);

  const offsets: IndustryCorrOffsetRow[] = rows.map((r) => ({
    industry_id: r.industry_id,
    benchmark_industry_id: r.benchmark_industry_id,
    industry_label: labelMap.get(r.industry_id) ?? r.industry_id,
    benchmark_industry_label:
      labelMap.get(r.benchmark_industry_id) ?? r.benchmark_industry_id,
    benchmark_code: benchmarkCode,
    start_date: formatDate(r.start_date),
    end_ma20_20d: r.end_ma20_20d == null ? null : formatDate(r.end_ma20_20d),
    end_ma60_60d: r.end_ma60_60d == null ? null : formatDate(r.end_ma60_60d),
    end_ma255_255d:
      r.end_ma255_255d == null ? null : formatDate(r.end_ma255_255d),
    interval: r.interval,
    pool_size: poolSize,
    overall_corr_ma20_20d: toNum(r.overall_corr_ma20_20d),
    overall_corr_ma60_60d: toNum(r.overall_corr_ma60_60d),
    overall_corr_ma255_255d: toNum(r.overall_corr_ma255_255d),
    offset_sub_corr_ma20_20d: toNum(r.offset_sub_corr_ma20_20d),
    offset_sub_corr_ma60_60d: toNum(r.offset_sub_corr_ma60_60d),
    offset_sub_corr_ma255_255d: toNum(r.offset_sub_corr_ma255_255d),
  }));

  return {
    industry_ids: uniqueIds,
    pool_size: poolSize,
    benchmark_code: benchmarkCode,
    // Explicit backend arg for the shared ExpandedTable — this endpoint
    // opts IN to the audit table's per-column header filters (they are
    // disabled by default everywhere else).
    enable_filters: true,
    offsets,
  };
}

// ----------------------------------------------------------------------------
//  GET benchmarks — the distinct benchmark_code values materialized in the
//  table (drives the benchmark dropdown; defaults to 000300 when empty).
// ----------------------------------------------------------------------------
interface DbBenchmarkRow extends QueryResultRow {
  benchmark_code: string;
  benchmark_label: string | null;
  n_industries: number;
  first_start_date: Date | string;
  last_start_date: Date | string;
}

export async function listIndustryCorrOffsetBenchmarks(): Promise<
  IndustryCorrOffsetBenchmarksResponse
> {
  // One pass over the offsets table with BOTH pair endpoints unnested —
  // COUNT(DISTINCT industry_id) alone undercounts (an industry that is
  // only ever the lexicographically LARGER endpoint of its pairs would be
  // missing; e.g. {AI, BANKS, SEMI} pairs count 2 instead of 3).
  const rows = await queryRows<DbBenchmarkRow>(`
    SELECT
      u.benchmark_code,
      (
        SELECT COALESCE(NULLIF(MAX(sc.industry_label), ''), u.benchmark_code)
        FROM stats.sec_classification sc
        WHERE sc.type = 'index' AND sc.code = u.benchmark_code
      ) AS benchmark_label,
      COUNT(DISTINCT u.ind) AS n_industries,
      MIN(u.start_date) AS first_start_date,
      MAX(u.start_date) AS last_start_date
    FROM (
      SELECT t.benchmark_code, t.industry_id AS ind, t.start_date
      FROM analysis_composites.industry_corr_benchmark_offsets t
      UNION ALL
      SELECT t.benchmark_code, t.benchmark_industry_id, t.start_date
      FROM analysis_composites.industry_corr_benchmark_offsets t
    ) u
    GROUP BY u.benchmark_code
    ORDER BY u.benchmark_code
  `);
  return {
    benchmarks: rows.map((r) => ({
      benchmark_code: r.benchmark_code,
      benchmark_label: r.benchmark_label ?? r.benchmark_code,
      n_industries: Number(r.n_industries ?? 0),
      first_start_date: formatDate(r.first_start_date),
      last_start_date: formatDate(r.last_start_date),
    })),
  };
}

// ----------------------------------------------------------------------------
//  GET industries — the selectable industry list for the page's
//  multi-select (distinct type='index' industries from
//  stats.sec_classification, flagged with whether the offsets table has
//  any materialized row for them).
// ----------------------------------------------------------------------------
interface DbIndustryRow extends QueryResultRow {
  industry_id: string;
  industry_label: string | null;
  has_rows: boolean | null;
}

export async function listIndustryCorrOffsetIndustries(): Promise<
  IndustryCorrOffsetIndustriesResponse
> {
  // ONE pass over the offsets table (UNION dedups both pair endpoints),
  // then a cheap LEFT JOIN — avoids per-industry count scans.
  const rows = await queryRows<DbIndustryRow>(`
    WITH cov AS (
      SELECT industry_id AS ind FROM analysis_composites.industry_corr_benchmark_offsets
      UNION
      SELECT benchmark_industry_id FROM analysis_composites.industry_corr_benchmark_offsets
    )
    SELECT
      sc.industry_id,
      COALESCE(NULLIF(sc.industry_label, ''), sc.industry_id) AS industry_label,
      (c.ind IS NOT NULL) AS has_rows
    FROM (
      SELECT DISTINCT industry_id, industry_label
      FROM stats.sec_classification
      WHERE type = 'index'
        AND industry_id IS NOT NULL
        AND industry_id <> ''
    ) sc
    LEFT JOIN cov c ON c.ind = sc.industry_id
    ORDER BY sc.industry_id
  `);
  return {
    industries: rows.map((r) => ({
      industry_id: r.industry_id,
      industry_label: r.industry_label ?? r.industry_id,
      has_rows: r.has_rows === true,
    })),
  };
}
