/**
 * SZSE Options page — interactive mirror of plot_szse_options.py.
 *
 *   • SnapshotControls — underlying ETF selector + snapshot date picker
 *   • StatTable — 4 auto-derived snapshot columns (Q4 Start / Last Quarter / Last Month / Latest)
 *   • VolSmilePanel — IV smile for the selected snapshot date + corr chart
 *   • MarketInterestWallPanel — OI wall by expiry for the selected snapshot date
 *   • OptionsTrendPanel — OI bands + P/C Ratio + Total OI (merged card)
 *   • AnnualSentimentPanel — ETF OHLC price & volume (separate card)
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  CircularProgress,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import SnapshotControls, { autoDeriveSnapshots } from "@/components/SnapshotControls";
import StatTable from "@/components/StatTable";
import RefreshButton from "@/components/RefreshButton";
import VolSmilePanel from "@/dataviz/features/szse-options/VolSmilePanel";
import MarketInterestWallPanel from "@/dataviz/features/szse-options/MarketInterestWallPanel";
import { OptionsTrendPanel } from "@/dataviz/features/szse-options/options-trend";
import { buildOhlcOption } from "@/dataviz/features/szse-options/annual-sentiment";
import ChartCard from "@/components/ChartCard";
import EChart from "@/components/EChart";
import OhlcModeToggle from "@/components/OhlcModeToggle";
import {
  fetchEtfOhlcv,
  fetchOptionsCombined,
  fetchUnderlyings,
  invalidateCacheForPrefix,
} from "@/lib/api-client";
import {
  fetchOptionsSkewnessCorr,
  fetchOptionsSkewnessCrossCounts,
} from "@/lib/api-client/options";
import { computeDailySkewSeries } from "@/dataviz/features/szse-options/vol-smile/skewSeries";
import {
  buildCorrTimeSeriesOption,
  type CorrMode,
} from "@/dataviz/features/szse-options/vol-smile/corrTimeSeriesOption";
import { useStore } from "@/store/filters";
import type {
  EtfOhlcvResponse,
  OptionsCombinedResponse,
  OptionsUnderlying,
  SkewnessCorrRow,
  SkewnessCrossCountRow,
} from "@shared/types";
import { UNDERLYING_LABELS } from "@/theme/chart-palette";
import { computeSnapshotStats } from "@/lib/options-stats";
import type { OhlcMode } from "@/lib/ohlc";
import type { EChartsOption } from "echarts";

const CORR_MODES: { value: CorrMode; label: string }[] = [
  { value: "ma5", label: "MA5" },
  { value: "ma20", label: "MA20" },
  { value: "ma60", label: "MA60" },
];

export default function SzseOptionsPage() {
  const underlyingCode = useStore((s) => s.underlyingCode);
  const setUnderlyingCode = useStore((s) => s.setUnderlyingCode);
  const optionsTargetType = useStore((s) => s.optionsTargetType);
  const themeMode = useStore((s) => s.themeMode);
  const snapshotDates = useStore((s) => s.snapshotDates);
  const setSnapshotDates = useStore((s) => s.setSnapshotDates);

  const [underlyings, setUnderlyings] = useState<OptionsUnderlying[]>([]);
  const [optionsData, setOptionsData] = useState<OptionsCombinedResponse | null>(null);
  const [ohlcv, setOhlcv] = useState<EtfOhlcvResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedDate, setSelectedDate] = useState<string>("");
  const [ohlcMode, setOhlcMode] = useState<OhlcMode>("percentage");
  const [refreshKey, setRefreshKey] = useState(0);

  // Corr chart state
  const [corrRows, setCorrRows] = useState<SkewnessCorrRow[]>([]);
  const [corrMode, setCorrMode] = useState<CorrMode>("ma5");

  // Cross counts state
  const [crossCountRows, setCrossCountRows] = useState<SkewnessCrossCountRow[]>([]);

  // Load underlyings list once (and on refresh / target-type change).
  useEffect(() => {
    fetchUnderlyings(optionsTargetType)
      .then((list) => {
        setUnderlyings(list);
        if (list.length > 0 && !list.some((u) => u.code === underlyingCode)) {
          setUnderlyingCode(list[0].code);
        }
      })
      .catch((e: Error) => setError(e.message));
  }, [optionsTargetType, refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // Load ALL options + OHLCV data (no date filter — trend plots need full history)
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([
      fetchOptionsCombined(underlyingCode, null, null, optionsTargetType),
      fetchEtfOhlcv(underlyingCode, null, null, optionsTargetType),
    ])
      .then(([opts, ohlc]) => {
        if (cancelled) return;
        setOptionsData(opts);
        setOhlcv(ohlc);
        setSelectedDate(opts.dates.length > 0 ? opts.dates[opts.dates.length - 1] : "");
        setLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message);
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [underlyingCode, optionsTargetType, refreshKey]);

  // Fetch corr data for the correlation chart + cross counts
  useEffect(() => {
    if (!underlyingCode || !optionsData) return;
    const dates = optionsData.dates;
    if (dates.length === 0) return;
    const startDate = dates[0];
    const endDate = dates[dates.length - 1];
    let cancelled = false;

    Promise.all([
      fetchOptionsSkewnessCorr(underlyingCode, startDate, endDate),
      fetchOptionsSkewnessCrossCounts(underlyingCode, startDate, endDate),
    ])
      .then(([corrResp, crossResp]) => {
        if (cancelled) return;
        setCorrRows(corrResp.rows);
        setCrossCountRows(crossResp.rows);
      })
      .catch(() => {
        if (!cancelled) {
          setCorrRows([]);
          setCrossCountRows([]);
        }
      });

    return () => { cancelled = true; };
  }, [underlyingCode, optionsData]);

  const handleRefresh = () => {
    invalidateCacheForPrefix("/api/szse-options/");
    setRefreshKey((k) => k + 1);
  };

  // Auto-derive snapshot dates once options data arrives (only if all 4 are blank)
  useEffect(() => {
    if (!optionsData || optionsData.dates.length === 0) return;
    const allBlank = snapshotDates.every((sd) => !sd.date);
    if (allBlank) {
      setSnapshotDates(autoDeriveSnapshots(optionsData.dates));
    }
  }, [optionsData, snapshotDates, setSnapshotDates]);

  const snapshotStats = useMemo(() => {
    if (!optionsData) return [];
    return snapshotDates.map((sd) => {
      const snap = optionsData.rows.filter((r) => r.date === sd.date);
      return {
        label: sd.label,
        date: sd.date,
        stats: computeSnapshotStats(snap),
      };
    });
  }, [optionsData, snapshotDates]);

  const underlyingName =
    UNDERLYING_LABELS[underlyingCode] ??
    underlyings.find((u) => u.code === underlyingCode)?.name ??
    underlyingCode;

  // Correlation chart option
  const dailySkewSeries = useMemo(
    () => (optionsData ? computeDailySkewSeries(optionsData.rows, crossCountRows) : []),
    [optionsData, crossCountRows],
  );

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

  return (
    <Stack spacing={2}>
      <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 2, flexWrap: "wrap" }}>
        <Box>
          <Typography variant="h5" sx={{ fontWeight: 700 }}>
            Options
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {underlyingName} ({underlyingCode}) —{" "}
            {optionsTargetType === "INDEX" ? "CFFEX index options" : "SZSE ETF options"} analytics
            dashboard
          </Typography>
        </Box>
        <RefreshButton
          onClick={handleRefresh}
          loading={loading}
          label="Refresh"
          tooltip="Refresh options + OHLCV + underlyings (bypass cache)"
        />
      </Box>

      <SnapshotControls
        underlyings={underlyings}
        dates={optionsData?.dates ?? []}
        selectedDate={selectedDate}
        onSelectedDateChange={setSelectedDate}
      />

      {loading && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
          <CircularProgress size={32} />
        </Box>
      )}
      {error && (
        <Alert severity="error" variant="filled">
          Failed to load options data: {error}
        </Alert>
      )}
      {!loading && !error && optionsData && (
        <>
          {optionsData.rows.length === 0 ? (
            <Alert severity="warning">No options data available.</Alert>
          ) : (
            <>
              <Typography variant="caption" color="text.secondary">
                {optionsData.rows.length} option contracts · {optionsData.dates.length} trading days ·{" "}
                {snapshotDates.filter((sd) => sd.date).length}/{snapshotDates.length} snapshots in table ·{" "}
                {optionsData.dates[0] ?? "—"} → {optionsData.dates[optionsData.dates.length - 1] ?? "—"}
              </Typography>

              <StatTable statsList={snapshotStats} />

              <VolSmilePanel
                rows={optionsData.rows}
                selectedDate={selectedDate}
                onDateChange={setSelectedDate}
                crossCounts={crossCountRows}
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
              <MarketInterestWallPanel rows={optionsData.rows} selectedDate={selectedDate} />
              <OptionsTrendPanel rows={optionsData.rows} />

              <ChartCard
                title="ETF Price & Volume"
                subtitle="OHLC + volume (price-up green / price-down red)"
                height={360}
                action={<OhlcModeToggle value={ohlcMode} onChange={setOhlcMode} />}
              >
                {ohlcv && ohlcv.rows.length > 0 ? (
                  <EChart
                    option={buildOhlcOption(ohlcv, themeMode, ohlcMode)}
                    height={340}
                  />
                ) : (
                  <Alert severity="info">No ETF OHLCV data available for this underlying.</Alert>
                )}
              </ChartCard>
            </>
          )}
        </>
      )}
    </Stack>
  );
}
