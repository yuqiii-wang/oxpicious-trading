/**
 * Intraday Movements service — reads the LIVE schema tables populated by
 * `python -m live.sec_alloc_live_attribution` (the analysis.intraday_industry_
 * sentiments pipeline is NO LONGER needed for this page):
 *
 *   • live.sec_alloc_live_attribution — per-5-min-tick member % vs prev-day
 *     close + denormalized benchmark % + GENERATED diff (tick rows only for
 *     index/etf members).
 *
 * Industry identity comes from stats.sec_classification at QUERY time (the
 * former live.sec_alloc_live_prev_ref denormalization was consolidated away —
 * weights/prev closes are derivable from base tables at read time; this
 * service is equal-weight only and needs no weights at all).
 *
 * Industry-level aggregates are computed AT QUERY TIME (equal-weight AVG of
 * member pcts per (industry, tick)), so NO analysis recompute pass is required
 * for this page.
 *
 * Three series queries per request (all run in parallel; each SELECT
 * lives in a sql view under
 * database/sql_views/data_viz/api/services/analysis/intraday-movements/):
 *   1. benchmark_series — benchmark_price_pct per 5-min tick (top plot
 *      main line). MAX() per tick collapses the denormalized per-member
 *      copies and skips NULL benchmark bars.
 *   2. industry_series — equal-weight industry % + diff vs benchmark
 *      per (tick, industry) for ALL industries (top plot shaded areas +
 *      middle plot bars at any clicked tick). Excludes BROAD_* industry_ids.
 *   3. member_series — code_price_pct per (code, tick, industry) for
 *      ALL member indices (bottom plot bars at clicked tick + industry).
 *
 * GET /api/live-data/intraday-movements
 *   ?benchmark_code=000922&date=YYYY-MM-DD
 */
import { formatDate, toNum } from "../../lib/db.js";
import { querySqlView, sqlFrag } from "@api/lib/sql-views";
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
//  Benchmark list — BROAD-MARKET benchmarks only. The full SELECT lives in
//  the sql view
//  data_viz/api/services/analysis/intraday-movements/benchmark_list
//  (the dropdown is intentionally restricted to broad-market indices so
//  the user picks a true market-wide benchmark, not a sector or strategy
//  index; wrapper CTE so ORDER BY can resolve the is_broad_market column).
//
//  The all_curated_with_data CTE (benchmarks that have pair-grain
//  attribution history in stats.cross_stats sec_type='index' AND raw
//  intraday data) has two source variants picked at call time — the
//  fragment builder below stays in code:
//    • summary (default): UNNEST of stats.cross_stats_code_summary's
//      per-code benchmarks arrays — a benchmark has history iff it appears
//      in at least one subject's benchmarks array. Millisecond read; the
//      rollup is maintained by builds.cross_stats (15_cross_stats_code_
//      summary.sql).
//    • live (fallback, only while the summary has no 'index' rows yet —
//      fresh DB before the first builds.cross_stats run): the former
//      full-table DISTINCT scan over the 70M+ row main table.
// ----------------------------------------------------------------------------
function buildAllCuratedWithDataCte(useSummary: boolean): string {
    const history = useSummary
        ? `
        SELECT UNNEST(cs.benchmarks) AS benchmark_code
        FROM stats.cross_stats_code_summary cs
        WHERE cs.sec_type = 'index'`
        : `
        SELECT sap.benchmark_code
        FROM stats.cross_stats sap
        WHERE sap.sec_type = 'index'`;
    return `
all_curated_with_data AS (
    -- Benchmarks with attribution history (pair grain) + intraday data,
    -- used when the live pipeline hasn't processed them yet.
    SELECT DISTINCT h.benchmark_code
    FROM (${history}) h
    WHERE h.benchmark_code IN (SELECT benchmark_code FROM curated)
      AND EXISTS (
          SELECT 1 FROM stats.index_intraday_5min i5
          WHERE i5.code = h.benchmark_code AND i5.close IS NOT NULL
          LIMIT 1
      )
)`;
}

// ----------------------------------------------------------------------------
//  Benchmark % change per 5-min tick — the SELECT lives in the sql view
//  data_viz/api/services/analysis/intraday-movements/benchmark_series
//  (MAX() per time collapses the denormalized per-member copies and skips
//  NULL benchmark bars).
// ----------------------------------------------------------------------------

// ----------------------------------------------------------------------------
//  Fallback benchmark % change — computed on-the-fly from the raw intraday
//  table + previous trading day's close (used when the live tick table
//  hasn't been populated for the target date yet). The SELECT lives in the
//  sql view
//  data_viz/api/services/analysis/intraday-movements/benchmark_series_fallback.
// ----------------------------------------------------------------------------

// ----------------------------------------------------------------------------
//  Industry % change per (tick, industry) — ALL industries, computed at
//  query time from the live tick table (equal-weight AVG of member pcts —
//  same semantics as the retired analysis pre-compute). The SELECT lives
//  in the sql view
//  data_viz/api/services/analysis/intraday-movements/industry_series.
// ----------------------------------------------------------------------------

// ----------------------------------------------------------------------------
//  Fallback industry % change — equal-weight industry aggregates computed
//  on-the-fly from the RAW intraday table when the live tick table has no
//  rows for the target date. The SELECT lives in the sql view
//  data_viz/api/services/analysis/intraday-movements/industry_series_fallback.
// ----------------------------------------------------------------------------

// ----------------------------------------------------------------------------
//  Fallback member index % change — per (code, tick) pcts computed from
//  the RAW intraday table. The SELECT lives in the sql view
//  data_viz/api/services/analysis/intraday-movements/member_series_fallback.
// ----------------------------------------------------------------------------

// ----------------------------------------------------------------------------
//  Member index % change per (code, tick, industry) — ALL members with
//  tick rows. The SELECT lives in the sql view
//  data_viz/api/services/analysis/intraday-movements/member_series.
// ----------------------------------------------------------------------------

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
  // Prefer the latest date from the RAW intraday table (stats.index_intraday_5min)
  // so the page always shows the most recent trading day, even when the live
  // tick pipeline hasn't ingested that date yet.
  let targetDate: string | null = date;
  if (!targetDate) {
    const rawDateRows = await querySqlView<DbLatestDateRow>(
      "data_viz/api/services/analysis/intraday-movements/latest_raw_date",
      { benchmark_code: benchmarkCode },
    );
    targetDate = rawDateRows[0]?.max_date ? formatDate(rawDateRows[0].max_date) : null;
    // Fallback: if raw table has no data, try the live tick table.
    if (!targetDate) {
      const liveDateRows = await querySqlView<DbLatestDateRow>(
        "data_viz/api/services/analysis/intraday-movements/latest_live_date",
        { benchmark_code: benchmarkCode },
      );
      targetDate = liveDateRows[0]?.max_date ? formatDate(liveDateRows[0].max_date) : null;
    }
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
    querySqlView<DbBenchmarkTickRow>(
      "data_viz/api/services/analysis/intraday-movements/benchmark_series",
      { date: targetDate, benchmark_code: benchmarkCode },
    ),
    querySqlView<DbIndustryTickRow>(
      "data_viz/api/services/analysis/intraday-movements/industry_series",
      { date: targetDate, benchmark_code: benchmarkCode },
    ),
    querySqlView<DbMemberTickRow>(
      "data_viz/api/services/analysis/intraday-movements/member_series",
      { date: targetDate, benchmark_code: benchmarkCode },
    ),
    querySqlView<DbBenchmarkNameRow>(
      "data_viz/api/services/analysis/intraday-movements/benchmark_name",
      { benchmark_code: benchmarkCode },
    ),
  ]);

  // Step 2b: if the live tick table has no benchmark data OR its latest
  // tick lags behind the raw intraday table, compute the benchmark %
  // on-the-fly from the raw intraday table. This handles the case where the
  // live pipeline lags behind raw data ingestion (e.g. during market hours
  // when today's data is still flowing in).
  let effectiveBenchRows = benchRows;
  let useFallback = false;
  if (effectiveBenchRows.length === 0) {
    useFallback = true;
  } else {
    // Check if raw table has newer ticks than the live tick table.
    const rawLatestRows = await querySqlView<DbBenchmarkTickRow>(
      "data_viz/api/services/analysis/intraday-movements/latest_raw_tick",
      { benchmark_code: benchmarkCode, date: targetDate },
    );
    const rawLatestTime = rawLatestRows[0]?.time ?? "";
    const liveLatestTime = effectiveBenchRows[effectiveBenchRows.length - 1].time;
    if (rawLatestTime && rawLatestTime > liveLatestTime) {
      useFallback = true;
    }
  }
  if (useFallback) {
    const fallbackRows = await querySqlView<DbBenchmarkTickRow>(
      "data_viz/api/services/analysis/intraday-movements/benchmark_series_fallback",
      { benchmark_code: benchmarkCode, date: targetDate },
    );
    effectiveBenchRows = fallbackRows;
  }

  // Step 3: filter ALL series to only include ticks that exist in the
  // raw benchmark intraday table. This excludes extended-hours ticks
  // (e.g. 09:30 pre-open, 15:05+ post-close) that member indices may
  // have but the benchmark doesn't.
  const rawTickRows = await querySqlView<DbBenchmarkTickRow>(
    "data_viz/api/services/analysis/intraday-movements/raw_tick_times",
    { benchmark_code: benchmarkCode, date: targetDate },
  );
  const validTickTimes = new Set(rawTickRows.map((r) => r.time));
  effectiveBenchRows = effectiveBenchRows.filter((r) => validTickTimes.has(r.time));
  let filteredIndustryRows = industryRows.filter((r) => validTickTimes.has(r.time));
  let filteredMemberRows = memberRows.filter((r) => validTickTimes.has(r.time));

  // Step 3b: if the live tick table has NO industry/member rows for the
  // target date (the live pipeline is latest-GLOBAL-date-scoped, so a
  // benchmark whose latest raw date lags the global latest — e.g. its own
  // 09-01 ticks not ingested yet — never gets live rows), compute the
  // industry aggregates + member pcts on-the-fly from the RAW intraday
  // table (same ref-less fallback semantics as the live pipeline).
  if (filteredIndustryRows.length === 0) {
    const fbIndustryRows = await querySqlView<DbIndustryTickRow>(
      "data_viz/api/services/analysis/intraday-movements/industry_series_fallback",
      { benchmark_code: benchmarkCode, date: targetDate },
    );
    filteredIndustryRows = fbIndustryRows.filter((r) => validTickTimes.has(r.time));
  }
  if (filteredMemberRows.length === 0) {
    const fbMemberRows = await querySqlView<DbMemberTickRow>(
      "data_viz/api/services/analysis/intraday-movements/member_series_fallback",
      { benchmark_code: benchmarkCode, date: targetDate },
    );
    filteredMemberRows = fbMemberRows.filter((r) => validTickTimes.has(r.time));
  }

  if (effectiveBenchRows.length === 0) {
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

  // Step 4: latest time = last benchmark tick.
  const latestTime = effectiveBenchRows[effectiveBenchRows.length - 1].time;

  // Step 5: assemble response.
  const benchmark_series: IntradayMovementsBenchmarkTick[] = effectiveBenchRows.map((r) => ({
    time: r.time,
    benchmark_price_pct: toNum(r.benchmark_price_pct),
  }));

  const industry_series: IntradayMovementsIndustryTick[] = filteredIndustryRows.map((r) => ({
    time: r.time,
    industry_id: r.industry_id,
    industry_label: r.industry_label ?? r.industry_id,
    is_strategy: r.is_industry_not_strategy !== true,
    industry_price_pct: toNum(r.industry_price_pct),
    industry_price_pct_vs_benchmark: toNum(r.industry_price_pct_vs_benchmark),
  }));

  const member_series: IntradayMovementsMemberTick[] = filteredMemberRows.map((r) => ({
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
//  the live tick table, enriched with display name and is_broad_market flag.
// ----------------------------------------------------------------------------
export async function listIntradayMovementsBenchmarks(): Promise<{
  benchmark_code: string;
  benchmark_name: string;
  is_broad_market: boolean | null;
}[]> {
  // Summary rollup first (millisecond read); the live variant is only used
  // while the rollup has no pair-grain rows yet (fresh DB before the first
  // builds.cross_stats run).
  const summaryProbe = await querySqlView<{ ok: number }>(
    "data_viz/api/services/analysis/intraday-movements/summary_probe",
  );
  const rows = await querySqlView<DbBenchmarkListRow>(
    "data_viz/api/services/analysis/intraday-movements/benchmark_list",
    { all_curated_with_data: sqlFrag(buildAllCuratedWithDataCte(summaryProbe.length > 0)) },
  );
  return rows.map((r) => ({
    benchmark_code: r.benchmark_code,
    benchmark_name: r.benchmark_name ?? r.benchmark_code,
    is_broad_market: r.is_broad_market === null ? null : Boolean(r.is_broad_market),
  }));
}

// ----------------------------------------------------------------------------
//  listIntradayMovementsDates — distinct dates available for ONE benchmark
//  (union of raw intraday bars + live tick rows), newest first. Drives the
//  page's date selector. The SELECT lives in the sql view
//  data_viz/api/services/analysis/intraday-movements/available_dates.
// ----------------------------------------------------------------------------
export async function listIntradayMovementsDates(
  rawBenchmarkCode: string,
): Promise<string[]> {
  const benchmarkCode = (rawBenchmarkCode ?? "").trim();
  if (!benchmarkCode) {
    throw new Error("Missing 'benchmark_code' parameter");
  }
  interface DbDateRow extends QueryResultRow {
    date: string;
  }
  const rows = await querySqlView<DbDateRow>(
    "data_viz/api/services/analysis/intraday-movements/available_dates",
    { benchmark_code: benchmarkCode },
  );
  return rows.map((r) => r.date);
}
