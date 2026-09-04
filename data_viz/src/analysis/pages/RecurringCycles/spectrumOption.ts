/**
 * ECharts option builder for ONE per-range_days recurring-cycle bar chart
 * (the charts below the top index price plot on the Recurring Cycles page).
 *
 * The spectra are stored DAY-ALIGNED in analysis.recurring_cycles
 * (element j = integer day period d = j + 2, length floor(N/2) − 1) and
 * precomputed in Python by analyze.recurring_cycles.pattern_score:
 *   • amp (left y-axis, bars) — energy-merged FFT amplitude (yuan): the
 *     Fourier REFERENCE for which day periods carry swing energy. NOT
 *     recurrence evidence by itself.
 *   • count (right y-axis, bars) — the recurrence COUNT factor:
 *     extrema evidence (prominence-filtered alternating swing highs/lows
 *     whose full-cycle spacing lands within ±15% of d) × ACF coherence
 *     (multiples of d with significant autocorrelation ≥ 1.96/√N after
 *     MA detrending). Says whether price actually REPEATED that
 *     rise/drop spacing.
 *   • strength (right y-axis, bars) — the summarized recurring strength:
 *     strength(d) = (amp(d)/σ_band) × count(d). A day period that
 *     recurs with noticeable highs and lows peaks here; one-off swings,
 *     trends, and noise do not. 0 (not auditable) for d > N/3 (under 3
 *     cycles in the window).
 *   • significance (right y-axis, bars) — the POISSON AUDIT of the
 *     recurrence evidence: −log10 of the Bonferroni-adjusted tail
 *     p-value P(Poisson(λ̂₀) ≥ hits), where hits(d) is the observed
 *     prominence-filtered swing-hit count and λ̂₀(d) the chance
 *     expectation of a point-process null, empirically calibrated on
 *     random-walk + stochastic-vol price nulls (validated FPR ≤ 5%,
 *     power 99%+ on synthetic 20d cycles). 0 = not significant; the
 *     dashed markline at 1.301 marks p < 0.05 (≥ 2.0 ⇔ p < 0.01).
 *     Zero for d > N/3. Separates a REAL repeated rise/drop pattern
 *     from the deterministic count factor, which scores ~0.83 on pure
 *     noise (study: temp_scripts/study_rc_poisson*.py).
 *
 * The RECURRING period (row.period_days = argmax of strength) is
 * highlighted green — the headline answer to "at what spacing does this
 * security's price repeatedly rise and drop" — with its audit verdict
 * (significance tier + evidence ratio vs the chance rate) in the title
 * and tooltip. The X-axis is integer day
 * periods, descending left→right (long cycles left) — the conventional
 * spectrum layout (low frequency → high frequency).
 *
 * Tooltips also show the strength decomposition amp/σ_band × count.
 *
 * Cutoff: by default day periods < 5 are hidden (high-frequency noise on
 * a close-price series); the expand button in the panel header reveals
 * the full granular spectrum. (Nyquist: the shortest representable day
 * period is 2d.)
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
import type { RecurringCyclesSpectrumRow } from "@shared/types";

/** Right-axis bar color for the recurrence count factor. */
export const COUNT_COLOR = "#ffa726";
/** Right-axis bar color for the summarized recurring strength (amp × count). */
export const STRENGTH_COLOR = "#ab47bc";
/** Right-axis bar color for the Poisson-audit significance (−log10 p). */
export const SIG_COLOR = "#26c6da";
/** −log10(0.05): the Bonferroni significance threshold drawn as markline. */
export const SIG05 = 1.30103;

/** p-value tier for a stored significance value (−log10 Bonferroni p). */
function pTier(sig: number): string {
  if (sig >= 2.0) return "p<0.01";
  if (sig >= SIG05) return "p<0.05";
  if (sig >= 1.0) return "p<0.1";
  return "n.s.";
}

export interface SpectrumOptionParams {
  row: RecurringCyclesSpectrumRow;
  themeMode: ThemeMode;
  /** When false (default), hide granular high-freq day periods (< 5d).
   *  When true, show all day periods. Toggled by the expand button. */
  expanded: boolean;
}

/** Build the per-day recurring-cycle bar chart option for one range_days. */
export function buildSpectrumOption(params: SpectrumOptionParams): EChartsOption {
  const { row, themeMode, expanded } = params;
  const c = axisColors(themeMode);
  const N = row.range_days;
  const totalWindows = row.total_windows;

  // Day-aligned spectra: element j = day period d = j + 2.
  const ampSpec = row.amplitude_spectrum;
  const countSpec = row.count_spectrum;
  const strengthSpec = row.strength_spectrum;
  const sigSpec = row.significance_spectrum ?? [];
  const nDays = ampSpec.length;

  // Default cutoff: hide day periods < 5d (high-freq noise on close-price
  // series). The expand button reveals them. Nyquist floor is 2d.
  const MIN_DAY = expanded ? 2 : 5;

  // σ_band — total swing energy of the band (periods ≤ N/4); basis for
  // the amp/σ_band factor shown in the strength decomposition.
  let sb2 = 0;
  const bandMaxDay = Math.floor(N / 4);
  for (let j = 0; j < nDays; j++) {
    const d = j + 2;
    if (d <= bandMaxDay) sb2 += ampSpec[j] * ampSpec[j];
  }
  const sigmaBand = Math.sqrt(sb2 / 2);

  // Visible day periods (>= MIN_DAY), descending — long cycles left.
  const js: number[] = [];
  for (let j = nDays - 1; j >= 0; j--) {
    if (j + 2 >= MIN_DAY) js.push(j);
  }
  const nVisible = js.length;

  const categories: string[] = new Array(nVisible);
  const ampData: number[] = new Array(nVisible);
  const countData: number[] = new Array(nVisible);
  const strengthData: number[] = new Array(nVisible);
  const sigData: number[] = new Array(nVisible);
  const tipRows: Array<{
    day: number;
    amp: number;
    count: number;
    strength: number;
    sig: number;
    ampNorm: number;
    auditable: boolean;
    isDom: boolean;
  }> = new Array(nVisible);

  // The recurring period (argmax of strength) — the headline highlight.
  const domDay = row.period_days;
  for (let v = 0; v < nVisible; v++) {
    const j = js[v];
    const d = j + 2;
    const amp = ampSpec[j] ?? 0;
    const count = countSpec[j] ?? 0;
    const strength = strengthSpec[j] ?? 0;
    const sig = sigSpec[j] ?? 0;
    const ampNorm = sigmaBand > 0 ? amp / sigmaBand : 0;
    const auditable = d >= 2 && d <= Math.floor(N / 3);
    categories[v] = `${d}d`;
    ampData[v] = amp;
    countData[v] = count;
    strengthData[v] = strength;
    sigData[v] = sig;
    tipRows[v] = {
      day: d,
      amp,
      count,
      strength,
      sig,
      ampNorm,
      auditable,
      isDom: d === domDay && domDay > 0,
    };
  }

  const domPos = domDay >= MIN_DAY ? domDay - 2 : -1; // index into DESC categories
  const domIdx = domPos >= 0 ? nVisible - 1 - domPos : -1; // visible-array index
  const domTxt =
    domDay > 0
      ? `recurring period ≈ ${domDay}d (strength ${fmtNum(row.strength, 3)} · amp ${fmtNum(row.amplitude, 2)})` +
        ` · audit ${pTier(row.significance)}` +
        (row.evidence_ratio > 0
          ? ` · evidence ${fmtNum(row.evidence_ratio, 1)}× null`
          : "")
      : "no recurring period detected";

  const showZoom = nVisible > 40;

  return {
    backgroundColor: "transparent",
    animation: false,
    title: {
      text:
        `${N}d window · ${domTxt}` +
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
      data: ["amp", "count", "strength", "significance"],
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
        const v = arr[0].dataIndex;
        const t = tipRows[v];
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
        lines.push(tooltipComponents.Bold({ children: `${t.day}d period` }));
        if (t.isDom) {
          lines.push(tooltipComponents.Bold({ children: " · recurring period" }));
        }
        lines.push(React.createElement("br"));

        // Amplitude (left axis) — the Fourier reference.
        lines.push(
          React.createElement(React.Fragment, { key: "amp" }, [
            swatch("amp", PALETTE_HI),
            "amp: ",
            tooltipComponents.Bold({ children: `${fmtNum(t.amp, 2)} yuan` }),
            React.createElement("br"),
          ]),
        );

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

        // Significance (right axis) — the Poisson audit of the hit
        // count vs the calibrated chance rate λ̂₀.
        lines.push(
          React.createElement(React.Fragment, { key: "sig" }, [
            swatch("significance", SIG_COLOR),
            "significance: ",
            tooltipComponents.Bold({ children: fmtNum(t.sig, 3) }),
            ` — Poisson audit ${t.auditable ? pTier(t.sig) : "— not auditable"}` +
              ` (−log10 Bonferroni p; ≥ ${fmtNum(SIG05, 2)} ⇔ p<0.05)`,
            React.createElement("br"),
          ]),
        );
        if (t.isDom && row.evidence_ratio > 0) {
          lines.push(
            React.createElement(
              "span",
              { key: "ev", style: { opacity: 0.85 } },
              `evidence: swing-hits ${fmtNum(row.evidence_ratio, 2)}× the ` +
                `chance expectation λ̂₀ at the recurring period`,
            ),
            React.createElement("br"),
          );
        }

        lines.push(
          React.createElement(
            "span",
            { style: { opacity: 0.85 } },
            "a period scores only when price REPEATED its rise/drop spacing" +
              " — the significance bars flag when the repetition beats the" +
              " empirically calibrated chance rate",
          ),
        );

        return renderReactElement(React.createElement(React.Fragment, null, lines));
      },
    },
    xAxis: {
      type: "category",
      data: categories,
      name: "day period",
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
        name: "count / strength / sig",
        nameTextStyle: { color: STRENGTH_COLOR, fontSize: 9 },
        position: "right",
        axisLine: { lineStyle: { color: STRENGTH_COLOR } },
        axisLabel: { color: STRENGTH_COLOR, fontSize: 9, formatter: (v: number) => fmtNum(v, 2) },
        splitLine: { show: false },
      },
    ],
    dataZoom: showZoom ? commonDataZoom({}) : undefined,
    series: [
      // Amplitude bars (left axis) — the Fourier reference for which day
      // periods carry swing energy.
      {
        name: "amp",
        type: "bar",
        yAxisIndex: 0,
        data: ampData,
        itemStyle: { color: PALETTE_HI, opacity: 0.85 },
        z: 2,
      },
      // Count bars (right axis) — the recurrence COUNT factor
      // (extrema evidence × ACF coherence). Says whether the day
      // period actually repeats.
      {
        name: "count",
        type: "bar",
        yAxisIndex: 1,
        data: countData,
        itemStyle: { color: COUNT_COLOR, opacity: 0.8 },
        z: 3,
      },
      // Strength bars (right axis) — the summarized recurring strength:
      // (amp/σ_band) × count. Zero for d > N/3 (not auditable). The
      // RECURRING period (argmax of strength) is highlighted green.
      {
        name: "strength",
        type: "bar",
        yAxisIndex: 1,
        data: strengthData.map((v, idx) => ({
          value: v,
          itemStyle: {
            color: idx === domIdx ? UP_COLOR : STRENGTH_COLOR,
            opacity: idx === domIdx ? 1 : 0.8,
          },
        })),
        z: 4,
      },
      // Significance bars (right axis) — the Poisson audit: −log10 of
      // the Bonferroni-adjusted tail p of the swing-hit count vs the
      // empirically calibrated chance rate λ̂₀. The dashed markline is
      // the p<0.05 threshold (≥ 1.301); bars above it flag day periods
      // whose rise/drop repetition is statistically real, not chance.
      {
        name: "significance",
        type: "bar",
        yAxisIndex: 1,
        data: sigData,
        itemStyle: { color: SIG_COLOR, opacity: 0.85 },
        z: 5,
        markLine: {
          silent: true,
          symbol: "none",
          animation: false,
          lineStyle: { color: SIG_COLOR, type: "dashed", width: 1, opacity: 0.7 },
          label: {
            color: SIG_COLOR,
            fontSize: 8,
            formatter: "p<0.05",
            position: "insideEndTop",
          },
          data: [{ yAxis: SIG05 }],
        },
      },
    ],
  };
}
