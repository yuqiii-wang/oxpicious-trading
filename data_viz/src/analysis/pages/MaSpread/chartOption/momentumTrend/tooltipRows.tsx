/**
 * Momentum tooltip readout (the pair chart) — the picked terms'
 * lookback-adaptive momentum at the hovered date. The adaptive terms
 * spell their effective lookback (e.g. "short·14d") so the
 * regime-varying n is readable per date.
 */
import React from "react";
import { Row } from "../_common/tooltipKit";
import { fmtPct } from "@/lib/series";
import type { TooltipContext } from "../_common";

/** The "Mom: …" row when the overlay is on and at least one line has a
 *  reading at the hovered date. */
export function appendMomentumTrendRows(
  children: React.ReactNode[],
  ctx: TooltipContext,
  idx: number,
): void {
  const spec = ctx.momentumTrend;
  if (spec == null || spec.lines.length === 0) return;
  const pct = (v: number | null | undefined) =>
    fmtPct(v == null ? null : v * 100, 1);
  const vals = spec.lines.map((l) => {
    const lb = spec.lbs?.[l.term]?.[idx];
    const suffix =
      l.term !== "5d" && l.term !== "20d" && lb != null ? `·${lb}d` : "";
    return `${l.name}${suffix} ${pct(l.data[idx])}`;
  });
  if (vals.every((v) => v.endsWith("—"))) return;
  children.push(
    React.createElement(Row, {
      key: "momentumTrend",
      style: { marginTop: "2px", opacity: 0.85 },
    }, `Mom: ${vals.join(" · ")}`),
  );
}
