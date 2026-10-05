/**
 * Trading-amount tooltip rows — the pair chart's "Trading Amt" +
 * "Amt slope · mkt share" rows (the Amt overlay mode's readouts), and
 * the Amt Envelope view's whole amount block (amt bars + the 5 Amt MA
 * lines + the amt-vs-MA gap + slope/share + the lowkey H/L row).
 */
import React from "react";
import { fmtPct } from "@/lib/series";
import { UP_COLOR, DOWN_COLOR } from "@/theme/chart-palette";
import { Row } from "../_common/tooltipKit";
import { fmtAmtYi } from "../_common/tooltipFmt";
import type { TooltipContext } from "../_common";

/** The pair chart's "Trading Amt" row (shown whenever the day has an
 *  amount, in every overlay mode). */
export function appendTradingAmtRow(
  children: React.ReactNode[],
  ctx: TooltipContext,
  idx: number,
): void {
  const amt = ctx.tradingAmts[idx];
  if (amt == null) return;
  children.push(
    React.createElement(Row, {
      key: "amt",
      style: { marginTop: "2px", opacity: 0.85 },
    }, `Trading Amt: ${fmtAmtYi(amt)}`),
  );
}

/** The pair chart's "Amt {long} slope · mkt share" row — only while the
 *  Amt overlay mode is on (the readouts belong to that view). */
export function appendAmtSlopeRow(
  children: React.ReactNode[],
  ctx: TooltipContext,
  idx: number,
): void {
  if (ctx.tradingAmtMode === "off") return;
  const maLabel = ctx.pair.kind === "ema" ? `EMA${ctx.pair.ma_long}` : `MA${ctx.pair.ma_long}`;
  const aSlope = ctx.amtMaSlopeOfLong[idx];
  const aShare = ctx.amtMarketShareOfLong[idx];
  if (aSlope == null && aShare == null) return;
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
        key: "slope",
        style: { color: slopeColor, fontWeight: 600 },
      }, slopeStr),
      ` · mkt share: ${shareStr}`,
    ]),
  );
}

/** The Amt Envelope view's tooltip arguments (from buildAmtEnvelopeOption). */
export interface AmtEnvelopeTooltipArgs {
  idx: number;
  tradingAmts: Array<number | null>;
  amtMaArrays: Record<number, Array<number | null>>;
  amtMaSlopeArrays: Record<number, Array<number | null>>;
  amtMarketShareArrays: Record<number, Array<number | null>>;
  amtMaWindows: readonly number[];
  selectedWindow: number;
  highs: Array<number | null>;
  lows: Array<number | null>;
  fmtPrice: (v: number | null | undefined) => string;
}

/** The Amt Envelope view's tooltip block: Trading Amt, the 5 Amt MA
 *  rows (the selected window emphasized), the amt-vs-MA gap, the
 *  selected window's slope + market share, and the lowkey H/L row. */
export function appendAmtEnvelopeRows(
  children: React.ReactNode[],
  args: AmtEnvelopeTooltipArgs,
): void {
  const {
    idx, tradingAmts, amtMaArrays, amtMaSlopeArrays, amtMarketShareArrays,
    amtMaWindows, selectedWindow, highs, lows, fmtPrice,
  } = args;

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
          key: "slope",
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
}
