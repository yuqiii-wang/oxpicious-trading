/**
 * Amt Envelope chart — rendered when an Amt/MA pair is selected.
 *
 * Shows trading_amount BARS + a slim envelope of all 5 trading_amt_ma lines
 * on the right y-axis (yuan), with the OHLC/price curve shown lowkey
 * (dimmed) on the left y-axis (price).
 */
import { fmtNum, fmtPct, fmtYi } from "@/lib/series";
import {
  MA5_COLOR,
  MA20_COLOR,
  MA60_COLOR,
  MA120_COLOR,
  MA255_COLOR,
  UP_COLOR,
  DOWN_COLOR,
  SPOT_COLOR,
  BOLL_BAND_COLOR,
  BOLL_BAND_FILL,
  axisColors,
  commonLegend,
  commonGrid,
  commonDataZoom,
} from "@/theme/chart-palette";
import type { ThemeMode } from "@/store/filters";
import type { EChartsOption } from "echarts";
import type { MovAveSpreadHypeEpisodes, MovAveSpreadPairSeries } from "@shared/types";
import type { OhlcMode } from "@/lib/ohlc";
import {
  hypeEpisodesToMarkArea,
  HYPE_ACCENT_COLOR,
  HYPE_SHADE_COLOR,
} from "./hypeBands";
import { buildAmtTooltipFormatter } from "./tooltipFormatter";

/** Trading-amount MA windows in canonical order. */
const AMT_MA_WINDOWS = [5, 20, 60, 120, 255] as const;

/** Color for each trading_amt_ma window. */
const AMT_MA_COLORS: Record<number, string> = {
  5:   MA5_COLOR,
  20:  MA20_COLOR,
  60:  MA60_COLOR,
  120: MA120_COLOR,
  255: MA255_COLOR,
};

export interface BuildAmtEnvelopeOptionArgs {
  /** The selected Amt/MA pair's full time series. */
  pair: MovAveSpreadPairSeries;
  /** Current theme mode (light / dark). */
  themeMode: ThemeMode;
  /** Display mode for the lowkey price series (absolute / percentage). */
  ohlcMode?: OhlcMode;
  /** Bollinger multiplier k in MA ± k × σ. Default 2.0; 0 = hidden. */
  bollingerK?: number;
  /** Enabled market-hype check-in window (trading days) — null/undefined =
   *  off. When set, hyped date periods are shaded light purple. */
  hypeWindow?: number | null;
  /** Market-hype episodes keyed by check-in window. Source:
   *  analysis.mov_ave_market_hypes — episodes are date spans, so no
   *  index-alignment with pair.rows is required. */
  hypeEpisodes?: MovAveSpreadHypeEpisodes | null;
}

/** Format a yuan amount as 亿元 (100M yuan). */
function fmtAmtYi(v: number | null | undefined, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return fmtYi(v, digits);
}

/** Build the ECharts option for the Amt Envelope chart. */
export function buildAmtEnvelopeOption({
  pair,
  themeMode,
  ohlcMode = "absolute",
  bollingerK = 2,
  hypeWindow = null,
  hypeEpisodes = null,
}: BuildAmtEnvelopeOptionArgs): EChartsOption {
  const c = axisColors(themeMode);
  const rows = pair.rows;
  const n = rows.length;
  const selectedWindow = pair.ma_long;

  const dates = rows.map((r) => r.date);
  const highs = rows.map((r) => r.high);
  const lows = rows.map((r) => r.low);
  const tradingAmts = rows.map((r) => r.trading_amount);

  // long_std holds trading_amt_stdW for amt pairs (set by the backend).
  const longStds = rows.map((r) => r.long_std);

  const amtMaArrays: Record<number, Array<number | null>> = {
    5:   rows.map((r) => r.trading_amt_ma5),
    20:  rows.map((r) => r.trading_amt_ma20),
    60:  rows.map((r) => r.trading_amt_ma60),
    120: rows.map((r) => r.trading_amt_ma120),
    255: rows.map((r) => r.trading_amt_ma255),
  };
  const amtMaSlopeArrays: Record<number, Array<number | null>> = {
    5:   rows.map((r) => r.trading_amt_ma5_slope),
    20:  rows.map((r) => r.trading_amt_ma20_slope),
    60:  rows.map((r) => r.trading_amt_ma60_slope),
    120: rows.map((r) => r.trading_amt_ma120_slope),
    255: rows.map((r) => r.trading_amt_ma255_slope),
  };
  const amtMarketShareArrays: Record<number, Array<number | null>> = {
    5:   rows.map((r) => r.trading_amt_market_share_ma5),
    20:  rows.map((r) => r.trading_amt_market_share_ma20),
    60:  rows.map((r) => r.trading_amt_market_share_ma60),
    120: rows.map((r) => r.trading_amt_market_share_ma120),
    255: rows.map((r) => r.trading_amt_market_share_ma255),
  };

  // Envelope band
  const envUpper: Array<number | null> = new Array(n).fill(null);
  const envLower: Array<number | null> = new Array(n).fill(null);
  for (let i = 0; i < n; i++) {
    const vals: number[] = [];
    for (const w of AMT_MA_WINDOWS) {
      const v = amtMaArrays[w][i];
      if (v != null && Number.isFinite(v)) vals.push(v);
    }
    if (vals.length > 0) {
      envUpper[i] = Math.max(...vals);
      envLower[i] = Math.min(...vals);
    }
  }

  // ---- Percentage mode base ----
  let baseVal: number | null = null;
  for (let i = 0; i < n; i++) {
    const v = highs[i];
    if (v != null && Number.isFinite(v) && Math.abs(v) >= 1e-9) {
      baseVal = v;
      break;
    }
  }
  const hasBase = baseVal != null && Number.isFinite(baseVal);
  const fmtPctFromBase = (v: number | null | undefined): string => {
    if (v == null || !Number.isFinite(v) || !hasBase || baseVal == null) return "—";
    return fmtPct((v / baseVal - 1) * 100, 2);
  };
  const fmtPrice = (v: number | null | undefined): string =>
    ohlcMode === "percentage" ? fmtPctFromBase(v) : fmtNum(v);

  // ---- Build series ----
  const echartsSeries: NonNullable<EChartsOption["series"]> = [];

  // ---- Market-hype shading (light purple over hyped periods) ------------
  // Active only when a hype window button is enabled AND episode data was
  // fetched for this code. Episodes are date spans (first/last satisfied
  // dates of each hyped run), so they apply directly as markArea
  // rectangles — no index-alignment with the pair's rows needed.
  // Attached to the invisible "_hlBase" band series (z=0, price y-axis) so
  // the shade sits behind everything and spans the full plot height.
  const hypeW = hypeWindow ?? null;
  const hypeMarkAreaData =
    hypeW != null && hypeEpisodes != null
      ? hypeEpisodesToMarkArea(hypeEpisodes[hypeW])
      : [];

  // Lowkey high-low band
  const hlBase: Array<number | null> = lows.slice();
  const hlDelta: Array<number | null> = new Array(n).fill(null);
  for (let i = 0; i < n; i++) {
    const h = highs[i];
    const l = lows[i];
    if (h != null && l != null) {
      hlDelta[i] = h - l;
    }
  }
  echartsSeries.push({
    type: "line",
    name: "_hlBase",
    data: hlBase,
    stack: "hlBand",
    symbol: "none",
    lineStyle: { opacity: 0 },
    yAxisIndex: 0,
    z: 0,
    ...(hypeMarkAreaData.length > 0
      ? {
          markArea: {
            silent: true as const,
            itemStyle: { borderWidth: 0 },
            data: hypeMarkAreaData,
          },
        }
      : {}),
  });
  echartsSeries.push({
    type: "line",
    name: "_hlDelta",
    data: hlDelta,
    stack: "hlBand",
    symbol: "none",
    lineStyle: { opacity: 0 },
    areaStyle: { color: SPOT_COLOR, opacity: 0.08 },
    yAxisIndex: 0,
    z: 0,
  });

  // Lowkey price line (close proxy)
  const closeProxy: Array<number | null> = new Array(n).fill(null);
  for (let i = 0; i < n; i++) {
    const h = highs[i];
    const l = lows[i];
    if (h != null && l != null) {
      closeProxy[i] = (h + l) / 2;
    }
  }
  echartsSeries.push({
    type: "line",
    name: "Price",
    data: closeProxy,
    symbol: "none",
    lineStyle: { color: SPOT_COLOR, width: 1, opacity: 0.25 },
    yAxisIndex: 0,
    z: 1,
  });

  // Envelope fill band
  echartsSeries.push({
    type: "line",
    name: "_envBase",
    data: envLower,
    stack: "envBand",
    symbol: "none",
    lineStyle: { opacity: 0 },
    yAxisIndex: 1,
    z: 2,
  });
  echartsSeries.push({
    type: "line",
    name: "_envDelta",
    data: envUpper.map((u, i) => {
      const l = envLower[i];
      return u != null && l != null ? u - l : null;
    }),
    stack: "envBand",
    symbol: "none",
    lineStyle: { opacity: 0 },
    areaStyle: { color: BOLL_BAND_FILL, opacity: 0.10 },
    yAxisIndex: 1,
    z: 2,
  });

  // Trading amount bars
  const selectedAmtMa = amtMaArrays[selectedWindow];
  const amtUpData: Array<number | null> = new Array(n).fill(null);
  const amtDownData: Array<number | null> = new Array(n).fill(null);
  for (let i = 0; i < n; i++) {
    const amt = tradingAmts[i];
    const ma = selectedAmtMa[i];
    if (amt == null || !Number.isFinite(amt)) continue;
    const isUp = ma != null && Number.isFinite(ma) ? amt >= ma : true;
    if (isUp) amtUpData[i] = amt;
    else amtDownData[i] = amt;
  }
  echartsSeries.push({
    type: "bar",
    name: "Amt Above",
    data: amtUpData,
    yAxisIndex: 1,
    barWidth: "60%",
    stack: "amt",
    itemStyle: { color: UP_COLOR, opacity: 0.42 },
    z: 3,
  });
  echartsSeries.push({
    type: "bar",
    name: "Amt Below",
    data: amtDownData,
    yAxisIndex: 1,
    barWidth: "60%",
    stack: "amt",
    itemStyle: { color: DOWN_COLOR, opacity: 0.42 },
    z: 3,
  });

  // 5 trading_amt_ma lines
  for (const w of AMT_MA_WINDOWS) {
    const isSelected = w === selectedWindow;
    echartsSeries.push({
      type: "line",
      name: `Amt MA${w}`,
      data: amtMaArrays[w],
      symbol: "none",
      smooth: 0.2,
      lineStyle: {
        color: AMT_MA_COLORS[w],
        width: isSelected ? 1.5 : 0.8,
        opacity: isSelected ? 0.85 : 0.4,
      },
      yAxisIndex: 1,
      z: isSelected ? 7 : 5,
    });
  }

  // ---- Bollinger envelope around selected trading_amt_maW -----------
  const showBoll = bollingerK > 0;
  const amtUpperData: Array<number | null> = new Array(n).fill(null);
  const amtLowerData: Array<number | null> = new Array(n).fill(null);
  const bollBase: Array<number | null> = new Array(n).fill(null);
  const bollDelta: Array<number | null> = new Array(n).fill(null);
  if (showBoll) {
    const selectedAmtMaData = amtMaArrays[selectedWindow];
    for (let i = 0; i < n; i++) {
      const ma = selectedAmtMaData[i];
      const sd = longStds[i];
      if (ma == null || sd == null || !Number.isFinite(ma) || !Number.isFinite(sd)) continue;
      const upper = ma + bollingerK * sd;
      const lower = ma - bollingerK * sd;
      amtUpperData[i] = upper;
      amtLowerData[i] = lower;
      bollBase[i] = lower;
      bollDelta[i] = upper - lower;
    }
    const upperName = `Amt Upper (+${bollingerK}σ)`;
    const lowerName = `Amt Lower (−${bollingerK}σ)`;
    echartsSeries.push({
      type: "line",
      name: upperName,
      data: amtUpperData,
      symbol: "none",
      lineStyle: { color: BOLL_BAND_COLOR, width: 1, type: "dashed", opacity: 0.7 },
      yAxisIndex: 1,
      z: 6,
    });
    echartsSeries.push({
      type: "line",
      name: lowerName,
      data: amtLowerData,
      symbol: "none",
      lineStyle: { color: BOLL_BAND_COLOR, width: 1, type: "dashed", opacity: 0.7 },
      yAxisIndex: 1,
      z: 6,
    });
    echartsSeries.push({
      type: "line",
      name: "_bollBase",
      data: bollBase,
      stack: "bollBand",
      symbol: "none",
      lineStyle: { opacity: 0 },
      areaStyle: { opacity: 0 },
      yAxisIndex: 1,
      z: 0,
    });
    echartsSeries.push({
      type: "line",
      name: "_bollDelta",
      data: bollDelta,
      stack: "bollBand",
      symbol: "none",
      lineStyle: { opacity: 0 },
      areaStyle: { color: BOLL_BAND_FILL, opacity: 0.12 },
      yAxisIndex: 1,
      z: 0,
    });
  }

  // ---- Legend ----
  const legendData: string[] = ["Price", "Amt Above", "Amt Below"];
  for (const w of AMT_MA_WINDOWS) {
    legendData.push(`Amt MA${w}`);
  }
  if (showBoll) {
    legendData.push(`Amt Upper (+${bollingerK}σ)`, `Amt Lower (−${bollingerK}σ)`);
  }
  if (hypeMarkAreaData.length > 0 && hypeW != null) {
    legendData.push(`Hyped(${hypeW}d)`);
    // Cosmetic legend marker for the hype shading (light purple rect).
    echartsSeries.push({
      type: "scatter",
      name: `Hyped(${hypeW}d)`,
      data: [null],
      symbol: "rect",
      symbolSize: [10, 8],
      itemStyle: { color: HYPE_ACCENT_COLOR, opacity: 0.45, borderColor: HYPE_SHADE_COLOR },
      z: 0,
    });
  }

  const grid = commonGrid({ left: 55, right: 65, bottom: 50 });

  // ---- Build tooltip ----
  const formatter = buildAmtTooltipFormatter(
    dates,
    tradingAmts,
    amtMaArrays,
    amtMaSlopeArrays,
    amtMarketShareArrays,
    highs,
    lows,
    selectedWindow,
    AMT_MA_WINDOWS,
    fmtPrice,
  );

  return {
    backgroundColor: "transparent",
    animation: false,
    grid,
    dataZoom: commonDataZoom(),
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "line", snap: true },
      backgroundColor: c.tooltipBg,
      borderColor: c.splitLineColor,
      textStyle: { color: c.textColor, fontSize: 11 },
      formatter,
    },
    legend: commonLegend(themeMode, { itemWidth: 12, itemHeight: 7, data: legendData }),
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
    yAxis: [
      {
        type: "value",
        scale: true,
        name: ohlcMode === "percentage" ? "%" : "Price",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: {
          color: c.textColor,
          fontSize: 9,
          formatter: (v: number) => fmtPrice(v),
        },
        splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.3 } },
      },
      {
        type: "value" as const,
        scale: true,
        name: "Amt (亿)",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: {
          color: c.textColor,
          fontSize: 9,
          formatter: (v: number) => fmtNum(v / 1e8, 1),
        },
        splitLine: { show: false },
      },
    ],
    series: echartsSeries,
  };
}
