/**
 * Industry Correlations - pairwise rolling Pearson correlation between
 * industries' mean_price series.
 * Extracted from the former analysis.service.ts.
 */
import { queryRows, formatDate, toNum } from "../../lib/db.js";
import type { QueryResultRow } from "pg";
import type {
  IndustryCorrelationRow,
  IndustryCorrelationsResponse,
} from "../../../shared/types.js";

// ----------------------------------------------------------------------------
//  Industry Correlations — pairwise rolling Pearson correlation between two
//  industries' mean_price series, one row per (date, industry_id,
//  benchmark_industry_id, pool_size). Drives the "Correlation" expandable
//  chart on the IndustrySentiments page (multi-industry mode only).
//
//  Source: analysis.industry_correlations (built by
//  analyze_industry_correlations.py from analysis.industry_sentiments).
//
//  Order convention: rows are stored with industry_id < benchmark_industry_id
//  (lexicographic, COLLATE "C"). The API returns rows matching either
//  direction of the user-selected industry_ids set — i.e. any pair where
//  both endpoints are in industry_ids (regardless of which is "subject" vs
//  "benchmark" in the stored row).
//
//  Both industries are compared in the SAME pool_size slice (single
//  pool_size column — cross-pool comparisons are NOT materialized). The
//  `pool_size` query param selects the slice (default 'all').
// ----------------------------------------------------------------------------
interface DbIndustryCorrelationRow extends QueryResultRow {
  industry_id: string;
  benchmark_industry_id: string;
  date: Date | string;
  industry_mean_corr_5d: number | null;
  industry_mean_corr_20d: number | null;
  industry_mean_corr_60d: number | null;
  industry_mean_corr_255d: number | null;
}

const VALID_INDUSTRY_CORR_POOLS = new Set(["all", "small", "mid", "large"]);

/** Lookup table for industry_label by industry_id, populated lazily inside
 *  getIndustryCorrelations() so the response carries human-readable
 *  industry labels alongside the bare IDs (the frontend uses them for the
 *  pair labels in the legend and tooltip). */
async function fetchIndustryLabels(
  industryIds: string[],
): Promise<Map<string, string>> {
  if (industryIds.length === 0) return new Map();
  const rows = await queryRows<{ industry_id: string; industry_label: string | null }>(
    `SELECT industry_id, COALESCE(NULLIF(industry_label, ''), industry_id) AS industry_label
     FROM (
       SELECT DISTINCT industry_id, industry_label
       FROM stats.sec_classification
       WHERE type = 'index' AND industry_id = ANY($1::text[])
     ) t`,
    [industryIds],
  );
  const m = new Map<string, string>();
  for (const r of rows) {
    m.set(r.industry_id, r.industry_label ?? r.industry_id);
  }
  // Fallback: any ID without a label maps to itself.
  for (const id of industryIds) if (!m.has(id)) m.set(id, id);
  return m;
}

export async function getIndustryCorrelations(
  rawIndustryIds: string[],
  rawPoolSize: string,
): Promise<IndustryCorrelationsResponse> {
  const industryIds = (rawIndustryIds ?? [])
    .map((s) => (s ?? "").trim())
    .filter((s) => s.length > 0);
  if (industryIds.length < 2) {
    throw new Error(
      `Need at least 2 distinct industry_ids (got ${industryIds.length}).`,
    );
  }
  // Deduplicate (case-sensitive) — the user might pass the same ID twice.
  const uniqueIds = Array.from(new Set(industryIds));
  if (uniqueIds.length < 2) {
    throw new Error(
      `Need at least 2 DISTINCT industry_ids (got ${uniqueIds.length}).`,
    );
  }
  const poolSize = VALID_INDUSTRY_CORR_POOLS.has(rawPoolSize)
    ? (rawPoolSize as "all" | "small" | "mid" | "large")
    : "all";

  // Build the list of (a, b) pairs where a < b lexicographically (COLLATE
  // "C", matching the CHECK constraint). For each pair, the stored row
  // uses the lexicographically-smaller ID as `industry_id`. The SQL uses
  // `(a, b)` tuples for an IN clause.
  const pairs: Array<[string, string]> = [];
  for (let i = 0; i < uniqueIds.length; i++) {
    for (let j = i + 1; j < uniqueIds.length; j++) {
      const [x, y] = [uniqueIds[i], uniqueIds[j]];
      // Sort using simple code-point comparison (matches COLLATE "C" for
      // ASCII strings — all industry_ids are ASCII uppercase + underscore).
      const pair: [string, string] = x < y ? [x, y] : [y, x];
      pairs.push(pair);
    }
  }

  // Build parameterized IN clause: each pair is ($n, $n+1). With N pairs
  // we need 2N placeholders. Cap at a reasonable number to avoid huge queries
  // (the UI is unlikely to select more than ~10 industries → 45 pairs →
  // 90 placeholders, well within PostgreSQL's 65535 limit).
  const pairPlaceholders = pairs
    .map((_, i) => `($${i * 2 + 1}::text, $${i * 2 + 2}::text)`)
    .join(", ");
  const pairParams = pairs.flat();

  const sql = `
    SELECT
      industry_id,
      benchmark_industry_id,
      date,
      industry_mean_corr_5d,
      industry_mean_corr_20d,
      industry_mean_corr_60d,
      industry_mean_corr_255d
    FROM analysis.industry_correlations
    WHERE pool_size = $${pairParams.length + 1}::text
      AND (industry_id, benchmark_industry_id) IN (${pairPlaceholders})
    ORDER BY date ASC, industry_id, benchmark_industry_id
  `;
  const params = [...pairParams, poolSize];

  const [rows, labelMap] = await Promise.all([
    queryRows<DbIndustryCorrelationRow>(sql, params),
    fetchIndustryLabels(uniqueIds),
  ]);

  const correlations: IndustryCorrelationRow[] = rows.map((r) => ({
    industry_id: r.industry_id,
    benchmark_industry_id: r.benchmark_industry_id,
    industry_label: labelMap.get(r.industry_id) ?? r.industry_id,
    benchmark_industry_label: labelMap.get(r.benchmark_industry_id) ?? r.benchmark_industry_id,
    date: formatDate(r.date),
    pool_size: poolSize,
    corr_5d: toNum(r.industry_mean_corr_5d),
    corr_20d: toNum(r.industry_mean_corr_20d),
    corr_60d: toNum(r.industry_mean_corr_60d),
    corr_255d: toNum(r.industry_mean_corr_255d),
  }));

  return {
    industry_ids: uniqueIds,
    pool_size: poolSize,
    correlations,
  };
}
