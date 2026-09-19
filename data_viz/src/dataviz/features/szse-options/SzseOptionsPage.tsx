/**
 * SZSE Options page — interactive mirror of plot_szse_options.py.
 *
 *   • SnapshotControls — underlying ETF selector + snapshot date picker
 *   • StatTable — 4 auto-derived snapshot columns (Q4 Start / Last Quarter / Last Month / Latest)
 *   • VolSmilePanel — IV smile snapshot for the selected date
 *   • SpotSkewTrendPanel — smile chronology: spot vs ATM IV level vs
 *     25Δ/10Δ risk-reversal skew over time (see docs/options_vol_smile_study.md)
 *   • VolIndexPanel — 30d model-free vol index for the selected underlying (VIX-style)
 *   • SharedSkewPanel (oi_moneyness) — OI-wtd moneyness skew over time
 *   • MarketInterestWallPanel — OI wall by expiry for the selected snapshot date
 *   • OptionsTrendPanel — OI bands + P/C Ratio + Total OI (merged card)
 *   • AnnualSentimentPanel — ETF OHLC price & volume (separate card)
 */
import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  CircularProgress,
  Stack,
  Typography,
} from "@mui/material";
import SnapshotControls, { autoDeriveSnapshots } from "@/components/SnapshotControls";
import StatTable from "@/components/StatTable";
import RefreshButton from "@/components/RefreshButton";
import { BaseChart, useChartThemeMode } from "@/shared/charts/base-chart";
import VolSmilePanel from "@/dataviz/features/szse-options/VolSmilePanel";
import SpotSkewTrendPanel from "@/dataviz/features/szse-options/spot-skew/SpotSkewTrendPanel";
import VolIndexPanel from "@/dataviz/features/szse-options/vol-index/VolIndexPanel";
import SharedSkewPanel from "@/dataviz/features/szse-options/skew-shared/SharedSkewPanel";
import MarketInterestWallPanel from "@/dataviz/features/szse-options/MarketInterestWallPanel";
import { OptionsTrendPanel } from "@/dataviz/features/szse-options/options-trend";
import { buildOhlcOption } from "@/dataviz/features/szse-options/annual-sentiment";
import OhlcModeToggle from "@/components/OhlcModeToggle";
import {
  fetchEtfOhlcv,
  fetchOptionsCombined,
  fetchOptionsWalls,
  fetchUnderlyings,
  invalidateCacheForPrefix,
} from "@/lib/api-client";
import { useStore } from "@/store/filters";
import type {
  EtfOhlcvResponse,
  OptionsCombinedResponse,
  OptionsUnderlying,
  OptionsWallsResponse,
} from "@shared/types";
import { UNDERLYING_LABELS } from "@/theme/chart-palette";
import { computeSnapshotStats } from "@/lib/options-stats";
import type { OhlcMode } from "@/lib/ohlc";

export default function SzseOptionsPage() {
  const underlyingCode = useStore((s) => s.underlyingCode);
  const setUnderlyingCode = useStore((s) => s.setUnderlyingCode);
  const optionsTargetType = useStore((s) => s.optionsTargetType);
  const themeMode = useChartThemeMode();
  const snapshotDates = useStore((s) => s.snapshotDates);
  const setSnapshotDates = useStore((s) => s.setSnapshotDates);

  const [underlyings, setUnderlyings] = useState<OptionsUnderlying[]>([]);
  const [optionsData, setOptionsData] = useState<OptionsCombinedResponse | null>(null);
  const [wallsData, setWallsData] = useState<OptionsWallsResponse | null>(null);
  const [ohlcv, setOhlcv] = useState<EtfOhlcvResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedDate, setSelectedDate] = useState<string>("");
  const [ohlcMode, setOhlcMode] = useState<OhlcMode>("percentage");
  const [refreshKey, setRefreshKey] = useState(0);

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
      // Zone walls are optional — don't fail the page when the walls table
      // is missing or empty for this underlying.
      fetchOptionsWalls(underlyingCode).catch(() => null),
    ])
      .then(([opts, ohlc, walls]) => {
        if (cancelled) return;
        setOptionsData(opts);
        setOhlcv(ohlc);
        setWallsData(walls);
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
              />
              <SpotSkewTrendPanel
                rows={optionsData.rows}
                selectedDate={selectedDate}
                onDateChange={setSelectedDate}
                underlyingCode={underlyingCode}
              />
              <VolIndexPanel />
              <SharedSkewPanel
                mode="oi_moneyness"
                rows={optionsData.rows}
                selectedDate={selectedDate}
                onDateChange={setSelectedDate}
              />
              <MarketInterestWallPanel rows={optionsData.rows} selectedDate={selectedDate} />
              <OptionsTrendPanel rows={optionsData.rows} walls={wallsData?.rows ?? []} />

              <BaseChart
                title="ETF Price & Volume"
                subtitle="OHLC + volume (price-up green / price-down red)"
                height={340}
                headerAction={<OhlcModeToggle value={ohlcMode} onChange={setOhlcMode} />}
                option={
                  ohlcv && ohlcv.rows.length > 0
                    ? buildOhlcOption(ohlcv, themeMode, ohlcMode)
                    : null
                }
                emptyText="No ETF OHLCV data available for this underlying."
              />
            </>
          )}
        </>
      )}
    </Stack>
  );
}
