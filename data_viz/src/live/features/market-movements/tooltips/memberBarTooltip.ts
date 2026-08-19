import React from "react";
import { renderReactElement, signedPct, tooltipComponents } from "@/lib/react-tooltip-renderer";
import { UP_COLOR, DOWN_COLOR } from "@/theme/chart-palette";

interface MemberBarTooltipProps {
  name: string;
  code: string;
  pct: number | null;
}

function MemberBarTooltip({ name, code, pct }: MemberBarTooltipProps) {
  const color = pct != null && pct >= 0 ? UP_COLOR : DOWN_COLOR;
  const pctStr = pct != null ? signedPct(pct, 4) : "—";
  return React.createElement(
    React.Fragment,
    null,
    React.createElement(tooltipComponents.Header, null, `${name} (${code})`),
    React.createElement(tooltipComponents.Bold, { style: { color } }, pctStr),
  );
}

export function renderMemberBarTooltip(
  name: string,
  code: string,
  pct: number | null,
): string {
  return renderReactElement(
    React.createElement(MemberBarTooltip, { name, code, pct }),
  );
}
