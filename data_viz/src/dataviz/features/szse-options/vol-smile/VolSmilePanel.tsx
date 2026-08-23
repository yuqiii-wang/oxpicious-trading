/**
 * Volatility Smile panel — snapshot-only chart for the selected date.
 *
 * IV (%) vs moneyness (Strike/Spot) for CALL and PUT, grouped by expiry
 * month, with an ATM vertical line at moneyness=1.0 and per-expiry
 * OI-weighted skewness (3rd standardized moment) markers.
 *
 * The skew-over-time + correlation charts moved to the shared
 * SharedSkewPanel (skew-shared/), which renders the same layout for both
 * data sources (oi_moneyness / iv_smile).
 */
import { useMemo } from "react";
import ChartCard from "@/components/ChartCard";
import EChart from "@/components/EChart";
import type { OptionsRow } from "@shared/types";
import { fmtNum } from "@/lib/series";
import { computeSmileSkewness } from "@/lib/options-stats";
import { buildSmileOption } from "./smileOption";

interface Props {
  rows: OptionsRow[];
  selectedDate: string;
}

export default function VolSmilePanel({ rows, selectedDate }: Props) {
  const snap = rows.filter((r) => r.date === selectedDate);
  const skewness = computeSmileSkewness(snap);
  const option = useMemo(
    () => buildSmileOption(snap, "Volatility Smile", selectedDate),
    [snap, selectedDate],
  );

  const skewTags = skewness
    .map((s) => {
      const parts: string[] = [s.expiry];
      if (s.overallSkew != null && Number.isFinite(s.overallSkew)) {
        parts.push(`${s.overallSkew >= 0 ? "+" : ""}${fmtNum(s.overallSkew, 2)}`);
      }
      if (s.callSkew != null && Number.isFinite(s.callSkew)) {
        parts.push(`C${s.callSkew >= 0 ? "+" : ""}${fmtNum(s.callSkew, 2)}`);
      }
      if (s.putSkew != null && Number.isFinite(s.putSkew)) {
        parts.push(`P${s.putSkew >= 0 ? "+" : ""}${fmtNum(s.putSkew, 2)}`);
      }
      return parts.join(" ");
    })
    .join("  |  ");

  return (
    <ChartCard
      title="Volatility Smile · Snapshot"
      subtitle={
        skewTags
          ? `IV vs Moneyness · Blue gradient (dark=near expiry, light=far) · ATM (Moneyness=1) + Skewness markers · Per-expiry OI-wtd skewness (3rd moment): ${skewTags}`
          : "IV vs Moneyness (Strike/Spot) · CALL (solid) / PUT (dashed) · ATM + Skewness markers"
      }
      height={400}
    >
      <EChart option={option} height={360} />
    </ChartCard>
  );
}
