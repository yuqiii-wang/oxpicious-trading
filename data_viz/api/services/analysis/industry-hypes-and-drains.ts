/**
 * Industry Hypes & Drains — SEASONAL (monthly) ranking service.
 *
 * Reads from analysis.industry_hypes_seasonal (built by
 * analyze.industry_sentiments.hypes_and_drains — truncate-then-recompute).
 * Returns ALL seasonal rankings (which industry is top/bottom 5 per month),
 * the benchmark's full daily price series, season boundary info, and each
 * ranked industry's full daily curve.
 *
 * Each industry's `rolling` value is the SHARED PORTFOLIO's trailing-N-day
 * 100-based factor, compounded in this service from the daily
 * decomposition identity (bench_1d, non-industry daily price, shared
 * weight) — the same math as the build. The frontend plots it directly as
 * the industry curve (rebased to 100 at the window start, like the
 * benchmark's own N-day rebasing).
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
  non_ind_price: number | null;
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

// Industry DAILY series for ALL ranked industries — the raw material for
// the shared-portfolio curve. Returns the DAILY non-this-industry price
// level + benchmark_shared_weight per date; the SERVICE compounds the
// daily decomposition identity into the trailing-N-day shared return
// (same math as analyze.industry_sentiments.hypes_and_drains) and outputs
// it as `rolling`. Reading the pre-materialized rolling_{N}days_price
// column instead would force the chart to invert it (1/swf amplification
// of its clamping/compounding artifacts — +-hundreds-of-percent noise for
// narrow industries).
//
// $1 = industry_ids (text[])
// $2 = benchmark_code
// $3 = attribution_type ('equal' | 'trading_amt' — matching the requested
//      weighting; each (industry, benchmark, date) has TWO rows, one per
//      type, and mixing them would corrupt the consecutive-day returns)
const INDUSTRY_SERIES_SQL = `
    SELECT
        ia.date,
        ia.industry_id,
        ia.benchmark_non_this_industry_price AS non_ind_price,
        ia.benchmark_shared_weight
    FROM analysis.industry_attributions ia
    WHERE ia.industry_id = ANY($1::text[])
      AND ia.benchmark_code = $2::text
      AND ia.attribution_type = $3::text
      AND ia.benchmark_non_this_industry_price IS NOT NULL
    ORDER BY ia.industry_id, ia.date
`;

// Compound the daily shared-return series into the trailing-N-day
// 100-based factor (the chart's industry curve). Mirrors the build SQL:
//   non_ind_1d = non_ind_price[t] / bench_close[t-1] - 1   (EXACT — the
//       build materializes the level as bench_close[t-1] * (1 + r_t);
//       ratios of consecutive price LEVELS instead carry a cross-term
//       that random-walks to ~±20% error over the window)
//   shared_1d  = (bench_1d - (1-swf) * non_ind_1d) / swf
//   factor_nd  = 100 * exp(SUM ln(1 + shared_1d))   over the FULL N-row
//               window; daily returns outside (-0.5, 0.5] contribute 0.
// Returns a map date -> factor (null when the window is not full / the
// daily legs are missing).
function buildSharedRollingFactor(
  rows: DbIndustrySeriesRow[],
  benchRet1dByDate: Map<string, number>,
  benchPrevCloseByDate: Map<string, number>,
  periodDays: number,
): Map<string, number | null> {
  const out = new Map<string, number | null>();
  const n = periodDays;
  // Sliding window of daily log contributions (null = invalid day) + a
  // running sum + non-null counter. Validity is tracked per-entry — a
  // numeric 0 sentinel would misclassify the rare shared_1d === 0 day
  // (log(1) = 0) and permanently deflate the window's validity count.
  const window: Array<number | null> = [];
  let sumLog = 0;
  let validCount = 0;
  for (let i = 0; i < rows.length; i++) {
    const row = rows[i];
    const date = formatDate(row.date);
    const sw = toNum(row.benchmark_shared_weight);
    const bench1d = benchRet1dByDate.get(date) ?? null;
    const benchPrevClose = benchPrevCloseByDate.get(date) ?? null;
    const curPrice = toNum(row.non_ind_price);
    // Exact daily non-industry return (NULL when the benchmark's previous
    // close is unknown — e.g. the series' first date).
    let nonInd1d: number | null = null;
    if (benchPrevClose != null && benchPrevClose !== 0 && curPrice != null) {
      nonInd1d = curPrice / benchPrevClose - 1;
    }
    let shared1d: number | null = null;
    if (sw != null && sw > 0 && sw < 100 && bench1d != null && nonInd1d != null) {
      const swf = sw / 100;
      shared1d = (bench1d - (1 - swf) * nonInd1d) / swf;
    }
    // Push this row's log contribution into the window (clamp: daily
    // returns outside (-0.5, 0.5] contribute 0 — same convention as the
    // attributions build's rolling columns).
    const entry =
      shared1d != null && shared1d > -0.5 && shared1d <= 0.5
        ? Math.log(1 + shared1d)
        : null;
    window.push(entry);
    if (entry != null) {
      sumLog += entry;
      validCount++;
    }
    if (window.length > n) {
      const dropped = window.shift()!;
      if (dropped != null) {
        sumLog -= dropped;
        validCount--;
      }
    }
    // Full window only — partial windows understate the compounded return.
    if (window.length === n && validCount === n) {
      out.set(date, 100 * Math.exp(sumLog));
    } else {
      out.set(date, null);
    }
  }
  return out;
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

  // Step 2: fetch the benchmark's full daily price series and derive the
  // daily close-to-close return map (the bench leg of the daily identity).
  const benchmarkRows = await queryRows<DbBenchmarkRow>(
    BENCHMARK_SERIES_SQL,
    [benchmarkCode],
  );
  const benchRet1dByDate = new Map<string, number>();
  const benchPrevCloseByDate = new Map<string, number>();
  let prevClose: number | null = null;
  for (const r of benchmarkRows) {
    const date = formatDate(r.date);
    const close = toNum(r.close);
    if (prevClose != null) {
      benchPrevCloseByDate.set(date, prevClose);
      if (prevClose !== 0 && close != null) {
        benchRet1dByDate.set(date, close / prevClose - 1);
      }
    }
    prevClose = close;
  }

  // Step 3: collect all unique industry_ids from the seasonal rankings.
  const industryIds = [...new Set(seasonalRows.map((r) => r.industry_id))];

  // Step 4: fetch each unique industry's full DAILY series for the
  // attribution type matching the requested weighting (the same type the
  // build ranked on).
  const attributionType = weighting === "amt" ? "trading_amt" : "equal";
  let industrySeriesRows: DbIndustrySeriesRow[] = [];
  if (industryIds.length > 0) {
    industrySeriesRows = await queryRows<DbIndustrySeriesRow>(
      INDUSTRY_SERIES_SQL,
      [industryIds, benchmarkCode, attributionType],
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

  // Step 7: group industry series by industry_id, compound the daily
  // identity into the trailing-N-day shared factor (100-based), and build
  // the response.
  const rowsByIndustry = new Map<string, DbIndustrySeriesRow[]>();
  for (const r of industrySeriesRows) {
    if (!rowsByIndustry.has(r.industry_id)) rowsByIndustry.set(r.industry_id, []);
    rowsByIndustry.get(r.industry_id)!.push(r);
  }

  // Build industry_label lookup from seasonal rankings (first occurrence).
  const labelByIndustry = new Map<string, string>();
  for (const r of seasonalRows) {
    if (!labelByIndustry.has(r.industry_id)) {
      labelByIndustry.set(r.industry_id, r.industry_label || r.industry_id);
    }
  }

  const industrySeries: HypesDrainsIndustrySeries[] = industryIds.map((id) => {
    const rawRows = rowsByIndustry.get(id) ?? [];
    const factorByDate = buildSharedRollingFactor(
      rawRows, benchRet1dByDate, benchPrevCloseByDate, periodDays,
    );
    return {
      industry_id: id,
      industry_label: labelByIndustry.get(id) ?? id,
      rows: rawRows.map((r) => ({
        date: formatDate(r.date),
        // The shared portfolio's trailing-N-day 100-based factor (the
        // chart's industry curve); null before the window fills.
        rolling: factorByDate.get(formatDate(r.date)) ?? null,
        benchmark_shared_weight: toNum(r.benchmark_shared_weight),
      })),
    };
  });

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
