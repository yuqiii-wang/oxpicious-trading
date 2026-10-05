/**
 * RSI tooltip row (the pair chart) — the six Wilder-RSI window readings
 * at the hovered date, colored by the reference window's overbought /
 * oversold level (the 70/30 thresholds the drawn guides use).
 */
import React from "react";
import { Row } from "../_common/tooltipKit";
import type { TooltipContext } from "../_common";

/** The "RSI: 3d … · 60d …" row when the hovered date has any reading. */
export function appendRsiRow(
  children: React.ReactNode[],
  ctx: TooltipContext,
  idx: number,
): void {
  const r3 = ctx.rsi3[idx], r6 = ctx.rsi6[idx], r10 = ctx.rsi10[idx], r14 = ctx.rsi14[idx], r20 = ctx.rsi20[idx], r60 = ctx.rsi60[idx];
  if (r3 == null && r6 == null && r10 == null && r14 == null && r20 == null && r60 == null) return;
  const fmtRsi = (v: number | null | undefined): string =>
    v != null && Number.isFinite(v) ? v.toFixed(1) : "—";
  const ref = r14 ?? r10 ?? r6 ?? r3 ?? r20;
  let rsiColor = "#9E9E9E";
  if (ref != null && Number.isFinite(ref)) {
    if (ref >= 70) rsiColor = "#FB8C00";
    else if (ref <= 30) rsiColor = "#43A047";
  }
  children.push(
    React.createElement(Row, {
      key: "rsi",
      style: { marginTop: "2px", color: rsiColor, opacity: 0.9 },
    }, `RSI: 3d ${fmtRsi(r3)} · 6d ${fmtRsi(r6)} · 10d ${fmtRsi(r10)} · 14d ${fmtRsi(r14)} · 20d ${fmtRsi(r20)} · 60d ${fmtRsi(r60)}`),
  );
}
