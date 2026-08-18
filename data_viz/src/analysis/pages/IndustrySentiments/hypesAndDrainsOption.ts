/**
 * Build the ECharts option for the Industry Hypes & Drains SEASONAL chart.
 *
 * PLOT STYLE
 *   Benchmark line (rebased to 100) with each industry's OWN return curve
 *   overlaid. Green shade = curve above benchmark (HYPE), red shade =
 *   curve below benchmark (DRAIN).
 *
 * INDUSTRY CURVE FORMULA (industry's own return, NOT ex-industry benchmark)
 *   Given the identity: bench_return = swf × ind_return + (1-swf) × non_ind_return
 *   Solve for ind_return:
 *     ind_return = (bench_return - (1-swf) × non_ind_return) / swf
 *   where swf = benchmark_shared_weight / 100 (0..1)
 *         non_ind_return = rolling / 100 - 1  (from the non-this-industry
 *                        rolling price column, which is a 100-based factor)
 *   The curve is rebased to 100:  curve(t) = 100 × (1 + ind_return(t))
 *
 *   With this formula, HYPE industries (ind_return > bench_return) plot
 *   ABOVE the benchmark, and DRAIN industries plot BELOW — intuitive.
 *
 * SEASONAL STATE MACHINE
 *   Industries are ranked per CALENDAR MONTH. The plot is daily, but WHICH
 *   industries appear and at what OPACITY depends on the seasonal ranking:
 *
 *   ACTIVE  — industry is in the current month's top/bottom 5.
 *             Full opacity line + shade.
 *   FADING  — was ranked in a past month, NOT in the current month, but
 *             the curve is still on the SAME side of the benchmark (HYPE:
 *             above, DRAIN: below). Very light transparent line, no shade.
 *   HIDDEN  — curve has CROSSED the benchmark (flipped sides), or the
 *             industry was never ranked. Null (not rendered).
 *
 *   Once HIDDEN, the industry can only reappear as ACTIVE (when it returns
 *   to the top/bottom 5 in a future month). It CANNOT go HIDDEN → FADING.
 */
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import type {
  IndustryHypesAndDrainsResponse,
  SeasonalRankingRow,
} from "@shared/types";
import {
  UP_COLOR,
  DOWN_COLOR,
  PALETTE_HI,
  axisColors,
  commonLegend,
  commonGrid,
  commonDataZoom,
} from "@/theme/chart-palette";
import { fmtNum } from "@/lib/series";
import { buildBenchmarkCenteredShadeSeries } from "@/lib/benchmark-shade";

// ----------------------------------------------------------------------------
//  Types
// ----------------------------------------------------------------------------

/** Per-date visual state for an industry curve. */
const ACTIVE = 2;
const FADING = 1;
const HIDDEN = 0;

/** One industry's computed state + display data, ready for ECharts series. */
interface IndustryComputed {
  industry_id: string;
  industry_label: string;
  rank_side: "HYPE" | "DRAIN";
  /** Latest season's rank (1-3), for the legend label. */
  latest_rank: number;
  /** Per-date state: ACTIVE / FADING / HIDDEN (aligned to benchmark dates). */
  states: Uint8Array;
  /** Per-date display values (rebased to 100), aligned to benchmark dates. */
  displayValues: Array<number | null>;
  /** Per-date industry return (fractional, for tooltip). */
  industryReturns: Array<number | null>;
}

// ----------------------------------------------------------------------------
//  Helpers
// ----------------------------------------------------------------------------

/** Convert a YYYY-MM-DD date string to a season key like "2026-08". */
function dateToSeason(date: string): string {
  return date.slice(0, 7);
}

/** Format a fractional value as a signed percentage string. */
function fmtPctSigned(v: number | null, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return (v >= 0 ? "+" : "") + fmtNum(v * 100, digits) + "%";
}

// ----------------------------------------------------------------------------
//  State machine: compute per-date ACTIVE/FADING/HIDDEN for one industry
// ----------------------------------------------------------------------------

/**
 * Build a map: season_qkey → { rank_side, rank } for the given industry.
 * If an industry is ranked as BOTH HYPE and DRAIN in the same season, pick
 * the one with the higher |peak_metric_value|.
 */
function buildSeasonMap(
  rankings: SeasonalRankingRow[],
  industryId: string,
): Map<string, { rank_side: "HYPE" | "DRAIN"; rank: number }> {
  const map = new Map<string, { rank_side: "HYPE" | "DRAIN"; rank: number }>();
  for (const r of rankings) {
    if (r.industry_id !== industryId) continue;
    const existing = map.get(r.season_qkey);
    if (existing) {
      // Pick the one with higher |peak_metric_value|
      const existingRankings = rankings.filter(
        (x) => x.industry_id === industryId && x.season_qkey === r.season_qkey,
      );
      const hypeEntry = existingRankings.find((x) => x.rank_side === "HYPE");
      const drainEntry = existingRankings.find((x) => x.rank_side === "DRAIN");
      if (hypeEntry && drainEntry) {
        if (
          Math.abs(hypeEntry.peak_metric_value ?? 0) >=
          Math.abs(drainEntry.peak_metric_value ?? 0)
        ) {
          map.set(r.season_qkey, {
            rank_side: "HYPE",
            rank: hypeEntry.rank,
          });
        } else {
          map.set(r.season_qkey, {
            rank_side: "DRAIN",
            rank: drainEntry.rank,
          });
        }
      }
    } else {
      map.set(r.season_qkey, {
        rank_side: r.rank_side,
        rank: r.rank,
      });
    }
  }
  return map;
}

/**
 * Compute per-date state for one industry using the state machine.
 *
 * With the industry-own-return curve:
 *   HYPE: curve ABOVE benchmark (displayValue > 100) → FADING while above
 *   DRAIN: curve BELOW benchmark (displayValue < 100) → FADING while below
 *
 * @param dates          Benchmark dates (chronological).
 * @param displayValues  Industry's own-return curve (rebased to 100, aligned to dates).
 * @param seasonMap      season_qkey → { rank_side, rank } for this industry.
 * @returns Uint8Array of ACTIVE/FADING/HIDDEN per date.
 */
function computeStates(
  dates: string[],
  displayValues: Array<number | null>,
  seasonMap: Map<string, { rank_side: "HYPE" | "DRAIN"; rank: number }>,
): { states: Uint8Array; lastRankSide: "HYPE" | "DRAIN" } {
  const n = dates.length;
  const states = new Uint8Array(n);
  let currentState = HIDDEN;
  let currentRankSide: "HYPE" | "DRAIN" | null = null;

  for (let i = 0; i < n; i++) {
    const season = dateToSeason(dates[i]);
    const ranked = seasonMap.get(season);
    const dv = displayValues[i];

    if (ranked) {
      // Industry is ranked in this season → ACTIVE
      currentState = ACTIVE;
      currentRankSide = ranked.rank_side;
      states[i] = ACTIVE;
    } else {
      // Not ranked in this season
      if (currentState === HIDDEN || currentRankSide === null) {
        states[i] = HIDDEN;
      } else {
        if (dv == null) {
          currentState = HIDDEN;
          states[i] = HIDDEN;
        } else if (currentRankSide === "HYPE") {
          // HYPE: curve should be ABOVE benchmark (dv >= 100)
          if (dv >= 100) {
            currentState = FADING;
            states[i] = FADING;
          } else {
            // Crossed below benchmark → HIDDEN
            currentState = HIDDEN;
            states[i] = HIDDEN;
          }
        } else {
          // DRAIN: curve should be BELOW benchmark (dv <= 100)
          if (dv <= 100) {
            currentState = FADING;
            states[i] = FADING;
          } else {
            // Crossed above benchmark → HIDDEN
            currentState = HIDDEN;
            states[i] = HIDDEN;
          }
        }
      }
    }
  }

  return { states, lastRankSide: currentRankSide ?? "HYPE" };
}

// ----------------------------------------------------------------------------
//  Main option builder
// ----------------------------------------------------------------------------

export function buildHypesAndDrainsOption(
  data: IndustryHypesAndDrainsResponse,
  themeMode: ThemeMode,
  selectedDate?: string | null,
  maxRank: number = 3,
): EChartsOption {
  const c = axisColors(themeMode);
  const allDates = data.benchmark_series.map((r) => r.date);
  const allCloses = data.benchmark_series.map((r) => r.close);
  const allReturns = data.benchmark_series.map((r) => r.daily_return);
  const totalN = allDates.length;

  if (totalN === 0) {
    return { backgroundColor: "transparent", animation: false };
  }

  // ---- Filter seasonal rankings by maxRank (default 3, toggleable to 5) ----
  // Industries with rank > maxRank in ALL seasons will be skipped (seasonMap
  // empty). Industries ranked ≤ maxRank in at least one season stay computed;
  // in seasons where their rank exceeds maxRank they fall to FADING/HIDDEN.
  const filteredRankings = data.seasonal_rankings.filter((r) => r.rank <= maxRank);

  // ---- Compute benchmark values (rebase to 100 at first non-null close) ----
  const firstClose = allCloses.find((v) => v != null && v !== 0) ?? null;
  const benchmarkValues: Array<number | null> = firstClose
    ? allCloses.map((v) => (v != null ? (v / firstClose) * 100 : null))
    : allCloses.map(() => null);

  // ---- Compute benchmark N-day return for each date (for industry return formula) ----
  // The industry's own return is derived from the identity:
  //   bench_return = swf × ind_return + (1-swf) × non_ind_return
  // We compute bench_return from closes: close(t) / close(t-N) - 1
  // where N = data.period_days.
  const n = totalN;
  const dates = allDates;
  const closes = allCloses;
  const returns = allReturns;

  // ---- Compute each industry's state + display values ----
  const computed: IndustryComputed[] = [];

  for (const ind of data.industry_series) {
    // Build rolling + shared_weight lookup aligned to benchmark dates
    const rollingByDate = new Map<string, number | null>();
    const swByDate = new Map<string, number | null>();
    for (const r of ind.rows) {
      rollingByDate.set(r.date, r.rolling);
      swByDate.set(r.date, r.benchmark_shared_weight);
    }

    // Build season map for this industry (using rank-filtered rankings)
    const seasonMap = buildSeasonMap(filteredRankings, ind.industry_id);
    if (seasonMap.size === 0) continue; // industry has no rankings (all > maxRank) — skip

    // Compute industry's own return for each date using the identity:
    //   bench_return = swf × ind_return + (1-swf) × non_ind_return
    //   → ind_return = (bench_return - (1-swf) × non_ind_return) / swf
    //   displayValue = 100 × (1 + ind_return)
    const periodDays = data.period_days;

    const displayValues: Array<number | null> = new Array(n).fill(null);
    const industryReturns: Array<number | null> = new Array(n).fill(null);

    for (let i = 0; i < n; i++) {
      const rolling = rollingByDate.get(dates[i]) ?? null;
      const sw = swByDate.get(dates[i]) ?? null;
      const close = closes[i];
      if (rolling == null || sw == null || close == null || sw <= 0 || sw >= 95) continue;

      // Compute benchmark N-day return: close(i) / close(i - periodDays) - 1
      const lookbackIdx = i - periodDays;
      if (lookbackIdx < 0) continue;
      const closeNago = closes[lookbackIdx];
      if (closeNago == null || closeNago === 0) continue;

      const benchRet = close / closeNago - 1;
      const nonIndRet = rolling / 100 - 1;
      const swf = sw / 100;

      // Industry return: (benchRet - (1-swf) × nonIndRet) / swf
      const indRet = (benchRet - (1 - swf) * nonIndRet) / swf;
      industryReturns[i] = indRet;
      displayValues[i] = 100 * (1 + indRet);
    }

    // Compute states
    const { states, lastRankSide } = computeStates(dates, displayValues, seasonMap);

    // Find latest season's rank for the legend label
    const sortedSeasons = Array.from(seasonMap.keys()).sort();
    const latestSeason = sortedSeasons[sortedSeasons.length - 1];
    const latestInfo = seasonMap.get(latestSeason);
    const latest_rank = latestInfo?.rank ?? 1;

    computed.push({
      industry_id: ind.industry_id,
      industry_label: ind.industry_label,
      rank_side: lastRankSide,
      latest_rank,
      states,
      displayValues,
      industryReturns,
    });
  }

  // ---- Variance-based y-axis scaling: ignore extreme curves ----
  // Some industry curves "shoot too high" (high variance) and would compress
  // all other curves into a flat band. We compute the variance of each
  // industry's ACTIVE-period displayValues, detect outliers (variance >
  // 2 × median variance), and set explicit y-axis min/max from the
  // non-outlier curves + benchmark. Extreme curves are STILL PLOTTED — their
  // peaks/lows simply overflow the y-axis limit and are clipped (hidden).
  const curveVariances: number[] = [];
  const curveMinVals: number[] = [];
  const curveMaxVals: number[] = [];

  for (const ind of computed) {
    const activeVals: number[] = [];
    for (let i = 0; i < n; i++) {
      if (ind.states[i] === ACTIVE && ind.displayValues[i] != null) {
        activeVals.push(ind.displayValues[i]!);
      }
    }
    if (activeVals.length < 2) {
      curveVariances.push(0);
      curveMinVals.push(Infinity);
      curveMaxVals.push(-Infinity);
      continue;
    }
    const mean = activeVals.reduce((a, b) => a + b, 0) / activeVals.length;
    const variance = activeVals.reduce((a, b) => a + (b - mean) ** 2, 0) / activeVals.length;
    curveVariances.push(variance);
    curveMinVals.push(Math.min(...activeVals));
    curveMaxVals.push(Math.max(...activeVals));
  }

  // Detect extreme curves via median-multiplier on variance. Requires at
  // least 4 curves with non-zero variance for a robust median estimate.
  const nonZeroVariances = curveVariances.filter((v) => v > 0).sort((a, b) => a - b);
  const isExtremeCurve = new Array(computed.length).fill(false);
  if (nonZeroVariances.length >= 4) {
    const mid = Math.floor(nonZeroVariances.length / 2);
    const medianVar = nonZeroVariances.length % 2 === 0
      ? (nonZeroVariances[mid - 1] + nonZeroVariances[mid]) / 2
      : nonZeroVariances[mid];
    const varianceThreshold = medianVar * 2;
    for (let i = 0; i < computed.length; i++) {
      if (curveVariances[i] > varianceThreshold) {
        isExtremeCurve[i] = true;
      }
    }
  }

  // Compute y-axis bounds from non-extreme curves + benchmark (always
  // included). Falls back to null (auto-scale) if no valid bounds.
  let yMin = Infinity;
  let yMax = -Infinity;
  for (const v of benchmarkValues) {
    if (v != null) {
      yMin = Math.min(yMin, v);
      yMax = Math.max(yMax, v);
    }
  }
  for (let i = 0; i < computed.length; i++) {
    if (isExtremeCurve[i]) continue;
    yMin = Math.min(yMin, curveMinVals[i]);
    yMax = Math.max(yMax, curveMaxVals[i]);
  }
  let yAxisBounds: { min: number; max: number } | null = null;
  if (yMin !== Infinity && yMax !== -Infinity && yMax > yMin) {
    const range = yMax - yMin;
    const pad = range * 0.05;
    yAxisBounds = { min: yMin - pad, max: yMax + pad };
  }

  // ---- Build series array ----
  // The benchmark line + per-industry layered shades are built via the SHARED
  // benchmark-centered shade helper (same builder used by Market Movements).
  // The shade is centered about the BENCHMARK line (rebased to 100 here):
  // green (UP_COLOR) fills benchmark→industry when industry > benchmark
  // (HYPE), red (DOWN_COLOR) fills industry→benchmark when below (DRAIN).
  // Only ACTIVE-period ticks are shaded; FADING/HIDDEN get null via the
  // visible mask. Opacity 0.35 (heavier than Market Movements' 0.15 because
  // only the top/bottom 3-5 ACTIVE industries are shaded at any time).
  const series: EChartsOption["series"] = [];

  // 1. Benchmark line
  const benchmarkName = `${data.benchmark_name} (100-based)`;
  series.push({
    name: benchmarkName,
    type: "line",
    data: benchmarkValues,
    showSymbol: false,
    lineStyle: { color: PALETTE_HI, width: 1.5 },
    itemStyle: { color: PALETTE_HI },
    z: 10,
    // Vertical markLine at the user-clicked date — visual indicator for
    // which month the detail table below is reflecting.
    markLine: selectedDate
      ? {
          symbol: "none",
          silent: true,
          label: { show: false },
          lineStyle: { color: c.textColor, type: "dashed", width: 1, opacity: 0.5 },
          data: [{ xAxis: selectedDate }],
        }
      : undefined,
  });

  // 2. Per-industry layered shades via the shared builder.
  //    visible[i] = (state === ACTIVE) — only ACTIVE periods get shaded.
  const shadeIndustries = computed.map((ind) => ({
    id: ind.industry_id,
    label: `${ind.rank_side === "HYPE" ? "▲" : "▼"} #${ind.latest_rank} ${ind.industry_label}`,
    values: ind.displayValues,
    visible: Array.from(ind.states, (s) => s === ACTIVE),
  }));
  const { series: shadeSeries, legendLabels: shadeLegendLabels } =
    buildBenchmarkCenteredShadeSeries(benchmarkValues, shadeIndustries, {
      shadeOpacity: 0.35,
      stackPrefix: "hdShade",
      zBase: 4,
    });
  for (const s of shadeSeries) series.push(s);

  // Legend data: benchmark first, then per-industry labels.
  const legendData: string[] = [benchmarkName, ...shadeLegendLabels];

  // Tooltip lookup arrays (one entry per computed industry, aligned to
  // shadeIndustries order so the tooltip can index by the same order).
  const indReturnsForTooltip: Array<Array<number | null>> = computed.map((ind) => ind.industryReturns);
  const indDisplayForTooltip: Array<Array<number | null>> = computed.map((ind) => ind.displayValues);
  const indStatesForTooltip: Array<Uint8Array> = computed.map((ind) => ind.states);
  const indLabelsForTooltip: Array<{ label: string; rank_side: string }> = computed.map((ind) => ({
    label: `${ind.rank_side === "HYPE" ? "▲" : "▼"} #${ind.latest_rank} ${ind.industry_label}`,
    rank_side: ind.rank_side,
  }));

  // ---- X-axis: year-month ticks at 3-month interval ----
  const displayMonths = new Set<string>();
  {
    const orderedMonths: string[] = [];
    const seen = new Set<string>();
    for (const d of dates) {
      const ym = d.slice(0, 7);
      if (!seen.has(ym)) {
        seen.add(ym);
        orderedMonths.push(ym);
      }
    }
    for (let i = 0; i < orderedMonths.length; i += 3) {
      displayMonths.add(orderedMonths[i]);
    }
  }
  const firstDateOfMonth = new Set<string>();
  {
    let prev = "";
    for (const d of dates) {
      const ym = d.slice(0, 7);
      if (ym !== prev) {
        firstDateOfMonth.add(d);
        prev = ym;
      }
    }
  }

  // ---- Tooltip ----
  const tooltipFormatter = (params: unknown): string => {
    const arr = (Array.isArray(params) ? params : [params]) as Array<{
      dataIndex?: number;
    }>;
    if (arr.length === 0) return "";
    const idx = arr[0].dataIndex ?? 0;
    const dt = dates[idx] ?? "—";
    const bv = benchmarkValues[idx];
    const rt = returns[idx];
    const rsign = rt == null ? "" : rt >= 0 ? "▲ " : "▼ ";
    let html = `
      <div style="font-weight:600">${data.benchmark_name} (${data.benchmark_code})</div>
      <div style="margin-top:2px">${dt}</div>
      <div>Rebased: <b>${bv == null ? "—" : fmtNum(bv, 2)}</b></div>
      <div>${rsign}Daily Return: <b style="color:${rt == null ? c.textColor : rt >= 0 ? UP_COLOR : DOWN_COLOR}">${fmtPctSigned(rt, 2)}</b></div>
    `;
    // Append each industry's value + state
    for (let i = 0; i < indLabelsForTooltip.length; i++) {
      const { label } = indLabelsForTooltip[i];
      const state = indStatesForTooltip[i][idx];
      const indRet = indReturnsForTooltip[i]?.[idx] ?? null;
      const iv = indDisplayForTooltip[i]?.[idx] ?? null;
      if (indRet == null || iv == null) {
        continue; // skip industries with no data on this date
      }
      const stateLabel = state === ACTIVE ? "●" : state === FADING ? "○" : "✕";
      const stateColor = state === ACTIVE ? c.textColor : state === FADING ? "#999" : "#ccc";
      const retColor = indRet >= 0 ? UP_COLOR : DOWN_COLOR;
      const retSign = indRet >= 0 ? "▲ " : "▼ ";
      html += `<div style="opacity:${state === ACTIVE ? 1 : state === FADING ? 0.5 : 0.3}"><span style="color:${stateColor}">${stateLabel}</span> ${label}: <b>${fmtNum(iv, 2)}</b> <span style="opacity:0.7">${retSign}ret: <b style="color:${retColor}">${fmtPctSigned(indRet, 2)}</b></span></div>`;
    }
    return html;
  };

  return {
    backgroundColor: "transparent",
    animation: false,
    grid: commonGrid({ left: 64, right: 24, bottom: 50, top: 32 }),
    dataZoom: commonDataZoom(),
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "cross" },
      backgroundColor: c.tooltipBg,
      borderColor: c.splitLineColor,
      textStyle: { color: c.textColor, fontSize: 11 },
      formatter: tooltipFormatter,
    },
    legend: commonLegend(themeMode, {
      itemWidth: 12,
      itemHeight: 7,
      data: legendData,
    }),
    xAxis: {
      type: "category",
      data: dates,
      boundaryGap: true,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: {
        color: c.textColor,
        fontSize: 9,
        interval: (_idx: number, value: string) =>
          displayMonths.has(value.slice(0, 7)) && firstDateOfMonth.has(value),
        formatter: (v: string) => v.slice(0, 7),
      },
      splitLine: { show: false },
    },
    yAxis: {
      type: "value",
      scale: true,
      // Explicit min/max from non-extreme curves — extreme (high-variance)
      // curves overflow these bounds and are clipped, preventing them from
      // compressing the rest of the plot into a flat band.
      ...(yAxisBounds ? { min: yAxisBounds.min, max: yAxisBounds.max } : {}),
      name: "Rebased (100)",
      nameTextStyle: { color: c.textColor, fontSize: 9 },
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: {
        color: c.textColor,
        fontSize: 9,
        formatter: (v: number) => fmtNum(v, 0),
      },
      splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
    },
    series,
  };
}
