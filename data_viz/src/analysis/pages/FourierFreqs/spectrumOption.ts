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
 * Per integer day freq d the chart shows THREE bars — the periodicity
 * audit with the COUNT and AMP factors SEPARATED (both precomputed in
 * Python by analyze.fourier_freqs.pattern_score and stored bin-aligned
 * in analysis.fourier_freqs.count_spectrum / strength_spectrum):
 *   • amp (left y-axis, bars) — energy-merged FFT amplitude (yuan): the
 *     Fourier REFERENCE for which day freqs carry energy.
 *   • count (right y-axis, bars) — the recurrence COUNT factor:
 *     extrema evidence (prominence-filtered alternating swing highs/lows
 *     whose full-cycle spacing lands within ±15% of d) × ACF coherence
 *     (multiples of d with significant autocorrelation ≥ 1.96/√N after
 *     MA detrending). Says whether the day freq actually REPEATS.
 *   • strength (right y-axis, bars) — the summarized strength:
 *     strength(d) = (amp(d)/σ_band) × count(d) — the former consolidated
 *     "pattern score". A day freq that recurs with noticeable highs and
 *     lows peaks here; noise and one-off swings do not. 0 (not
 *     auditable) for d > N/3 (under 3 cycles in the window).
 *
 * The dominant day freq (highest merged amplitude) is highlighted green.
 * X-axis: integer day freqs, descending left→right (long cycles left) —
 * the conventional spectrum layout (low frequency → high frequency).
 *
 * Tooltips also capture the DIFFERENT REPEAT PERIODS merged into each
 * day freq: the FFT bin range k=kLo..kHi (each bin k is itself a repeat
 * period of k cycles per window that rounds to this day), and the
 * strength decomposition amp/σ_band × count.
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

/** Right-axis bar color for the recurrence count factor. */
export const COUNT_COLOR = "#ffa726";
/** Right-axis bar color for the summarized strength (amp × count). */
export const STRENGTH_COLOR = "#ab47bc";

export interface SpectrumOptionParams {
  row: FourierFreqsSpectrumRow;
  themeMode: ThemeMode;
  /** When false (default), hide granular high-freq day freqs (< 5d).
   *  When true, show all day freqs. Toggled by the expand button. */
  expanded: boolean;
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
  /** Recurrence count factor of the day (shared by all its bins). */
  count: number;
  /** Summarized strength of the day (shared by all its bins). */
  strength: number;
}

/** Build the merged day-frequency spectrum option for one range_days. */
export function buildSpectrumOption(params: SpectrumOptionParams): EChartsOption {
  const { row, themeMode, expanded } = params;
  const c = axisColors(themeMode);
  const N = row.range_days;
  const spectrum = row.spectrum;
  const totalWindows = row.total_windows;
  const nBins = spectrum.length;
  // Legacy rows (written before the pattern-score separation) carry no
  // factor arrays — count/strength bars read 0 with a tooltip hint.
  const hasFactors = row.count_spectrum.length > 0 && row.strength_spectrum.length > 0;

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
      e = { kLo: k, kHi: k, nBins: 0, sumSq: 0, maxAmp: 0, count: 0, strength: 0 };
      dayMap.set(day, e);
    }
    const a = spectrum[i];
    e.kHi = k; // k ascends with i — overwrite is enough
    e.nBins += 1;
    e.sumSq += a * a;
    if (a > e.maxAmp) e.maxAmp = a;
    // Bin-aligned factor arrays share the day's value across its bins —
    // max is a robust merge (identical by construction).
    if (hasFactors) {
      const cv = row.count_spectrum[i] ?? 0;
      const sv = row.strength_spectrum[i] ?? 0;
      if (cv > e.count) e.count = cv;
      if (sv > e.strength) e.strength = sv;
    }
  }

  // σ_band — total swing energy of the band (periods ≤ N/4); basis for
  // the amp/σ_band factor shown in the strength decomposition.
  let sb2 = 0;
  const bandMaxDay = Math.floor(N / 4);
  for (const [day, e] of dayMap) {
    if (day <= bandMaxDay) sb2 += e.sumSq;
  }
  const sigmaBand = Math.sqrt(sb2 / 2);

  // Visible day freqs (>= MIN_DAY), descending — long cycles left.
  const days = Array.from(dayMap.keys())
    .filter((d) => d >= MIN_DAY)
    .sort((a, b) => b - a);
  const nVisible = days.length;

  const categories: string[] = new Array(nVisible);
  const ampData: number[] = new Array(nVisible);
  const countData: number[] = new Array(nVisible);
  const strengthData: number[] = new Array(nVisible);
  const tipRows: Array<{
    day: number;
    kLo: number;
    kHi: number;
    nBins: number;
    amp: number;
    maxAmp: number;
    count: number;
    strength: number;
    ampNorm: number;
    auditable: boolean;
    isDom: boolean;
  }> = new Array(nVisible);

  // Dominant day freq (max merged amp) + top-strength day freq among the
  // visible days.
  let domPos = -1;
  let domVal = -Infinity;
  let topPos = -1;
  let topVal = -Infinity;
  for (let j = 0; j < nVisible; j++) {
    const d = days[j];
    const e = dayMap.get(d)!;
    const amp = Math.sqrt(e.sumSq); // energy-merged amplitude
    const ampNorm = sigmaBand > 0 ? amp / sigmaBand : 0;
    const auditable = d >= 2 && d <= Math.floor(N / 3);
    categories[j] = `${d}d`;
    ampData[j] = amp;
    countData[j] = e.count;
    strengthData[j] = e.strength;
    tipRows[j] = {
      day: d,
      kLo: e.kLo,
      kHi: e.kHi,
      nBins: e.nBins,
      amp,
      maxAmp: e.maxAmp,
      count: e.count,
      strength: e.strength,
      ampNorm,
      auditable,
      isDom: false,
    };
    if (amp > domVal) {
      domVal = amp;
      domPos = j;
    }
    if (e.strength > topVal) {
      topVal = e.strength;
      topPos = j;
    }
  }
  if (domPos >= 0) tipRows[domPos].isDom = true;
  const domDay = domPos >= 0 ? days[domPos] : 0;
  const domAmpTxt = domPos >= 0 ? fmtNum(domVal, 2) : "n/a";
  const topDay = topPos >= 0 ? days[topPos] : 0;
  const topScoreTxt = topPos >= 0 ? fmtNum(topVal, 3) : "none";

  const showZoom = nVisible > 40;

  return {
    backgroundColor: "transparent",
    animation: false,
    title: {
      text:
        `${N}d window · dominant ≈ ${domDay}d (amp ${domAmpTxt})` +
        ` · top strength ≈ ${topDay}d (${topScoreTxt})` +
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
      data: ["amp", "count", "strength"],
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

        const swatch = (name: string, fallback: string): React.ReactNode =>
          React.createElement("span", {
            style: {
              display: "inline-block",
              width: 8,
              height: 8,
              backgroundColor: arr.find((p) => p.seriesName === name)?.color || fallback,
              marginRight: 4,
            },
          });

        const lines: React.ReactNode[] = [];
        lines.push(tooltipComponents.Bold({ children: `${t.day}d freq` }));
        if (t.isDom) {
          lines.push(tooltipComponents.Bold({ children: " · dominant" }));
        }
        lines.push(React.createElement("br"));

        // Amplitude (left axis) — energy-merged across the day's bins.
        lines.push(
          React.createElement(React.Fragment, { key: "amp" }, [
            swatch("amp", PALETTE_HI),
            "amp: ",
            tooltipComponents.Bold({ children: `${fmtNum(t.amp, 2)} yuan` }),
            t.nBins > 1
              ? ` (${t.nBins} bins merged, max ${fmtNum(t.maxAmp, 2)})`
              : "",
            React.createElement("br"),
          ]),
        );

        if (!hasFactors) {
          // Legacy row (pre-separation) — factors not stored.
          lines.push(
            React.createElement(
              "span",
              { key: "legacy", style: { opacity: 0.85 } },
              "count/strength: n/a (legacy row — rerun the fourier_freqs builder)",
            ),
            React.createElement("br"),
          );
        } else {
          // Count factor (right axis) — extrema evidence × ACF coherence.
          lines.push(
            React.createElement(React.Fragment, { key: "count" }, [
              swatch("count", COUNT_COLOR),
              "count: ",
              tooltipComponents.Bold({ children: fmtNum(t.count, 3) }),
              " — recurrence evidence (extrema hits × ACF multiples)",
              React.createElement("br"),
            ]),
          );

          // Strength (right axis) — (amp/σ_band) × count with decomposition.
          lines.push(
            React.createElement(React.Fragment, { key: "strength" }, [
              swatch("strength", STRENGTH_COLOR),
              "strength: ",
              tooltipComponents.Bold({
                children: t.auditable
                  ? fmtNum(t.strength, 3)
                  : "— not auditable (< 3 cycles in window)",
              }),
              React.createElement("br"),
            ]),
          );
          if (t.auditable) {
            lines.push(
              React.createElement(
                "span",
                { key: "decomp", style: { opacity: 0.85 } },
                ` = ${fmtNum(t.ampNorm, 2)} amp/σband × ${fmtNum(t.count, 2)} count` +
                  ` · σband ${fmtNum(sigmaBand, 1)} yuan`,
              ),
              React.createElement("br"),
            );
          }
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
        name: "count / strength",
        nameTextStyle: { color: STRENGTH_COLOR, fontSize: 9 },
        position: "right",
        axisLine: { lineStyle: { color: STRENGTH_COLOR } },
        axisLabel: { color: STRENGTH_COLOR, fontSize: 9, formatter: (v: number) => fmtNum(v, 2) },
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
      // Count bars (right axis) — the recurrence COUNT factor
      // (extrema evidence × ACF coherence), precomputed in Python and
      // stored bin-aligned in count_spectrum. Says whether the day
      // freq actually repeats.
      {
        name: "count",
        type: "bar",
        yAxisIndex: 1,
        data: countData,
        itemStyle: { color: COUNT_COLOR, opacity: 0.8 },
        z: 3,
      },
      // Strength bars (right axis) — the summarized strength:
      // (amp/σ_band) × count (the former consolidated pattern score).
      // Zero for d > N/3 (not auditable).
      {
        name: "strength",
        type: "bar",
        yAxisIndex: 1,
        data: strengthData,
        itemStyle: { color: STRENGTH_COLOR, opacity: 0.8 },
        z: 4,
      },
    ],
  };
}
