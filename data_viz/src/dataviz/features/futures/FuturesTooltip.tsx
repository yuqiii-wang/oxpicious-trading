/**
 * React-based tooltip component for futures charts.
 *
 * Per-date list of contracts TRADING on the hovered date (plus the spot
 * line, with per-contract gap). Expiry dots render their own always-on
 * static label on the chart (see chartOption.ts) — no dot tooltip here.
 *
 * Colors/styles come from the shared theme (chart-palette.ts → colors.css)
 * so every tooltip card in the app renders identically.
 */
import React from "react";
import {
  TOOLTIP_CARD_STYLE,
  TOOLTIP_CARD_HEADER_STYLE,
  TOOLTIP_CARD_TEXT_MUTED,
} from "@/theme/chart-palette";

export interface TooltipItem {
  seriesName: string;
  value: number | null;
  color: string;
  isSpot?: boolean;
  gap?: number | null;
}

interface FuturesTooltipProps {
  date: string;
  items: TooltipItem[];
}

export const FuturesTooltip: React.FC<FuturesTooltipProps> = ({ date, items }) => {
  const validItems = items.filter(
    (it) => it.value != null && typeof it.value === "number" && !Number.isNaN(it.value),
  );
  if (validItems.length === 0) return null;

  return (
    <div style={{ ...TOOLTIP_CARD_STYLE, minWidth: 140 }}>
      <div style={TOOLTIP_CARD_HEADER_STYLE}>{date}</div>
      {validItems.map((item) => (
        <div
          key={item.seriesName}
          style={{
            display: "flex",
            alignItems: "center",
            padding: "2px 0",
            lineHeight: 1.6,
          }}
        >
          <span
            style={{
              display: "inline-block",
              width: 12,
              height: 12,
              borderRadius: 2,
              background: item.color,
              border: "1px solid rgba(0,0,0,0.15)",
              marginRight: 6,
              flexShrink: 0,
            }}
          />
          <span style={{ flex: 1, whiteSpace: "nowrap" }}>
            {item.isSpot ? `${item.seriesName} (spot)` : item.seriesName}
          </span>
          {item.gap !== undefined && item.gap !== null && typeof item.gap === "number" && !Number.isNaN(item.gap) && (
            <span
              style={{
                fontVariantNumeric: "tabular-nums",
                marginLeft: 8,
                fontSize: 11,
                color: TOOLTIP_CARD_TEXT_MUTED,
              }}
            >
              gap {(item.gap >= 0 ? "+" : "")}{item.gap.toFixed(4)}
            </span>
          )}
          <span
            style={{
              fontVariantNumeric: "tabular-nums",
              marginLeft: item.gap !== undefined && item.gap !== null ? 6 : 12,
              fontWeight: 500,
            }}
          >
            {typeof item.value === "number" ? item.value.toFixed(2) : "—"}
          </span>
        </div>
      ))}
    </div>
  );
};

export default FuturesTooltip;
