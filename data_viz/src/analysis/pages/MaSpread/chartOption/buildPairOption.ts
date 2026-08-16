/**
 * ECharts option builder for the MA-Spread pair chart (SMA + EMA).
 *
 * Extracted from the former MaSpreadPage.tsx so the panel component stays
 * focused on data fetching + layout.
 *
 * Implementation:
 *   For Price/MA pairs (ma_short === 0):
 *     - OHLC bars (shared ohlcSeries from @/lib/ohlc) — replaces the close line
 *     - Long MA line  (z=5, no stack)
 *     - Gap fill stack (3 series: _base, _pos, _neg)
 *     - Bollinger envelope (optional)
 *     - Trading amount bars on secondary y-axis
 *
 *   For MA/MA pairs (ma_short === 5):
 *     - Short MA line + Long MA line
 *     - Gap fill stack (3 series: _base, _pos, _neg)
 *     - Trading amount bars on secondary y-axis
 */
import { fmtNum, fmtPct, fmtYi } from "@/lib/series";
import { ohlcSeries, type OhlcMode } from "@/lib/ohlc";
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
import type { MovAveSpreadPairSeries, MovAveSpreadValleyLow } from "../../../../../shared/types";
import {
  computeTrendBands,
  trendBandsToMarkArea,
  shortLabel,
  type TrendBand,
  TREND_DOWN_COLOR,
  TREND_FLAT_COLOR,
  TREND_UP_COLOR,
} from "./trendBands";
import { buildPairTooltipFormatter, type TooltipContext } from "./tooltipFormatter";

// Color for the "price" series (ma_short = 0).
const PRICE_COLOR = SPOT_COLOR;

export type TradingAmtMode = "off" | "lowkey";

export interface BuildPairOptionArgs {
  /** The pair's full time series. */
  pair: MovAveSpreadPairSeries;
  /** Current theme mode (light / dark) for axis + tooltip colors. */
  themeMode: ThemeMode;
  /** Bollinger multiplier k in MA ± k × σ. Default 2. */
  bollingerK?: number;
  /** Trading amount display mode. Defaults to "lowkey". */
  tradingAmtMode?: TradingAmtMode;
  /** Per-extreme-date rows from analysis.mov_ave_peaks_and_floors. */
  valleyLows?: MovAveSpreadValleyLow[];
  /** Index into pair.rows of the currently hovered date. */
  hoveredIdx?: number | null;
  /** Display mode for price-derived series. */
  ohlcMode?: OhlcMode;
}

/** Format a yuan amount as 亿元 (100M yuan). */
function fmtAmtYi(v: number | null | undefined, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return fmtYi(v, digits);
}

export function buildPairOption({
  pair,
  themeMode,
  bollingerK = 2,
  tradingAmtMode = "lowkey",
  valleyLows = [],
  hoveredIdx = null,
  ohlcMode = "absolute",
}: BuildPairOptionArgs): EChartsOption {
  const c = axisColors(themeMode);
  const rows = pair.rows;
  const n = rows.length;

  const isPricePair = pair.ma_short === 0;

  const dates = rows.map((r) => r.date);
  const shorts = rows.map((r) => r.short_value);
  const longs = rows.map((r) => r.long_value);
  const shortSlopes = rows.map((r) => r.short_slope);
  const shortCurvs = rows.map((r) => r.short_curvature);
  const longSlopes = rows.map((r) => r.long_slope);
  const longCurvs = rows.map((r) => r.long_curvature);
  const longStds = rows.map((r) => r.long_std);
  const opens = rows.map((r) => r.open);
  const highs = rows.map((r) => r.high);
  const lows = rows.map((r) => r.low);
  const tradingAmts = rows.map((r) => r.trading_amount);
  const dateOfLastExtreme = rows.map((r) => r.date_of_last_extreme ?? null);
  const gapSinceLastExtreme = rows.map((r) => r.gap_since_last_extreme ?? null);
  const daysSinceLastExtreme = rows.map((r) => r.days_since_last_extreme ?? null);
  const rsi6 = rows.map((r) => r.rsi_6days ?? null);
  const rsi10 = rows.map((r) => r.rsi_10days ?? null);
  const rsi14 = rows.map((r) => r.rsi_14days ?? null);
  const rsi20 = rows.map((r) => r.rsi_20days ?? null);

  const amtMaSlopeOfLong = rows.map((r) => {
    switch (pair.ma_long) {
      case 5:   return r.trading_amt_ma5_slope ?? null;
      case 20:  return r.trading_amt_ma20_slope ?? null;
      case 60:  return r.trading_amt_ma60_slope ?? null;
      case 120: return r.trading_amt_ma120_slope ?? null;
      case 255: return r.trading_amt_ma255_slope ?? null;
      default:  return null;
    }
  });

  const amtMarketShareOfLong = rows.map((r) => {
    switch (pair.ma_long) {
      case 5:   return r.trading_amt_market_share_ma5 ?? null;
      case 20:  return r.trading_amt_market_share_ma20 ?? null;
      case 60:  return r.trading_amt_market_share_ma60 ?? null;
      case 120: return r.trading_amt_market_share_ma120 ?? null;
      case 255: return r.trading_amt_market_share_ma255 ?? null;
      default:  return null;
    }
  });

  const ohlcOpens = rows.map((r) => {
    switch (pair.ma_long) {
      case 20:  return r.open_20d ?? null;
      case 60:  return r.open_60d ?? null;
      case 120: return r.open_120d ?? null;
      case 255: return r.open_255d ?? null;
      case 500: return r.open_500d ?? null;
      case 750: return r.open_750d ?? null;
      default:  return null;
    }
  });
  const ohlcHighs = rows.map((r) => {
    switch (pair.ma_long) {
      case 20:  return r.high_20d ?? null;
      case 60:  return r.high_60d ?? null;
      case 120: return r.high_120d ?? null;
      case 255: return r.high_255d ?? null;
      case 500: return r.high_500d ?? null;
      case 750: return r.high_750d ?? null;
      default:  return null;
    }
  });
  const ohlcLows = rows.map((r) => {
    switch (pair.ma_long) {
      case 20:  return r.low_20d ?? null;
      case 60:  return r.low_60d ?? null;
      case 120: return r.low_120d ?? null;
      case 255: return r.low_255d ?? null;
      case 500: return r.low_500d ?? null;
      case 750: return r.low_750d ?? null;
      default:  return null;
    }
  });

  // ---- Valley-low / peak markers ----------------------------------------
  const floorMap = new Map<string, number>();
  const peakMap = new Map<string, number>();
  for (const v of valleyLows) {
    if (v.extreme_val != null && Number.isFinite(v.extreme_val)) {
      if (v.is_extreme_peak_not_floor) {
        peakMap.set(v.date, v.extreme_val);
      } else {
        floorMap.set(v.date, v.extreme_val);
      }
    }
  }

  const isMA60Pair = pair.ma_long === 60;
  const trendBands = isMA60Pair ? computeTrendBands(shorts, longs, longSlopes, longStds) : [];
  const hasTrendBands = trendBands.length > 0;

  const valleyLowData: Array<number | null> = new Array(n).fill(null);
  const peakData: Array<number | null> = new Array(n).fill(null);
  for (let i = 0; i < n; i++) {
    const fv = floorMap.get(dates[i]);
    if (fv != null) valleyLowData[i] = fv;
    const pv = peakMap.get(dates[i]);
    if (pv != null) peakData[i] = pv;
  }
  const hasValleyLows = valleyLowData.some((v) => v != null);
  const hasPeaks = peakData.some((v) => v != null);

  // ---- Last-extreme hover marker ----------------------------------------
  const lastExtremeData: Array<number | null> = new Array(n).fill(null);
  let lastExtremeRising = true;
  if (
    hoveredIdx != null
    && hoveredIdx >= 0
    && hoveredIdx < n
    && dateOfLastExtreme.some((d) => d != null)
  ) {
    const ed = dateOfLastExtreme[hoveredIdx];
    if (ed != null) {
      const sv = shorts[hoveredIdx];
      if (sv != null && Number.isFinite(sv)) {
        lastExtremeData[hoveredIdx] = sv;
      }
      const gap = gapSinceLastExtreme[hoveredIdx];
      lastExtremeRising = !(gap != null && Number.isFinite(gap) && gap < 0);
    }
  }
  const hasLastExtreme = lastExtremeData.some((v) => v != null);

  // ---- Nearby-extreme bands ---------------------------------------------
  const NEARBY_BAND_FILL = "rgba(229, 57, 53, 0.12)";
  const NEARBY_BAND_BORDER = "rgba(229, 57, 53, 0.35)";

  interface NearbyBand {
    startDate: string;
    endDate: string;
    startIndex: number;
    endIndex: number;
    lower: number;
    upper: number;
  }

  const ohlcByDate = new Map<
    string,
    { high: number | null; low: number | null; index: number }
  >();
  for (let i = 0; i < n; i++) {
    ohlcByDate.set(dates[i], { high: highs[i], low: lows[i], index: i });
  }

  const nearbyBands: NearbyBand[] = [];
  for (const v of valleyLows) {
    const nd = v.nearby_extreme_date;
    if (!nd || nd === v.date) continue;
    const o1 = ohlcByDate.get(v.date);
    const o2 = ohlcByDate.get(nd);
    if (!o1 || !o2) continue;
    const h1 = o1.high, l1 = o1.low, h2 = o2.high, l2 = o2.low;
    if (h1 == null || l1 == null || h2 == null || l2 == null) continue;
    if (
      !Number.isFinite(h1) || !Number.isFinite(l1) ||
      !Number.isFinite(h2) || !Number.isFinite(l2)
    ) continue;
    const lower = Math.min(l1, l2);
    const upper = Math.max(h1, h2);
    const i1 = o1.index;
    const i2 = o2.index;
    nearbyBands.push({
      startDate: i1 <= i2 ? v.date : nd,
      endDate: i1 <= i2 ? nd : v.date,
      startIndex: Math.min(i1, i2),
      endIndex: Math.max(i1, i2),
      lower,
      upper,
    });
  }
  const hasNearbyBands = nearbyBands.length > 0;
  type NearbyBandPoint = { coord: [string, number] };
  type NearbyBandMarkAreaItem = [
    { coord: [string, number]; itemStyle: { color: string; borderColor: string; borderWidth: number } },
    NearbyBandPoint,
  ];

  // ---- Percentage mode base --------------------------------------------
  let baseVal: number | null = null;
  for (let i = 0; i < n; i++) {
    const v = shorts[i];
    if (v != null && Number.isFinite(v) && Math.abs(v) >= 1e-9) {
      baseVal = v;
      break;
    }
  }
  const hasBase = baseVal != null && Number.isFinite(baseVal) && Math.abs(baseVal) >= 1e-9;

  const fmtPctFromBase = (v: number | null | undefined): string => {
    if (v == null || !Number.isFinite(v) || !hasBase || baseVal == null) return "—";
    return fmtPct((v / baseVal - 1) * 100, 2);
  };
  const fmtPrice = (v: number | null | undefined): string =>
    ohlcMode === "percentage" ? fmtPctFromBase(v) : fmtNum(v);

  const nearbyBandMarkArea: NearbyBandMarkAreaItem[] = nearbyBands.map((b) => [
    {
      coord: [b.startDate, b.lower],
      itemStyle: {
        color: NEARBY_BAND_FILL,
        borderColor: NEARBY_BAND_BORDER,
        borderWidth: 0.5,
      },
    },
    { coord: [b.endDate, b.upper] },
  ]) as NearbyBandMarkAreaItem[];

  // OHLC bar data
  const ohlcData: Array<Array<number | null>> = new Array(n).fill(null);
  if (isPricePair) {
    for (let i = 0; i < n; i++) {
      const o = opens[i];
      const cl = shorts[i];
      const l = lows[i];
      const h = highs[i];
      if (o != null && cl != null && l != null && h != null) {
        ohlcData[i] = [o, cl, l, h];
      }
    }
  }

  // Gap fill stack
  const baseData: Array<number | null> = new Array(n).fill(null);
  const posData: Array<number | null> = new Array(n).fill(null);
  const negData: Array<number | null> = new Array(n).fill(null);

  for (let i = 0; i < n; i++) {
    const s = shorts[i];
    const l = longs[i];
    if (s == null || l == null) continue;
    const diff = s - l;
    baseData[i] = Math.min(s, l);
    if (diff >= 0) {
      posData[i] = diff;
      negData[i] = 0;
    } else {
      posData[i] = 0;
      negData[i] = -diff;
    }
  }

  const sColor = PRICE_COLOR;
  const lColor = MA120_COLOR;
  const isEma = pair.kind === "ema";
  const sName = shortLabel(pair.ma_short, pair.kind);
  const lName = isEma ? `EMA${pair.ma_long}` : `MA${pair.ma_long}`;

  // ---- Bollinger envelope ----------------------------------------------
  const showBoll = isPricePair && bollingerK > 0;
  const upperData: Array<number | null> = new Array(n).fill(null);
  const lowerData: Array<number | null> = new Array(n).fill(null);
  const bollBase: Array<number | null> = new Array(n).fill(null);
  const bollDelta: Array<number | null> = new Array(n).fill(null);
  if (showBoll) {
    for (let i = 0; i < n; i++) {
      const l = longs[i];
      const sd = longStds[i];
      if (l == null || sd == null) continue;
      const upper = l + bollingerK * sd;
      const lower = l - bollingerK * sd;
      upperData[i] = upper;
      lowerData[i] = lower;
      bollBase[i] = lower;
      bollDelta[i] = upper - lower;
    }
  }
  const upperName = `Upper (+${bollingerK}σ)`;
  const lowerName = `Lower (−${bollingerK}σ)`;

  // Legend data
  const legendData: string[] = [sName, lName];
  if (showBoll) {
    legendData.push(upperName, lowerName);
  }
  if (tradingAmtMode !== "off") {
    legendData.push("Amt Up", "Amt Down");
  }
  if (hasValleyLows) {
    legendData.push("Valley Low");
  }
  if (hasNearbyBands) {
    legendData.push("Nearby Extreme");
  }
  const trendLegendNames = hasTrendBands ? ["▼ Downward", "▬ Flat", "▲ Upward"] : [];
  if (hasTrendBands) {
    legendData.push(...trendLegendNames);
  }

  const amtBarData: Array<number | null> = tradingAmts.map((v) =>
    v != null && Number.isFinite(v) ? v : null,
  );

  // ---- Build series ----------------------------------------------------
  const echartsSeries: EChartsOption["series"] = [];

  if (isPricePair) {
    echartsSeries.push(ohlcSeries(ohlcData, { name: sName, z: 5 }));
  } else {
    echartsSeries.push({
      type: "line",
      name: sName,
      data: shorts,
      symbol: "none",
      lineStyle: { color: sColor, width: 1.4 },
      z: 5,
    });
  }

  echartsSeries.push({
    type: "line",
    name: lName,
    data: longs,
    symbol: "none",
    lineStyle: { color: lColor, width: 1.4 },
    z: 5,
    ...(hasTrendBands
      ? {
          markArea: {
            silent: true,
            itemStyle: { borderWidth: 0 },
            data: trendBandsToMarkArea(trendBands, dates),
          },
        }
      : {}),
  });

  echartsSeries.push({
    type: "line",
    name: "_base",
    data: baseData,
    stack: "gapFill",
    symbol: "none",
    lineStyle: { opacity: 0 },
    z: 1,
  });
  echartsSeries.push({
    type: "line",
    name: "_pos",
    data: posData,
    stack: "gapFill",
    symbol: "none",
    lineStyle: { opacity: 0 },
    areaStyle: { color: UP_COLOR, opacity: 0.4 },
    z: 2,
  });
  echartsSeries.push({
    type: "line",
    name: "_neg",
    data: negData,
    stack: "gapFill",
    symbol: "none",
    lineStyle: { opacity: 0 },
    areaStyle: { color: DOWN_COLOR, opacity: 0.4 },
    z: 2,
  });

  if (showBoll) {
    echartsSeries.push({
      type: "line",
      name: upperName,
      data: upperData,
      symbol: "none",
      lineStyle: { color: BOLL_BAND_COLOR, width: 1, type: "dashed", opacity: 0.7 },
      z: 4,
    });
    echartsSeries.push({
      type: "line",
      name: lowerName,
      data: lowerData,
      symbol: "none",
      lineStyle: { color: BOLL_BAND_COLOR, width: 1, type: "dashed", opacity: 0.7 },
      z: 4,
    });
    echartsSeries.push({
      type: "line",
      name: "_bollBase",
      data: bollBase,
      stack: "bollBand",
      symbol: "none",
      lineStyle: { opacity: 0 },
      areaStyle: { opacity: 0 },
      z: 0,
    });
    echartsSeries.push({
      type: "line",
      name: "_bollDelta",
      data: bollDelta,
      stack: "bollBand",
      symbol: "none",
      lineStyle: { opacity: 0 },
      areaStyle: { color: BOLL_BAND_FILL, opacity: 0.08 },
      z: 0,
    });
  }

  if (hasPeaks) {
    echartsSeries.push({
      type: "scatter",
      name: "Peak",
      data: peakData,
      symbol: "triangle",
      symbolSize: 12,
      symbolRotate: 0,
      itemStyle: {
        color: "#43A047",
        borderColor: "#1B5E20",
        borderWidth: 0.5,
      },
      z: 20,
    });
  }

  if (hasValleyLows) {
    echartsSeries.push({
      type: "scatter",
      name: "Valley Low",
      data: valleyLowData,
      symbol: "triangle",
      symbolSize: 12,
      symbolRotate: 180,
      itemStyle: {
        color: "#E53935",
        borderColor: "#B71C1C",
        borderWidth: 0.5,
      },
      z: 20,
      ...(hasNearbyBands
        ? {
            markArea: {
              silent: true,
              data: nearbyBandMarkArea,
            },
          }
        : {}),
    });
  }

  if (hasLastExtreme) {
    const leColor = lastExtremeRising
      ? { rgb: "67, 160, 71", hex: "#43A047" }
      : { rgb: "229, 57, 53", hex: "#E53935" };
    echartsSeries.push({
      type: "scatter",
      name: "Last Extreme",
      data: lastExtremeData,
      symbol: "triangle",
      symbolSize: 7,
      symbolRotate: lastExtremeRising ? 0 : 180,
      itemStyle: {
        color: `rgba(${leColor.rgb}, 0.55)`,
        borderColor: leColor.hex,
        borderWidth: 0.5,
      },
      z: 19,
      tooltip: { show: false },
    });
  }

  if (hasNearbyBands) {
    echartsSeries.push({
      type: "scatter",
      name: "Nearby Extreme",
      data: [null],
      symbol: "rect",
      symbolSize: 7,
      itemStyle: { color: "rgba(229, 57, 53, 0.5)" },
      z: 0,
    });
  }

  if (hasTrendBands) {
    const trendLegendColors: Record<string, string> = {
      "▼ Downward": TREND_DOWN_COLOR.replace("0.07", "0.6"),
      "▬ Flat": TREND_FLAT_COLOR.replace("0.07", "0.6"),
      "▲ Upward": TREND_UP_COLOR.replace("0.05", "0.6"),
    };
    for (const name of trendLegendNames) {
      echartsSeries.push({
        type: "scatter",
        name,
        data: [null],
        symbol: "circle",
        symbolSize: 7,
        itemStyle: { color: trendLegendColors[name] },
        z: 0,
      });
    }
  }

  if (tradingAmtMode !== "off") {
    const barOpacity = 0.15;
    const barWidth = "50%";

    const upData: Array<number | null> = new Array(n).fill(null);
    const downData: Array<number | null> = new Array(n).fill(null);

    for (let i = 0; i < n; i++) {
      const amt = amtBarData[i];
      if (amt == null) continue;
      let isUp: boolean;
      if (isPricePair) {
        const o = opens[i];
        const cl = shorts[i];
        isUp = o != null && cl != null ? cl >= o : true;
      } else {
        const s = shorts[i];
        const l = longs[i];
        isUp = s != null && l != null ? s >= l : true;
      }
      if (isUp) {
        upData[i] = amt;
      } else {
        downData[i] = amt;
      }
    }

    echartsSeries.push({
      type: "bar",
      name: "Amt Up",
      data: upData,
      yAxisIndex: 1,
      barWidth,
      itemStyle: { color: UP_COLOR, opacity: barOpacity },
      z: 0,
      stack: "amt",
    });
    echartsSeries.push({
      type: "bar",
      name: "Amt Down",
      data: downData,
      yAxisIndex: 1,
      barWidth,
      itemStyle: { color: DOWN_COLOR, opacity: barOpacity },
      z: 0,
      stack: "amt",
    });
  }

  const grid = commonGrid({ left: 55, right: 55, bottom: 50 });

  // ---- Build tooltip context -------------------------------------------
  const tooltipContext: TooltipContext = {
    dates,
    shorts,
    longs,
    shortSlopes,
    shortCurvs,
    longSlopes,
    longCurvs,
    longStds,
    opens,
    highs,
    lows,
    tradingAmts,
    dateOfLastExtreme,
    gapSinceLastExtreme,
    daysSinceLastExtreme,
    rsi6,
    rsi10,
    rsi14,
    rsi20,
    amtMaSlopeOfLong,
    amtMarketShareOfLong,
    ohlcOpens,
    ohlcHighs,
    ohlcLows,
    peakData,
    valleyLowData,
    nearbyBands,
    trendBands,
    hasNearbyBands,
    hasTrendBands,
    hasBase,
    baseVal,
    showBoll,
    upperData,
    lowerData,
    bollBase,
    pair,
    isPricePair,
    sName,
    lName,
    tradingAmtMode,
    ohlcMode,
  };

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
      formatter: buildPairTooltipFormatter(tooltipContext),
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
        name: ohlcMode === "percentage" ? "%" : undefined,
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: {
          color: c.textColor,
          fontSize: 9,
          formatter: (v: number) => fmtPrice(v),
        },
        splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
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
