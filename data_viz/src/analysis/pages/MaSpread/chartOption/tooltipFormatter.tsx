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
import type { MovAveSpreadPairSeries } from "../../../../../shared/types";
import { type OhlcMode } from "@/lib/ohlc";
import { type TrendBand } from "./trendBands";

// ---- Custom React element → HTML renderer --------------------------------
// Minimal renderer that converts React.createElement results to HTML strings.
// Handles:
//   - React.Fragment (renders children without wrapper)
//   - String/number children (text nodes)
//   - Array children (rendered sequentially)
//   - DOM elements (div, span, etc.) with className, style, children
// Does NOT handle function components — callers must call the component
// function first (or use the helpers below).

type El = React.ReactElement | string | number | boolean | null | undefined;

function styleObjectToString(style: React.CSSProperties | undefined): string {
  if (!style) return "";
  const parts: string[] = [];
  for (const [key, val] of Object.entries(style)) {
    if (val == null) continue;
    // Convert camelCase to kebab-case
    const cssKey = key.replace(/[A-Z]/g, (m) => `-${m.toLowerCase()}`);
    if (typeof val === "number") {
      // Numeric values default to px except for unitless properties
      const unitless = ["opacity", "zIndex", "fontWeight", "lineHeight"];
      if (unitless.includes(key) || val === 0) {
        parts.push(`${cssKey}:${val}`);
      } else {
        parts.push(`${cssKey}:${val}px`);
      }
    } else {
      parts.push(`${cssKey}:${String(val)}`);
    }
  }
  return parts.join(";");
}

function renderChildren(children: React.ReactNode): string {
  if (children == null || children === false || children === true) return "";
  if (typeof children === "string") return children;
  if (typeof children === "number") return String(children);
  if (Array.isArray(children)) {
    return children.map((c) => renderChildren(c)).join("");
  }
  if (React.isValidElement(children)) {
    return renderEl(children);
  }
  return String(children);
}

function renderEl(el: El): string {
  if (el == null || el === false || el === true) return "";
  if (typeof el === "string") return el;
  if (typeof el === "number") return String(el);
  if (Array.isArray(el)) return el.map((c) => renderEl(c)).join("");
  if (!React.isValidElement(el)) return "";

  const { type, props } = el as React.ReactElement;

  // React.Fragment (symbol type) — render children without wrapper.
  // Must be checked before the function-component branch.
  if (type === React.Fragment) {
    return renderChildren(props?.children);
  }

  // Handle function components by calling them
  if (typeof type === "function") {
    return renderEl((type as React.FC<Record<string, unknown>>)(props ?? {}) as El);
  }

  // HTML element (div, span, etc.)
  const tag = String(type).toLowerCase();
  const styleStr = styleObjectToString(props?.style as React.CSSProperties | undefined);
  const classStr = props?.className ? ` class="${String(props.className)}"` : "";
  const styleAttr = styleStr ? ` style="${styleStr}"` : "";
  const childHtml = renderChildren(props?.children as React.ReactNode);

  return `<${tag}${classStr}${styleAttr}>${childHtml}</${tag}>`;
}

/** Final render function — takes React element, returns HTML string. */
function render(el: React.ReactElement): string {
  return renderEl(el);
}

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
  peakData: Array<number | null>;
  valleyLowData: Array<number | null>;
  nearbyBands: Array<{ startIndex: number; endIndex: number; lower: number; upper: number }>;
  trendBands: TrendBand[];
  hasNearbyBands: boolean;
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
    if (idx < 0) return render(React.createElement(Header, null, dateStr));

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

    // Rolling OHLC for SMA pairs only (kind === "price")
    if (pair.ma_long >= 20 && pair.kind === "price") {
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

    // Slope + curvature
    const isPctSlope = ohlcMode === "percentage" && hasBase && baseVal != null;
    const slopeScale = isPctSlope && baseVal != null ? 100 / baseVal : 1;
    const fmtSlope = (v: number | null | undefined): string =>
      fmtVal(v != null && Number.isFinite(v) ? v * slopeScale : null);
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

    // Peak / valley-low marker info
    const pk = peakData[idx];
    const vl = valleyLowData[idx];
    if (pk != null) {
      children.push(
        React.createElement(Row, {
          key: "peak",
          style: { marginTop: "2px", color: "#43A047", fontWeight: 600 },
        }, `▲ Peak: ${fmtPrice(pk)}`),
      );
    }
    if (vl != null) {
      children.push(
        React.createElement(Row, {
          key: "valley",
          style: { marginTop: "2px", color: "#E53935", fontWeight: 600 },
        }, `▼ Valley Low: ${fmtPrice(vl)}`),
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
        }, `gap_since_last_extreme: ${leGap != null && Number.isFinite(leGap) ? fmtPct(leGap * 100, 2) : "—"} · days_since_last_extreme: ${leDays != null && Number.isFinite(leDays) ? Math.round(leDays) : "—"}`),
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

    // Nearby-extreme band info
    if (hasNearbyBands) {
      const band = nearbyBands.find(
        (b) => idx >= b.startIndex && idx <= b.endIndex,
      );
      if (band) {
        children.push(
          React.createElement(Row, {
            key: "nearby",
            style: { marginTop: "2px", color: "#E53935", opacity: 0.9 },
          }, `Nearby Extreme: ${dates[band.startIndex]} ↔ ${dates[band.endIndex]} · [${fmtPrice(band.lower)}, ${fmtPrice(band.upper)}]`),
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

    return render(React.createElement(React.Fragment, null, children));
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
    if (idx < 0) return render(React.createElement(Header, null, dateStr));

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

    return render(React.createElement(React.Fragment, null, children));
  };
}
