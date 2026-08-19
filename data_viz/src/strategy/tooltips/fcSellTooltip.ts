import React from "react";
import { renderReactElement, tooltipComponents } from "@/lib/react-tooltip-renderer";
import type { StrategyDecision } from "@shared/types";

interface CreateFcSellTooltipFormatterParams {
  fcPurple: string;
  upColor: string;
  downColor: string;
  textColor: string;
  isNormalized: boolean;
  fmtNum: (v: number, d?: number) => string;
  stripMixPrefix: (r: string | null | undefined) => string;
}

export function createFcSellTooltipFormatter({
  fcPurple,
  upColor,
  downColor,
  textColor,
  isNormalized,
  fmtNum,
  stripMixPrefix,
}: CreateFcSellTooltipFormatterParams): (params: unknown) => string {
  return (params: unknown): string => {
    const p = params as { data?: { decision?: StrategyDecision } };
    const d = p.data?.decision;
    if (!d) return "";
    const confidence = d.total_qty_before > 0
      ? (d.qty / d.total_qty_before) * 100 : 0;
    const priceStr = isNormalized
      ? `${fmtNum(d.fill_price, 4)} (idx ${fmtNum(d.normalized_fill_price, 1)})`
      : fmtNum(d.fill_price, 4);

    const children: React.ReactNode[] = [
      React.createElement("b", { style: { color: fcPurple } }, `FC Sell #${d.decision_no}`),
      React.createElement("br"),
      `Exec: ${d.exec_date}`,
      React.createElement("br"),
      `Confidence: ${fmtNum(confidence, 1)} / 100 | Qty: ${fmtNum(d.qty, 2)} @ ${priceStr}`,
      React.createElement("br"),
      "Realized P&L: ",
      React.createElement("b", { style: { color: d.realized_pnl >= 0 ? upColor : downColor } },
        `${d.realized_pnl >= 0 ? "+" : ""}${fmtNum(d.realized_pnl, 2)}`),
      React.createElement("br"),
      `Position: ${fmtNum(d.position_before, 2)} → ${fmtNum(d.position_after, 2)}`,
      React.createElement("br"),
      React.createElement("span", { style: { color: textColor, fontSize: 11 } }, stripMixPrefix(d.signal_reason)),
    ];

    return renderReactElement(React.createElement(React.Fragment, null, children));
  };
}

export function createFcSellFallbackTooltipFormatter(): (params: unknown) => string {
  return (params: unknown): string => {
    const p = params as { data?: { forecastRow?: { forecast_day: number; sell_confidence: number; close_price: number; realized_pnl_forecast: number } } };
    const r = p.data?.forecastRow;
    if (!r) return "";
    const day = r.forecast_day;
    const conf = r.sell_confidence.toFixed(1);
    const close = r.close_price.toFixed(2);
    const pnl = r.realized_pnl_forecast.toFixed(2);
    return renderReactElement(
      React.createElement(React.Fragment, null, [
        tooltipComponents.Bold({ children: `FC Sell · F+${day}` }),
        React.createElement("br"),
        `Close: ${close} (norm)`,
        React.createElement("br"),
        `Sell Conf: ${conf}%`,
        React.createElement("br"),
        `P&L: ${pnl}`,
      ]),
    );
  };
}