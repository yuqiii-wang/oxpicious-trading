/**
 * Shared skew-over-time chart tooltip — works on the unified
 * SharedSkewPoint[] model for all data sources (oi_moneyness / greek_*).
 */
import React from "react";
import { fmtCompact, fmtNum } from "@/lib/series";
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

/** Signed compact form — explicit "+" for non-negative OI deltas. */
function signedCompact(v: number): string {
  return (v >= 0 ? "+" : "") + fmtCompact(v);
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

  // Absolute OI context: chain-wide total plus each group's share — the
  // curves' thickness encodes the same numbers (peak OI per expiry).
  const oiRows = d.perExpiry.filter((pe) => pe.oiTotal != null);
  if (oiRows.length > 0) {
    const chainOi = oiRows.reduce((s, pe) => s + (pe.oiTotal ?? 0), 0);
    children.push(
      <div key="chain-oi">
        Chain OI: <b>{fmtCompact(chainOi)}</b> contracts
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

      children.push(
        <div key={`pe-${idx}`} style={{ paddingLeft: "8px" }}>
          <ColoredDot color={expiryColorMap.get(pe.expiry) ?? "#888"} />
          {pe.expiry}: <b>{sign}</b>{" "}
          ΔSpot=<b>{gapSpotStr}</b>
          {pe.oiTotal != null && (
            <span style={{ opacity: 0.75 }}>
              {" "}
              · OI {fmtCompact(pe.oiTotal)}
              {pe.oiDelta5d != null && <> · Δ5d {signedCompact(pe.oiDelta5d)}</>}
              {pe.oiDelta20d != null && <> · Δ20d {signedCompact(pe.oiDelta20d)}</>}
              {pe.oiMax20d != null && <> · 20d max {fmtCompact(pe.oiMax20d)}</>}
            </span>
          )}
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
