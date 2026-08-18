import React from "react";
import { fmtNum } from "@/lib/series";
import { renderTooltip } from "./renderTooltip";
import type { DailySkew, ExpiryGapsMap } from "./types";
import { expiryToYyyyMm } from "./expiryUtils";
import type { ExpiryGapRow } from "@shared/types";

interface SkewTooltipData {
  date: string;
  seriesItems: Array<{ seriesName: string; value: [string, number | null]; marker?: string; color?: string }>;
  dailySkew: DailySkew[];
  gapsMap: ExpiryGapsMap | null;
  allExpiries: Map<string, string>;
  expiryGapMap: Map<string, { max: number; min: number }>;
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

function SkewTooltipContent({
  date,
  seriesItems,
  dailySkew,
  gapsMap,
  allExpiries,
  expiryGapMap,
  expiryColorMap,
}: SkewTooltipData) {
  const d = dailySkew.find((s) => s.date === date);

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
        Per-expiry (ΔSpot / ↓Min / ↑Max):
      </div>,
    );

    d.perExpiry.forEach((pe, idx) => {
      const dbGap = gapsMap ? gapsMap.get(`${date}|${pe.expiry}`) : null;
      const expDate = allExpiries.get(pe.expiry) ?? pe.expiryDate ?? "";
      const maturedByDate = !!(expDate && expDate <= date);
      const hasDbMinMax = !!(
        dbGap &&
        dbGap.today_gap_from_max_before_expiry != null &&
        Number.isFinite(dbGap.today_gap_from_max_before_expiry) &&
        dbGap.today_gap_from_min_before_expiry != null &&
        Number.isFinite(dbGap.today_gap_from_min_before_expiry)
      );

      let gapSpotStr: React.ReactNode = "—";
      let gapMinStr: React.ReactNode = "—";
      let gapMaxStr: React.ReactNode = "—";

      if (
        dbGap &&
        dbGap.today_gap_from_today_spot != null &&
        Number.isFinite(dbGap.today_gap_from_today_spot)
      ) {
        gapSpotStr = fmtNum(dbGap.today_gap_from_today_spot, 2);
      } else {
        const spot = d.S;
        const sk = pe.skewPrice;
        if (sk != null && Number.isFinite(sk)) {
          gapSpotStr = fmtNum(spot - (sk as number), 2);
        }
      }

      if (hasDbMinMax) {
        gapMinStr = fmtNum((dbGap as ExpiryGapRow).today_gap_from_min_before_expiry!, 2);
        gapMaxStr = fmtNum(
          -(dbGap as ExpiryGapRow).today_gap_from_max_before_expiry!,
          2,
        );
      } else if (maturedByDate) {
        const gapInfo = expiryGapMap.get(pe.expiry);
        const sk = pe.skewPrice;
        if (gapInfo && sk != null && Number.isFinite(sk)) {
          gapMinStr = fmtNum((sk as number) - gapInfo.min, 2);
          gapMaxStr = fmtNum(gapInfo.max - (sk as number), 2);
        }
      } else {
        gapMinStr = <span style={{ opacity: 0.5 }}>future</span>;
        gapMaxStr = <span style={{ opacity: 0.5 }}>future</span>;
      }

      const sign = pe.skewPct != null && Number.isFinite(pe.skewPct)
        ? (pe.skewPct >= 0 ? "+" : "") + fmtNum(pe.skewPct, 2)
        : "—";

      children.push(
        <div key={`pe-${idx}`} style={{ paddingLeft: "8px" }}>
          <ColoredDot color={expiryColorMap.get(pe.expiry) ?? "#888"} />
          {pe.expiry}: <b>{sign}</b>{" "}
          ΔSpot=<b>{gapSpotStr}</b> ↓=<b>{gapMinStr}</b> ↑=<b>{gapMaxStr}</b>
        </div>,
      );
    });
  }

  return <React.Fragment>{children}</React.Fragment>;
}

export function makeSkewTooltipFormatter(
  dailySkew: DailySkew[],
  gapsMap: ExpiryGapsMap | null,
  allExpiries: Map<string, string>,
  expiryGapMap: Map<string, { max: number; min: number }>,
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
      <SkewTooltipContent
        date={date}
        seriesItems={items}
        dailySkew={dailySkew}
        gapsMap={gapsMap}
        allExpiries={allExpiries}
        expiryGapMap={expiryGapMap}
        expiryColorMap={expiryColorMap}
      />,
    );
  };
}

export { expiryToYyyyMm };
