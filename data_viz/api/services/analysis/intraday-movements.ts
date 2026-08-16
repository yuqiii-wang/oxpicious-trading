/**
 * Intraday Movements service — reads pre-computed per-5-min-tick % change
 * data from analysis.intraday_industry_market_movements (parent, industry
 * aggregate) + analysis.intraday_index_market_movements (child, individual
 * index). Populated by analyze.intraday_industry_sentiments (Python).
 *
 * Three SQL queries per request (all run in parallel):
 *   1. BENCHMARK_SERIES_SQL — benchmark_price_pct per 5-min tick (top plot
 *      main line). Deduplicated from the parent table (the benchmark %
 *      is the same across all industries at each tick).
 *   2. INDUSTRY_SERIES_SQL — industry_price_pct per (tick, industry) for
 *      ALL industries (top plot shaded areas + middle plot bars at any
 *      clicked tick). Excludes BROAD_* industry_ids.
 *   3. MEMBER_SERIES_SQL — code_price_pct per (code, tick, industry) for
 *      ALL member indices (bottom plot bars at clicked tick + industry).
 *
 * GET /api/live-data/intraday-movements
 *   ?benchmark_code=000922&date=YYYY-MM-DD
 */
import { queryRows, formatDate, toNum } from "../../lib/db.js";
import type { QueryResultRow } from "pg";
import type {
  IntradayMovementsBenchmarkTick,
  IntradayMovementsIndustryTick,
  IntradayMovementsMemberTick,
  IntradayMovementsIndustry,
  IntradayMovementsResponse,
} from "../../../shared/types.js";

// ----------------------------------------------------------------------------
//  DB row interfaces
// ----------------------------------------------------------------------------
interface DbBenchmarkNameRow extends QueryResultRow {
  name: string | null;
}

interface DbLatestDateRow extends QueryResultRow {
  max_date: Date | string | null;
}

interface DbBenchmarkListRow extends QueryResultRow {
  benchmark_code: string;
  benchmark_name: string | null;
  is_broad_market: boolean | null;
}

interface DbBenchmarkTickRow extends QueryResultRow {
  time: string;
  benchmark_price_pct: number | null;
}

interface DbIndustryTickRow extends QueryResultRow {
  time: string;
  industry_id: string;
  industry_label: string | null;
  is_industry_not_strategy: boolean;
  industry_price_pct: number | null;
  industry_price_pct_vs_benchmark: number | null;
}

interface DbMemberTickRow extends QueryResultRow {
  time: string;
  code: string;
  code_name: string | null;
  industry_id: string;
  code_price_pct: number | null;
}

// ----------------------------------------------------------------------------
//  SQL: Benchmark list — BROAD-MARKET benchmarks only.
//  The dropdown is intentionally restricted to broad-market indices so the
//  user picks a true market-wide benchmark (not a sector or strategy index).
//  Wrapper CTE so ORDER BY can resolve the is_broad_market column.
// ----------------------------------------------------------------------------
const BENCHMARK_LIST_SQL = `
WITH bench_codes AS (
    SELECT m.benchmark_code
    FROM analysis.intraday_industry_market_movements m
    JOIN LATERAL (
        SELECT BOOL_OR(t.is_broad_market) AS is_bm
        FROM stats.sec_index_tags t
        WHERE t.code = m.benchmark_code
    ) tag ON TRUE
    WHERE tag.is_bm = TRUE
    GROUP BY m.benchmark_code
),
enriched AS (
    SELECT
        bc.benchmark_code,
        (SELECT name FROM stats.index_identity WHERE code = bc.benchmark_code ORDER BY date DESC LIMIT 1) AS benchmark_name,
        (SELECT BOOL_OR(is_broad_market) FROM stats.sec_index_tags WHERE code = bc.benchmark_code) AS is_broad_market
    FROM bench_codes bc
)
SELECT * FROM enriched
ORDER BY benchmark_code
`;

// ----------------------------------------------------------------------------
//  SQL: Benchmark % change per 5-min tick.
//  The benchmark_price_pct is the same across all industries at each tick,
//  so we DISTINCT on (date, time) and take the value from any row.
//  $1 = date, $2 = benchmark_code
// ----------------------------------------------------------------------------
const BENCHMARK_SERIES_SQL = `
SELECT DISTINCT ON (date, time)
    time::text           AS time,
    benchmark_price_pct_relative_prev_date_close AS benchmark_price_pct
FROM analysis.intraday_industry_market_movements
WHERE benchmark_code = $2::text
  AND date = $1::date
ORDER BY date, time
`;

// ----------------------------------------------------------------------------
//  SQL: Industry % change per (tick, industry) — ALL industries.
//  Excludes BROAD_* (broad-market indices that are themselves benchmarks,
//  not real industries). Uses NOT LIKE 'BROAD_%' prefix match because the
//  actual industry_ids are BROAD_CSI300, BROAD_SSE50, BROAD_GEM, etc. —
//  an exact-match exclusion list would miss most of them.
//  Returns the precomputed diff (industry_pct - benchmark_pct) so the UI
//  top-plot shade can be driven directly by the diff column without
//  recomputing on every render.
//  $1 = date, $2 = benchmark_code
// ----------------------------------------------------------------------------
const INDUSTRY_SERIES_SQL = `
SELECT
    m.time::text           AS time,
    m.industry_id,
    m.is_industry_not_strategy,
    COALESCE(sc.industry_label, m.industry_id) AS industry_label,
    m.industry_price_pct_relative_prev_date_close AS industry_price_pct,
    m.industry_price_pct_vs_benchmark_price_pct AS industry_price_pct_vs_benchmark
FROM analysis.intraday_industry_market_movements m
LEFT JOIN LATERAL (
    SELECT industry_label
    FROM stats.sec_classification
    WHERE industry_id = m.industry_id AND type = 'index'
    LIMIT 1
) sc ON true
WHERE m.benchmark_code = $2::text
  AND m.date = $1::date
  AND m.industry_id NOT LIKE 'BROAD_%'
ORDER BY m.time, m.industry_id
`;

// ----------------------------------------------------------------------------
//  SQL: Member index % change per (code, tick, industry) — ALL members.
//  $1 = date, $2 = benchmark_code
// ----------------------------------------------------------------------------
const MEMBER_SERIES_SQL = `
SELECT
    m.time::text           AS time,
    m.code,
    COALESCE(ii.name, m.code) AS code_name,
    m.industry_id,
    m.code_price_pct_relative_prev_date_close AS code_price_pct
FROM analysis.intraday_index_market_movements m
LEFT JOIN LATERAL (
    SELECT name FROM stats.index_identity
    WHERE code = m.code ORDER BY date DESC LIMIT 1
) ii ON true
WHERE m.benchmark_code = $2::text
  AND m.date = $1::date
ORDER BY m.time, m.industry_id, m.code
`;

// ----------------------------------------------------------------------------
//  getIntradayMovements — main service function.
// ----------------------------------------------------------------------------
export async function getIntradayMovements(
  rawBenchmarkCode: string,
  rawDate?: string | null,
): Promise<IntradayMovementsResponse> {
  const benchmarkCode = (rawBenchmarkCode ?? "").trim();
  const date = (rawDate ?? "").trim() || null;

  if (!benchmarkCode) {
    throw new Error("Missing 'benchmark_code' parameter");
  }

  // Step 1: resolve target date.
  let targetDate: string | null = date;
  if (!targetDate) {
    const dateRows = await queryRows<DbLatestDateRow>(
      `SELECT MAX(date) AS max_date FROM analysis.intraday_industry_market_movements WHERE benchmark_code = $1::text`,
      [benchmarkCode],
    );
    targetDate = dateRows[0]?.max_date ? formatDate(dateRows[0].max_date) : null;
  }
  if (!targetDate) {
    return {
      benchmark_code: benchmarkCode,
      benchmark_name: benchmarkCode,
      date: "",
      latest_time: "",
      benchmark_series: [],
      industry_series: [],
      member_series: [],
      industries: [],
    };
  }

  // Step 2: fetch benchmark series + industry series + member series + name in parallel.
  const [benchRows, industryRows, memberRows, nameRows] = await Promise.all([
    queryRows<DbBenchmarkTickRow>(BENCHMARK_SERIES_SQL, [targetDate, benchmarkCode]),
    queryRows<DbIndustryTickRow>(INDUSTRY_SERIES_SQL, [targetDate, benchmarkCode]),
    queryRows<DbMemberTickRow>(MEMBER_SERIES_SQL, [targetDate, benchmarkCode]),
    queryRows<DbBenchmarkNameRow>(
      `SELECT name FROM stats.index_identity WHERE code = $1::text ORDER BY date DESC LIMIT 1`,
      [benchmarkCode],
    ),
  ]);

  if (benchRows.length === 0) {
    return {
      benchmark_code: benchmarkCode,
      benchmark_name: nameRows[0]?.name ?? benchmarkCode,
      date: targetDate,
      latest_time: "",
      benchmark_series: [],
      industry_series: [],
      member_series: [],
      industries: [],
    };
  }

  // Step 3: latest time = last benchmark tick.
  const latestTime = benchRows[benchRows.length - 1].time;

  // Step 4: assemble response.
  const benchmark_series: IntradayMovementsBenchmarkTick[] = benchRows.map((r) => ({
    time: r.time,
    benchmark_price_pct: toNum(r.benchmark_price_pct),
  }));

  const industry_series: IntradayMovementsIndustryTick[] = industryRows.map((r) => ({
    time: r.time,
    industry_id: r.industry_id,
    industry_label: r.industry_label ?? r.industry_id,
    is_strategy: r.is_industry_not_strategy !== true,
    industry_price_pct: toNum(r.industry_price_pct),
    industry_price_pct_vs_benchmark: toNum(r.industry_price_pct_vs_benchmark),
  }));

  const member_series: IntradayMovementsMemberTick[] = memberRows.map((r) => ({
    time: r.time,
    code: r.code,
    code_name: r.code_name ?? r.code,
    industry_id: r.industry_id,
    code_price_pct: toNum(r.code_price_pct),
  }));

  // Distinct industries from the industry_series — for the legend & color map.
  const industrySeen = new Set<string>();
  const industries: IntradayMovementsIndustry[] = [];
  for (const r of industry_series) {
    if (industrySeen.has(r.industry_id)) continue;
    industrySeen.add(r.industry_id);
    industries.push({
      industry_id: r.industry_id,
      industry_label: r.industry_label,
      is_strategy: r.is_strategy,
    });
  }

  return {
    benchmark_code: benchmarkCode,
    benchmark_name: nameRows[0]?.name ?? benchmarkCode,
    date: targetDate,
    latest_time: latestTime,
    benchmark_series,
    industry_series,
    member_series,
    industries,
  };
}

// ----------------------------------------------------------------------------
//  listIntradayMovementsBenchmarks — list all benchmark codes that appear in
//  the parent table, enriched with display name and is_broad_market flag.
// ----------------------------------------------------------------------------
export async function listIntradayMovementsBenchmarks(): Promise<{
  benchmark_code: string;
  benchmark_name: string;
  is_broad_market: boolean | null;
}[]> {
  const rows = await queryRows<DbBenchmarkListRow>(BENCHMARK_LIST_SQL, []);
  return rows.map((r) => ({
    benchmark_code: r.benchmark_code,
    benchmark_name: r.benchmark_name ?? r.benchmark_code,
    is_broad_market: r.is_broad_market === null ? null : Boolean(r.is_broad_market),
  }));
}
