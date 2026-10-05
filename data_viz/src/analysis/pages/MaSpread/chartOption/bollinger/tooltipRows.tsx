/**
 * Bollinger band tooltip rows (the pair chart) — the band edges at the
 * hovered date plus σ and the band width (rebased in % mode).
 */
import React from "react";
import { Row } from "../_common/tooltipKit";
import type { TooltipContext, TooltipFmtHelpers } from "../_common";

/** The "Upper · Lower" and "σ · band width" rows when the envelope is on. */
export function appendBollingerRows(
  children: React.ReactNode[],
  ctx: TooltipContext,
  idx: number,
  helpers: TooltipFmtHelpers,
): void {
  if (!ctx.showBoll) return;
  const maLabel = ctx.pair.kind === "ema" ? `EMA${ctx.pair.ma_long}` : `MA${ctx.pair.ma_long}`;
  const uv = ctx.upperData[idx];
  const lo = ctx.lowerData[idx];
  const sd = ctx.longStds[idx];
  const bw = uv != null && lo != null ? uv - lo : null;
  children.push(
    React.createElement(Row, {
      key: "boll1",
      style: { marginTop: "2px", opacity: 0.85 },
    }, `Upper: ${helpers.fmtPrice(uv)} · Lower: ${helpers.fmtPrice(lo)}`),
  );
  children.push(
    React.createElement(Row, {
      key: "boll2",
      style: { opacity: 0.85 },
    }, [
      `σ${maLabel}: ${helpers.fmtSlope(sd)} · band width: ${helpers.fmtSlope(bw)}`,
      helpers.rebasedTag,
    ]),
  );
}
