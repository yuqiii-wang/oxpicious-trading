/**
 * Volatility Smile panel for Options Analysis page.
 *
 * Wraps the base VolSmilePanel with precomputed expiry gap data from
 * analysis.options_stats_before_expiry and skewness correlation data
 * from analysis.options_skewness_stats.
 *
 * The gaps (ΔSpot / ↓Min / ↑Max) are fetched once via the API and
 * passed as a gapsMap prop to the base panel, which renders them in
 * the tooltip. The corr data (whole-period correlation between
 * skewness and spot) is rendered as a 3rd chart below the skew time
 * series, with a toggle to switch between Daily / MA5 / MA20 / MA60.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { ToggleButton, ToggleButtonGroup } from "@mui/material";
import VolSmilePanel, {
  expiryToYyyyMm,
  type ExpiryGapsMap,
} from "@/dataviz/features/szse-options/VolSmilePanel";
import {
  fetchOptionsExpiryGaps,
  fetchOptionsSkewnessCorr,
} from "@/lib/api-client/options";
import { computeDailySkewSeries } from "@/dataviz/features/szse-options/vol-smile/skewSeries";
import {
  buildCorrTimeSeriesOption,
  type CorrMode,
} from "@/dataviz/features/szse-options/vol-smile/corrTimeSeriesOption";
import type { OptionsRow, SkewnessCorrRow } from "@shared/types";
import type { EChartsOption } from "echarts";

interface Props {
  rows: OptionsRow[];
  selectedDate: string;
  onDateChange?: (date: string) => void;
}

const CORR_MODES: { value: CorrMode; label: string }[] = [
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
  const [gapsMap, setGapsMap] = useState<ExpiryGapsMap | null>(null);
  const [corrRows, setCorrRows] = useState<SkewnessCorrRow[]>([]);
  const [corrMode, setCorrMode] = useState<CorrMode>("ma5");

  useEffect(() => {
    if (!underlyingCode) return;
    const dates = rows.map((r) => r.date).sort();
    const startDate = dates[0] ?? undefined;
    const endDate = dates[dates.length - 1] ?? undefined;
    let cancelled = false;

    Promise.all([
      fetchOptionsExpiryGaps(underlyingCode, startDate, endDate),
      fetchOptionsSkewnessCorr(underlyingCode, startDate, endDate),
    ])
      .then(([gapsResp, corrResp]) => {
        if (cancelled) return;

        const map = new Map<string, typeof gapsResp.rows[number]>();
        for (const g of gapsResp.rows) {
          const key = `${g.date}|${expiryToYyyyMm(g.expiry_date)}`;
          map.set(key, g);
        }
        setGapsMap(map as unknown as ExpiryGapsMap);
        setCorrRows(corrResp.rows);
      })
      .catch(() => {
        if (!cancelled) {
          setGapsMap(null);
          setCorrRows([]);
        }
      });

    return () => { cancelled = true; };
  }, [underlyingCode, rows.length]);

  const dailySkewSeries = useMemo(() => computeDailySkewSeries(rows), [rows]);

  const corrOption: EChartsOption | null = useMemo(() => {
    if (corrRows.length === 0 || dailySkewSeries.length === 0) return null;
    return buildCorrTimeSeriesOption(
      dailySkewSeries, corrRows, selectedDate, corrMode,
    );
  }, [corrRows, dailySkewSeries, selectedDate, corrMode]);

  const handleCorrModeChange = useCallback(
    (_e: React.MouseEvent<HTMLElement>, newMode: CorrMode | null) => {
      if (newMode) setCorrMode(newMode);
    },
    [],
  );

  const handleCanvasClick = useCallback(
    (dataIndex: number) => {
      if (!onDateChange) return;
      const entry =
        dataIndex < dailySkewSeries.length ? dailySkewSeries[dataIndex] : undefined;
      if (entry) onDateChange(entry.date);
    },
    [dailySkewSeries, onDateChange],
  );

  return (
    <VolSmilePanel
      rows={rows}
      selectedDate={selectedDate}
      onDateChange={onDateChange}
      gapsMap={gapsMap}
      bottomChartOption={corrOption}
      bottomChartHeight={200}
      bottomChartControls={
        <ToggleButtonGroup
          value={corrMode}
          exclusive
          onChange={handleCorrModeChange}
          size="small"
          sx={{
            bgcolor: "background.paper",
            "& .MuiToggleButton-root": {
              px: 1.5,
              py: 0.25,
              fontSize: "0.7rem",
              minWidth: 48,
            },
          }}
        >
          {CORR_MODES.map((m) => (
            <ToggleButton key={m.value} value={m.value}>
              {m.label}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
      }
    />
  );
}
