/**
 * Industry Hypes & Drains — SEASONAL (monthly) ranking service.
 *
 * Reads from analysis.industry_hypes_seasonal (built by
 * analyze.industry_sentiments.hypes_and_drains — truncate-then-recompute).
 * Returns ALL seasonal rankings (which industry is top/bottom 5 per month),
 * the benchmark's full daily price series, season boundary info, and each
 * ranked industry's full daily rolling price series.
 *
 * The frontend uses the seasonal rankings to drive an ACTIVE/FADING/HIDDEN
 * state machine per industry per date:
 *   ACTIVE  — industry is in the current month's top/bottom 5 → full opacity
 *   FADING  — was ranked in a past month, not current, curve still on same
 *             side of benchmark → very light transparent
 *   HIDDEN  — curve crossed the benchmark, or never ranked → not rendered
 */
import { queryRows, formatDate, toNum } from "../../lib/db.js";
import type { QueryResultRow } from "pg";
import type {
  IndustryHypesAndDrainsResponse,
  HypesDrainsBenchmarkRow,
  HypesDrainsIndustrySeries,
  HypesDrainsIndustrySeriesRow,
  SeasonalRankingRow,
  SeasonInfo,
} from "../../../shared/types.js";

const VALID_PERIODS = new Set([5, 20, 60, 120, 255, 500]);
const VALID_WEIGHTINGS = new Set(["equal", "amt"]);

// ----------------------------------------------------------------------------
//  DB row interfaces
// ----------------------------------------------------------------------------
interface DbSeasonalRow extends QueryResultRow {
  season_qkey: string;
  rank_side: string;
  rank: number;
  industry_id: string;
  industry_label: string;
  peak_metric_value: number | null;
}

interface DbSeasonInfoRow extends QueryResultRow {
  season_qkey: string;
  season_start: Date | string;
  season_end: Date | string;
}

interface DbBenchmarkRow extends QueryResultRow {
  date: Date | string;
  close: number | null;
  daily_return: number | null;
  trading_amount: number | null;
}

interface DbIndustrySeriesRow extends QueryResultRow {
  date: Date | string;
  industry_id: string;
  rolling: number | null;
  benchmark_shared_weight: number | null;
}

// ----------------------------------------------------------------------------
//  SQL templates
// ----------------------------------------------------------------------------

// All seasonal rankings for the given (benchmark_code, period, weighting).
const SEASONAL_RANKINGS_SQL = `
    SELECT season_qkey, rank_side, rank, industry_id, industry_label,
           peak_metric_value
    FROM analysis.industry_hypes_seasonal
    WHERE benchmark_code = $1::text
      AND period_days = $2::int
      AND weighting = $3::text
    ORDER BY season_qkey, rank_side, rank
`;

// Distinct seasons with their date boundaries.
const SEASONS_SQL = `
    SELECT DISTINCT season_qkey, season_start, season_end
    FROM analysis.industry_hypes_seasonal
    WHERE benchmark_code = $1::text
      AND period_days = $2::int
      AND weighting = $3::text
    ORDER BY season_qkey
`;

// Benchmark price series — daily close + fractional daily return + trading
// amount. Same data as BenchmarkPriceChart's source (stats.index_basic_stats).
const BENCHMARK_SERIES_SQL = `
    SELECT
        ib.date,
        ib.close,
        CASE
            WHEN ib.close IS NOT NULL AND LAG(ib.close) OVER w IS NOT NULL
                 AND LAG(ib.close) OVER w != 0
            THEN (ib.close - LAG(ib.close) OVER w) / LAG(ib.close) OVER w
            ELSE NULL
        END AS daily_return,
        ib.trading_amount
    FROM stats.index_basic_stats ib
    WHERE ib.code = $1::text
    WINDOW w AS (ORDER BY ib.date)
    ORDER BY ib.date
`;

// Benchmark display name (from stats.index_identity).
const BENCHMARK_NAME_SQL = `
    SELECT DISTINCT ON (code) name
    FROM stats.index_identity
    WHERE code = $1::text
    ORDER BY code, date DESC
    LIMIT 1
`;

// Industry rolling price series for ALL ranked industries.
// Returns benchmark_non_this_industry_rolling_{N}days_price (the 100-based
// cumulative non-industry return factor) + benchmark_shared_weight per date.
//
// $1 = industry_ids (text[])
// $2 = benchmark_code
// $3 = rolling column name (frozen — derived from period_days, NOT user input)
const INDUSTRY_SERIES_SQL = (rollingCol: string) => `
    SELECT
        ia.date,
        ia.industry_id,
        ia.${rollingCol} AS rolling,
        ia.benchmark_shared_weight
    FROM analysis.industry_attributions ia
    WHERE ia.industry_id = ANY($1::text[])
      AND ia.benchmark_code = $2::text
      AND ia.${rollingCol} IS NOT NULL
    ORDER BY ia.industry_id, ia.date
`;

function _rollingCol(periodDays: number): string {
  return `benchmark_non_this_industry_rolling_${periodDays}days_price`;
}

// ----------------------------------------------------------------------------
//  getIndustryHypesAndDrains — main service function.
// ----------------------------------------------------------------------------
export async function getIndustryHypesAndDrains(
  rawBenchmarkCode: string,
  rawPeriodDays: string | number | null,
  rawWeighting: string | null,
): Promise<IndustryHypesAndDrainsResponse> {
  const benchmarkCode = (rawBenchmarkCode ?? "").trim();
  const periodDays = typeof rawPeriodDays === "number"
    ? rawPeriodDays
    : parseInt(String(rawPeriodDays ?? "120"), 10);
  const weighting = (rawWeighting ?? "equal").trim();

  if (!benchmarkCode) {
    throw new Error("Missing 'benchmark_code' parameter");
  }
  if (!VALID_PERIODS.has(periodDays)) {
    throw new Error(`Invalid period_days: must be one of 5, 20, 60, 120, 255, 500, got ${periodDays}`);
  }
  if (!VALID_WEIGHTINGS.has(weighting)) {
    throw new Error(`Invalid weighting: must be 'equal' or 'amt', got '${weighting}'`);
  }

  const rollingCol = _rollingCol(periodDays);

  // Step 1: fetch all seasonal rankings + season boundaries + benchmark
  // name — all in parallel.
  const [seasonalRows, seasonRows, nameRows] = await Promise.all([
    queryRows<DbSeasonalRow>(SEASONAL_RANKINGS_SQL, [benchmarkCode, periodDays, weighting]),
    queryRows<DbSeasonInfoRow>(SEASONS_SQL, [benchmarkCode, periodDays, weighting]),
    queryRows<{ name: string | null }>(BENCHMARK_NAME_SQL, [benchmarkCode]),
  ]);

  // Empty response when no seasonal data exists yet.
  if (seasonalRows.length === 0) {
    return {
      benchmark_code: benchmarkCode,
      benchmark_name: nameRows[0]?.name ?? benchmarkCode,
      period_days: periodDays,
      weighting: weighting as "equal" | "amt",
      benchmark_series: [],
      seasonal_rankings: [],
      seasons: [],
      industry_series: [],
    };
  }

  // Step 2: fetch the benchmark's full daily price series.
  const benchmarkRows = await queryRows<DbBenchmarkRow>(
    BENCHMARK_SERIES_SQL,
    [benchmarkCode],
  );

  // Step 3: collect all unique industry_ids from the seasonal rankings.
  const industryIds = [...new Set(seasonalRows.map((r) => r.industry_id))];

  // Step 4: fetch each unique industry's full daily rolling series.
  let industrySeriesRows: DbIndustrySeriesRow[] = [];
  if (industryIds.length > 0) {
    industrySeriesRows = await queryRows<DbIndustrySeriesRow>(
      INDUSTRY_SERIES_SQL(rollingCol),
      [industryIds, benchmarkCode],
    );
  }

  // Step 5: build seasonal ranking rows.
  const seasonalRankings: SeasonalRankingRow[] = seasonalRows.map((r) => ({
    season_qkey: r.season_qkey,
    rank_side: r.rank_side as "HYPE" | "DRAIN",
    rank: r.rank,
    industry_id: r.industry_id,
    industry_label: r.industry_label || r.industry_id,
    peak_metric_value: toNum(r.peak_metric_value),
  }));

  // Step 6: build season info rows.
  const seasons: SeasonInfo[] = seasonRows.map((r) => ({
    season_qkey: r.season_qkey,
    season_start: formatDate(r.season_start),
    season_end: formatDate(r.season_end),
  }));

  // Step 7: group industry series by industry_id and build the response.
  const seriesByIndustry = new Map<string, HypesDrainsIndustrySeriesRow[]>();
  for (const r of industrySeriesRows) {
    const id = r.industry_id;
    if (!seriesByIndustry.has(id)) seriesByIndustry.set(id, []);
    seriesByIndustry.get(id)!.push({
      date: formatDate(r.date),
      rolling: toNum(r.rolling),
      benchmark_shared_weight: toNum(r.benchmark_shared_weight),
    });
  }

  // Build industry_label lookup from seasonal rankings (first occurrence).
  const labelByIndustry = new Map<string, string>();
  for (const r of seasonalRows) {
    if (!labelByIndustry.has(r.industry_id)) {
      labelByIndustry.set(r.industry_id, r.industry_label || r.industry_id);
    }
  }

  const industrySeries: HypesDrainsIndustrySeries[] = industryIds.map((id) => ({
    industry_id: id,
    industry_label: labelByIndustry.get(id) ?? id,
    rows: seriesByIndustry.get(id) ?? [],
  }));

  // Step 8: build benchmark series.
  const benchmarkSeries: HypesDrainsBenchmarkRow[] = benchmarkRows.map((r) => ({
    date: formatDate(r.date),
    close: toNum(r.close),
    daily_return: toNum(r.daily_return),
    trading_amount: toNum(r.trading_amount),
  }));

  return {
    benchmark_code: benchmarkCode,
    benchmark_name: nameRows[0]?.name ?? benchmarkCode,
    period_days: periodDays,
    weighting: weighting as "equal" | "amt",
    benchmark_series: benchmarkSeries,
    seasonal_rankings: seasonalRankings,
    seasons,
    industry_series: industrySeries,
  };
}
