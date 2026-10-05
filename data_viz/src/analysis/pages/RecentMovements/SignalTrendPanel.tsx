/**
 * SignalTrendPanel — the Recent Movements content section for one picked
 * security: the shared SignalCodeTrendChart (daily trend + the FULL
 * signal history as buy/sell markers, PE overlay suppressed — the
 * signal overlay below replaces the valuation context) with the
 * signal-history table beneath it, and the CURVE overlay panel between
 * them: the continuous depth-response curves mu(s) per regime. (The
 * retired THRESHOLD bars overlay was removed 2026-10-05; the curves
 * subsume it.)
 *
 * The history response feeds BOTH halves: the chart's markers and the
 * table's per-date groups. Clicking a buy/sell marker (or any point of a
 * signal date) on the chart expands that date's signals in the table —
 * the chart's onDateClick resolves any in-grid click to its date, and
 * the handler keeps only dates that actually carry signal records.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  CircularProgress,
  Paper,
  Stack,
  Typography,
} from "@mui/material";
import {
  SignalCodeTrendChart,
  SignalOverlayPanel,
} from "@/shared/components/signal-code-trend";
import type { CodeTrendChartOptions, CodeTrendSecType } from "@/ui/OhlcChart";
import {
  fetchTradingSignalHistory,
  type TradingSignalHistoryResponse,
} from "@/lib/api-client";
import SignalHistoryTable from "./SignalHistoryTable";

export interface SignalTrendPanelProps {
  secType: CodeTrendSecType;
  code: string;
  /** Display name (e.g. from the nav tree); falls back to the API's. */
  name?: string;
}

export default function SignalTrendPanel({
  secType,
  code,
  name,
}: SignalTrendPanelProps) {
  const [history, setHistory] = useState<TradingSignalHistoryResponse | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  // The expanded signal date in the table (chart marker clicks drive it).
  const [expandedDate, setExpandedDate] = useState<string | null>(null);

  // The code's FULL signal history (all dates). Kept here — not inside
  // the chart — so the same response feeds the table; fetchJson's cache
  // keeps chart-side and table-side on one request.
  useEffect(() => {
    let cancelled = false;
    setHistory(null);
    setError(null);
    setExpandedDate(null);
    fetchTradingSignalHistory(secType, code, null, null, null)
      .then((resp) => {
        if (!cancelled) setHistory(resp);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [secType, code]);

  // Chart marker/date click → expand that date's signals. Dates without
  // records (plain chart clicks) are ignored.
  const signalDates = useMemo(
    () => new Set((history?.signals ?? []).map((h) => h.date)),
    [history],
  );
  const handleDateClick = useCallback(
    (date: string) => {
      if (!signalDates.has(date)) return;
      setExpandedDate((cur) => (cur === date ? null : date));
    },
    [signalDates],
  );
  // Identity-stable chart options (the skill's memo rule) — showPe
  // false: the threshold/curve panel below replaces the PE overlay.
  const chartOptions = useMemo<CodeTrendChartOptions>(
    () => ({ onDateClick: handleDateClick, showPe: false }),
    [handleDateClick],
  );

  return (
    <Stack spacing={2}>
      <SignalCodeTrendChart
        secType={secType}
        code={code}
        name={name}
        // undefined→internal fetch would duplicate this panel's request;
        // null = parent-owned, still loading (markers arrive with it).
        history={history}
        selectedSignalDate={expandedDate}
        chartOptions={chartOptions}
      />
      <Paper variant="outlined" sx={{ px: 1.5, py: 1 }}>
        <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
          Signal overlay — depth-response curves μ(s)
        </Typography>
        <SignalOverlayPanel secType={secType} code={code} />
      </Paper>
      {error ? (
        <Alert severity="error">Failed to load {code} signal history: {error}</Alert>
      ) : history === null ? (
        <Box sx={{ display: "flex", justifyContent: "center", py: 2 }}>
          <CircularProgress size={24} />
        </Box>
      ) : (
        <SignalHistoryTable
          signals={history.signals}
          expandedDate={expandedDate}
          onExpandedDateChange={setExpandedDate}
        />
      )}
    </Stack>
  );
}
