/**
 * React-based tooltip component for futures charts.
 *
 * Renders a styled tooltip with colored swatches and values using proper
 * React elements instead of HTML string concatenation.
 */
import React from "react";

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
    <div
      style={{
        padding: "6px 10px",
        background: "rgba(255,255,255,0.96)",
        border: "1px solid #ddd",
        borderRadius: 4,
        boxShadow: "0 2px 8px rgba(0,0,0,0.08)",
        fontSize: 12,
        color: "#333",
        minWidth: 140,
      }}
    >
      <div
        style={{
          fontWeight: 600,
          marginBottom: 4,
          fontSize: 12,
          color: "#555",
          borderBottom: "1px solid #eee",
          paddingBottom: 4,
        }}
      >
        {date}
      </div>
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
                color: "#666",
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
