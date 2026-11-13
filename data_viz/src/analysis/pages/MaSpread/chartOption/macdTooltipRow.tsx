/**
 * MACD tooltip row (the pair chart) — the MATERIALIZED tech_stats
 * MACD(12,26,9) block at the hovered date (stats.*_tech_stats via the
 * chart's shared block): the line (DIF), the signal (DEA) and the
 * histogram, colored by the histogram's sign + direction (the house
 * momentum-confirmation read: rising positive hist = the move is
 * CONFIRMED; falling beneath a move = FADING / exhaustion caveat).
 */
import React from "react";
import { Row } from "./_common/tooltipKit";
import type { TooltipContext } from "./_common";

export function appendMacdRow(
  children: React.ReactNode[],
  ctx: TooltipContext,
  idx: number,
): void {
  const line = ctx.macdLine[idx];
  const sig = ctx.macdSignal[idx];
  const hist = ctx.macdHist[idx];
  if (line == null && sig == null && hist == null) return;
  const fmt = (v: number | null | undefined): string =>
    v != null && Number.isFinite(v) ? v.toFixed(2) : "—";
  const prev = idx > 0 ? ctx.macdHist[idx - 1] : null;
  let color = "#9E9E9E";
  if (hist != null && Number.isFinite(hist)) {
    if (hist > 0) color = prev != null && hist >= prev ? "#E53935" : "#EF9A9A";
    else if (hist < 0) color = prev != null && hist <= prev ? "#43A047" : "#A5D6A7";
  }
  children.push(
    React.createElement(Row, {
      key: "macd",
      style: { marginTop: "2px", color, opacity: 0.9 },
    }, `MACD: dif ${fmt(line)} · dea ${fmt(sig)} · hist ${fmt(hist)}`),
  );
}
