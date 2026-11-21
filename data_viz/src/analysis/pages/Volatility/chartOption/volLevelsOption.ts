/**
 * Historical vol levels option — the close-to-close HV across the
 * 5/20/60/252-row windows + the RiskMetrics EWMA vol, percent
 * annualized, one line per window over the code's full history (the
 * UI slices via dataZoom), with the raw close price overlaid behind
 * them on its own right-hand price axis. The window colors reuse the
 * MA window palette so the windows read like the rest of the app —
 * except hv_5, softened to the muted-red slot (the MA5 magenta was
 * the only neon-saturated line on the chart, too harsh next to the
 * muted scheme).
 *
 * This is the merged vol box's FIRST chart — the ONLY one that keeps
 * the commonDataZoom time slider; the other three option builders hide
 * the slider (inside-only zoom) and react to it via echarts.connect.
 */
import type { EChartsOption } from "echarts";
import type { ThemeMode } from "@/store/filters";
import { baseChartOption, commonTooltip } from "@/shared/charts/base-chart";
import {
  axisColors,
  commonDataZoom,
  commonGrid,
  MA20_COLOR,
  MA60_COLOR,
  MA252_COLOR,
  MUTED_PALETTE,
} from "@/theme/chart-palette";
import type { VolatilityRow } from "@shared/types";
import { buildCloseSeries, closeAxis } from "./closeOverlay";

/** The windows the card plots (5/10 folded into the tooltip only). */
const PLOTTED_HV_WINDOWS = ["hv_5", "hv_20", "hv_60", "hv_252"] as const;

const WINDOW_COLORS: Record<string, string> = {
  // hv_5 softened off the MA5 magenta — the muted-red slot keeps the
  // red identity without the neon contrast (blue stays Close-only).
  hv_5: MUTED_PALETTE[3],
  hv_20: MA20_COLOR,
  hv_60: MA60_COLOR,
  hv_252: MA252_COLOR,
};

const WINDOW_LABELS: Record<string, string> = {
  hv_5: "HV 5d",
  hv_20: "HV 20d",
  hv_60: "HV 60d",
  hv_252: "HV 252d",
};

export function buildVolLevelsOption(
  rows: VolatilityRow[],
  mode: ThemeMode,
): EChartsOption {
  const c = axisColors(mode);
  const dates = rows.map((r) => r.date);
  // The close line is the price context — drawn FIRST so the vol
  // curves paint on top of it.
  const series = [
    buildCloseSeries(rows, 1),
    ...PLOTTED_HV_WINDOWS.map((key) => ({
      type: "line" as const,
      name: WINDOW_LABELS[key],
      data: rows.map((r) => r[key]),
      showSymbol: false,
      lineStyle: { width: 1.4, color: WINDOW_COLORS[key] },
      itemStyle: { color: WINDOW_COLORS[key] },
      emphasis: { focus: "series" as const },
    })),
    {
      type: "line" as const,
      name: "EWMA (λ=0.94)",
      data: rows.map((r) => r.ewma_vol),
      showSymbol: false,
      lineStyle: {
        width: 2,
        type: "dashed" as const,
        color: MUTED_PALETTE[2],
      },
      itemStyle: { color: MUTED_PALETTE[2] },
      emphasis: { focus: "series" as const },
    },
  ];

  return baseChartOption(mode, {
    grid: commonGrid({ bottom: 50 }),
    dataZoom: commonDataZoom(),
    tooltip: commonTooltip(mode, {
      valueFormatter: (v) => (v == null ? "—" : `${Number(v).toFixed(2)}%`),
    }),
    legend: {
      data: [
        "Close",
        ...PLOTTED_HV_WINDOWS.map((key) => WINDOW_LABELS[key]),
        "EWMA (λ=0.94)",
      ],
    },
    xAxis: {
      type: "category",
      data: dates,
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: { color: c.textColor, fontSize: 9 },
    },
    yAxis: [
      {
        type: "value",
        name: "annualized vol (%)",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLabel: { color: c.textColor, fontSize: 9, formatter: "{value}%" },
        splitLine: { lineStyle: { color: c.splitLineColor } },
      },
      closeAxis(mode),
    ],
    series,
  });
}
