/**
 * SZSE Options page — interactive mirror of plot_szse_options.py.
 *
 * Per-plot loading (one plot, one SQL — see api/services/options.service.ts
 * "Per-plot queries"): the page loads the light /dates index (date picker +
 * caption), the selected date's /snapshot-rows (wall / smile), the
 * history-wide /snapshot-axes (wall axes) and ONE batched /snapshot-rows
 * fetch for the StatTable's 4 derived dates; the trend, skew, vol-index
 * and chronology panels fetch their own aggregates. No full-history
 * /combined blob (the biggest CFFEX underlying ships ~200MB of JSON
 * through it).
 *
 *   • SnapshotControls — underlying ETF selector + snapshot date picker
 *   • StatTable — 4 auto-derived snapshot columns (Q4 Start / Last Quarter / Last Month / Latest)
 *   • VolSmilePanel — IV smile snapshot for the selected date
 *   • SpotSkewTrendPanel — smile chronology: spot vs ATM IV level vs
 *     25Δ/10Δ risk-reversal skew over time (see docs/options_vol_smile_study.md)
 *   • VolIndexPanel — 30d model-free vol index for the selected underlying
 *     (VIX-style) + the underlying spot close on a right-hand axis
 *   • SharedSkewPanel (oi_moneyness) — OI-wtd moneyness skew over time
 *   • MarketInterestWallPanel — OI wall by expiry for the selected snapshot date
 *   • OptionsTrendPanel — OI bands + P/C Ratio + Total OI (merged card)
 *   • AnnualSentimentPanel — ETF OHLC price & volume (separate card)
 */
import { useEffect, useState } from "react";
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
import VolSmilePanel from "@/dataviz/features/options/VolSmilePanel";
import SpotSkewTrendPanel from "@/dataviz/features/options/spot-skew/SpotSkewTrendPanel";
import VolIndexPanel from "@/dataviz/features/options/vol-index/VolIndexPanel";
import SharedSkewPanel from "@/dataviz/features/options/skew-shared/SharedSkewPanel";
import MarketInterestWallPanel from "@/dataviz/features/options/MarketInterestWallPanel";
import { OptionsTrendPanel } from "@/dataviz/features/options/options-trend";
import { buildOhlcOption } from "@/dataviz/features/options/annual-sentiment";
import OhlcModeToggle from "@/shared/components/OhlcModeToggle";
import {
  fetchEtfOhlcv,
  fetchOptionsSnapshotRows,
  fetchOptionsWalls,
  fetchUnderlyings,
  invalidateCacheForPrefix,
  venueToTargetType,
} from "@/lib/api-client";
import { useStore } from "@/store/filters";
import type {
  EtfOhlcvResponse,
  OptionsUnderlying,
  OptionsWallsResponse,
} from "@shared/types";
import { UNDERLYING_LABELS } from "@/theme/chart-palette";
import { computeSnapshotStats } from "@/lib/options-stats";
import type { OhlcMode } from "@/lib/ohlc";
import {
  useOptionsDates,
  useOptionsSnapshotAxes,
  useOptionsSnapshotRows,
} from "@/dataviz/features/options/data-hooks";

export default function OptionsPage() {
  const underlyingCode = useStore((s) => s.underlyingCode);
  const setUnderlyingCode = useStore((s) => s.setUnderlyingCode);
  const optionsVenue = useStore((s) => s.optionsVenue);
  const themeMode = useChartThemeMode();
  const snapshotDates = useStore((s) => s.snapshotDates);
  const setSnapshotDates = useStore((s) => s.setSnapshotDates);
  const optionsTargetType = venueToTargetType(optionsVenue);

  const [underlyings, setUnderlyings] = useState<OptionsUnderlying[]>([]);
  const [wallsData, setWallsData] = useState<OptionsWallsResponse | null>(null);
  const [ohlcv, setOhlcv] = useState<EtfOhlcvResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedDate, setSelectedDate] = useState<string>("");
  const [ohlcMode, setOhlcMode] = useState<OhlcMode>("percentage");
  const [refreshKey, setRefreshKey] = useState(0);

  // Load underlyings list once (and on refresh / venue change).
  useEffect(() => {
    fetchUnderlyings(optionsTargetType, optionsVenue)
      .then((list) => {
        setUnderlyings(list);
        if (list.length > 0 && !list.some((u) => u.code === underlyingCode)) {
          setUnderlyingCode(list[0].code);
        }
      })
      .catch((e: Error) => setError(e.message));
  }, [optionsVenue, optionsTargetType, refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // Per-plot #1: the trading-date index (date picker + caption). Default
  // the selected date to the latest session once it arrives.
  const datesInfo = useOptionsDates(underlyingCode, optionsTargetType, optionsVenue, refreshKey);
  useEffect(() => {
    setSelectedDate((cur) =>
      cur && datesInfo.dates.includes(cur)
        ? cur
        : datesInfo.dates.length > 0
          ? datesInfo.dates[datesInfo.dates.length - 1]
          : "",
    );
  }, [datesInfo.dates]);

  // Per-plot #2: the selected date's snapshot rows (wall / smile panels).
  const { rows: snapshotRows, loading: snapshotLoading } = useOptionsSnapshotRows(
    underlyingCode,
    selectedDate ? [selectedDate] : [],
    optionsTargetType,
    optionsVenue,
    refreshKey,
  );

  // Per-plot #3: history-wide wall-panel axes — fetched once per
  // underlying/venue (it never varies with the selected date).
  const { axes } = useOptionsSnapshotAxes(underlyingCode, optionsTargetType, optionsVenue, refreshKey);

  // Underlying OHLCV + zone walls — light fetches, optional context for
  // their panels (walls missing ≠ page failure).
  useEffect(() => {
    let cancelled = false;
    setOhlcv(null);
    setWallsData(null);
    fetchEtfOhlcv(underlyingCode, null, null, optionsTargetType)
      .then((ohlc) => {
        if (!cancelled) setOhlcv(ohlc);
      })
      .catch(() => {
        if (!cancelled) setOhlcv(null);
      });
    fetchOptionsWalls(underlyingCode)
      .then((walls) => {
        if (!cancelled) setWallsData(walls);
      })
      .catch(() => {
        if (!cancelled) setWallsData(null);
      });
    return () => {
      cancelled = true;
    };
  }, [underlyingCode, optionsTargetType, refreshKey]);

  const handleRefresh = () => {
    invalidateCacheForPrefix("/api/szse-options/");
    setRefreshKey((k) => k + 1);
  };

  // Auto-derive snapshot dates once the date index arrives (only if all 4 are blank)
  useEffect(() => {
    if (datesInfo.dates.length === 0) return;
    const allBlank = snapshotDates.every((sd) => !sd.date);
    if (allBlank) {
      setSnapshotDates(autoDeriveSnapshots(datesInfo.dates));
    }
  }, [datesInfo.dates, snapshotDates, setSnapshotDates]);

  // Per-plot #4: StatTable's auto-derived snapshot columns — ONE batched
  // snapshot-rows fetch (all 4 dates in a single index-narrow query).
  const [snapshotStats, setSnapshotStats] = useState<
    { label: string; date: string; stats: ReturnType<typeof computeSnapshotStats> }[]
  >([]);
  useEffect(() => {
    if (datesInfo.dates.length === 0 || !underlyingCode) {
      setSnapshotStats([]);
      return;
    }
    let cancelled = false;
    const snaps = autoDeriveSnapshots(datesInfo.dates);
    const statDates = snaps.map((sd) => sd.date).filter(Boolean);
    fetchOptionsSnapshotRows(underlyingCode, statDates, optionsTargetType, optionsVenue, {
      fresh: refreshKey > 0,
    })
      .then((resp) => {
        if (cancelled) return;
        setSnapshotStats(
          snaps.map((sd) => ({
            label: sd.label,
            date: sd.date,
            stats: sd.date
              ? computeSnapshotStats(resp.rows.filter((r) => r.date === sd.date))
              : null,
          })),
        );
      })
      .catch(() => {
        if (!cancelled) setSnapshotStats([]);
      });
    return () => {
      cancelled = true;
    };
  }, [underlyingCode, datesInfo.dates, optionsTargetType, optionsVenue, refreshKey]);

  const underlyingName =
    UNDERLYING_LABELS[underlyingCode] ??
    underlyings.find((u) => u.code === underlyingCode)?.name ??
    underlyingCode;

  const loading = datesInfo.loading;

  return (
    <Stack spacing={2}>
      <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 2, flexWrap: "wrap" }}>
        <Box>
          <Typography variant="h5" sx={{ fontWeight: 700 }}>
            Options
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {underlyingName} ({underlyingCode}) —{" "}
            {optionsVenue === "CFFEX"
              ? "CFFEX index options"
              : optionsVenue === "SSE"
                ? "SSE ETF options"
                : "SZSE ETF options"}{" "}
            analytics
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
        dates={datesInfo.dates}
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
      {datesInfo.error && (
        <Alert severity="error" variant="filled">
          Failed to load options dates: {datesInfo.error}
        </Alert>
      )}
      {!loading && !error && (
        <>
          {datesInfo.dates.length === 0 ? (
            <Alert severity="warning">No options data available.</Alert>
          ) : (
            <>
              <Typography variant="caption" color="text.secondary">
                {datesInfo.rowCount} option contracts · {datesInfo.dates.length} trading days ·{" "}
                {snapshotDates.filter((sd) => sd.date).length}/{snapshotDates.length} snapshots in table ·{" "}
                {datesInfo.dates[0] ?? "—"} → {datesInfo.dates[datesInfo.dates.length - 1] ?? "—"}
              </Typography>

              <StatTable statsList={snapshotStats} />

              {snapshotLoading && (
                <Box sx={{ display: "flex", justifyContent: "center", py: 2 }}>
                  <CircularProgress size={24} />
                </Box>
              )}

              <VolSmilePanel
                rows={snapshotRows}
                selectedDate={selectedDate}
              />
              <SpotSkewTrendPanel
                selectedDate={selectedDate}
                onDateChange={setSelectedDate}
                underlyingCode={underlyingCode}
              />
              <VolIndexPanel />
              <SharedSkewPanel
                mode="oi_moneyness"
                underlying={underlyingCode}
                targetType={optionsTargetType}
                exchange={optionsVenue}
                selectedDate={selectedDate}
                onDateChange={setSelectedDate}
                reloadKey={refreshKey}
              />
              <MarketInterestWallPanel
                rows={snapshotRows}
                selectedDate={selectedDate}
                unifiedStrikes={axes?.strikes ?? []}
                expiryMonths={axes?.expiry_months ?? []}
                underlyingCode={underlyingCode}
              />
              <OptionsTrendPanel
                underlying={underlyingCode}
                targetType={optionsTargetType}
                exchange={optionsVenue}
                walls={wallsData?.rows ?? []}
                reloadKey={refreshKey}
              />

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
