import React from "react";
import { renderReactElement, signedPct, tooltipComponents } from "@/lib/react-tooltip-renderer";
import { UP_COLOR, DOWN_COLOR } from "@/theme/chart-palette";

interface IndustryBarTooltipProps {
  label: string;
  pct: number | null;
}

function IndustryBarTooltip({ label, pct }: IndustryBarTooltipProps) {
  const color = pct != null && pct >= 0 ? UP_COLOR : DOWN_COLOR;
  const pctStr = pct != null ? signedPct(pct, 4) : "—";
  return React.createElement(
    React.Fragment,
    null,
    React.createElement(tooltipComponents.Header, null, label),
    React.createElement(tooltipComponents.Bold, { style: { color } }, pctStr),
  );
}

export function makeIndustryBarTooltipFormatter() {
  return (params: unknown): string => {
    const p = params as { dataIndex?: number };
    if (p.dataIndex == null) return "";
    return renderReactElement(
      React.createElement(IndustryBarTooltip, {
        label: "",
        pct: 0,
      }),
    );
  };
}

export function renderIndustryBarTooltip(
  label: string,
  pct: number | null,
): string {
  return renderReactElement(
    React.createElement(IndustryBarTooltip, { label, pct }),
  );
}
