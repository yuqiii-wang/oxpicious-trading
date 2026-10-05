/**
 * Top-10% OHLC Days tooltip rows — the hovered day's classified pattern
 * + raw gap legs (analysis.mov_ave_spreads_detail_ohlc_gaps), shown
 * when the group's toggles are on. Shared by both tooltips (pair chart
 * + Amt Envelope).
 */
import React from "react";
import { Row } from "../_common/tooltipKit";
import type { GapTooltipInfo } from "./tooltipInfo";

/** The "Pattern" + "Gap legs" rows when the group is on. */
export function appendGapRows(
  children: React.ReactNode[],
  gapInfo: GapTooltipInfo | null,
  idx: number,
): void {
  if (gapInfo == null) return;
  const pat = gapInfo.pattern[idx];
  const g = gapInfo.gap[idx];
  const it = gapInfo.intra[idx];
  const uw = gapInfo.uw[idx];
  const lw = gapInfo.lw[idx];
  const cn: Record<string, string> = {
    spike_fade: "冲高回落",
    flush_recover: "探底回升",
    gap_up_advance: "高开高走",
    gap_up_decline: "高开低走",
    gap_down_advance: "低开高走",
    gap_down_decline: "低开低走",
  };
  if (pat != null) {
    const gloss = cn[pat] ? ` (${cn[pat]})` : "";
    const pc = pat === "spike_fade" ? "#E53935" : pat === "flush_recover" ? "#43A047" : undefined;
    children.push(
      React.createElement(Row, {
        key: "gapPattern",
        style: { marginTop: "2px", fontWeight: 600, ...(pc ? { color: pc } : {}) },
      }, `Pattern: ${pat}${gloss}`),
    );
  }
  if (g != null || it != null || uw != null || lw != null) {
    const f = (v: number | null): string =>
      v == null ? "—" : `${v >= 0 ? "+" : ""}${(v * 100).toFixed(2)}%`;
    children.push(
      React.createElement(Row, {
        key: "gapLegs",
        style: { opacity: 0.85 },
      }, `Gap legs: gap ${f(g)} · intra ${f(it)} · uw ${f(uw)} · lw ${f(lw)}`),
    );
  }
}
