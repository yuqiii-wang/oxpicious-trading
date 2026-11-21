/**
 * Oracle action chart option — the code's close curve (blue, the
 * Volatility page's close-line convention) over its full history with
 * the hindsight oracle's actions marked ON the local extremes
 * themselves: the shared signal-event marker layer
 * (signalEventScatterSeries — green up-triangle BUY on each placed
 * local-minimum action, red down-triangle SELL on each local maximum;
 * 5-bar-window pivots scanned daily, one action PER SIDE per 5-day
 * window — a buy and a sell may sit within the same 5 rows, same-side
 * actions sit >= 5 rows apart, y = that bar's own close). The v1
 * no_action diamond class is unused.
 */
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import { baseChartOption, commonTooltip } from "@/shared/charts/base-chart";
import { axisColors, commonDataZoom, commonGrid, PALETTE_HI } from "@/theme/chart-palette";
import {
  SIGNAL_EVENT_SERIES_NAMES,
  signalEventScatterSeries,
  type SignalEventPoint,
} from "@/shared/charts/signalMarker";
import type { OracleCloseRow, OracleRow } from "@shared/types";

export function buildOracleChartOption(
  closes: OracleCloseRow[],
  rows: OracleRow[],
  mode: ThemeMode,
): EChartsOption {
  const c = axisColors(mode);
  const dates = closes.map((p) => p.date);
  const closeByDate = new Map<string, number | null>(
    closes.map((p) => [p.date, p.close]),
  );

  // The action markers ride the shared signal-event layer, placed AT
  // the pivot date (the local extreme itself) — the triangle sits
  // exactly on the curve's own local min (buy) / local max (sell).
  const events: SignalEventPoint[] = rows
    .filter((r) => r.action === "buy" || r.action === "sell")
    .map((r) => ({ date: r.date, action: r.action }));
  const markerSeries = signalEventScatterSeries(events, closeByDate, {
    names: [
      "Oracle Buy (local min)",
      "Oracle Sell (local max)",
      SIGNAL_EVENT_SERIES_NAMES[2],
      SIGNAL_EVENT_SERIES_NAMES[3],
      SIGNAL_EVENT_SERIES_NAMES[4],
    ],
    z: 6,
  });

  return baseChartOption(mode, {
    legend: {
      data: ["Close", ...markerSeries.map((s) => String(s.name))],
      type: "scroll",
      top: 0,
    },
    grid: commonGrid({ bottom: 50, top: 28 }),
    dataZoom: commonDataZoom(),
    tooltip: commonTooltip(mode),
    xAxis: {
      type: "category",
      data: dates,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: { color: c.textColor, fontSize: 9 },
    },
    yAxis: {
      type: "value",
      scale: true,
      axisLabel: { color: c.textColor, fontSize: 9 },
      splitLine: { lineStyle: { color: c.splitLineColor } },
    },
    series: [
      {
        name: "Close",
        type: "line",
        data: closes.map((p) => p.close),
        showSymbol: false,
        lineStyle: { width: 1.2, color: PALETTE_HI },
        itemStyle: { color: PALETTE_HI },
        z: 1,
      },
      ...markerSeries,
    ],
  });
}
