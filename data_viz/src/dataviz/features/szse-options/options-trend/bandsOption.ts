/**
 * Expiry OI Bands chart option builder — aligned to the FULL trading date
 * range so the x-axis matches the P/C Ratio and Total OI plots.
 *
 * Extracted from ExpiryOiBandsPanel.tsx for the merged OptionsTrendPanel.
 * Uses shared bandsTooltip.ts for tooltip formatting.
 */
import {
  DOWN_COLOR,
  FUTURES_EXPIRY_DOT,
  SPOT_COLOR,
  UP_COLOR,
  axisColors,
  commonLegend,
  commonGrid,
} from "@/theme/chart-palette";
import { getBandTexture } from "./bandTexture";
import { makeBandsTooltipFormatter } from "./bandsTooltip";
import {
  EXPIRY_MARKERS_SERIES_NAME,
  buildExpiryData,
  makeExpiryDotTooltip,
} from "./expiryTooltip";
import {
  BEAR_THRESHOLD_SERIES_NAME,
  BULL_THRESHOLD_SERIES_NAME,
  PUT_PCT_GREEN,
  PUT_PCT_RED,
} from "./bandData";
import type { BandCell } from "./bandData";
import type { ExpiryMarker } from "./sharedData";
import type { EChartsOption } from "echarts";

function buildBandRenderItem(cells: BandCell[]) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return (params: any, api: any) => {
    const cell = cells[params.dataIndex as number];
    if (!cell) return null;
    const coord = api.coord([cell.value[0], cell.value[1]]) as number[];
    const cx = coord[0];
    const cy = coord[1];
    if (!Number.isFinite(cx) || !Number.isFinite(cy)) return null;
    const step = api.size ? (api.size([1, 0]) as number[]) : null;
    const half = step && step[0] > 0 ? step[0] / 2 : 4;
    return {
      type: "image" as const,
      style: {
        image: getBandTexture(cell.putPct, cell.strength),
        x: cx - half,
        y: cy - cell.h / 2,
        width: half * 2,
        height: cell.h,
      },
    };
  };
}

/**
 * Compute the >80% dominance boundary curves for the bands chart.
 *
 * Per date, strikes are sorted ascending by strike and the walls are placed
 * at the boundary of the DEEPEST contiguous dominant zone, anchored at the
 * chain edge (a stray dominant island separated by a non-dominant gap does
 * not extend the zone):
 *   - Bear (red): the put zone (putPct ≥ 80) grows UP from the lowest
 *     strike; the wall is its top boundary. The exact 80% level usually
 *     falls BETWEEN two strikes (e.g. 78% then 82%), so the boundary
 *     strike is linearly interpolated in between.
 *   - Bull (green): the call zone (putPct ≤ 20) grows DOWN from the highest
 *     strike; the wall is its bottom boundary, interpolated the same way.
 * Clamps to the chain edge when the zone spans the whole chain; null when
 * the zone does not exist on that date.
 */
export function computeThresholdCurves(
  cells: BandCell[],
  datesLength: number,
): {
  bull: (number | null)[];
  bear: (number | null)[];
} {
  const byIdx = new Map<number, BandCell[]>();
  for (const cell of cells) {
    const idx = cell.value[0];
    let group = byIdx.get(idx);
    if (!group) {
      group = [];
      byIdx.set(idx, group);
    }
    group.push(cell);
  }

  const interpolate = (a: BandCell, b: BandCell, target: number): number => {
    const dPct = b.putPct - a.putPct;
    if (dPct === 0) return b.strikeY;
    return a.strikeY + ((target - a.putPct) / dPct) * (b.strikeY - a.strikeY);
  };

  const bull: (number | null)[] = new Array(datesLength).fill(null);
  const bear: (number | null)[] = new Array(datesLength).fill(null);

  for (const [idx, group] of byIdx) {
    if (idx >= datesLength) continue;
    group.sort((a, b) => a.strikeY - b.strikeY);

    // Bear (red) wall: the put-dominant zone (putPct ≥ 80) is anchored at the
    // LOWEST strike and extends UP while strikes stay ≥ 80. The wall sits at
    // the top boundary of that contiguous run — stray ≥80% islands above a
    // <80% gap do NOT move it. E.g. putPct (low→high) 82%, 78%, 85%, 70%
    // → wall interpolated between the 82% and 78% strikes at 80%.
    let hi = -1;
    for (let i = 0; i < group.length; i++) {
      if (group[i].putPct >= PUT_PCT_RED) hi = i;
      else break;
    }
    if (hi >= 0) {
      bear[idx] = hi + 1 < group.length
        ? interpolate(group[hi], group[hi + 1], PUT_PCT_RED)
        : group[hi].strikeY; // run reaches chain top — clamp
    }

    // Bull (green) wall: the call-dominant zone (putPct ≤ 20) is anchored at
    // the HIGHEST strike and extends DOWN while strikes stay ≤ 20. The wall
    // sits at the bottom boundary of that contiguous run.
    let lo = -1;
    for (let i = group.length - 1; i >= 0; i--) {
      if (group[i].putPct <= PUT_PCT_GREEN) lo = i;
      else break;
    }
    if (lo >= 0) {
      bull[idx] = lo > 0
        ? interpolate(group[lo - 1], group[lo], PUT_PCT_GREEN)
        : group[lo].strikeY; // run reaches chain bottom — clamp
    }
  }

  return { bull, bear };
}

export function buildBandsOption(
  dates: string[],
  cells: BandCell[],
  spot: (number | null)[],
  themeMode: "light" | "dark",
  dataZoom: EChartsOption["dataZoom"] = undefined,
  expiryMarkers: ExpiryMarker[] = [],
): EChartsOption {
  const c = axisColors(themeMode);
  const renderItem = buildBandRenderItem(cells);
  const tooltipFormatter = makeBandsTooltipFormatter(c.textColor, c.tooltipBg, c.splitLineColor);
  const dotTooltip = makeExpiryDotTooltip({
    textColor: c.textColor,
    tooltipBg: c.tooltipBg,
    splitLineColor: c.splitLineColor,
  });
  const expiryData = buildExpiryData(dates, [spot], expiryMarkers);
  const { bull, bear } = computeThresholdCurves(cells, dates.length);

  return {
    backgroundColor: "transparent",
    animation: false,
    grid: commonGrid({ left: 56, right: 20, top: 36, bottom: 50 }),
    dataZoom,
    legend: commonLegend(themeMode),
    tooltip: {
      trigger: "axis",
      axisPointer: {
        type: "line",
        snap: true,
        link: [{ xAxisIndex: "all" }],
        lineStyle: { color: c.textColor, type: "dashed", opacity: 0.5 },
        label: {
          backgroundColor: c.tooltipBg,
          borderColor: c.splitLineColor,
          borderWidth: 1,
          padding: [3, 5],
          color: c.textColor,
          fontSize: 10,
        },
      },
      backgroundColor: c.tooltipBg,
      borderColor: c.splitLineColor,
      textStyle: { color: c.textColor, fontSize: 11 },
      formatter: tooltipFormatter,
    },
    xAxis: {
      type: "category",
      data: dates,
      boundaryGap: true,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: { color: c.textColor, fontSize: 9, rotate: 30 },
      splitLine: { show: false },
    },
    yAxis: {
      type: "value",
      scale: true,
      name: "Price (元)",
      nameTextStyle: { color: c.textColor, fontSize: 10 },
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: { color: c.textColor, fontSize: 10 },
      splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
    },
    series: [
      {
        type: "custom",
        name: "OI Bands",
        renderItem,
        data: cells,
        encode: { x: 0, y: 1 },
        clip: true,
        progressive: 0,
        z: 3,
      },
      {
        type: "line",
        name: "Spot",
        data: spot,
        showSymbol: false,
        smooth: false,
        connectNulls: false,
        lineStyle: { color: SPOT_COLOR, width: 1.6 },
        z: 10,
      },
      {
        type: "line",
        name: BULL_THRESHOLD_SERIES_NAME,
        data: bull,
        showSymbol: false,
        smooth: false,
        connectNulls: false,
        lineStyle: { color: UP_COLOR, width: 2.5 },
        itemStyle: { color: UP_COLOR },
        z: 9,
      },
      {
        type: "line",
        name: BEAR_THRESHOLD_SERIES_NAME,
        data: bear,
        showSymbol: false,
        smooth: false,
        connectNulls: false,
        lineStyle: { color: DOWN_COLOR, width: 2.5 },
        itemStyle: { color: DOWN_COLOR },
        z: 9,
      },
      {
        id: "bands-expiry-markers",
        type: "scatter",
        name: EXPIRY_MARKERS_SERIES_NAME,
        data: expiryData,
        symbolSize: 10,
        symbol: "circle",
        itemStyle: {
          color: FUTURES_EXPIRY_DOT,
          borderColor: "#ffffff",
          borderWidth: 1.5,
          shadowBlur: 4,
          shadowColor: "rgba(211, 47, 47, 0.4)",
        },
        z: 11,
        tooltip: {
          show: true,
          trigger: "item",
          backgroundColor: c.tooltipBg,
          borderColor: FUTURES_EXPIRY_DOT,
          textStyle: { color: c.textColor, fontSize: 11 },
          formatter: dotTooltip,
        },
      },
    ],
  };
}