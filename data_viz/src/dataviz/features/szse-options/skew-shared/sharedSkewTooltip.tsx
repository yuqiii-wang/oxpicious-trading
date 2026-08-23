/**
 * Shared skew-over-time chart tooltip — works on the unified
 * SharedSkewPoint[] model for both data sources (oi_moneyness / iv_smile).
 */
import React from "react";
import { fmtNum } from "@/lib/series";
import { renderTooltip } from "../vol-smile/renderTooltip";
import type { SharedSkewPoint } from "./types";

interface SharedSkewTooltipData {
  date: string;
  seriesItems: Array<{
    seriesName: string;
    value: [string, number | null];
    marker?: string;
    color?: string;
  }>;
  points: SharedSkewPoint[];
  expiryColorMap: Map<string, string>;
}

function ColoredDot({ color }: { color: string }) {
  return (
    <span
      style={{
        display: "inline-block",
        width: 10,
        height: 10,
        borderRadius: "50%",
        backgroundColor: color,
        marginRight: 5,
        verticalAlign: "middle",
        border: "1px solid rgba(0,0,0,0.2)",
      }}
    />
  );
}

function SharedSkewTooltipContent({
  date,
  seriesItems,
  points,
  expiryColorMap,
}: SharedSkewTooltipData) {
  const d = points.find((s) => s.date === date);

  const children: React.ReactNode[] = [<b key="date">{date}</b>];

  for (const item of seriesItems) {
    const val = item.value[1];
    children.push(
      <div key={item.seriesName}>
        {item.color ? <ColoredDot color={item.color} /> : item.marker ?? ""}
        {item.seriesName}: <b>{fmtNum(val as number)}</b>
      </div>,
    );
  }

  if (!d) return <React.Fragment>{children}</React.Fragment>;

  if (d.skewPct != null && Number.isFinite(d.skewPct)) {
    children.push(
      <div key="agg-skew">
        Skew Δ (agg):{" "}
        <b>
          {d.skewPct >= 0 ? "+" : ""}
          {fmtNum(d.skewPct, 2)}%
        </b>
      </div>,
    );
  }

  if (d.perExpiry.length > 0) {
    children.push(
      <div key="per-expiry-label" style={{ opacity: 0.7 }}>
        Per-expiry (ΔSpot):
      </div>,
    );

    d.perExpiry.forEach((pe, idx) => {
      const spot = d.spot;
      const sk = pe.skewPrice;
      let gapSpotStr: React.ReactNode = "—";
      if (sk != null && Number.isFinite(sk)) {
        gapSpotStr = fmtNum(spot - (sk as number), 2);
      }

      const sign =
        pe.skewPct != null && Number.isFinite(pe.skewPct)
          ? (pe.skewPct >= 0 ? "+" : "") + fmtNum(pe.skewPct, 2)
          : "—";

      const crossCount = pe.countSkewnessCurveCrossedSpot;
      const crossCountStr =
        crossCount != null && crossCount > 0 ? ` ×${crossCount}` : "";

      children.push(
        <div key={`pe-${idx}`} style={{ paddingLeft: "8px" }}>
          <ColoredDot color={expiryColorMap.get(pe.expiry) ?? "#888"} />
          {pe.expiry}: <b>{sign}</b>{" "}
          ΔSpot=<b>{gapSpotStr}</b>
          {crossCountStr ? (
            <span style={{ color: "#888", fontSize: 10 }}>{crossCountStr}</span>
          ) : null}
        </div>,
      );
    });
  }

  return <React.Fragment>{children}</React.Fragment>;
}

export function makeSharedSkewTooltipFormatter(
  points: SharedSkewPoint[],
  expiryColorMap: Map<string, string>,
): (params: unknown) => string {
  return (p: unknown): string => {
    const items = (Array.isArray(p) ? p : [p]) as Array<{
      seriesName: string;
      value: [string, number | null];
      marker?: string;
      color?: string;
    }>;
    if (items.length === 0) return "";
    const date = items[0].value[0];
    return renderTooltip(
      <SharedSkewTooltipContent
        date={date}
        seriesItems={items}
        points={points}
        expiryColorMap={expiryColorMap}
      />,
    );
  };
}
