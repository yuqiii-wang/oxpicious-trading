/**
 * Expiry OI Bands chart option builder — aligned to the FULL trading date
 * range so the x-axis matches the P/C Ratio and Total OI plots.
 *
 * Extracted from ExpiryOiBandsPanel.tsx for the merged OptionsTrendPanel.
 * Uses shared bandsTooltip.ts for tooltip formatting.
 *
 * Wall overlay: the backend's zone walls (analysis.options_walls,
 * wall_type='zone') are drawn as translucent low→high bands with a center
 * line per side. The legacy 80pct / large_num client-side wall curves were
 * removed — the zone wall supersedes them.
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
  CALL_ZONE_SERIES_NAME,
  PUT_ZONE_SERIES_NAME,
  type ZoneWallPoint,
  type ZoneWallSeries,
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

/** Fill opacity of the zone band by lifecycle state. */
function zoneFillOpacity(state: ZoneWallPoint["state"]): number {
  switch (state) {
    case "BREACHED":
      return 0.08;
    case "ERODED":
      return 0.16;
    default:
      return 0.26;
  }
}

/** Custom-series renderItem drawing one zone wall band (low→high rect +
 *  OI-weighted center line; dashed when BREACHED) per date. */
function buildZoneRenderItem(points: (ZoneWallPoint | null)[], color: string) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return (params: any, api: any) => {
    const p = points[params.dataIndex as number];
    if (!p) return null;
    const coordC = api.coord([params.dataIndex, p.center]) as number[];
    const yLow = (api.coord([params.dataIndex, p.low]) as number[])[1];
    const yHigh = (api.coord([params.dataIndex, p.high]) as number[])[1];
    const cx = coordC[0];
    const cy = coordC[1];
    if (!Number.isFinite(cx) || !Number.isFinite(yLow) || !Number.isFinite(yHigh)) return null;
    const step = api.size ? (api.size([1, 0]) as number[]) : null;
    const half = step && step[0] > 0 ? Math.max(step[0] / 2 - 1, 2) : 4;
    const breached = p.state === "BREACHED";
    const lineOpacity = breached ? 0.55 : 0.95;
    const lineDash = breached ? [3, 3] : undefined;
    return {
      type: "group" as const,
      children: [
        {
          type: "rect" as const,
          shape: {
            x: cx - half,
            y: yHigh,
            width: half * 2,
            height: Math.max(yLow - yHigh, 1),
          },
          style: { fill: color, opacity: zoneFillOpacity(p.state) },
        },
        // low/high edge rails — make the zone extent readable where the
        // fill blends into same-hue OI band cells (call zone in the
        // green call-side field)
        {
          type: "line" as const,
          shape: { x1: cx - half, y1: yHigh, x2: cx + half, y2: yHigh },
          style: { stroke: color, lineWidth: 1, opacity: lineOpacity * 0.55, lineDash },
        },
        {
          type: "line" as const,
          shape: { x1: cx - half, y1: yLow, x2: cx + half, y2: yLow },
          style: { stroke: color, lineWidth: 1, opacity: lineOpacity * 0.55, lineDash },
        },
        {
          type: "line" as const,
          shape: { x1: cx - half, y1: cy, x2: cx + half, y2: cy },
          style: {
            stroke: color,
            lineWidth: 1.6,
            opacity: lineOpacity,
            lineDash,
          },
        },
      ],
    };
  };
}

/** Custom-series data: one item per date (null where no zone that day). */
function zoneSeriesData(points: (ZoneWallPoint | null)[]) {
  return points.map((p, xi) => (p ? { value: [xi, p.center] } : null));
}

export function buildBandsOption(
  dates: string[],
  cells: BandCell[],
  spot: (number | null)[],
  themeMode: "light" | "dark",
  dataZoom: EChartsOption["dataZoom"] = undefined,
  expiryMarkers: ExpiryMarker[] = [],
  zones?: ZoneWallSeries,
): EChartsOption {
  const c = axisColors(themeMode);
  const renderItem = buildBandRenderItem(cells);
  const tooltipFormatter = makeBandsTooltipFormatter(c.textColor, c.tooltipBg, c.splitLineColor, zones);
  const dotTooltip = makeExpiryDotTooltip({
    textColor: c.textColor,
    tooltipBg: c.tooltipBg,
    splitLineColor: c.splitLineColor,
  });
  const expiryData = buildExpiryData(dates, [spot], expiryMarkers);

  const zoneSeries = zones
    ? [
        {
          type: "custom" as const,
          name: CALL_ZONE_SERIES_NAME,
          // series-level color only drives the legend swatch — the custom
          // renderItem styles its own shapes
          color: UP_COLOR,
          renderItem: buildZoneRenderItem(zones.call, UP_COLOR),
          data: zoneSeriesData(zones.call),
          encode: { x: 0, y: 1 },
          clip: true,
          progressive: 0,
          z: 8,
        },
        {
          type: "custom" as const,
          name: PUT_ZONE_SERIES_NAME,
          color: DOWN_COLOR,
          renderItem: buildZoneRenderItem(zones.put, DOWN_COLOR),
          data: zoneSeriesData(zones.put),
          encode: { x: 0, y: 1 },
          clip: true,
          progressive: 0,
          z: 8,
        },
      ]
    : [];

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
      ...zoneSeries,
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
