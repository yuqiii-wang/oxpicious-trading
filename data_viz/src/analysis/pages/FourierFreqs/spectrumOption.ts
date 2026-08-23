/**
 * ECharts option builder for ONE per-range_days FFT spectrum chart with
 * bins MERGED into integer DAY frequencies (the charts below the top
 * index price plot on the Fourier Frequencies page).
 *
 * Day-frequency merging:
 *   FFT bin k (k = 1..N//2) has an exact period of N/k days, which is
 *   generally NOT an integer. Bins are classified by their ROUNDED
 *   integer day period and all bins mapping to the same day are MERGED.
 *   At short periods many bins share one day (e.g. for N=1275, bins
 *   k=511..637 ALL round to "2d" — 127 same-day bins collapse into one
 *   bar). Merged amplitude = sqrt(Σ amp_k²) (band energy), so a crowd of
 *   tiny noise bins cannot inflate the bar the way a plain sum would.
 *
 * Per integer day freq d the chart shows the periodicity audit from its
 * two complementary sides:
 *   • amp (left y-axis, bars) — energy-merged FFT amplitude (yuan): the
 *     Fourier REFERENCE for which day freqs carry energy.
 *   • pattern score (right y-axis, bars) — the CONSOLIDATED periodic
 *     pattern bar (see patternScore.ts):
 *       score(d) = (amp(d)/σ_band) × recEXT(d) × acfFrac(d)
 *     i.e. noticeability × extrema evidence × ACF coherence. A day freq
 *     that ACTUALLY recurs with noticeable highs and lows — enough
 *     prominent swing extrema at that spacing AND significant
 *     self-similarity at its multiples — shows a peak here; noise and
 *     one-off swings do not. Auditable for d ≤ N/3 (≥ ~3 cycles).
 *
 * The dominant day freq (highest merged amplitude) is highlighted green.
 * X-axis: integer day freqs, descending left→right (long cycles left) —
 * the conventional spectrum layout (low frequency → high frequency).
 *
 * Tooltips also capture the DIFFERENT REPEAT PERIODS merged into each
 * day freq: the FFT bin range k=kLo..kHi (each bin k is itself a repeat
 * period of k cycles per window that rounds to this day).
 *
 * Cutoff: by default day freqs < 5 are hidden (high-frequency noise on
 * a close-price series); the expand button in the panel header reveals
 * the full granular spectrum. (Nyquist: the shortest representable day
 * freq is 2d.)
 */
import React from "react";
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import { renderReactElement, tooltipComponents } from "@/lib/react-tooltip-renderer";
import {
  UP_COLOR,
  PALETTE_HI,
  axisColors,
  commonGrid,
  commonDataZoom,
} from "@/theme/chart-palette";
import { fmtNum } from "@/lib/series";
import type { FourierFreqsSpectrumRow } from "@shared/types";
import { auditPatternScores, type PatternScoreAudit } from "./patternScore";

/** Right-axis bar color for the consolidated pattern score. */
export const SCORE_COLOR = "#ab47bc";

export interface SpectrumOptionParams {
  row: FourierFreqsSpectrumRow;
  themeMode: ThemeMode;
  /** When false (default), hide granular high-freq day freqs (< 5d).
   *  When true, show all day freqs. Toggled by the expand button. */
  expanded: boolean;
  /** The window's close prices (chronological, ending at the row's
   *  last_date) — input for the time-domain pattern-score audit. Empty
   *  when unavailable (score bars then read 0). */
  closes: readonly number[];
}

/** All FFT bins whose period rounds to the same integer day. */
interface DayEntry {
  /** FFT bin range merged into this day (1-based, inclusive). Each bin
   *  k is a distinct repeat period (k cycles per window) captured by
   *  this day freq. */
  kLo: number;
  kHi: number;
  /** Number of merged bins. */
  nBins: number;
  /** Σ amp² across merged bins (band energy²). */
  sumSq: number;
  /** Max single-bin amplitude. */
  maxAmp: number;
}

/** Build the merged day-frequency spectrum option for one range_days. */
export function buildSpectrumOption(params: SpectrumOptionParams): EChartsOption {
  const { row, themeMode, expanded, closes } = params;
  const c = axisColors(themeMode);
  const N = row.range_days;
  const spectrum = row.spectrum;
  const totalWindows = row.total_windows;
  const nBins = spectrum.length;

  // Default cutoff: hide day freqs < 5d (high-freq noise on close-price
  // series). The expand button reveals them. Nyquist floor is 2d.
  const MIN_DAY = expanded ? 1 : 5;

  // ---- Merge FFT bins into integer day frequencies ----------------------
  // Bin k has exact period N/k days → classified by Math.round(N/k).
  const dayMap = new Map<number, DayEntry>();
  for (let i = 0; i < nBins; i++) {
    const k = i + 1;
    const day = Math.round(N / k);
    let e = dayMap.get(day);
    if (!e) {
      e = { kLo: k, kHi: k, nBins: 0, sumSq: 0, maxAmp: 0 };
      dayMap.set(day, e);
    }
    const a = spectrum[i];
    e.kHi = k; // k ascends with i — overwrite is enough
    e.nBins += 1;
    e.sumSq += a * a;
    if (a > e.maxAmp) e.maxAmp = a;
  }

  // ---- Consolidated pattern-score audit (time domain) -------------------
  // Computed once per chart from the window's closes + merged amps.
  const mergedAmps = new Map<number, number>();
  for (const [day, e] of dayMap) mergedAmps.set(day, Math.sqrt(e.sumSq));
  const { audits, sigmaBand, nExtrema } = auditPatternScores(closes, mergedAmps, N);

  // Visible day freqs (>= MIN_DAY), descending — long cycles left.
  const days = Array.from(dayMap.keys())
    .filter((d) => d >= MIN_DAY)
    .sort((a, b) => b - a);
  const nVisible = days.length;

  const categories: string[] = new Array(nVisible);
  const ampData: number[] = new Array(nVisible);
  const scoreData: number[] = new Array(nVisible);
  const tipRows: Array<{
    day: number;
    kLo: number;
    kHi: number;
    nBins: number;
    amp: number;
    maxAmp: number;
    audit: PatternScoreAudit | undefined;
    isDom: boolean;
  }> = new Array(nVisible);

  // Dominant day freq (max merged amp) + top-score day freq among the
  // visible days.
  let domPos = -1;
  let domVal = -Infinity;
  let topPos = -1;
  let topVal = -Infinity;
  for (let j = 0; j < nVisible; j++) {
    const d = days[j];
    const e = dayMap.get(d)!;
    const amp = Math.sqrt(e.sumSq); // energy-merged amplitude
    const audit = audits.get(d);
    categories[j] = `${d}d`;
    ampData[j] = amp;
    scoreData[j] = audit ? audit.score : 0;
    tipRows[j] = {
      day: d,
      kLo: e.kLo,
      kHi: e.kHi,
      nBins: e.nBins,
      amp,
      maxAmp: e.maxAmp,
      audit,
      isDom: false,
    };
    if (amp > domVal) {
      domVal = amp;
      domPos = j;
    }
    if (audit && audit.auditable && audit.score > topVal) {
      topVal = audit.score;
      topPos = j;
    }
  }
  if (domPos >= 0) tipRows[domPos].isDom = true;
  const domDay = domPos >= 0 ? days[domPos] : 0;
  const domAmpTxt = domPos >= 0 ? fmtNum(domVal, 2) : "n/a";
  const topDay = topPos >= 0 ? days[topPos] : 0;
  const topScoreTxt = topPos >= 0 ? fmtNum(topVal, 3) : "none";

  const showZoom = nVisible > 40;

  /** Half-circle duration label (day/2, may be a half-integer). */
  const halfDay = (d: number): string => {
    const h = d / 2;
    return Number.isInteger(h) ? `${h}d` : `${h.toFixed(1)}d`;
  };

  return {
    backgroundColor: "transparent",
    animation: false,
    title: {
      text:
        `${N}d window · dominant ≈ ${domDay}d (amp ${domAmpTxt})` +
        ` · top score ≈ ${topDay}d (${topScoreTxt})` +
        (totalWindows > 0 ? ` · ${totalWindows} windows` : ""),
      left: 8,
      top: 4,
      textStyle: { fontSize: 11, fontWeight: 600, color: c.textColor },
    },
    legend: {
      show: true,
      top: 22,
      right: 8,
      orient: "horizontal",
      textStyle: { color: c.textColor, fontSize: 9 },
      itemWidth: 10,
      itemHeight: 6,
      data: ["amp", "pattern score"],
    },
    grid: commonGrid({ top: 56, bottom: showZoom ? 56 : 32, left: 48, right: 48 }),
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      backgroundColor: c.tooltipBg,
      borderColor: c.axisLineColor,
      textStyle: { color: c.textColor, fontSize: 11 },
      formatter: (params: unknown) => {
        const arr = params as Array<{
          seriesName: string;
          value: number;
          dataIndex: number;
          color: string;
        }>;
        if (!arr || arr.length === 0) return "";
        const j = arr[0].dataIndex;
        const t = tipRows[j];
        if (!t) return "";

        const lines: React.ReactNode[] = [];
        lines.push(tooltipComponents.Bold({ children: `${t.day}d freq` }));
        if (t.isDom) {
          lines.push(tooltipComponents.Bold({ children: " · dominant" }));
        }
        lines.push(React.createElement("br"));

        // Amplitude (left axis) — energy-merged across the day's bins.
        lines.push(
          React.createElement(React.Fragment, { key: "amp" }, [
            React.createElement("span", {
              style: {
                display: "inline-block",
                width: 8,
                height: 8,
                backgroundColor: arr.find((p) => p.seriesName === "amp")?.color || PALETTE_HI,
                marginRight: 4,
              },
            }),
            "amp: ",
            tooltipComponents.Bold({ children: `${fmtNum(t.amp, 2)} yuan` }),
            t.nBins > 1
              ? ` (${t.nBins} bins merged, max ${fmtNum(t.maxAmp, 2)})`
              : "",
            React.createElement("br"),
          ]),
        );

        // Consolidated pattern score (right axis) — amp/σ_band × extrema
        // evidence × ACF coherence, with its full decomposition.
        const a = t.audit;
        lines.push(
          React.createElement(React.Fragment, { key: "score" }, [
            React.createElement("span", {
              style: {
                display: "inline-block",
                width: 8,
                height: 8,
                backgroundColor:
                  arr.find((p) => p.seriesName === "pattern score")?.color || SCORE_COLOR,
                marginRight: 4,
              },
            }),
            "pattern score: ",
            tooltipComponents.Bold({
              children: !a
                ? "n/a (window closes unavailable)"
                : a.auditable
                  ? fmtNum(a.score, 3)
                  : "— not auditable (< 3 cycles in window)",
            }),
            React.createElement("br"),
          ]),
        );
        if (a && a.auditable) {
          lines.push(
            React.createElement(
              "span",
              { key: "decomp", style: { opacity: 0.85 } },
              ` = ${fmtNum(a.ampNorm, 2)} amp/σband × ${fmtNum(a.recEXT, 2)} evidence ` +
                `× ${fmtNum(a.acfFrac, 2)} acf · σband ${fmtNum(sigmaBand, 1)} yuan`,
            ),
            React.createElement("br"),
            React.createElement(
              "span",
              { key: "evid", style: { opacity: 0.85 } },
              `evidence: ${a.evidence} hits / ${a.maxRepeats} possible · ` +
                `${nExtrema} swing extrema (prominence ≥ 1.5σ)`,
            ),
            React.createElement("br"),
            React.createElement(
              "span",
              { key: "acf", style: { opacity: 0.85 } },
              `acf: ${a.repeats}× cycles (${a.repeats * 2} half-circles of ~${halfDay(t.day)})` +
                ` · ${a.repeats}/${a.maxRepeats} multiples ≥ 1.96/√N`,
            ),
            React.createElement("br"),
          );
        }

        // The different repeat periods (FFT bins) captured by this day.
        lines.push(
          React.createElement(
            "span",
            { style: { opacity: 0.85 } },
            `captured repeat periods: k=${t.kLo}..${t.kHi}` +
              (t.nBins > 1 ? ` (${t.nBins} bins)` : ""),
          ),
        );

        return renderReactElement(React.createElement(React.Fragment, null, lines));
      },
    },
    xAxis: {
      type: "category",
      data: categories,
      name: "day freq",
      nameLocation: "middle",
      nameGap: 22,
      nameTextStyle: { color: c.textColor, fontSize: 9 },
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: {
        color: c.textColor,
        fontSize: 8,
        hideOverlap: true,
        interval: Math.max(0, Math.floor(nVisible / 8) - 1),
      },
      splitLine: { show: false },
    },
    yAxis: [
      {
        type: "value",
        name: "amp (yuan)",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: { color: c.textColor, fontSize: 9, formatter: (v: number) => fmtNum(v) },
        splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
      },
      {
        type: "value",
        name: "pattern score",
        nameTextStyle: { color: SCORE_COLOR, fontSize: 9 },
        position: "right",
        axisLine: { lineStyle: { color: SCORE_COLOR } },
        axisLabel: { color: SCORE_COLOR, fontSize: 9, formatter: (v: number) => fmtNum(v, 2) },
        splitLine: { show: false },
      },
    ],
    dataZoom: showZoom ? commonDataZoom({}) : undefined,
    series: [
      // Amplitude bars (left axis) — energy-merged per day freq; the
      // dominant day freq is highlighted green.
      {
        name: "amp",
        type: "bar",
        yAxisIndex: 0,
        data: ampData.map((v, j) => ({
          value: v,
          itemStyle: { color: j === domPos ? UP_COLOR : PALETTE_HI },
        })),
        itemStyle: { opacity: 0.85 },
        z: 2,
      },
      // Consolidated pattern-score bars (right axis) — the periodic
      // noticeable-highs-and-lows bar: (amp/σ_band) × extrema evidence
      // × ACF coherence (see patternScore.ts). Fourier is the
      // reference; this is the consolidated audit of what actually
      // recurs with noticeable swings. Zero for d > N/3 (not auditable).
      {
        name: "pattern score",
        type: "bar",
        yAxisIndex: 1,
        data: scoreData,
        itemStyle: { color: SCORE_COLOR, opacity: 0.8 },
        z: 3,
      },
    ],
  };
}
