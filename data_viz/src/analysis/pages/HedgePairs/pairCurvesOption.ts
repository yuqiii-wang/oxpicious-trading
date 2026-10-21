/**
 * ECharts option builder for the Hedge Pairs upper plot — the PAIR
 * PORTFOLIO chart: the pair's TWO industry composite curves plus the
 * broad-market benchmark overlay, ALL REBASED TO 100 on ONE shared
 * LEFT y-axis (each curve normalized at its own first close by the
 * component — the benchmark at its own first close, the composites at
 * theirs — so the three curves compare directly; the former
 * dual-axis split is gone: rebased curves need no second axis), plus
 * the portfolio's signal-history events as action markers (buy ▲ /
 * sell ▼ / wait ◆ — the shared signalMarker language, one scatter
 * series per class per leg).
 *
 * Curves are the industries' mean_close composites
 * (stats.industry_basic_stats via /api/analysis/industry-sentiments/chart,
 * pool_size="all" slice, rebased by the component) and the benchmark
 * close (stats.index_basic_stats via /api/analysis/industry-attribution/
 * benchmark-price, rebased to 100 at its own first close — the
 * composites' base semantics). The benchmark draws dashed behind the
 * two main lines (reference-curve look).
 *
 * The portfolio's signal-history markers (`signalEvents` — the
 * vol-index in-signal precedent): the events mark BOTH legs' curves
 * at the event date (the event is PORTFOLIO-level — flagging the
 * date across the pair), buy/sell hugging their leg's point, the
 * wait diamond centered. One scatter series per class per leg, all
 * sharing the class name so the legend toggles the class.
 *
 * X is the sorted UNION of all curves' dates: each series maps its values
 * onto the union positions and bridges its null days with
 * connectNulls: true.
 */
import type { EChartsOption, SeriesOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import { baseChartOption, commonTooltip } from "@/shared/charts/base-chart";
import { signalEventScatterSeries } from "@/shared/charts/signalMarker";
import {
  MA120_COLOR,
  PALETTE_HI,
  SPOT_COLOR,
  axisColors,
  commonDataZoom,
  commonGrid,
  commonLegend,
} from "@/theme/chart-palette";
import { fmtNum } from "@/lib/series";

export interface PairCurveSeries {
  /** Industry_id (the pair-table code domain, e.g. DIV_GENERAL). */
  code: string;
  label: string;
  /** The industry's composite close series (mean_close, pool "all"). */
  rows: ReadonlyArray<{ date: string; close: number | null }>;
}

/** One portfolio signal-history event for the marker layer (the
 *  vol-index VolIndexSignalEvent shape): wait/rejection events draw
 *  the grey diamond INSTEAD of a triangle. */
export interface PairSignalEvent {
  date: string;
  action: string;
  is_rejection?: boolean;
}

/** Identity-stable no-event default (a fresh [] per render dirties the
 *  option memo and forces a full notMerge rebuild). */
export const NO_PAIR_SIGNAL_EVENTS: readonly PairSignalEvent[] = [];

/** The marker classes' legend names (the vol-index "Signal Buy /
 *  Signal Sell / Signal No-Action" convention; wait IS this family's
 *  no-action word). */
const MARKER_CLASS_NAMES = ["Signal Buy", "Signal Sell", "Signal Wait"] as const;

/** Map one series' values onto the union date axis (null elsewhere). */
function closeOnUnion(
  rows: ReadonlyArray<{ date: string; close: number | null }>,
  dateIdx: Map<string, number>,
  nDates: number,
): Array<number | null> {
  const out: Array<number | null> = new Array(nDates).fill(null);
  for (const r of rows) {
    const v = r.close;
    if (v == null || !Number.isFinite(v)) continue;
    const i = dateIdx.get(r.date);
    if (i != null) out[i] = v;
  }
  return out;
}

/** The portfolio's signal-history markers on BOTH legs' curves (see
 *  the module doc) — one scatter series per (class, leg), the class
 *  name shared so the legend toggles the whole class. */
function pairSignalMarkerSeries(
  events: readonly PairSignalEvent[],
  aData: ReadonlyArray<number | null>,
  bData: ReadonlyArray<number | null>,
  dateIdx: Map<string, number>,
): SeriesOption[] {
  // the legs' date → close lookups (union-aligned value arrays)
  const closeByDate = (data: ReadonlyArray<number | null>) => {
    const m = new Map<string, number>();
    for (const [d, i] of dateIdx) {
      const v = data[i];
      if (v != null && Number.isFinite(v)) m.set(d, v);
    }
    return m;
  };
  const aByDate = closeByDate(aData);
  const bByDate = closeByDate(bData);
  // THE global shared action-marker layer, one call per leg (the
  // class names stay shared so the legend toggles the whole class;
  // this family's no-action word is "Wait" — the grey diamond; no
  // white ring, z 6 — the plain Short-Term Tech look above the curves)
  const names = MARKER_CLASS_NAMES;
  return [
    ...signalEventScatterSeries(events, aByDate, { names, z: 6 }),
    ...signalEventScatterSeries(events, bByDate, { names, z: 6 }),
  ];
}

export function buildPairCurvesOption(
  a: PairCurveSeries,
  b: PairCurveSeries,
  mode: ThemeMode,
  benchmark?: PairCurveSeries | null,
  signalEvents?: readonly PairSignalEvent[],
): EChartsOption | null {
  if (a.rows.length === 0 && b.rows.length === 0) return null;
  const c = axisColors(mode);
  const events = signalEvents ?? NO_PAIR_SIGNAL_EVENTS;

  // Sorted union of all curves' calendars.
  const dateSet = new Set<string>();
  for (const r of a.rows) dateSet.add(r.date);
  for (const r of b.rows) dateSet.add(r.date);
  if (benchmark) for (const r of benchmark.rows) dateSet.add(r.date);
  const dates = Array.from(dateSet).sort();
  const dateIdx = new Map(dates.map((d, i) => [d, i]));
  const n = dates.length;

  const aName = `${a.label} (${a.code})`;
  const bName = `${b.label} (${b.code})`;
  const aData = closeOnUnion(a.rows, dateIdx, n);
  const bData = closeOnUnion(b.rows, dateIdx, n);

  // Benchmark overlay — omitted from legend/series when absent or empty.
  const benchName = benchmark && benchmark.rows.length > 0
    ? `${benchmark.label} (${benchmark.code})`
    : null;
  const benchData = benchName
    ? closeOnUnion(benchmark.rows, dateIdx, n)
    : null;
  const legendData = benchName ? [aName, bName, benchName] : [aName, bName];
  // the marker classes PRESENT in the event payload join the legend (an
  // entry with no series behind it is a dead toggle)
  const evClass = (ev: PairSignalEvent): "buy" | "sell" | "wait" =>
    ev.is_rejection === true || ev.action === "wait"
      ? "wait"
      : ev.action === "buy" ? "buy" : ev.action === "sell" ? "sell" : "wait";
  const presentClasses = new Set(events.map(evClass));
  const classKeys = ["buy", "sell", "wait"] as const;
  MARKER_CLASS_NAMES.forEach((name, ci) => {
    if (presentClasses.has(classKeys[ci])) legendData.push(name);
  });
  // Series palette in series order — without it the legend icons and the
  // axis-tooltip swatches get the ECharts default palette (blue/green/…)
  // instead of each line's actual color.
  const seriesColors = benchName
    ? [SPOT_COLOR, MA120_COLOR, PALETTE_HI]
    : [SPOT_COLOR, MA120_COLOR];

  return baseChartOption(mode, {
    color: seriesColors,
    title: {
      text: "Pair Portfolio",
      left: 4,
      top: 0,
      textStyle: { color: c.textColor, fontSize: 11, fontWeight: 600 },
    },
    // top: 72 keeps the bands apart — the in-chart title row, the
    // top-right legend row (the marker classes may append a second
    // legend line), the y-axis name (nameLocation "end" renders ~26px
    // above the grid), and the plot itself. right: default (no second
    // axis since the 2026-10-18 rebase — all curves share the LEFT).
    grid: commonGrid({ left: 60, bottom: 50, top: 72 }),
    dataZoom: commonDataZoom(),
    tooltip: commonTooltip(mode, {
      axisPointer: { type: "line", snap: true },
      valueFormatter: (v) => fmtNum(v as number, 2),
    }),
    legend: commonLegend(mode, {
      itemWidth: 12,
      itemHeight: 7,
      data: legendData,
    }),
    xAxis: {
      type: "category",
      data: dates,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: {
        color: c.textColor,
        fontSize: 9,
        formatter: (v: string) => v.slice(0, 7),
        interval: Math.max(1, Math.floor(n / 6)),
      },
      splitLine: { show: false },
    },
    // ONE shared value axis: all three curves are rebased to 100 at
    // their own first close (by the component), so they compare
    // directly on the same scale — the former left/right industry
    // split existed only because the raw composites diverged on
    // different bases
    yAxis: {
      type: "value",
      scale: true,
      name: "Rebased close (start = 100)",
      nameTextStyle: { color: c.textColor, fontSize: 9 },
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: {
        color: c.textColor,
        fontSize: 9,
        formatter: (v: number) => fmtNum(v, 1),
      },
      splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
    },
    series: [
      {
        type: "line",
        name: aName,
        data: aData,
        symbol: "none",
        connectNulls: true,
        lineStyle: { color: SPOT_COLOR, width: 1.4 },
        z: 5,
      },
      {
        type: "line",
        name: bName,
        data: bData,
        symbol: "none",
        connectNulls: true,
        lineStyle: { color: MA120_COLOR, width: 1.4 },
        z: 5,
      },
      // Benchmark overlay — dashed rebased-close blue on the LEFT axis
      // (rebased to 100 like the composites), behind both main lines.
      ...(benchName && benchData
        ? [
            {
              type: "line" as const,
              name: benchName,
              data: benchData,
              symbol: "none",
              connectNulls: true,
              lineStyle: { color: PALETTE_HI, width: 1.2, type: "dashed" },
              itemStyle: { color: PALETTE_HI },
              z: 4,
            },
          ]
        : []),
      // The portfolio's signal-history markers (see the module doc).
      ...pairSignalMarkerSeries(events, aData, bData, dateIdx),
    ],
  });
}
