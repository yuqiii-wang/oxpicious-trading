/**
 * React-based tooltip formatters for MA-Spread charts.
 *
 * Uses React.createElement + a custom element-to-HTML renderer to produce
 * HTML strings for ECharts tooltips, giving us proper React syntax (elements,
 * components, conditional rendering) while still being compatible with ECharts'
 * `formatter` API — without requiring react-dom/server (SSR-only).
 */
import React from "react";
import { fmtNum, fmtPct, fmtYi } from "@/lib/series";
import { UP_COLOR, DOWN_COLOR } from "@/theme/chart-palette";
import type { MovAveSpreadPairSeries } from "@shared/types";
import { type OhlcMode } from "@/lib/ohlc";
import { type TrendBand } from "./trendBands";

import { renderReactElement } from "@/lib/react-tooltip-renderer";

// ---- Shared helpers ------------------------------------------------------

/** Format a yuan amount as 亿元 (100M yuan). */
function fmtAmtYi(v: number | null | undefined, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return fmtYi(v, digits);
}

/** Null-safe number formatter with "—" fallback. */
function fmtVal(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return fmtNum(v);
}

// ---- Shared type: tooltip context ----------------------------------------

/** Context shared by both tooltip formatters (built by buildPairOption). */
export interface TooltipContext {
  dates: string[];
  shorts: Array<number | null>;
  longs: Array<number | null>;
  shortSlopes: Array<number | null>;
  shortCurvs: Array<number | null>;
  longSlopes: Array<number | null>;
  longCurvs: Array<number | null>;
  longStds: Array<number | null>;
  opens: Array<number | null>;
  highs: Array<number | null>;
  lows: Array<number | null>;
  tradingAmts: Array<number | null>;
  dateOfLastExtreme: Array<string | null>;
  gapSinceLastExtreme: Array<number | null>;
  daysSinceLastExtreme: Array<number | null>;
  rsi6: Array<number | null>;
  rsi10: Array<number | null>;
  rsi14: Array<number | null>;
  rsi20: Array<number | null>;
  amtMaSlopeOfLong: Array<number | null>;
  amtMarketShareOfLong: Array<number | null>;
  ohlcOpens: Array<number | null>;
  ohlcHighs: Array<number | null>;
  ohlcLows: Array<number | null>;
  /** Enabled rolling-OHLC window (trading days) — null when the OHLC-window
   *  overlay is off. */
  ohlcWindow: number | null;
  /** Per-date OHLC extrema arrays for the enabled window (from the top-level
   *  chartData.ohlc rows) — null when the overlay is off. */
  ohlcWinArrays: {
    open: Array<number | null>;
    high: Array<number | null>;
    highDate: Array<string | null>;
    high2nd: Array<number | null>;
    high2ndDate: Array<string | null>;
    highSlope: Array<number | null>;
    low: Array<number | null>;
    lowDate: Array<string | null>;
    low2nd: Array<number | null>;
    low2ndDate: Array<string | null>;
    lowSlope: Array<number | null>;
  } | null;
  trendBands: TrendBand[];
  hasTrendBands: boolean;
  hasBase: boolean;
  baseVal: number | null;
  showBoll: boolean;
  upperData: Array<number | null>;
  lowerData: Array<number | null>;
  bollBase: Array<number | null>;
  pair: MovAveSpreadPairSeries;
  isPricePair: boolean;
  sName: string;
  lName: string;
  tradingAmtMode: "off" | "lowkey";
  ohlcMode: OhlcMode;
}

// ---- Reusable React tooltip components -----------------------------------

/** A styled row with optional left padding and opacity. */
function Row({
  children,
  style,
}: {
  children?: React.ReactNode;
  style?: React.CSSProperties;
}) {
  const defaultStyle: React.CSSProperties = { marginBottom: "2px" };
  return React.createElement("div", {
    style: { ...defaultStyle, ...style },
    children,
  });
}

/** A bold header row. */
function Header({ children }: { children?: React.ReactNode }) {
  return React.createElement(Row, {
    style: { fontWeight: 600, marginBottom: "2px" },
    children,
  });
}

// ---- Main tooltip formatter for buildPairOption --------------------------

/**
 * Build the ECharts tooltip formatter for the main pair chart
 * (SMA + EMA price/MA pairs).
 */
export function buildPairTooltipFormatter(ctx: TooltipContext) {
  const {
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
    ohlcWindow,
    ohlcWinArrays,
    trendBands,
    hasTrendBands,
    hasBase,
    baseVal,
    showBoll,
    upperData,
    lowerData,
    pair,
    isPricePair,
    sName,
    lName,
    tradingAmtMode,
    ohlcMode,
  } = ctx;

  // Derive the MA/EMA prefix from the pair kind.
  const maPrefix = pair.kind === "ema" ? "EMA" : "MA";
  const maLabel = `${maPrefix}${pair.ma_long}`;

  const fmtPrice = (v: number | null | undefined): string => {
    if (v == null || !Number.isFinite(v) || !hasBase || baseVal == null) return "—";
    if (ohlcMode === "percentage") {
      return fmtPct((v / baseVal - 1) * 100, 2);
    }
    return fmtNum(v);
  };

  return (params: unknown): string => {
    const arr = (Array.isArray(params) ? params : [params]) as Array<{
      axisValue?: string;
    }>;
    if (arr.length === 0) return "";
    const dateStr = (arr[0].axisValue as string) || "";
    const idx = dates.indexOf(dateStr);
    if (idx < 0) return renderReactElement(React.createElement(Header, null, dateStr));

    const children: React.ReactNode[] = [];

    // Date header
    children.push(React.createElement(Header, { key: "date" }, dateStr));

    const sv = shorts[idx];
    const lv = longs[idx];
    const gv = sv != null && lv != null && lv !== 0 ? (sv - lv) / lv : null;
    const ss = shortSlopes[idx];
    const sc = shortCurvs[idx];
    const ls = longSlopes[idx];
    const lc = longCurvs[idx];

    // OHLC for Price/MA pairs
    if (isPricePair) {
      const o = opens[idx];
      const h = highs[idx];
      const l = lows[idx];
      const cl = shorts[idx];
      children.push(
        React.createElement(Row, { key: "ohlc1" }, `O: ${fmtPrice(o)}  H: ${fmtPrice(h)}`),
      );
      children.push(
        React.createElement(Row, { key: "ohlc2" }, `L: ${fmtPrice(l)}  C: ${fmtPrice(cl)}`),
      );
    } else {
      children.push(
        React.createElement(Row, { key: "short" }, `${sName}: ${fmtPrice(sv)}`),
      );
    }

    // Long MA line + gap
    children.push(
      React.createElement(Row, { key: "long" }, `${lName}: ${fmtPrice(lv)}`),
    );
    children.push(
      React.createElement(Row, { key: "gap" }, `gap: ${gv != null ? fmtPct(gv * 100, 3) : "—"}`),
    );

    // Rolling OHLC for SMA pairs only (kind === "price"). Skipped when the
    // enabled OHLC-window overlay covers the same window (the overlay
    // section below already shows it).
    if (pair.ma_long >= 20 && pair.kind === "price" && pair.ma_long !== ohlcWindow) {
      const ohlcO = ohlcOpens[idx];
      const ohlcH = ohlcHighs[idx];
      const ohlcL = ohlcLows[idx];
      const hasOhlcData = ohlcO != null || ohlcH != null || ohlcL != null;
      if (hasOhlcData) {
        children.push(
          React.createElement(Row, {
            key: "ohlcWin1",
            style: { marginTop: "2px", opacity: 0.85 },
          }, `${maLabel}d Open: ${fmtPrice(ohlcO)} · High: ${fmtPrice(ohlcH)}`),
        );
        children.push(
          React.createElement(Row, {
            key: "ohlcWin2",
            style: { opacity: 0.85 },
          }, `${maLabel}d Low: ${fmtPrice(ohlcL)}`),
        );
      }
    }

    // Percentage-mode slope scale — shared by the roof/floor line slopes
    // and the MA slope/curvature rows below.
    const isPctSlope = ohlcMode === "percentage" && hasBase && baseVal != null;
    const slopeScale = isPctSlope && baseVal != null ? 100 / baseVal : 1;
    const fmtSlope = (v: number | null | undefined): string =>
      fmtVal(v != null && Number.isFinite(v) ? v * slopeScale : null);

    // Enabled OHLC-window overlay: rolling O/H/L of the window ending at the
    // hovered date plus its (top, 2nd) extrema with dates — the four points
    // that determine the roof/floor trendlines. The DB-persisted
    // high/low_line_slope_Wd (price units per trading day, slope of the
    // line through the two anchors) is shown alongside the anchor points.
    if (ohlcWinArrays != null && ohlcWindow != null) {
      const w = ohlcWindow;
      const A = ohlcWinArrays;
      const o = A.open[idx];
      const h = A.high[idx];
      const l = A.low[idx];
      children.push(
        React.createElement(Row, {
          key: "ohlcOverlay",
          style: { marginTop: "2px", opacity: 0.85 },
        }, `OHLC ${w}d: O ${fmtPrice(o)} · H ${fmtPrice(h)} · L ${fmtPrice(l)}`),
      );
      const hDate = A.highDate[idx];
      const h2 = A.high2nd[idx];
      const h2Date = A.high2ndDate[idx];
      const hSlope = A.highSlope[idx];
      if (hDate != null || h2Date != null || hSlope != null) {
        children.push(
          React.createElement(Row, {
            key: "ohlcPeaks",
            style: { color: "#FB8C00", opacity: 0.95 },
          }, `Roof pts: top ${fmtPrice(h)} @ ${hDate ?? "—"} · 2nd ${fmtPrice(h2)} @ ${h2Date ?? "—"} · slope ${fmtSlope(hSlope)}/d`),
        );
      }
      const lDate = A.lowDate[idx];
      const l2 = A.low2nd[idx];
      const l2Date = A.low2ndDate[idx];
      const lSlope = A.lowSlope[idx];
      if (lDate != null || l2Date != null || lSlope != null) {
        children.push(
          React.createElement(Row, {
            key: "ohlcTroughs",
            style: { color: "#1E88E5", opacity: 0.95 },
          }, `Floor pts: top ${fmtPrice(l)} @ ${lDate ?? "—"} · 2nd ${fmtPrice(l2)} @ ${l2Date ?? "—"} · slope ${fmtSlope(lSlope)}/d`),
        );
      }
    }

    // Trading amount
    const amt = tradingAmts[idx];
    if (amt != null) {
      children.push(
        React.createElement(Row, {
          key: "amt",
          style: { marginTop: "2px", opacity: 0.85 },
        }, `Trading Amt: ${fmtAmtYi(amt)}`),
      );
    }

    // Trading-amount MA slope + market share
    if (tradingAmtMode !== "off") {
      const aSlope = amtMaSlopeOfLong[idx];
      const aShare = amtMarketShareOfLong[idx];
      if (aSlope != null || aShare != null) {
        const slopeStr = aSlope != null ? fmtPct(aSlope * 100, 2) : "—";
        const shareStr = aShare != null ? fmtPct(aShare * 100, 4) : "—";
        const slopeColor = aSlope != null && aSlope < 0 ? DOWN_COLOR : UP_COLOR;
        children.push(
          React.createElement(Row, {
            key: "amtSlope",
            style: { opacity: 0.85 },
          }, [
            `Amt ${maLabel} slope: `,
            React.createElement("span", {
              style: { color: slopeColor, fontWeight: 600 },
            }, slopeStr),
            ` · mkt share: ${shareStr}`,
          ]),
        );
      }
    }

    // Slope + curvature (fmtSlope/slopeScale defined above, shared with the
    // roof/floor line-slope rows).
    const rebasedTag = isPctSlope
      ? React.createElement("span", {
          style: { opacity: 0.6, fontSize: "0.9em" },
        }, " (rebased to 100)")
      : null;

    children.push(
      React.createElement(Row, {
        key: "shortSlope",
        style: { marginTop: "2px", opacity: 0.85 },
      }, [
        `${sName} slope: ${fmtSlope(ss)} · curv: ${fmtSlope(sc)}`,
        rebasedTag,
      ]),
    );
    children.push(
      React.createElement(Row, {
        key: "longSlope",
        style: { opacity: 0.85 },
      }, [
        `${lName} slope: ${fmtSlope(ls)} · curv: ${fmtSlope(lc)}`,
        rebasedTag,
      ]),
    );

    // Bollinger band values
    if (showBoll) {
      const uv = upperData[idx];
      const lo = lowerData[idx];
      const sd = longStds[idx];
      const bw = uv != null && lo != null ? uv - lo : null;
      children.push(
        React.createElement(Row, {
          key: "boll1",
          style: { marginTop: "2px", opacity: 0.85 },
        }, `Upper: ${fmtPrice(uv)} · Lower: ${fmtPrice(lo)}`),
      );
      children.push(
        React.createElement(Row, {
          key: "boll2",
          style: { opacity: 0.85 },
        }, [
          `σ${maLabel}: ${fmtSlope(sd)} · band width: ${fmtSlope(bw)}`,
          rebasedTag,
        ]),
      );
    }

    // Wilder RSI
    const r6 = rsi6[idx], r10 = rsi10[idx], r14 = rsi14[idx], r20 = rsi20[idx];
    if (r6 != null || r10 != null || r14 != null || r20 != null) {
      const fmtRsi = (v: number | null | undefined): string =>
        v != null && Number.isFinite(v) ? v.toFixed(1) : "—";
      const ref = r14 ?? r10 ?? r6 ?? r20;
      let rsiColor = "#9E9E9E";
      if (ref != null && Number.isFinite(ref)) {
        if (ref >= 70) rsiColor = "#FB8C00";
        else if (ref <= 30) rsiColor = "#43A047";
      }
      children.push(
        React.createElement(Row, {
          key: "rsi",
          style: { marginTop: "2px", color: rsiColor, opacity: 0.9 },
        }, `RSI: 6d ${fmtRsi(r6)} · 10d ${fmtRsi(r10)} · 14d ${fmtRsi(r14)} · 20d ${fmtRsi(r20)}`),
      );
    }

    // Last-extreme info
    const leDate = dateOfLastExtreme[idx];
    if (leDate != null) {
      const leGap = gapSinceLastExtreme[idx];
      const leDays = daysSinceLastExtreme[idx];
      const isMin = !(leGap != null && Number.isFinite(leGap) && leGap < 0);
      const leHex = isMin ? "#43A047" : "#E53935";
      const leMarkIdx = dates.indexOf(leDate);
      const leMark = leMarkIdx >= 0 ? shorts[leMarkIdx] : null;
      const arrow = leGap != null && Number.isFinite(leGap)
        ? (isMin ? "▲ MIN" : "▼ MAX")
        : "▲";
      children.push(
        React.createElement(Row, {
          key: "leDate",
          style: { marginTop: "2px", color: leHex, fontWeight: 600 },
        }, `Last Extreme: ${leDate} (${arrow}${leDays != null && Number.isFinite(leDays) ? `, ${Math.round(leDays)}d` : ""})`),
      );
      children.push(
        React.createElement(Row, {
          key: "leGap",
          style: { color: leHex, opacity: 0.9 },
        }, `gap_since_last_extreme_500days: ${leGap != null && Number.isFinite(leGap) ? fmtPct(leGap * 100, 2) : "—"} · days_since_last_extreme_500days: ${leDays != null && Number.isFinite(leDays) ? Math.round(leDays) : "—"}`),
      );
      if (leMark != null && Number.isFinite(leMark)) {
        children.push(
          React.createElement(Row, {
            key: "leMark",
            style: { color: leHex, opacity: 0.9 },
          }, `extreme ${sName}: ${fmtPrice(leMark)}`),
        );
      }
    }

    // Trend classification
    if (hasTrendBands) {
      const band = trendBands.find(
        (b) => idx >= b.startIdx && idx <= b.endIdx,
      );
      if (band) {
        const trendLabels: Record<string, string> = {
          downward: "▼ Downward",
          flat: "▬ Flat",
          upward: "▲ Upward",
        };
        const trendColors: Record<string, string> = {
          downward: "#E53935",
          flat: "#FFB300",
          upward: "#43A047",
        };
        const periodStart = dates[band.startIdx];
        const periodEnd = dates[band.endIdx];
        const nDays = band.endIdx - band.startIdx + 1;
        const periodText = periodStart === periodEnd
          ? `${periodStart} (${nDays}d)`
          : `${periodStart} → ${periodEnd} (${nDays}d)`;
        children.push(
          React.createElement(Row, {
            key: "trend",
            style: { marginTop: "2px", color: trendColors[band.trend], fontWeight: 600 },
          }, `Trend: ${trendLabels[band.trend]} · ${periodText}`),
        );
      }
    }

    return renderReactElement(React.createElement(React.Fragment, null, children));
  };
}

// ---- Tooltip formatter for buildAmtEnvelopeOption -------------------------

/**
 * Build the ECharts tooltip formatter for the Amt Envelope chart.
 */
export function buildAmtTooltipFormatter(
  dates: string[],
  tradingAmts: Array<number | null>,
  amtMaArrays: Record<number, Array<number | null>>,
  amtMaSlopeArrays: Record<number, Array<number | null>>,
  amtMarketShareArrays: Record<number, Array<number | null>>,
  highs: Array<number | null>,
  lows: Array<number | null>,
  selectedWindow: number,
  amtMaWindows: readonly number[],
  fmtPrice: (v: number | null | undefined) => string,
) {
  return (params: unknown): string => {
    const arr = (Array.isArray(params) ? params : [params]) as Array<{
      axisValue?: string;
    }>;
    if (arr.length === 0) return "";
    const dateStr = (arr[0].axisValue as string) || "";
    const idx = dates.indexOf(dateStr);
    if (idx < 0) return renderReactElement(React.createElement(Header, null, dateStr));

    const children: React.ReactNode[] = [];

    children.push(React.createElement(Header, { key: "date" }, dateStr));

    const amt = tradingAmts[idx];
    children.push(
      React.createElement(Row, { key: "amt", style: { marginTop: "2px" } }, `Trading Amt: ${fmtAmtYi(amt)}`),
    );

    for (const w of amtMaWindows) {
      const v = amtMaArrays[w][idx];
      const marker = w === selectedWindow ? " ●" : "";
      children.push(
        React.createElement(Row, {
          key: `amtma-${w}`,
          style: { opacity: w === selectedWindow ? 1 : 0.7 },
        }, `Amt MA${w}: ${fmtAmtYi(v)}${marker}`),
      );
    }

    const sv = tradingAmts[idx];
    const lv = amtMaArrays[selectedWindow][idx];
    if (sv != null && lv != null && lv !== 0) {
      const gap = (sv - lv) / lv;
      const gapColor = gap >= 0 ? UP_COLOR : DOWN_COLOR;
      children.push(
        React.createElement(Row, {
          key: "gap",
          style: { marginTop: "2px", color: gapColor, fontWeight: 600 },
        }, `gap (Amt vs MA${selectedWindow}): ${fmtPct(gap * 100, 3)}`),
      );
    }

    const sl = amtMaSlopeArrays[selectedWindow][idx];
    const sh = amtMarketShareArrays[selectedWindow][idx];
    if (sl != null || sh != null) {
      const slopeStr = sl != null ? fmtPct(sl * 100, 2) : "—";
      const shareStr = sh != null ? fmtPct(sh * 100, 4) : "—";
      const slopeColor = sl != null && sl < 0 ? DOWN_COLOR : UP_COLOR;
      children.push(
        React.createElement(Row, {
          key: "slope",
          style: { marginTop: "2px", opacity: 0.85 },
        }, [
          `Amt MA${selectedWindow} slope: `,
          React.createElement("span", {
            style: { color: slopeColor, fontWeight: 600 },
          }, slopeStr),
          ` · mkt share: ${shareStr}`,
        ]),
      );
    }

    const h = highs[idx];
    const l = lows[idx];
    children.push(
      React.createElement(Row, {
        key: "hl",
        style: { marginTop: "2px", opacity: 0.5 },
      }, `H: ${fmtPrice(h)} · L: ${fmtPrice(l)}`),
    );

    return renderReactElement(React.createElement(React.Fragment, null, children));
  };
}
