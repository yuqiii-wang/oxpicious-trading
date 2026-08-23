/**
 * Volatility Smile panel for Options Analysis page.
 *
 *   • Snapshot smile chart (base VolSmilePanel — IV vs moneyness for the
 *     selected date).
 *   • SharedSkewPanel in iv_smile mode — IV smile skewness over time
 *     (rebased to price space, with expiry shade bands) + the
 *     skewness–spot correlation from analysis.options_skewness_stats
 *     (skew_type='iv_smile').
 *   • IV skew chart — 25Δ risk reversal per expiry group from
 *     analysis.options_iv_skew_stats, with a Daily / MA5 / MA20 / MA60
 *     toggle.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { ToggleButton, ToggleButtonGroup } from "@mui/material";
import VolSmilePanel from "@/dataviz/features/szse-options/VolSmilePanel";
import SharedSkewPanel from "@/dataviz/features/szse-options/skew-shared/SharedSkewPanel";
import ChartCard from "@/components/ChartCard";
import EChart from "@/components/EChart";
import { fetchOptionsIvSkew } from "@/lib/api-client/options";
import {
  buildIvSkewTimeSeriesOption,
  ivSkewAxisDates,
  type IvSkewMode,
} from "@/dataviz/features/szse-options/vol-smile/ivSkewTimeSeriesOption";
import type { OptionsRow, IvSkewRow } from "@shared/types";
import type { EChartsOption } from "echarts";

interface Props {
  rows: OptionsRow[];
  selectedDate: string;
  onDateChange?: (date: string) => void;
}

const IV_SKEW_MODES: { value: IvSkewMode; label: string }[] = [
  { value: "daily", label: "Daily" },
  { value: "ma5", label: "MA5" },
  { value: "ma20", label: "MA20" },
  { value: "ma60", label: "MA60" },
];

export default function AnalysisVolSmilePanel({
  rows,
  selectedDate,
  onDateChange,
}: Props) {
  const underlyingCode = rows[0]?.underlying_code ?? "";
  const [ivSkewRows, setIvSkewRows] = useState<IvSkewRow[]>([]);
  const [ivSkewMode, setIvSkewMode] = useState<IvSkewMode>("daily");

  useEffect(() => {
    if (!underlyingCode) return;
    const dates = rows.map((r) => r.date).sort();
    const startDate = dates[0] ?? undefined;
    const endDate = dates[dates.length - 1] ?? undefined;
    let cancelled = false;

    fetchOptionsIvSkew(underlyingCode, startDate, endDate)
      .then((ivSkewResp) => {
        if (cancelled) return;
        setIvSkewRows(ivSkewResp.rows);
      })
      .catch(() => {
        if (!cancelled) setIvSkewRows([]);
      });

    return () => { cancelled = true; };
  }, [underlyingCode, rows.length]);

  const ivSkewOption: EChartsOption | null = useMemo(() => {
    if (ivSkewRows.length === 0) return null;
    return buildIvSkewTimeSeriesOption(ivSkewRows, selectedDate, ivSkewMode);
  }, [ivSkewRows, selectedDate, ivSkewMode]);

  const ivSkewDates = useMemo(() => ivSkewAxisDates(ivSkewRows), [ivSkewRows]);

  const handleIvSkewModeChange = useCallback(
    (_e: React.MouseEvent<HTMLElement>, newMode: IvSkewMode | null) => {
      if (newMode) setIvSkewMode(newMode);
    },
    [],
  );

  const handleIvSkewCanvasClick = useCallback(
    (dataIndex: number) => {
      if (!onDateChange) return;
      const date = dataIndex < ivSkewDates.length ? ivSkewDates[dataIndex] : undefined;
      if (date) onDateChange(date);
    },
    [ivSkewDates, onDateChange],
  );

  const toggleGroupSx = {
    bgcolor: "background.paper",
    "& .MuiToggleButton-root": {
      px: 1.5,
      py: 0.25,
      fontSize: "0.7rem",
      minWidth: 48,
    },
  } as const;

  return (
    <>
      <VolSmilePanel rows={rows} selectedDate={selectedDate} />
      <SharedSkewPanel
        mode="iv_smile"
        rows={rows}
        selectedDate={selectedDate}
        onDateChange={onDateChange}
      />
      {ivSkewOption ? (
        <ChartCard
          title="IV Skew · 25Δ Risk Reversal (Call − Put)"
          subtitle="From analysis.options_iv_skew_stats (premium-calibrated implied vol). Negative = OTM puts richer = downside hedging demand. Thin lines: per-expiry-month groups · Thick line: mean across groups · Click to select date."
          height={280}
        >
          <div style={{ position: "relative" }}>
            <div style={{
              position: "absolute",
              top: 0,
              right: 8,
              zIndex: 10,
            }}>
              <ToggleButtonGroup
                value={ivSkewMode}
                exclusive
                onChange={handleIvSkewModeChange}
                size="small"
                sx={toggleGroupSx}
              >
                {IV_SKEW_MODES.map((m) => (
                  <ToggleButton key={m.value} value={m.value}>
                    {m.label}
                  </ToggleButton>
                ))}
              </ToggleButtonGroup>
            </div>
            <EChart
              option={ivSkewOption}
              height={240}
              onCanvasClick={handleIvSkewCanvasClick}
            />
          </div>
        </ChartCard>
      ) : null}
    </>
  );
}
