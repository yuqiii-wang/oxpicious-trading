/**
 * SZSE Options page — interactive mirror of plot_szse_options.py.
 *
 *   • SnapshotControls — underlying ETF selector + snapshot date picker
 *   • StatTable — 4 auto-derived snapshot columns (Q4 Start / Last Quarter / Last Month / Latest)
 *   • VolSmilePanel — IV smile for the selected snapshot date
 *   • MarketInterestWallPanel — OI wall by expiry for the selected snapshot date
 *   • AnnualSentimentPanel — trend plots (uses ECharts dataZoom sliders for date range)
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
import VolSmilePanel from "@/dataviz/features/szse-options/VolSmilePanel";
import MarketInterestWallPanel from "@/dataviz/features/szse-options/MarketInterestWallPanel";
import ExpiryOiBandsPanel from "@/dataviz/features/szse-options/ExpiryOiBandsPanel";
import AnnualSentimentPanel from "@/dataviz/features/szse-options/AnnualSentimentPanel";
import {
  fetchEtfOhlcv,
  fetchOptionsCombined,
  fetchUnderlyings,
  invalidateCacheForPrefix,
} from "@/lib/api-client";
import { useStore } from "@/store/filters";
import type {
  EtfOhlcvResponse,
  OptionsCombinedResponse,
  OptionsUnderlying,
} from "../../../../shared/types";
import { UNDERLYING_LABELS } from "@/theme/chart-palette";
import { computeSnapshotStats } from "@/lib/options-stats";

export default function SzseOptionsPage() {
  const underlyingCode = useStore((s) => s.underlyingCode);
  const setUnderlyingCode = useStore((s) => s.setUnderlyingCode);
  const optionsTargetType = useStore((s) => s.optionsTargetType);
  const snapshotDates = useStore((s) => s.snapshotDates);
  const setSnapshotDates = useStore((s) => s.setSnapshotDates);

  const [underlyings, setUnderlyings] = useState<OptionsUnderlying[]>([]);
  const [optionsData, setOptionsData] = useState<OptionsCombinedResponse | null>(null);
  const [ohlcv, setOhlcv] = useState<EtfOhlcvResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedDate, setSelectedDate] = useState<string>("");
  // Page-level refresh key — bumped by the header refresh button to force
  // a cache bypass + refetch of the three endpoints that drive the page:
  //   • /api/szse-options/combined        (VolSmile + MarketInterestWall + AnnualSentiment)
  //   • /api/szse-options/etf-ohlcv       (AnnualSentiment ETF OHLC)
  //   • /api/szse-options/underlyings     (the underlying dropdown)
  // The underlyings list is also re-fetched because it's small and the
  // user expects a fully refreshed page after clicking the button.
  const [refreshKey, setRefreshKey] = useState(0);

  // Load underlyings list once (and on refresh / target-type change).
  // Falls back to the first available underlying when the current selection
  // has no options in the newly selected target type (e.g. 399006 has no
  // CFFEX index options).
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
        // Default selectedDate to the latest date in the new data
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
    // All three endpoints share the "/api/szse-options/" prefix:
    //   • /api/szse-options/combined?underlying=…  (3 panels)
    //   • /api/szse-options/etf-ohlcv?code=…        (ETF OHLC)
    //   • /api/szse-options/underlyings             (dropdown)
    // Prefix invalidation covers all of them in one call.
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
              {/* Summary caption — mirrors the etf-margin page header pattern */}
              <Typography variant="caption" color="text.secondary">
                {optionsData.rows.length} option contracts · {optionsData.dates.length} trading days ·{" "}
                {snapshotDates.filter((sd) => sd.date).length}/{snapshotDates.length} snapshots in table ·{" "}
                {optionsData.dates[0] ?? "—"} → {optionsData.dates[optionsData.dates.length - 1] ?? "—"}
              </Typography>

              {/* StatTable at top — snapshot summary first, charts below */}
              <StatTable statsList={snapshotStats} />

              <VolSmilePanel rows={optionsData.rows} selectedDate={selectedDate} />
              <MarketInterestWallPanel rows={optionsData.rows} selectedDate={selectedDate} />
              <ExpiryOiBandsPanel rows={optionsData.rows} />
              <AnnualSentimentPanel rows={optionsData.rows} ohlcv={ohlcv} />
            </>
          )}
        </>
      )}
    </Stack>
  );
}
