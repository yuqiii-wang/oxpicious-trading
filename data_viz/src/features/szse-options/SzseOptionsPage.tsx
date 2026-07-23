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
import VolSmilePanel from "@/features/szse-options/VolSmilePanel";
import MarketInterestWallPanel from "@/features/szse-options/MarketInterestWallPanel";
import AnnualSentimentPanel from "@/features/szse-options/AnnualSentimentPanel";
import {
  fetchEtfOhlcv,
  fetchOptionsCombined,
  fetchUnderlyings,
} from "@/lib/api-client";
import { useStore } from "@/store/filters";
import type {
  EtfOhlcvResponse,
  OptionsCombinedResponse,
  OptionsUnderlying,
} from "../../../shared/types";
import { UNDERLYING_LABELS } from "@/theme/chart-palette";
import { computeSnapshotStats } from "@/lib/options-stats";

export default function SzseOptionsPage() {
  const underlyingCode = useStore((s) => s.underlyingCode);
  const snapshotDates = useStore((s) => s.snapshotDates);
  const setSnapshotDates = useStore((s) => s.setSnapshotDates);

  const [underlyings, setUnderlyings] = useState<OptionsUnderlying[]>([]);
  const [optionsData, setOptionsData] = useState<OptionsCombinedResponse | null>(null);
  const [ohlcv, setOhlcv] = useState<EtfOhlcvResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedDate, setSelectedDate] = useState<string>("");

  // Load underlyings list once
  useEffect(() => {
    fetchUnderlyings()
      .then(setUnderlyings)
      .catch((e: Error) => setError(e.message));
  }, []);

  // Load ALL options + OHLCV data (no date filter — trend plots need full history)
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([
      fetchOptionsCombined(underlyingCode, null, null),
      fetchEtfOhlcv(underlyingCode, null, null),
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
  }, [underlyingCode]);

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
      <Box>
        <Typography variant="h5" sx={{ fontWeight: 700 }}>
          SZSE ETF Options
        </Typography>
        <Typography variant="body2" color="text.secondary">
          {underlyingName} ({underlyingCode}) — interactive mirror of plot_szse_options.py
        </Typography>
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
              <AnnualSentimentPanel rows={optionsData.rows} ohlcv={ohlcv} />
            </>
          )}
        </>
      )}
    </Stack>
  );
}
