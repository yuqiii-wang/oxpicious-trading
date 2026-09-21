import React from "react";
import { escapeTooltipText, renderReactElement } from "@/lib/react-tooltip-renderer";
import type { StrategyDecision } from "@shared/types";

interface CreateMarkerTooltipFormatterParams {
  upColor: string;
  downColor: string;
  textColor: string;
  isNormalized: boolean;
  fmtNum: (v: number, d?: number) => string;
  stripMixPrefix: (r: string | null | undefined) => string;
}

export function createMarkerTooltipFormatter({
  upColor,
  downColor,
  textColor,
  isNormalized,
  fmtNum,
  stripMixPrefix,
}: CreateMarkerTooltipFormatterParams): (params: unknown) => string {
  return (params: unknown): string => {
    const p = params as {
      data?: { decision?: StrategyDecision };
      seriesName?: string;
    };
    const d = p.data?.decision;
    if (!d) return "";
    const isLastDay = p.seriesName === "LAST DAY SELL";
    const sideColor = d.side === "BUY"
      ? upColor
      : isLastDay
        ? "#9575cd"
        : downColor;
    const confidence = d.side === "BUY"
      ? d.qty
      : d.total_qty_before > 0
        ? (d.qty / d.total_qty_before) * 100
        : 0;
    const priceStr = isNormalized
      ? `${fmtNum(d.fill_price, 4)} (idx ${fmtNum(d.normalized_fill_price, 1)})`
      : fmtNum(d.fill_price, 4);

    // Vertical layout: one row (div) per semantic item.
    const rows: React.ReactNode[] = [
      React.createElement("b", { style: { color: sideColor } }, `${d.side} #${d.decision_no}`),
      `Exec: ${d.exec_date}`,
      `Confidence: ${fmtNum(confidence, 1)} / 100`,
      `Qty: ${fmtNum(d.qty, 2)}`,
      `Fill Price: ${priceStr}`,
      `Position: ${fmtNum(d.position_before, 2)} → ${fmtNum(d.position_after, 2)}`,
      `Cash: ${fmtNum(d.cash_before, 2)} → ${fmtNum(d.cash_after, 2)}`,
    ];

    if (d.side === "SELL") {
      rows.push(
        React.createElement(
          React.Fragment,
          null,
          "Realized P&L: ",
          React.createElement("b", { style: { color: d.realized_pnl >= 0 ? upColor : downColor } },
            `${d.realized_pnl >= 0 ? "+" : ""}${fmtNum(d.realized_pnl, 2)}`),
        ),
        `Mean Buy idx: ${fmtNum(d.normalized_mean_buy_price, 1)}`,
      );
    }

    rows.push(
      React.createElement(
        "span",
        { style: { color: textColor, fontSize: 11 } },
        escapeTooltipText(stripMixPrefix(d.signal_reason)),
      ),
    );

    const children: React.ReactNode[] = rows.map((row) => React.createElement("div", null, row));

    return renderReactElement(React.createElement(React.Fragment, null, children));
  };
}