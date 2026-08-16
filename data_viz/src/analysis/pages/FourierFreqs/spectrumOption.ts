/**
 * ECharts option builder for ONE per-range_days FFT amplitude-spectrum bar
 * chart (the bar charts below the top index price plot on the Fourier
 * Frequencies page).
 *
 * Each chart shows the FULL one-sided amplitude spectrum for one
 * (code, last_date, range_days) window: one bar per FFT bin k=1..N//2
 * (DC at k=0 excluded). The bar height = |X[k]| × 2 / N (amplitude in
 * yuan — half the peak-to-peak swing of that sinusoidal component).
 *
 * The DOMINANT bin (highest amplitude → the dominant cycle period stored
 * as `freq`) is highlighted in green; the rest are muted blue. The x-axis
 * is labelled by cycle PERIOD in days (period = N / k) so a financial
 * reader sees "cycle of X days has amplitude Y" rather than an opaque
 * FFT bin index. Periods descend left-to-right (long cycles left, short
 * cycles right) — the conventional FFT-spectrum layout (low frequency →
 * high frequency).
 *
 * Bins are uniformly spaced by index (one bar per bin) regardless of the
 * non-linear period spacing — this is the standard discrete-spectrum bar
 * chart; the period label communicates the actual cycle length.
 */
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import {
  UP_COLOR,
  PALETTE_HI,
  axisColors,
  commonGrid,
  commonDataZoom,
} from "@/theme/chart-palette";
import { fmtNum } from "@/lib/series";
import type { FourierFreqsSpectrumRow } from "../../../../shared/types";

/** Build the bar-chart option for one range_days spectrum. */
export function buildSpectrumOption(
  row: FourierFreqsSpectrumRow,
  themeMode: ThemeMode,
): EChartsOption {
  const c = axisColors(themeMode);
  const N = row.range_days;
  const spectrum = row.spectrum;
  const nBins = spectrum.length;

  // For long windows (>= 255d) cut off bins whose cycle period is < 5d —
  // those high-frequency components are noise on a financial close-price
  // series (periods of 1–4 trading days have no economic meaning and just
  // clutter the right tail of the spectrum). Short windows (20/60d) keep
  // all bins since their shortest periods are already meaningful.
  const MIN_PERIOD = N >= 255 ? 5 : 1;

  // Build the visible-bin index list (bins whose period >= MIN_PERIOD).
  // Bin i corresponds to FFT bin k = i + 1 and period = round(N / k).
  const visibleIdx: number[] = [];
  for (let i = 0; i < nBins; i++) {
    const k = i + 1;
    const period = Math.round(N / k);
    if (period >= MIN_PERIOD) visibleIdx.push(i);
  }

  // Dominant bin (0-based index, in the ORIGINAL spectrum array) of the
  // max amplitude among the VISIBLE bins. Recomputed here (not read from
  // row.freq) so the highlight always matches the displayed bars even if
  // the stored freq was rounded or falls in the cut-off region.
  let domIdx = -1;
  let domVal = -Infinity;
  for (const i of visibleIdx) {
    if (spectrum[i] > domVal) {
      domVal = spectrum[i];
      domIdx = i;
    }
  }
  const domK = domIdx + 1;
  const domPeriod = Math.round(N / domK);

  // x-axis categories: cycle period in days (period = N / k). k ascending
  // → period descending left-to-right. One category per VISIBLE bin.
  const nVisible = visibleIdx.length;
  const categories: string[] = new Array(nVisible);
  const barData: Array<{ value: number; itemStyle: { color: string } }> = new Array(nVisible);
  for (let j = 0; j < nVisible; j++) {
    const i = visibleIdx[j];
    const k = i + 1;
    const period = Math.round(N / k);
    categories[j] = `${period}d`;
    barData[j] = {
      value: spectrum[i],
      itemStyle: { color: i === domIdx ? UP_COLOR : PALETTE_HI },
    };
  }

  // Show the dataZoom slider only when there are enough visible bars to
  // warrant zooming (< 40 bars fits comfortably without a slider).
  const showZoom = nVisible > 40;

  return {
    backgroundColor: "transparent",
    animation: false,
    title: {
      text: `${N}d window · dominant ≈ ${domPeriod}d (amp ${fmtNum(domVal, 2)})`,
      left: 8,
      top: 4,
      textStyle: { fontSize: 11, fontWeight: 600, color: c.textColor },
    },
    grid: commonGrid({ top: 36, bottom: showZoom ? 56 : 32, left: 48, right: 16 }),
    tooltip: {
      trigger: "item",
      backgroundColor: c.tooltipBg,
      borderColor: c.axisLineColor,
      textStyle: { color: c.textColor, fontSize: 11 },
      formatter: (params: unknown) => {
        const p = params as { dataIndex?: number; value?: number; color?: string };
        const j = p.dataIndex ?? 0; // index into the visible-bars array
        const i = visibleIdx[j] ?? 0; // original spectrum bin index
        const k = i + 1;
        const period = Math.round(N / k);
        const amp = p.value ?? 0;
        const isDom = i === domIdx ? " · <b>dominant</b>" : "";
        return `<b>${period}d cycle</b> (bin k=${k})<br/>`
          + `amplitude: <b>${fmtNum(amp, 4)}</b> yuan${isDom}`;
      },
    },
    xAxis: {
      type: "category",
      data: categories,
      name: "cycle period",
      nameLocation: "middle",
      nameGap: 22,
      nameTextStyle: { color: c.textColor, fontSize: 9 },
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: {
        color: c.textColor,
        fontSize: 8,
        hideOverlap: true,
        // Show ~8 labels across the axis regardless of visible-bin count.
        interval: Math.max(0, Math.floor(nVisible / 8) - 1),
      },
      splitLine: { show: false },
    },
    yAxis: {
      type: "value",
      name: "amp (yuan)",
      nameTextStyle: { color: c.textColor, fontSize: 9 },
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: { color: c.textColor, fontSize: 9, formatter: (v: number) => fmtNum(v) },
      splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
    },
    dataZoom: showZoom ? commonDataZoom({}) : undefined,
    series: [
      {
        type: "bar",
        data: barData,
        barWidth: nVisible > 200 ? "100%" : nVisible > 60 ? "90%" : "70%",
        // Muted opacity for non-dominant bars so the dominant (green)
        // bar pops without hiding the overall spectrum shape.
        itemStyle: { opacity: 0.85 },
        z: 2,
      },
    ],
  };
}
