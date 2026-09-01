/**
 * Intraday Movements service — reads the LIVE schema tables populated by
 * `python -m live.sec_alloc_live_attribution` (the analyze.intraday_industry_
 * sentiments pipeline is NO LONGER needed for this page):
 *
 *   • live.sec_alloc_live_attribution — per-5-min-tick member % vs prev-day
 *     close + denormalized benchmark % + GENERATED diff (tick rows only for
 *     index/etf members; stocks hold weights in the ref but no ticks).
 *   • live.sec_alloc_live_prev_ref — per-(benchmark, date, code, sec_type)
 *     industry_id / is_industry_not_strategy / prev-day share weights.
 *
 * Industry-level aggregates are computed AT QUERY TIME (equal-weight AVG of
 * member pcts per (industry, tick)), so NO analyze recompute pass is required
 * for this page.
 *
 * Three SQL queries per request (all run in parallel):
 *   1. BENCHMARK_SERIES_SQL — benchmark_price_pct per 5-min tick (top plot
 *      main line). MAX() per tick collapses the denormalized per-member
 *      copies and skips NULL benchmark bars.
 *   2. INDUSTRY_SERIES_SQL — equal-weight industry % + diff vs benchmark
 *      per (tick, industry) for ALL industries (top plot shaded areas +
 *      middle plot bars at any clicked tick). Excludes BROAD_* industry_ids.
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
WITH
-- The curated broad-market benchmark set: indices carrying the
-- hand-authored BROAD/benchmark_broadmarket tag.
curated AS (
    SELECT DISTINCT t.code AS benchmark_code
    FROM stats.sec_index_tags t
    WHERE t.industry_id = 'benchmark_broadmarket'
),
processed AS (
    SELECT DISTINCT a.benchmark_code
    FROM live.sec_alloc_live_attribution a
    WHERE a.benchmark_code IN (SELECT benchmark_code FROM curated)
),
all_curated_with_data AS (
    -- Curated benchmarks that have attribution history and intraday data,
    -- used when the live pipeline hasn't processed them yet.
    SELECT DISTINCT sap.benchmark_code
    FROM analysis.sec_alloc_perf_attribution sap
    WHERE sap.sec_type = 'index'
      AND sap.benchmark_code IN (SELECT benchmark_code FROM curated)
      AND EXISTS (
          SELECT 1 FROM stats.index_intraday_5min i5
          WHERE i5.code = sap.benchmark_code AND i5.close IS NOT NULL
          LIMIT 1
      )
),
bench_codes AS (
    SELECT benchmark_code FROM processed
    UNION
    SELECT benchmark_code FROM all_curated_with_data
),
-- Legacy fallback: before the classification build materializes the
-- benchmark_broadmarket tag into stats.sec_index_tags, fall back to the
-- old is_broad_market selection so the dropdown is never empty.
legacy_broad AS (
    SELECT DISTINCT t.code AS benchmark_code
    FROM stats.sec_index_tags t
    WHERE t.is_broad_market = TRUE
),
final_codes AS (
    SELECT benchmark_code FROM bench_codes
    UNION ALL
    SELECT lb.benchmark_code
    FROM legacy_broad lb
    WHERE NOT EXISTS (SELECT 1 FROM stats.sec_index_tags t2 WHERE t2.industry_id = 'benchmark_broadmarket')
),
enriched AS (
    SELECT
        bc.benchmark_code,
        (SELECT name FROM stats.index_identity WHERE code = bc.benchmark_code ORDER BY date DESC LIMIT 1) AS benchmark_name,
        (SELECT BOOL_OR(is_broad_market) FROM stats.sec_index_tags WHERE code = bc.benchmark_code) AS is_broad_market
    FROM final_codes bc
)
SELECT * FROM enriched
ORDER BY benchmark_code
`;

// ----------------------------------------------------------------------------
//  SQL: Benchmark % change per 5-min tick (from the live tick table).
//  The benchmark % is denormalized onto every member row at each tick —
//  MAX() per time collapses the copies and ignores member rows whose
//  benchmark bar was NULL at that tick (all non-NULL copies are identical).
//  $1 = date, $2 = benchmark_code
// ----------------------------------------------------------------------------
const BENCHMARK_SERIES_SQL = `
SELECT
    time::text                                                    AS time,
    MAX(benchmark_price_pct_relative_prev_date_close)::float8      AS benchmark_price_pct
FROM live.sec_alloc_live_attribution
WHERE benchmark_code = $2::text
  AND date = $1::date
GROUP BY time
ORDER BY time
`;

// ----------------------------------------------------------------------------
//  SQL: Fallback benchmark % change — computed on-the-fly from the raw
//  intraday table + previous trading day's close. Used when the live tick
//  table hasn't been populated for the target date yet (e.g. the live
//  pipeline lags behind the raw data ingestion).
//  $1 = benchmark_code, $2 = date
// ----------------------------------------------------------------------------
const BENCHMARK_SERIES_FALLBACK_SQL = `
SELECT
    i5.time::text AS time,
    -- FRACTION (not percent): matches the live tables' scale convention
    -- (benchmark_price_pct_relative_prev_date_close etc. are fractions). The
    -- UI tooltip / yAxis formatters multiply by 100 at render time, and the
    -- top-plot shade builder diffs benchmark vs industry directly — a ×100
    -- mismatch here puts the benchmark 100x above every industry curve,
    -- breaking the benchmark-centered shades during live market hours
    -- (the fallback is active exactly then: raw ticks ahead of live ticks).
    ROUND((i5.close - bs.prev_close) / NULLIF(bs.prev_close, 0), 6) AS benchmark_price_pct
FROM stats.index_intraday_5min i5
CROSS JOIN (
    SELECT close AS prev_close
    FROM stats.index_basic_stats
    WHERE code = $1::text
      AND date < $2::date
      AND close IS NOT NULL
    ORDER BY date DESC
    LIMIT 1
) bs
WHERE i5.code = $1::text
  AND i5.date = $2::date
  AND i5.close IS NOT NULL
ORDER BY i5.time
`;

// ----------------------------------------------------------------------------
//  SQL: Industry % change per (tick, industry) — ALL industries, computed at
//  query time from the live tick table (equal-weight AVG of member pcts —
//  same semantics as the retired analysis pre-compute). Excludes BROAD_*
//  (broad-market indices that are themselves benchmarks, not real
//  industries). Industry identity prefers the ref's denormalized
//  industry_id and falls back to stats.sec_classification (fallback tick
//  rows have no ref parent). diff = AVG(member pct) − benchmark pct at the
//  tick so the UI top-plot shade can be driven directly.
//  $1 = date, $2 = benchmark_code
// ----------------------------------------------------------------------------
const INDUSTRY_SERIES_SQL = `
WITH cls AS (
    SELECT DISTINCT ON (code)
        code,
        industry_id,
        is_industry_not_strategy
    FROM stats.sec_classification
    WHERE is_active = TRUE
      AND industry_id IS NOT NULL AND industry_id <> ''
    ORDER BY code
),
base AS (
    SELECT
        a.time,
        a.code_price_pct_relative_prev_date_close          AS pct,
        a.benchmark_price_pct_relative_prev_date_close     AS bench_pct,
        COALESCE(r.industry_id, cls.industry_id)           AS industry_id,
        COALESCE(r.is_industry_not_strategy, cls.is_industry_not_strategy, TRUE)
                                                         AS is_industry_not_strategy
    FROM live.sec_alloc_live_attribution a
    LEFT JOIN live.sec_alloc_live_prev_ref r
        ON r.benchmark_code = a.benchmark_code
       AND r.date = a.date
       AND r.code = a.code
       AND r.sec_type = a.sec_type
    LEFT JOIN cls ON cls.code = a.code
    WHERE a.benchmark_code = $2::text
      AND a.date = $1::date
)
SELECT
    b.time::text                     AS time,
    b.industry_id,
    BOOL_OR(b.is_industry_not_strategy) AS is_industry_not_strategy,
    COALESCE(sc.industry_label, b.industry_id) AS industry_label,
    AVG(b.pct)::float8               AS industry_price_pct,
    (AVG(b.pct) - MAX(b.bench_pct))::float8
                                     AS industry_price_pct_vs_benchmark
FROM base b
LEFT JOIN LATERAL (
    SELECT industry_label
    FROM stats.sec_classification
    WHERE industry_id = b.industry_id AND type = 'index'
    LIMIT 1
) sc ON true
WHERE b.industry_id IS NOT NULL
  AND b.industry_id NOT LIKE 'BROAD_%'
GROUP BY b.time, b.industry_id, sc.industry_label
ORDER BY b.time, b.industry_id
`;

// ----------------------------------------------------------------------------
//  SQL: Fallback industry % change — equal-weight industry aggregates computed
//  on-the-fly from the RAW intraday table when the live tick table has no rows
//  for the target date (the live pipeline is LATEST-date-scoped by design, so
//  a benchmark whose latest raw date lags the global latest — or any date the
//  live keeper never reached — would otherwise render benchmark line but NO
//  industry shades). Mirrors the live pipeline's ref-less FALLBACK semantics:
//  member universe = active classification indices/ETFs with a non-BROAD
//  industry (benchmark itself excluded); prev close = the member's latest
//  close strictly before the target date from stats.index_basic_stats (same
//  source as BENCHMARK_SERIES_FALLBACK_SQL and the live pipeline's weighted
//  ref — the raw intraday table's historical code coverage can lag the
//  classification universe); pct = close/prev_close - 1 (FRACTION, same
//  scale as live rows).
//  $1 = benchmark_code, $2 = date
// ----------------------------------------------------------------------------
const INDUSTRY_SERIES_FALLBACK_SQL = `
WITH cls AS (
    SELECT DISTINCT ON (code)
        code,
        industry_id,
        is_industry_not_strategy
    FROM stats.sec_classification
    WHERE is_active = TRUE
      AND industry_id IS NOT NULL AND industry_id <> ''
    ORDER BY code
),
universe AS (
    SELECT
        sc.code,
        cls.industry_id,
        COALESCE(cls.is_industry_not_strategy, TRUE) AS is_industry_not_strategy
    FROM stats.sec_classification sc
    JOIN cls ON cls.code = sc.code
    WHERE sc.is_active = TRUE
      AND sc.type IN ('index', 'etf')
      AND cls.industry_id NOT LIKE 'BROAD_%'
      AND sc.code <> $1::text
),
prev_d AS (
    SELECT MAX(date) AS d
    FROM stats.index_basic_stats
    WHERE date < $2::date AND close IS NOT NULL
),
member_prev_close AS (
    SELECT b.code, b.close AS prev_close
    FROM stats.index_basic_stats b
    WHERE b.date = (SELECT d FROM prev_d)
      AND b.close IS NOT NULL AND b.close::text <> 'NaN'
      AND b.code IN (SELECT code FROM universe)
),
bench_prev_close AS (
    SELECT close AS prev_close
    FROM stats.index_basic_stats
    WHERE code = $1::text
      AND date = (SELECT d FROM prev_d)
      AND close IS NOT NULL AND close::text <> 'NaN'
    LIMIT 1
),
bench_ticks AS (
    SELECT bb.time, (bb.close / bpc.prev_close - 1) AS bench_pct
    FROM stats.index_intraday_5min bb
    CROSS JOIN bench_prev_close bpc
    WHERE bb.code = $1::text
      AND bb.date = $2::date
      AND bb.close IS NOT NULL AND bb.close::text <> 'NaN'
),
member_ticks AS (
    SELECT i5.code, i5.time,
           (i5.close / mp.prev_close - 1) AS pct
    FROM stats.index_intraday_5min i5
    JOIN member_prev_close mp ON mp.code = i5.code
    WHERE i5.date = $2::date
      AND i5.close IS NOT NULL AND i5.close::text <> 'NaN'
)
SELECT
    m.time::text                            AS time,
    u.industry_id,
    BOOL_OR(u.is_industry_not_strategy)     AS is_industry_not_strategy,
    COALESCE(sc.industry_label, u.industry_id) AS industry_label,
    AVG(m.pct)::float8                      AS industry_price_pct,
    (AVG(m.pct) - MAX(bt.bench_pct))::float8 AS industry_price_pct_vs_benchmark
FROM member_ticks m
JOIN universe u ON u.code = m.code
LEFT JOIN bench_ticks bt ON bt.time = m.time
LEFT JOIN LATERAL (
    SELECT industry_label
    FROM stats.sec_classification
    WHERE industry_id = u.industry_id AND type = 'index'
    LIMIT 1
) sc ON true
GROUP BY m.time, u.industry_id, sc.industry_label
ORDER BY m.time, u.industry_id
`;

// ----------------------------------------------------------------------------
//  SQL: Fallback member index % change — per (code, tick) pcts computed from
//  the RAW intraday table (same universe/prev-close semantics as the industry
//  fallback above). Drives the bottom plot when live tick rows are missing.
//  $1 = benchmark_code, $2 = date
// ----------------------------------------------------------------------------
const MEMBER_SERIES_FALLBACK_SQL = `
WITH cls AS (
    SELECT DISTINCT ON (code)
        code,
        industry_id
    FROM stats.sec_classification
    WHERE is_active = TRUE
      AND industry_id IS NOT NULL AND industry_id <> ''
    ORDER BY code
),
universe AS (
    SELECT sc.code, cls.industry_id
    FROM stats.sec_classification sc
    JOIN cls ON cls.code = sc.code
    WHERE sc.is_active = TRUE
      AND sc.type IN ('index', 'etf')
      AND cls.industry_id NOT LIKE 'BROAD_%'
      AND sc.code <> $1::text
),
prev_d AS (
    SELECT MAX(date) AS d
    FROM stats.index_basic_stats
    WHERE date < $2::date AND close IS NOT NULL
),
member_prev_close AS (
    SELECT b.code, b.close AS prev_close
    FROM stats.index_basic_stats b
    WHERE b.date = (SELECT d FROM prev_d)
      AND b.close IS NOT NULL AND b.close::text <> 'NaN'
      AND b.code IN (SELECT code FROM universe)
)
SELECT
    i5.time::text                     AS time,
    i5.code,
    COALESCE(ii.name, i5.code)        AS code_name,
    u.industry_id,
    (i5.close / mp.prev_close - 1)::float8 AS code_price_pct
FROM stats.index_intraday_5min i5
JOIN universe u ON u.code = i5.code
JOIN member_prev_close mp ON mp.code = i5.code
LEFT JOIN LATERAL (
    SELECT name FROM stats.index_identity
    WHERE code = i5.code ORDER BY date DESC LIMIT 1
) ii ON true
WHERE i5.date = $2::date
  AND i5.close IS NOT NULL AND i5.close::text <> 'NaN'
ORDER BY i5.time, u.industry_id, i5.code
`;

// ----------------------------------------------------------------------------
//  SQL: Member index % change per (code, tick, industry) — ALL members with
//  tick rows (index/etf members; stocks carry no ticks by design).
//  $1 = date, $2 = benchmark_code
// ----------------------------------------------------------------------------
const MEMBER_SERIES_SQL = `
WITH cls AS (
    SELECT DISTINCT ON (code)
        code,
        industry_id
    FROM stats.sec_classification
    WHERE is_active = TRUE
      AND industry_id IS NOT NULL AND industry_id <> ''
    ORDER BY code
)
SELECT
    a.time::text                                      AS time,
    a.code,
    COALESCE(ii.name, a.code)                         AS code_name,
    COALESCE(r.industry_id, cls.industry_id)          AS industry_id,
    a.code_price_pct_relative_prev_date_close         AS code_price_pct
FROM live.sec_alloc_live_attribution a
LEFT JOIN live.sec_alloc_live_prev_ref r
    ON r.benchmark_code = a.benchmark_code
   AND r.date = a.date
   AND r.code = a.code
   AND r.sec_type = a.sec_type
LEFT JOIN cls ON cls.code = a.code
LEFT JOIN LATERAL (
    SELECT name FROM stats.index_identity
    WHERE code = a.code ORDER BY date DESC LIMIT 1
) ii ON true
WHERE a.benchmark_code = $2::text
  AND a.date = $1::date
ORDER BY a.time, COALESCE(r.industry_id, cls.industry_id), a.code
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
  // Prefer the latest date from the RAW intraday table (stats.index_intraday_5min)
  // so the page always shows the most recent trading day, even when the live
  // tick pipeline hasn't ingested that date yet.
  let targetDate: string | null = date;
  if (!targetDate) {
    const rawDateRows = await queryRows<DbLatestDateRow>(
      `SELECT MAX(date) AS max_date FROM stats.index_intraday_5min WHERE close IS NOT NULL AND code = $1::text`,
      [benchmarkCode],
    );
    targetDate = rawDateRows[0]?.max_date ? formatDate(rawDateRows[0].max_date) : null;
    // Fallback: if raw table has no data, try the live tick table.
    if (!targetDate) {
      const liveDateRows = await queryRows<DbLatestDateRow>(
        `SELECT MAX(date) AS max_date FROM live.sec_alloc_live_attribution WHERE benchmark_code = $1::text`,
        [benchmarkCode],
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
    queryRows<DbBenchmarkTickRow>(BENCHMARK_SERIES_SQL, [targetDate, benchmarkCode]),
    queryRows<DbIndustryTickRow>(INDUSTRY_SERIES_SQL, [targetDate, benchmarkCode]),
    queryRows<DbMemberTickRow>(MEMBER_SERIES_SQL, [targetDate, benchmarkCode]),
    queryRows<DbBenchmarkNameRow>(
      `SELECT name FROM stats.index_identity WHERE code = $1::text ORDER BY date DESC LIMIT 1`,
      [benchmarkCode],
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
    const rawLatestRows = await queryRows<DbBenchmarkTickRow>(
      `SELECT time::text AS time FROM stats.index_intraday_5min WHERE code = $1::text AND date = $2::date AND close IS NOT NULL ORDER BY time DESC LIMIT 1`,
      [benchmarkCode, targetDate],
    );
    const rawLatestTime = rawLatestRows[0]?.time ?? "";
    const liveLatestTime = effectiveBenchRows[effectiveBenchRows.length - 1].time;
    if (rawLatestTime && rawLatestTime > liveLatestTime) {
      useFallback = true;
    }
  }
  if (useFallback) {
    const fallbackRows = await queryRows<DbBenchmarkTickRow>(
      BENCHMARK_SERIES_FALLBACK_SQL,
      [benchmarkCode, targetDate],
    );
    effectiveBenchRows = fallbackRows;
  }

  // Step 3: filter ALL series to only include ticks that exist in the
  // raw benchmark intraday table. This excludes extended-hours ticks
  // (e.g. 09:30 pre-open, 15:05+ post-close) that member indices may
  // have but the benchmark doesn't.
  const rawTickRows = await queryRows<DbBenchmarkTickRow>(
    `SELECT DISTINCT time::text AS time FROM stats.index_intraday_5min WHERE code = $1::text AND date = $2::date AND close IS NOT NULL ORDER BY time`,
    [benchmarkCode, targetDate],
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
    const fbIndustryRows = await queryRows<DbIndustryTickRow>(
      INDUSTRY_SERIES_FALLBACK_SQL,
      [benchmarkCode, targetDate],
    );
    filteredIndustryRows = fbIndustryRows.filter((r) => validTickTimes.has(r.time));
  }
  if (filteredMemberRows.length === 0) {
    const fbMemberRows = await queryRows<DbMemberTickRow>(
      MEMBER_SERIES_FALLBACK_SQL,
      [benchmarkCode, targetDate],
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
  const rows = await queryRows<DbBenchmarkListRow>(BENCHMARK_LIST_SQL, []);
  return rows.map((r) => ({
    benchmark_code: r.benchmark_code,
    benchmark_name: r.benchmark_name ?? r.benchmark_code,
    is_broad_market: r.is_broad_market === null ? null : Boolean(r.is_broad_market),
  }));
}

// ----------------------------------------------------------------------------
//  listIntradayMovementsDates — distinct dates available for ONE benchmark
//  (union of raw intraday bars + live tick rows), newest first. Drives the
//  page's date selector.
//  $1 = benchmark_code
// ----------------------------------------------------------------------------
const DATES_SQL = `
SELECT DISTINCT date::text AS date
FROM (
    SELECT date
    FROM stats.index_intraday_5min
    WHERE code = $1::text AND close IS NOT NULL
    UNION
    SELECT date
    FROM live.sec_alloc_live_attribution
    WHERE benchmark_code = $1::text
) d
ORDER BY date DESC
`;

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
  const rows = await queryRows<DbDateRow>(DATES_SQL, [benchmarkCode]);
  return rows.map((r) => r.date);
}
