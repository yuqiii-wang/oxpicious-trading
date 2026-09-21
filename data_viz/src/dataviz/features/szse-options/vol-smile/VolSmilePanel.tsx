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
import { BaseChart, useChartThemeMode } from "@/shared/charts/base-chart";
import type { AiAskSpec } from "@/shared/ai-ask";
import type { OptionsRow } from "@shared/types";
import { fmtNum } from "@/lib/series";
import { computeSmileSkewness } from "@/lib/options-stats";
import { buildSmileOption } from "./smileOption";

interface Props {
  rows: OptionsRow[];
  selectedDate: string;
}

export default function VolSmilePanel({ rows, selectedDate }: Props) {
  const themeMode = useChartThemeMode();
  const snap = rows.filter((r) => r.date === selectedDate);
  const skewness = computeSmileSkewness(snap);
  const option = useMemo(
    () => buildSmileOption(snap, "Volatility Smile", selectedDate, themeMode),
    [snap, selectedDate, themeMode],
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

  // AI Ask — concise legend decoding; per-expiry skewness readout rides in
  // the notes, the card keeps the identity.
  const aiAskSpec = useMemo<AiAskSpec>(
    () => ({
      intro:
        "IV (%) vs moneyness m = K/S — calls solid, puts dashed, grouped by expiry month " +
        "(blue gradient: dark = near). ATM line at m = 1; markers = per-expiry OI-weighted " +
        "smile skewness γ₁ = μ₃/σ³, IV weighted by raw OI count (floored at 1) — premium " +
        "never weights. Indication: fat negative γ₁ = put-wing bid (crash-hedge demand); " +
        "stretched positive = call chase — crowded wings both snap back.",
      instruments: rows[0]?.underlying_code
        ? [{ code: rows[0].underlying_code }]
        : [],
      state: { selectedDate },
      notes: [
        "The skew-over-time + correlation charts for this data live in the shared skew panel (skew-shared/).",
        ...(skewTags
          ? [`Per-expiry OI-wtd skewness (3rd moment): ${skewTags}`]
          : []),
      ],
    }),
    [rows, selectedDate, skewTags],
  );

  return (
    <BaseChart
      title="Volatility Smile · Snapshot"
      subtitle="IV vs Moneyness (Strike/Spot) · blue: dark=near expiry · ATM line + per-expiry skewness markers"
      aiAsk={aiAskSpec}
      height={360}
      option={option}
    />
  );
}
