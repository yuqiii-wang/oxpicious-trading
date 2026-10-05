/**
 * Slopes/Curvatures tooltip readout (the pair chart) — the price
 * derivative and the short-window MA/EMA derivative at the hovered
 * date (rebased in pct mode by the shared fmtSlope).
 */
import React from "react";
import { Row } from "../_common/tooltipKit";
import type { TooltipContext, TooltipFmtHelpers } from "../_common";

/** The "Slopes: … · …" (or "Curvs: …") row when the overlay is on and
 *  at least one line has a reading at the hovered date. */
export function appendDerivTrendRows(
  children: React.ReactNode[],
  ctx: TooltipContext,
  idx: number,
  helpers: TooltipFmtHelpers,
): void {
  if (ctx.derivTrend == null || ctx.derivTrend.lines.length === 0) return;
  const vals = ctx.derivTrend.lines.map(
    (l) => `${l.name.replace(/ (Slope|Curv)$/, "")} ${helpers.fmtSlope(l.data[idx])}`,
  );
  if (!vals.some((v) => !v.endsWith("—"))) return;
  children.push(
    React.createElement(Row, {
      key: "derivTrend",
      style: { marginTop: "2px", opacity: 0.85 },
    }, [
      `${ctx.derivTrend.mode === "slope" ? "Slopes" : "Curvs"}: ${vals.join(" · ")}`,
      helpers.rebasedTag,
    ]),
  );
}
