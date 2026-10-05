/**
 * React-based tooltip formatters for MA-Spread charts — composers only.
 *
 * Uses React.createElement + a custom element-to-HTML renderer to produce
 * HTML strings for ECharts tooltips, giving us proper React syntax (elements,
 * components, conditional rendering) while still being compatible with ECharts'
 * `formatter` API — without requiring react-dom/server (SSR-only).
 *
 * Each button group's rows live in its subdirectory's tooltipRows.tsx
 * (roofFloor/, tradingAmt/, bollinger/, rsiTrend/, derivTrend/, ohlcGaps/);
 * the shared Row/Header kit + formatter helpers live in _common/tooltipKit.
 * The rows below belong to the pair chips — the always-on core chart (date,
 * OHLC/curves, gap, rolling-window OHLC, price slope/curvature) and the
 * tooltip-only trend classification.
 */
import React from "react";
import { fmtPct } from "@/lib/series";
import { renderReactElement } from "@/lib/react-tooltip-renderer";
import {
  fmtVal,
  Header,
  Row,
  makeFmtPrice,
  tooltipAxisValue,
} from "./_common";
import type { TooltipContext, TooltipFmtHelpers } from "./_common";
import { appendBollingerRows } from "./bollinger";
import { appendRsiRow } from "./rsiTrend";
import { appendDerivTrendRows } from "./derivTrend";
import {
  appendTradingAmtRow,
  appendAmtSlopeRow,
  appendAmtEnvelopeRows,
} from "./tradingAmt";
import { appendRoofFloorRows } from "./roofFloor";
import { appendGapRows } from "./ohlcGaps";
import type { GapTooltipInfo } from "./ohlcGaps";

/** Shared fmt helpers for the pair tooltip's sections (built once per
 *  formatter from the context's display-mode state). */
function pairFmtHelpers(ctx: TooltipContext): TooltipFmtHelpers {
  const fmtPrice = makeFmtPrice(ctx.ohlcMode, {
    baseVal: ctx.baseVal,
    hasBase: ctx.hasBase,
  });
  // Percentage-mode slope scale — shared by the roof/floor line slopes
  // and the MA slope/curvature rows below.
  const isPctSlope = ctx.ohlcMode === "percentage" && ctx.hasBase && ctx.baseVal != null;
  const slopeScale = isPctSlope && ctx.baseVal != null ? 100 / ctx.baseVal : 1;
  const fmtSlope = (v: number | null | undefined): string =>
    fmtVal(v != null && Number.isFinite(v) ? v * slopeScale : null);
  const rebasedTag = isPctSlope
    ? React.createElement("span", {
        key: "rebased",
        style: { opacity: 0.6, fontSize: "0.9em" },
      }, " (rebased to 100)")
    : null;
  return { fmtPrice, fmtSlope, isPctSlope, slopeScale, rebasedTag };
}

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
    opens,
    highs,
    lows,
    ohlcOpens,
    ohlcHighs,
    ohlcLows,
    roofFloorWindow,
    trendBands,
    hasTrendBands,
    pair,
    isPricePair,
    sName,
    lName,
  } = ctx;
  const helpers = pairFmtHelpers(ctx);
  const { fmtPrice, fmtSlope } = helpers;

  // Derive the MA/EMA prefix from the pair kind.
  const maPrefix = pair.kind === "ema" ? "EMA" : "MA";
  const maLabel = `${maPrefix}${pair.ma_long}`;

  return (params: unknown): string => {
    const arr = Array.isArray(params) ? params : [params];
    if (arr.length === 0) return "";
    const dateStr = tooltipAxisValue(params);
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
    if (pair.ma_long >= 20 && pair.kind === "price" && pair.ma_long !== roofFloorWindow) {
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

    // Enabled OHLC-window overlay (roof/floor group).
    appendRoofFloorRows(children, ctx, idx, helpers);

    // Trading amount + its MA slope / market share (tradingAmt group).
    appendTradingAmtRow(children, ctx, idx);
    appendAmtSlopeRow(children, ctx, idx);

    // Price slope + curvature. The ONLY persisted derivatives — shown on
    // the price legs (Price/* pairs); MA5/EMA6-short pairs and the long
    // legs have no derivative columns anymore.
    if (isPricePair) {
      children.push(
        React.createElement(Row, {
          key: "shortSlope",
          style: { marginTop: "2px", opacity: 0.85 },
        }, [
          `Price slope: ${fmtSlope(ss)} · curv: ${fmtSlope(sc)}`,
          helpers.rebasedTag,
        ]),
      );
    }

    // Slopes/Curvatures overlay (derivTrend group).
    appendDerivTrendRows(children, ctx, idx, helpers);

    // Bollinger band values (bollinger group).
    appendBollingerRows(children, ctx, idx, helpers);

    // Wilder RSI (rsiTrend group).
    appendRsiRow(children, ctx, idx);

    // Trend classification (tooltip-only — computed from the pair data).
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

    // Top-10% OHLC Days (ohlcGaps group).
    appendGapRows(children, ctx.ohlcGapInfo, idx);

    return renderReactElement(React.createElement(React.Fragment, null, children));
  };
}

/** The Amt Envelope tooltip's arguments (from buildAmtEnvelopeOption). */
export interface AmtTooltipArgs {
  dates: string[];
  tradingAmts: Array<number | null>;
  amtMaArrays: Record<number, Array<number | null>>;
  amtMaSlopeArrays: Record<number, Array<number | null>>;
  amtMarketShareArrays: Record<number, Array<number | null>>;
  highs: Array<number | null>;
  lows: Array<number | null>;
  selectedWindow: number;
  amtMaWindows: readonly number[];
  fmtPrice: (v: number | null | undefined) => string;
  gapInfo: GapTooltipInfo | null;
}

/**
 * Build the ECharts tooltip formatter for the Amt Envelope chart.
 */
export function buildAmtTooltipFormatter(args: AmtTooltipArgs) {
  const {
    dates, gapInfo,
  } = args;
  return (params: unknown): string => {
    const arr = Array.isArray(params) ? params : [params];
    if (arr.length === 0) return "";
    const dateStr = tooltipAxisValue(params);
    const idx = dates.indexOf(dateStr);
    if (idx < 0) return renderReactElement(React.createElement(Header, null, dateStr));

    const children: React.ReactNode[] = [];

    children.push(React.createElement(Header, { key: "date" }, dateStr));

    // The whole amount block (tradingAmt group).
    appendAmtEnvelopeRows(children, { ...args, idx });

    // Top-10% OHLC Days (ohlcGaps group).
    appendGapRows(children, gapInfo, idx);

    return renderReactElement(React.createElement(React.Fragment, null, children));
  };
}
