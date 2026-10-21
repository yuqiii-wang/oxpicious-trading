/**
 * shadeBuilders — the MaSpreadPanel's pure assembly layer: the memos the
 * panel previously carried inline, extracted as plain functions over their
 * exact inputs. Each builder turns loaded extras rows + the section picks
 * into the overlay specs buildPairOption / buildAmtEnvelopeOption consume
 * (right-axis derivative/momentum lines, markArea shade overlays), with no
 * React state of their own — the panel keeps the useMemos and owns the
 * dependency lists.
 */
import { UP_COLOR, DOWN_COLOR, MUTED_PALETTE } from "@/theme/chart-palette";
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import type { OhlcMode } from "@/lib/ohlc";
import {
  REGIME_ACCENT_COLORS,
  REGIME_LABELS,
  REGIME_SHADE_COLORS,
  ALL_REGIMES,
  regimeSpansToMarkArea,
} from "@/shared/charts/regimeBands";
import type { LongBandStreak } from "@/shared/charts/streakBands";
import { trendSpansToMarkArea } from "@/shared/charts/trendBands";
import type {
  MarketRegime,
  MarketRegimeSpans,
  MovAveSpreadChartResponse,
  MovAveSpreadMomentumDay,
  MovAveSpreadOhlcGapDay,
  MovAveSpreadOhlcRow,
  MovAveSpreadPairRow,
  MovAveSpreadPairSeries,
  MovAveSpreadPriceVsAmtDay,
  MovAveSpreadRoofFloorEstimateRow,
  MovAveSpreadRoofFloorRebound,
  MovAveSpreadRsiRankDay,
  MovAveSpreadAmtRow,
  RoofFloorEstimateMethod,
} from "@shared/types";
import {
  computePxVolStates,
  pxVolMatchRuns,
  pxVolRunsToMarkArea,
  pxVolShadeColor,
  pxVolAccentColor,
  PX_VOL_SPEED_OPTIONS,
  PX_VOL_VOL_OPTIONS,
  buildPairOption,
  buildAmtEnvelopeOption,
  type PxVolDayState,
  type PxVolRun,
  type PxVolSpeed,
  type PxVolVolState,
} from "./chartOption";
import {
  TREND_TYPES,
  TREND_TYPE_LABELS,
  TREND_ACCENTS,
  TREND_SHADES,
  type DerivTrendSpec,
  type MomentumTerm,
  type MomentumTrendSpec,
  type ShadeOverlay,
  type TrendSpansRecord,
  type TrendType,
} from "./chartOption";
import {
  MOMENTUM_TERM_COLORS,
  MOMENTUM_VALUE_KEYS,
  MOMENTUM_TERMS,
  RSI_SHADE_BASE_OPACITY,
  RSI_WINDOWS,
} from "./constants";
import type { ChartOverlay, GapMarksState } from "./types";

/** A span-overlay entry structurally identical to ShadeOverlay (the
 *  regime/trend builders' local type — kept explicit to match the arrays
 *  the panel spreads into the option builders' regimeShades arg). */
export interface SpanShade {
  label: string;
  data: ReturnType<typeof regimeSpansToMarkArea>;
  accent: string;
}

/** The picked regimes' span overlays (one legend series each — the
 *  regimeBands palette's per-regime shade color, calm's grey included). */
export function buildRegimeShades(
  regimePicks: Set<MarketRegime>,
  regimeSpans: Partial<MarketRegimeSpans> | null,
): SpanShade[] {
  if (regimePicks.size === 0 || regimeSpans == null) return [];
  const out: SpanShade[] = [];
  for (const r of ALL_REGIMES) {
    if (!regimePicks.has(r)) continue;
    const spans = regimeSpans[r] ?? [];
    out.push({
      label: `Regime(${REGIME_LABELS[r]})`,
      accent: REGIME_ACCENT_COLORS[r],
      data: regimeSpansToMarkArea(spans, REGIME_SHADE_COLORS[r]),
    });
  }
  return out;
}

/**
 * Per-date (speed, vol) state for the Px-Vol section — the DB rows from
 * the chart endpoint when the "pxvol" extras group has landed, else a
 * client-side replication of the registry's computation
 * (computePxVolStates) for cached responses that predate the field.
 * Computed lazily (either Px-Vol button picked).
 */
export function buildPxVolStates(
  rows: MovAveSpreadPairRow[],
  dbDays: MovAveSpreadPriceVsAmtDay[] | undefined,
  active: boolean,
): Array<PxVolDayState | null> | null {
  if (!active) return null;
  if (dbDays != null) {
    const byDate = new Map(dbDays.map((d) => [d.date, d]));
    return rows.map((r) => {
      const d = byDate.get(r.date);
      return d != null ? { speed: d.speed, vol: d.vol } : null;
    });
  }
  return computePxVolStates(
    rows.map((r) => r.short_value),
    rows.map((r) => r.trading_amount),
  );
}

/** The selected combo's consecutive matched runs (the shade's data +
 *  caption stats source). */
export function buildPxVolRuns(
  rows: MovAveSpreadPairRow[],
  states: Array<PxVolDayState | null> | null,
  speed: PxVolSpeed | null,
  vol: PxVolVolState | null,
): PxVolRun[] {
  if (speed == null || vol == null || states == null) return [];
  return pxVolMatchRuns(
    rows.map((r) => r.date),
    states,
    speed,
    vol,
  );
}

/** The selected Px-Vol combo's chart overlay (legend label + markArea
 *  rects shaded by the combo's strength color). */
export function buildPxVolShade(
  speed: PxVolSpeed | null,
  vol: PxVolVolState | null,
  runs: PxVolRun[],
): ShadeOverlay | null {
  if (speed == null || vol == null || runs.length === 0) return null;
  const speedOpt = PX_VOL_SPEED_OPTIONS.find((o) => o.key === speed);
  const volOpt = PX_VOL_VOL_OPTIONS.find((o) => o.key === vol);
  const label = `PxVol(${speedOpt?.label ?? speed}·${volOpt?.label ?? vol})`;
  return {
    label,
    accent: pxVolAccentColor(speed),
    data: pxVolRunsToMarkArea(runs, pxVolShadeColor(speed, vol)),
  };
}

/**
 * The Slopes/Curvatures overlay lines (the header toggle's two derivative
 * modes). THREE DIRECT derivative curves read from the DB-STORED columns
 * (no client-side computation, no averaging):
 *   Price  — price_slope / price_curvature (P[t] − P[t−1] and its diff)
 *   MA5/EMA6 + MA20/EMA20 — the slope of the moving-average LINE itself
 *            (maW[t] − maW[t−1] / emaW[t] − emaW[t−1], curvature = that
 *            slope's diff), straight from the ma{5,20}_* / ema{6,20}_*
 *            detail columns.
 * The family follows the pair switch: an EMA chip swaps the MA curves to
 * EMA6/EMA20; a Simple-MA chip (the default) shows MA5/MA20.
 */
export function buildDerivTrend(
  chartOverlay: ChartOverlay,
  selectedPair: MovAveSpreadPairSeries | undefined,
  pairs: MovAveSpreadPairSeries[],
  pairIndexMap: Map<string, number>,
): DerivTrendSpec | null {
  if (chartOverlay !== "slope" && chartOverlay !== "curv") return null;
  const mode = chartOverlay === "slope" ? "slope" : "curvature";
  const suffix = mode === "slope" ? "Slope" : "Curv";
  const isEma = selectedPair?.kind === "ema";
  // The Price/{W} pairs: short leg = price (short_slope / short_curvature
  // carry the DB's price derivatives), long leg = the window's MA/EMA
  // (long_slope / long_curvature carry its stored derivative).
  const anchor = pairs[pairIndexMap.get(isEma ? "ema-0-6" : "price-0-5") ?? -1];
  const long20 = pairs[pairIndexMap.get(isEma ? "ema-0-20" : "price-0-20") ?? -1];
  const pick = (
    p: typeof anchor,
    leg: "short" | "long",
  ): Array<number | null> =>
    (p?.rows ?? []).map((r) =>
      mode === "slope"
        ? leg === "short"
          ? r.short_slope
          : r.long_slope
        : leg === "short"
          ? r.short_curvature
          : r.long_curvature,
    );
  const shortName = isEma ? "EMA6" : "MA5";
  const longName = isEma ? "EMA20" : "MA20";
  return {
    mode,
    lines: [
      { name: `Price ${suffix}`, data: pick(anchor, "short"), color: MUTED_PALETTE[0] },
      { name: `${shortName} ${suffix}`, data: pick(anchor, "long"), color: MUTED_PALETTE[1] },
      { name: `${longName} ${suffix}`, data: pick(long20, "long"), color: MUTED_PALETTE[2] },
    ],
  };
}

/**
 * The Momentum section's overlay lines. The "momentum" extras rows are
 * aligned to the selected pair's dates via a date→row Map (the same
 * alignment the Top-10% OHLC shades use); the adaptive terms' per-date
 * lookbacks ride along for the tooltip's "short·14d" suffixes. One line
 * per PICKED term, in the constants' canonical order.
 */
export function buildMomentumTrend(
  momentumLinesActive: boolean,
  momentumTerms: MomentumTerm[],
  rows: MovAveSpreadMomentumDay[] | undefined,
  selectedPair: MovAveSpreadPairSeries | undefined,
): MomentumTrendSpec | null {
  if (!momentumLinesActive || selectedPair == null) return null;
  if (rows == null || rows.length === 0) return null;
  const byDate = new Map(rows.map((r) => [r.date, r]));
  const n = selectedPair.rows.length;
  const lbs: NonNullable<MomentumTrendSpec["lbs"]> = {
    short: new Array<number | null>(n).fill(null),
    mid: new Array<number | null>(n).fill(null),
    long: new Array<number | null>(n).fill(null),
  };
  for (let i = 0; i < n; i++) {
    const r = byDate.get(selectedPair.rows[i].date);
    if (!r) continue;
    lbs.short[i] = r.lb_short;
    lbs.mid[i] = r.lb_mid;
    lbs.long[i] = r.lb_long;
  }
  const order = MOMENTUM_TERMS as readonly MomentumTerm[];
  const picked = order.filter((t) => momentumTerms.includes(t));
  return {
    lines: picked.map((term) => ({
      name: `Mom ${term}`,
      term,
      color: MOMENTUM_TERM_COLORS[term],
      data: selectedPair.rows.map(
        (r) =>
          (byDate.get(r.date)?.[MOMENTUM_VALUE_KEYS[term]] as
            | number
            | null
            | undefined) ?? null,
      ),
    })),
    lbs,
  };
}

/** The Top-10% OHLC Days shades + flagged-day counts (the section's
 *  caption reads the counts). */
export interface OhlcGapShades {
  shades: ShadeOverlay[];
  counts: { spike: number; flush: number };
}

/**
 * The Top-10% OHLC Days SHADES — light red bands over spike-fade days
 * (upper wick in its trailing-252d top decile), light green over
 * flush-recover days (lower wick likewise). Consecutive flagged dates
 * merge into one band (single-day bands allowed); rendered through the
 * shared markArea overlay path (same shape as regimeShades).
 */
export function buildOhlcGapShades(
  gapMarks: GapMarksState,
  ohlcGaps: MovAveSpreadOhlcGapDay[] | undefined,
  selectedPair: MovAveSpreadPairSeries | undefined,
): OhlcGapShades {
  const empty: OhlcGapShades = {
    shades: [],
    counts: { spike: 0, flush: 0 },
  };
  if (!gapMarks.spike && !gapMarks.flush) return empty;
  if (ohlcGaps == null || selectedPair == null) return empty;
  const byDate = new Map(ohlcGaps.map((d) => [d.date, d]));
  const kinds = [
    {
      kind: "spike" as const,
      on: gapMarks.spike,
      flag: "uw_top10" as const,
      label: "Spike fade (top-10% uw)",
      accent: "rgb(192, 57, 43)",
      rgba: "rgba(192, 57, 43, 0.16)",
    },
    {
      kind: "flush" as const,
      on: gapMarks.flush,
      flag: "lw_top10" as const,
      label: "Flush recover (top-10% lw)",
      accent: "rgb(39, 174, 96)",
      rgba: "rgba(39, 174, 96, 0.16)",
    },
  ];
  const dates = selectedPair.rows.map((r) => r.date);
  const shades: ShadeOverlay[] = [];
  const counts: Record<"spike" | "flush", number> = { spike: 0, flush: 0 };
  for (const k of kinds) {
    if (!k.on) continue;
    // consecutive flagged dates merge into one band (single-day bands
    // allowed — the marked days are scattered by nature)
    const runs: Array<{ start: string; end: string; days: number }> = [];
    let cur: { start: string; end: string; days: number } | null = null;
    let flagged = 0;
    for (const dt of dates) {
      const d = byDate.get(dt);
      if (d != null && d[k.flag] === true) {
        flagged += 1;
        if (cur == null) {
          cur = { start: dt, end: dt, days: 1 };
          runs.push(cur);
        } else {
          cur.end = dt;
          cur.days += 1;
        }
      } else {
        cur = null;
      }
    }
    counts[k.kind] = flagged;
    if (runs.length === 0) continue;
    shades.push({
      label: k.label,
      accent: k.accent,
      data: runs.map((r) => [
        { xAxis: r.start, itemStyle: { color: k.rgba } },
        { xAxis: r.end },
      ]),
    });
  }
  return { shades, counts };
}

/**
 * The RSI Top-% SHADES (the RSI Trends section's pct row): the picked pct
 * is SPLIT EVENLY across the two ends of the code's own RSI-rank axis
 * (analysis.mov_ave_rsi_ranks — ONE rank family, rank 1 = the code's
 * highest RSI reading, dense 1..N; the axis is treated as a signed
 * scale): green shades mark the dates ranking within the top pct/2 of the
 * window's own history (rises, UP_COLOR), red the bottom pct/2 (drops,
 * DOWN_COLOR). Per window the half cutoff is ceil(pct/2 × N_w) with N_w
 * the window's count of ranked days = the max stored rank; the bottom
 * side qualifies at rank > N_w − cutoff.
 *
 * DEPTH: every picked window contributes the SAME base strength and
 * transparency (one qualifying window = the flat base fill), and each
 * ADDITIONAL picked window whose ranks qualify on the same date stacks
 * the shade darker — per-date qualifying-window COUNT per side, merged
 * into runs of equal count, rect opacity = base × count (capped). The
 * days row scopes the ranks (picked windows only; rsi3 1% and rsi60 1%
 * shade different date sets), and a single side's date may qualify for
 * both sides via different windows — the green and red overlays then
 * overlap (each independently legend-toggleable). The overlays draw in
 * every right-axis mode once a pct is picked.
 */
export function buildRsiRankShades(
  rsiTopPct: number | null,
  rsiDays: number[],
  ranks: MovAveSpreadRsiRankDay[] | undefined,
  selectedPair: MovAveSpreadPairSeries | undefined,
): ShadeOverlay[] {
  if (rsiTopPct == null) return [];
  if (ranks == null || ranks.length === 0 || selectedPair == null) return [];
  // The days row scopes the rank windows: picked windows only (sorted),
  // or all six when the row is empty.
  const shadeWindows =
    rsiDays.length > 0
      ? [...rsiDays].sort((a, b) => a - b)
      : [...RSI_WINDOWS];
  // Per-window N_w = the count of ranked (non-null-RSI) days — the max
  // stored rank, exact because the ranks are dense 1..N per window.
  const totals = new Map<number, number>();
  for (const r of ranks) {
    for (const w of shadeWindows) {
      const rk = r[`rank_rsi_${w}days`];
      if (rk != null && rk > (totals.get(w) ?? 0)) totals.set(w, rk);
    }
  }
  // The picked pct split evenly: half for the top (rise) end, half for
  // the bottom (drop) end — one cutoff per window serves both sides.
  const cutoffs = new Map<number, number>();
  for (const [w, n] of totals) {
    cutoffs.set(w, Math.max(1, Math.ceil((rsiTopPct / 2) * n)));
  }
  // Per-date qualifying-window count per side — the shade's depth.
  const counts = new Map<string, { rise: number; drop: number }>();
  for (const r of ranks) {
    let rise = 0;
    let drop = 0;
    for (const w of shadeWindows) {
      const cutoff = cutoffs.get(w);
      const rk = r[`rank_rsi_${w}days`];
      const n = totals.get(w);
      if (cutoff == null || n == null || rk == null) continue;
      if (rk <= cutoff) rise += 1;
      else if (rk > n - cutoff) drop += 1;
    }
    if (rise > 0 || drop > 0) counts.set(r.date, { rise, drop });
  }
  const byDateHas = new Set(counts.keys());
  const dates = selectedPair.rows.map((r) => r.date);
  // Consecutive dates of EQUAL count merge into one band; a count change
  // starts a new band (single-day bands allowed — the marked days are
  // scattered by nature).
  const buildCountedRuns = (
    side: "rise" | "drop",
  ): Array<{ start: string; end: string; count: number }> => {
    const runs: Array<{ start: string; end: string; count: number }> = [];
    let cur: { start: string; end: string; count: number } | null = null;
    for (const dt of dates) {
      const c = byDateHas.has(dt) ? counts.get(dt)![side] : 0;
      if (c > 0) {
        if (cur != null && cur.count === c) {
          cur.end = dt;
        } else {
          cur = { start: dt, end: dt, count: c };
          runs.push(cur);
        }
      } else {
        cur = null;
      }
    }
    return runs;
  };
  const shadeRects = (
    runs: Array<{ start: string; end: string; count: number }>,
    accent: string,
  ): ShadeOverlay["data"] =>
    runs.map((r) => [
      {
        xAxis: r.start,
        itemStyle: {
          color: accent,
          opacity: Math.min(RSI_SHADE_BASE_OPACITY * r.count, 0.8),
        },
      },
      { xAxis: r.end },
    ]);
  const riseRuns = buildCountedRuns("rise");
  const dropRuns = buildCountedRuns("drop");
  // The half pct in percent (e.g. picked 1% → 0.5) for the legend labels.
  const halfPct = Math.round(rsiTopPct * 500) / 10;
  // A single picked window names it in the legend (RSI3 Rise 0.5% vs
  // RSI60's — the comparison the days row exists for); multi/none stays
  // generic.
  const wLabel =
    shadeWindows.length === 1 ? `RSI${shadeWindows[0]} ` : "";
  const out: ShadeOverlay[] = [];
  if (riseRuns.length > 0) {
    out.push({
      label: `${wLabel}Rise ${halfPct}%`,
      accent: UP_COLOR,
      data: shadeRects(riseRuns, UP_COLOR),
    });
  }
  if (dropRuns.length > 0) {
    out.push({
      label: `${wLabel}Drop ${halfPct}%`,
      accent: DOWN_COLOR,
      data: shadeRects(dropRuns, DOWN_COLOR),
    });
  }
  return out;
}

/**
 * The Trend section's SHADES — the signals tier's spans
 * (analysis.trend_days, the RSI re-entry classification; date-based rects
 * — no pair-row alignment needed, so the shades draw on the selected pair
 * whatever it is). Each picked phase contributes one legend series of
 * merged markArea rects in its light fill — rendered through the shared
 * shade-overlay path alongside the regime / gap / RSI-rank overlays.
 */
export function buildTrendShades(
  trendPicks: Set<TrendType>,
  trendSpans: TrendSpansRecord | null,
): ShadeOverlay[] {
  if (trendPicks.size === 0 || trendSpans == null) return [];
  const out: ShadeOverlay[] = [];
  for (const t of TREND_TYPES) {
    if (!trendPicks.has(t)) continue;
    const spans = trendSpans[t];
    if (!spans || spans.length === 0) continue;
    out.push({
      label: `Trend(${TREND_TYPE_LABELS[t]})`,
      accent: TREND_ACCENTS[t],
      data: trendSpansToMarkArea(spans, TREND_SHADES[t]),
    });
  }
  return out;
}

/** Inputs of composeChartOption — the panel's full overlay/control state
 *  at one render (the memo keeps its dependency list on these values). */
export interface ComposeChartOptionArgs {
  loading: boolean;
  error: string | null;
  selectedPair: MovAveSpreadPairSeries | undefined;
  amtPairSelected: boolean;
  chartData: MovAveSpreadChartResponse | null;
  themeMode: ThemeMode;
  bollingerK: number;
  ohlcMode: OhlcMode;
  chartOverlay: ChartOverlay;
  rsiDays: number[];
  rsiPopulation: "typical" | "re-entry" | null;
  rsiLinesActive: boolean;
  derivTrend: DerivTrendSpec | null;
  momentumTrend: MomentumTrendSpec | null;
  momentumLinesActive: boolean;
  roofFloorWindow: number | null;
  anchorIdx: number | null;
  rfEstOn: boolean;
  rfEstMethod: RoofFloorEstimateMethod;
  longStreaks: { high: LongBandStreak[]; low: LongBandStreak[] } | null;
  streakPeriod: number | null;
  streakPct: number | null;
  pxVolShade: ShadeOverlay | null;
  regimeShades: SpanShade[];
  ohlcGapShades: ShadeOverlay[];
  rsiRankShades: ShadeOverlay[];
  trendShades: ShadeOverlay[];
  ohlcGapRows: MovAveSpreadOhlcGapDay[] | null;
  /** Whether either Top-10% OHLC Days toggle is on (the tooltip's gap
   *  readout gate). */
  ohlcGapActive: boolean;
  trendSpans: TrendSpansRecord | null;
  /** The "ohlc" extras group's rolling OHLC extrema rows (the roof/floor
   *  trendlines) — null while unloaded. */
  ohlcRows: MovAveSpreadOhlcRow[] | null;
  /** The "rfest" extras group's estimate rows — null while unloaded. */
  rfEstimates: MovAveSpreadRoofFloorEstimateRow[] | null;
  /** The "rfaudit" extras group's rebound-audit rows — null while unloaded. */
  rfRebounds: MovAveSpreadRoofFloorRebound[] | null;
}

/**
 * The panel's chart option: null while loading / on error / when the pair
 * has no rows (BaseChart then renders the shared spinner / error Alert /
 * empty placeholder instead of the chart); otherwise the Amt/MA envelope
 * option for amt pairs, the pair option for everything else. Pure — the
 * panel memoizes over the args' values.
 */
export function composeChartOption(args: ComposeChartOptionArgs): EChartsOption | null {
  const {
    loading,
    error,
    selectedPair,
    amtPairSelected,
    chartData,
    themeMode,
    bollingerK,
    ohlcMode,
    chartOverlay,
    rsiDays,
    rsiPopulation,
    rsiLinesActive,
    derivTrend,
    momentumTrend,
    momentumLinesActive,
    roofFloorWindow,
    anchorIdx,
    rfEstOn,
    rfEstMethod,
    longStreaks,
    streakPeriod,
    streakPct,
    pxVolShade,
    regimeShades,
    ohlcGapShades,
    rsiRankShades,
    trendShades,
    ohlcGapRows,
    ohlcGapActive,
    trendSpans,
    ohlcRows,
    rfEstimates,
    rfRebounds,
  } = args;
  if (loading || error || !selectedPair || selectedPair.rows.length === 0) {
    return null;
  }
  return amtPairSelected
    ? buildAmtEnvelopeOption({
        pair: selectedPair as Omit<MovAveSpreadPairSeries, "rows"> & { rows: MovAveSpreadAmtRow[] },
        shared: chartData?.shared ?? null,
        themeMode,
        ohlcMode,
        bollingerK,
        longStreaks,
        streakPeriod,
        streakPct,
        streakAnchorIdx: anchorIdx,
        pxVolShade,
        regimeShades: [...regimeShades, ...ohlcGapShades, ...trendShades],
        ohlcGapRows,
        ohlcGapActive,
      })
    : buildPairOption({
        pair: selectedPair,
        shared: chartData?.shared ?? null,
        themeMode,
        bollingerK,
        tradingAmtMode:
          chartOverlay === "amt" && rsiDays.length === 0 && !momentumLinesActive
            ? "lowkey"
            : "off",
        rsiTrendMode: rsiLinesActive,
        rsiWindows: rsiDays,
        rsiPopulation,
        derivTrend,
        momentumTrend,
        ohlcMode,
        roofFloorWindow,
        roofFloorClickIdx: anchorIdx,
        roofFloorRows: ohlcRows,
        roofFloorEstRows: rfEstimates,
        roofFloorEstMethod: rfEstOn ? rfEstMethod : null,
        roofFloorRebounds: rfRebounds,
        longStreaks,
        streakPeriod,
        streakPct,
        streakAnchorIdx: anchorIdx,
        pxVolShade,
        regimeShades: [
          ...regimeShades,
          ...ohlcGapShades,
          ...rsiRankShades,
          ...trendShades,
        ],
        ohlcGapRows,
        ohlcGapActive,
        trendSpans,
      });
}
