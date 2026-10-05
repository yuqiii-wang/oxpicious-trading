/**
 * Volatility Smile panel for Options Analysis page.
 *
 *   • Snapshot smile chart (base VolSmilePanel — IV vs moneyness for the
 *     selected date).
 *   • SpotSkewTrendPanel — smile chronology: spot vs smile level (ATM
 *     IV, VIX analog) vs smile tilt (25Δ/10Δ risk reversal, SKEW
 *     analog) over time — loads its own /skew-daily aggregate — see
 *     docs/options_vol_smile_study.md.
 *   • VolIndexPanel — 30d model-free vol index (VIX-style) for the
 *     selected underlying (store-driven, single curve).
 */
import VolSmilePanel from "@/dataviz/features/options/VolSmilePanel";
import SpotSkewTrendPanel from "@/dataviz/features/options/spot-skew/SpotSkewTrendPanel";
import VolIndexPanel from "@/dataviz/features/options/vol-index/VolIndexPanel";
import type { OptionsRow } from "@shared/types";

interface Props {
  rows: OptionsRow[];
  underlyingCode: string;
  selectedDate: string;
  onDateChange?: (date: string) => void;
}

export default function AnalysisVolSmilePanel({
  rows,
  underlyingCode,
  selectedDate,
  onDateChange,
}: Props) {
  return (
    <>
      <VolSmilePanel rows={rows} selectedDate={selectedDate} />
      <SpotSkewTrendPanel
        selectedDate={selectedDate}
        onDateChange={onDateChange}
        underlyingCode={underlyingCode}
      />
      <VolIndexPanel />
    </>
  );
}
