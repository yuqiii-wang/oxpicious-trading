/**
 * Volatility Smile panel for Options Analysis page.
 *
 *   • Snapshot smile chart (base VolSmilePanel — IV vs moneyness for the
 *     selected date).
 *   • SpotSkewTrendPanel — smile chronology: spot vs smile level (ATM
 *     IV, VIX analog) vs smile tilt (25Δ/10Δ risk reversal, SKEW
 *     analog) over time — see docs/options_vol_smile_study.md.
 *   • VolIndexPanel — 30d model-free vol index (VIX-style) for the
 *     selected underlying (store-driven, single curve).
 */
import VolSmilePanel from "@/dataviz/features/szse-options/VolSmilePanel";
import SpotSkewTrendPanel from "@/dataviz/features/szse-options/spot-skew/SpotSkewTrendPanel";
import VolIndexPanel from "@/dataviz/features/szse-options/vol-index/VolIndexPanel";
import type { OptionsRow } from "@shared/types";

interface Props {
  rows: OptionsRow[];
  selectedDate: string;
  onDateChange?: (date: string) => void;
}

export default function AnalysisVolSmilePanel({
  rows,
  selectedDate,
  onDateChange,
}: Props) {
  const underlyingCode = rows[0]?.underlying_code ?? "";

  return (
    <>
      <VolSmilePanel rows={rows} selectedDate={selectedDate} />
      <SpotSkewTrendPanel
        rows={rows}
        selectedDate={selectedDate}
        onDateChange={onDateChange}
        underlyingCode={underlyingCode}
      />
      <VolIndexPanel />
    </>
  );
}
