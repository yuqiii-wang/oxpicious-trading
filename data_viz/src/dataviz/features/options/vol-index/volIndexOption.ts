/**
 * Vol index chart builders — the daily 30-day model-free implied-volatility
 * index (analysis.options_vol_index, computed by
 * analysis/options/compute/vix.py with the CBOE VIX methodology on
 * settlement prices; see docs/options_vol_smile_study.md) plus its rolling
 * correlation against the underlying spot close
 * (analysis.options_vol_index_vs_spot_price_corr — Pearson ρ of daily spot
 * log-return vs vol-index log-change, CBOE's VIX-vs-spot convention).
 *
 * Series are aligned on the sorted union of dates (null where an
 * underlying has no value that day — honest gaps, no connecting).
 */
import {
  baseChartOption,
  commonTooltip,
} from "@/shared/charts/base-chart";
import {
  MUTED_PALETTE,
  SPOT_COLOR,
  UNDERLYING_LABELS,
  axisColors,
  commonDataZoom,
  commonGrid,
  commonLegend,
} from "@/theme/chart-palette";
import { fmtNum } from "@/lib/series";
import {
  rejectionMarkerSpec,
  signalMarkerSpec,
} from "@/shared/charts/signalMarker";
import type { ThemeMode } from "@/store/filters";
import type { VolIndexCorrRow, VolIndexRow } from "@shared/types";
import type { EChartsOption, SeriesOption } from "echarts";

export function volIndexUnderlyingLabel(code: string): string {
  return UNDERLYING_LABELS[code] ?? code;
}

/** One SIGNAL-history event to mark on the vol-index curve (the
 *  options page's IN-SIGNAL marker mode — analysis_signals.
 *  history_signals, the options_vix family): actionable events draw
 *  THE shared buy/sell triangle; a rejected event (is_rejection)
 *  draws the grey diamond instead — the same contract as the skew
 *  chart's SharedSkewSignalEvent. */
export interface VolIndexSignalEvent {
  date: string;
  /** "buy" / "sell" — the event's recorded action. */
  action: string;
  /** TRUE = the gate-declined no-action record (the grey diamond). */
  is_rejection?: boolean;
}

/** The in-signal builder options — the vol chart's marker layer +
 * selected-day pin; undefined leaves the in-analysis chart exactly as
 * the Options Analysis page renders it. */
export interface VolIndexSignalLayer {
  signalEvents?: readonly VolIndexSignalEvent[];
  /** The chart's selected day ('YYYY-MM-DD'; the pin on the vol line
   *  at that date). */
  selectedDate?: string | null;
}

/** The corr companion's in-signal options: the family's corr bars as
 * guide lines (-0.8 the fear bar, -0.5 the unwind bar; the emphasized
 * one thick) + the selected-day pin. */
export interface VolIndexCorrSignalLayer {
  /** value → TRUE = emphasized (the picked trigger-bar chip's config
   * bar; the wall-panel chip → bold-wall analog). */
  corrBars?: ReadonlyArray<{ value: number; emphasized?: boolean }>;
  selectedDate?: string | null;
}

/** Identity-stable no-event defaults (a fresh [] per render dirties
 * the option memo and forces a full notMerge rebuild). */
export const NO_VOL_SIGNAL_EVENTS: readonly VolIndexSignalEvent[] = [];
export const NO_VOL_CORR_BARS: readonly { value: number; emphasized?: boolean }[] = [];

/** Series names of the SIGNAL marker classes (legend order) — the
 * shared visual language of the skew chart's in-signal mode. */
export const VOL_SIGNAL_MARKER_SERIES_NAMES = [
  "Signal Buy",
  "Signal Sell",
  "Signal No-Action",
] as const;

/** Colored series dot inside tooltip rows (repo ColoredDot convention). */
function tooltipDot(color: string): string {
  return (
    `<span style="display:inline-block;width:9px;height:9px;border-radius:50%;` +
    `background:${color};margin-right:5px;border:1px solid rgba(0,0,0,0.2);` +
    `vertical-align:middle;"></span>`
  );
}

export function buildVolIndexOption(
  rows: VolIndexRow[],
  themeMode: ThemeMode,
  /** date → underlying spot close (yuan / index points) — optional context
   *  overlay on a second right-hand axis; absent → vol-only chart. */
  spotByDate?: Map<string, number | null>,
  /** The IN-SIGNAL layer (the options signals page): signal-history
   *  markers on the vol curve + the selected-day pin. Absent → the
   *  in-analysis chart exactly as before. */
  signalLayer?: VolIndexSignalLayer,
): EChartsOption {
  const c = axisColors(themeMode);

  const byUnderlying = new Map<string, Map<string, number | null>>();
  const dateSet = new Set<string>();
  for (const r of rows) {
    let series = byUnderlying.get(r.underlying_code);
    if (!series) {
      series = new Map();
      byUnderlying.set(r.underlying_code, series);
    }
    series.set(r.date, r.vol_index_30d);
    dateSet.add(r.date);
  }
  const dates = Array.from(dateSet).sort();
  const codes = Array.from(byUnderlying.keys()).sort();

  const hasSpot = spotByDate != null && spotByDate.size > 0;
  const events = signalLayer?.signalEvents ?? NO_VOL_SIGNAL_EVENTS;
  const selectedDate = signalLayer?.selectedDate ?? null;

  // Series naming: the vol line carries the " VIX" suffix (a bare code
  // name would read as the price), the spot line takes the code name
  // itself — tooltip rows and legend read unambiguously side by side.
  const volName = (code: string) => `${volIndexUnderlyingLabel(code)} VIX`;
  const spotName =
    codes.length === 1 ? volIndexUnderlyingLabel(codes[0]) : "Spot";

  return baseChartOption(themeMode, {
    title: {
      text: "Vol Index (vol pts) — smile LEVEL per underlying",
      left: 4,
      top: 0,
      textStyle: { color: c.textColor, fontSize: 11, fontWeight: 600 },
    },
    tooltip: commonTooltip(themeMode, {
      // The original tooltip block had no axisPointer — keep the default
      // line pointer (dropping the kit's cross/snap default).
      axisPointer: undefined,
      borderWidth: 1,
      padding: [6, 10],
      textStyle: { color: c.textColor, fontSize: 12 },
      formatter: (params: unknown) => {
        const list = params as Array<{
          dataIndex: number;
          seriesName: string;
          value: number | null;
          color?: string;
        }>;
        const idx = list[0]?.dataIndex ?? -1;
        const date = idx >= 0 && idx < dates.length ? dates[idx] : "";
        const lines = list
          .filter((p) => p.value != null)
          .map((p) => {
            const dot = p.color != null ? tooltipDot(p.color) : "";
            return (
              `<div style="line-height:1.7;">${dot}${p.seriesName}: ` +
              `<b>${fmtNum(p.value as number, 2)}</b></div>`
            );
          })
          .join("");
        return (
          `<div style="font-weight:600;border-bottom:1px solid ${c.splitLineColor};` +
          `margin-bottom:4px;padding-bottom:2px;">${date}</div>` +
          (lines || "<div>—</div>")
        );
      },
    }),
    legend: commonLegend(themeMode, {
      data: [
        ...codes.map((code) => volName(code)),
        ...(hasSpot ? [spotName] : []),
      ],
    }),
    dataZoom: commonDataZoom(),
    grid: commonGrid({ top: 40, bottom: 44 }),
    xAxis: {
      type: "category",
      data: dates,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisTick: { show: false },
      axisLabel: { color: c.textColor, fontSize: 9 },
    },
    yAxis: [
      {
        type: "value",
        name: "vol pts",
        scale: true,
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { show: false },
        axisLabel: { color: c.textColor, fontSize: 9 },
        splitLine: { lineStyle: { color: c.splitLineColor } },
      },
      // Right-hand price axis — only when the spot overlay is present
      // (no series binds to it otherwise).
      ...(hasSpot
        ? [
            {
              type: "value" as const,
              name: "spot close",
              scale: true,
              nameTextStyle: { color: c.textColor, fontSize: 9 },
              axisLine: { show: false },
              axisLabel: { color: c.textColor, fontSize: 9 },
              splitLine: { show: false },
            },
          ]
        : []),
    ],
    series: [
      ...codes.map((code, i) => {
        const series = byUnderlying.get(code)!;
        // The selected-day pin rides the (first) vol line's markPoint —
        // the skew chart's spot-pin convention.
        const isMarkerHost = i === 0 && selectedDate != null;
        const pinVal = isMarkerHost
          ? (series.get(selectedDate) ?? null)
          : null;
        return {
          name: volName(code),
          type: "line" as const,
          symbol: "none",
          connectNulls: false,
          lineStyle: {
            color: MUTED_PALETTE[i % MUTED_PALETTE.length],
            width: 1.4,
          },
          itemStyle: { color: MUTED_PALETTE[i % MUTED_PALETTE.length] },
          emphasis: { focus: "series" as const },
          data: dates.map((d) => series.get(d) ?? null),
          ...(isMarkerHost && pinVal != null
            ? {
                markPoint: {
                  symbol: "circle",
                  symbolSize: 9,
                  itemStyle: {
                    color: MUTED_PALETTE[0],
                    borderColor: "#fff",
                    borderWidth: 1,
                  },
                  data: [
                    { name: "selected", coord: [selectedDate, pinVal] },
                  ],
                  label: { show: false },
                  z: 10,
                },
              }
            : {}),
        };
      }),
      // Underlying spot close on the right axis (null where the OHLCV
      // source has no row that day — honest gaps, same as the vol series).
      ...(hasSpot
        ? [
            {
              name: spotName,
              type: "line" as const,
              yAxisIndex: 1,
              symbol: "none",
              connectNulls: false,
              lineStyle: { color: SPOT_COLOR, width: 1.2 },
              itemStyle: { color: SPOT_COLOR },
              emphasis: { focus: "series" as const },
              data: dates.map((d) => spotByDate!.get(d) ?? null),
            },
          ]
        : []),
      // The IN-SIGNAL marker layer (the options signals page): the
      // signal-history events on the vol curve — buy ▲ / sell ▼ via
      // THE shared triangles, the grey ◆ diamond on gate-rejected
      // events. One scatter series per class (only classes with >= 1
      // event), so the legend toggles classes individually — the skew
      // chart's signalEventSeries convention.
      ...volSignalMarkerSeries(events, byUnderlying, codes),
    ],
  });
}

/** SIGNAL-history scatter markers on the vol curve (the options page's
 * IN-SIGNAL marker mode — analysis_signals.history_signals, the
 * options_vix family, fetched with signal_type=options_vix). The
 * marker's y = the underlying's own vol_index_30d that day. */
function volSignalMarkerSeries(
  events: readonly VolIndexSignalEvent[],
  byUnderlying: Map<string, Map<string, number | null>>,
  codes: string[],
): SeriesOption[] {
  // the marker host series values: the single underlying's vol curve
  // (multi-underlying payloads mark the first code's line)
  const volByDate =
    byUnderlying.get(codes[0] ?? "") ?? new Map<string, number | null>();
  const classes: Array<{
    key: "buy" | "sell" | "rej";
    name: string;
    spec: { symbol: string; symbolSize: [number, number]; symbolOffset: [number, number]; itemStyle: { color: string } };
  }> = [
    {
      key: "buy",
      name: VOL_SIGNAL_MARKER_SERIES_NAMES[0],
      spec: signalMarkerSpec("buy"),
    },
    {
      key: "sell",
      name: VOL_SIGNAL_MARKER_SERIES_NAMES[1],
      spec: signalMarkerSpec("sell"),
    },
    {
      key: "rej",
      name: VOL_SIGNAL_MARKER_SERIES_NAMES[2],
      spec: rejectionMarkerSpec(),
    },
  ];
  const out: SeriesOption[] = [];
  for (const cls of classes) {
    const data: Array<[string, number]> = [];
    for (const ev of events) {
      const rej = ev.is_rejection === true;
      const isBuy = !rej && ev.action === "buy";
      const isSell = !rej && ev.action === "sell";
      const match = cls.key === "rej" ? rej
        : cls.key === "buy" ? isBuy : isSell;
      if (!match) continue;
      const y = volByDate.get(ev.date);
      if (y == null || !Number.isFinite(y)) continue;
      data.push([ev.date, y]);
    }
    if (data.length === 0) continue;
    out.push({
      type: "scatter" as const,
      name: cls.name,
      data,
      symbol: cls.spec.symbol,
      symbolSize: cls.spec.symbolSize,
      symbolOffset: cls.spec.symbolOffset,
      itemStyle: { color: cls.spec.itemStyle.color, borderColor: "#fff", borderWidth: 1 },
      z: 5,
      tooltip: { show: false },
    });
  }
  return out;
}

/** Corr chart series spec — one curve per rolling window. */
const CORR_SERIES: Array<{
  key: "corr_5d" | "corr_20d" | "corr_60d" | "corr_255d";
  name: string;
}> = [
  { key: "corr_5d", name: "Corr 5d" },
  { key: "corr_20d", name: "Corr 20d" },
  { key: "corr_60d", name: "Corr 60d" },
  { key: "corr_255d", name: "Corr 255d" },
];

export function buildVolIndexCorrOption(
  corrRows: VolIndexCorrRow[],
  volRows: VolIndexRow[],
  themeMode: ThemeMode,
  /** The IN-SIGNAL layer (the options signals page): the family's
   *  corr bars as guide lines (the emphasized config's bar thick) +
   *  the selected-day pin. Absent → the in-analysis chart exactly as
   *  before. */
  signalLayer?: VolIndexCorrSignalLayer,
): EChartsOption {
  const c = axisColors(themeMode);

  // SAME date axis as the vol chart (sorted union of the vol rows' dates)
  // so the echarts.connect group syncs hover + dataZoom by index exactly;
  // the API returns the corr series linearly interpolated onto every
  // vol-index session (the stored 5-session stride samples + daily tail
  // are interpolated server-side; sessions outside the stored span stay
  // null and connectNulls joins them).
  // No in-chart title — the embedding card's footer caption labels it.
  const dates = Array.from(new Set(volRows.map((r) => r.date))).sort();
  const byDate = new Map(corrRows.map((r) => [r.date, r]));
  const corrBars = signalLayer?.corrBars ?? NO_VOL_CORR_BARS;
  const selectedDate = signalLayer?.selectedDate ?? null;
  const selCorr = selectedDate != null
    ? (byDate.get(selectedDate)?.corr_20d ?? null)
    : null;

  return baseChartOption(themeMode, {
    tooltip: commonTooltip(themeMode, {
      axisPointer: undefined,
      borderWidth: 1,
      padding: [6, 10],
      textStyle: { color: c.textColor, fontSize: 12 },
      formatter: (params: unknown) => {
        const list = params as Array<{
          dataIndex: number;
          seriesName: string;
          value: number | null;
          color?: string;
        }>;
        const idx = list[0]?.dataIndex ?? -1;
        const date = idx >= 0 && idx < dates.length ? dates[idx] : "";
        const lines = list
          .filter((p) => p.value != null)
          .map((p) => {
            const dot = p.color != null ? tooltipDot(p.color) : "";
            return (
              `<div style="line-height:1.7;">${dot}${p.seriesName}: ` +
              `<b>${fmtNum(p.value as number, 2)}</b></div>`
            );
          })
          .join("");
        return (
          `<div style="font-weight:600;border-bottom:1px solid ${c.splitLineColor};` +
          `margin-bottom:4px;padding-bottom:2px;">${date}</div>` +
          (lines || "<div>— (no corr sample that day)</div>")
        );
      },
    }),
    legend: commonLegend(themeMode, {
      data: CORR_SERIES.map((s) => s.name),
    }),
    // The vol chart's slider (same echarts.connect group) drives this
    // chart — keep the dataZoom pair so the relayed actions zoom it, but
    // hide the slider (one slider per card, no duplicated time scrubber).
    dataZoom: commonDataZoom().map((d, i) =>
      i === 1 ? { ...d, show: false } : d,
    ),
    grid: commonGrid({ top: 30, bottom: 28 }),
    xAxis: {
      type: "category",
      data: dates,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisTick: { show: false },
      axisLabel: { color: c.textColor, fontSize: 9 },
    },
    yAxis: {
      type: "value",
      min: -1,
      max: 1,
      interval: 0.5,
      name: "ρ",
      nameTextStyle: { color: c.textColor, fontSize: 9 },
      axisLine: { show: false },
      axisLabel: { color: c.textColor, fontSize: 9 },
      splitLine: { lineStyle: { color: c.splitLineColor } },
    },
    series: CORR_SERIES.map((s, i) => {
      // The corr_20d curve hosts the in-signal layers: the family's
      // bars as markLine guides (the emphasized config's bar thick +
      // labeled) and the selected-day pin (the corr may be null on a
      // stride-gap day — the pin then draws on the vol chart only).
      const host = s.key === "corr_20d";
      const isSelHost = host && selectedDate != null && selCorr != null;
      return {
        name: s.name,
        type: "line" as const,
        symbol: "none",
        connectNulls: true,
        lineStyle: { color: MUTED_PALETTE[i % MUTED_PALETTE.length], width: 1.4 },
        itemStyle: { color: MUTED_PALETTE[i % MUTED_PALETTE.length] },
        emphasis: { focus: "series" as const },
        data: dates.map((d) => byDate.get(d)?.[s.key] ?? null),
        ...(host && corrBars.length > 0
          ? {
              markLine: {
                silent: true,
                symbol: "none",
                data: corrBars.map((b) => ({
                  yAxis: b.value,
                  lineStyle: {
                    color: c.textColor,
                    width: b.emphasized ? 2 : 1,
                    type: "dashed" as const,
                    opacity: b.emphasized ? 0.9 : 0.45,
                  },
                  label: {
                    show: b.emphasized === true,
                    formatter: `bar ${b.value}`,
                    color: c.textColor,
                    fontSize: 9,
                    position: "insideEndTop" as const,
                  },
                })),
                z: 4,
              },
            }
          : {}),
        ...(isSelHost
          ? {
              markPoint: {
                symbol: "circle",
                symbolSize: 9,
                itemStyle: {
                  color: MUTED_PALETTE[1] ?? MUTED_PALETTE[0],
                  borderColor: "#fff",
                  borderWidth: 1,
                },
                data: [{ name: "selected", coord: [selectedDate, selCorr] }],
                label: { show: false },
                z: 10,
              },
            }
          : {}),
      };
    }),
  });
}
