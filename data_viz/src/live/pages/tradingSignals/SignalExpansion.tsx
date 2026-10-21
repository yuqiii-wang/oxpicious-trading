/**
 * SignalExpansion — one parent row's expansion panel: the tick's
 * collapsed signals (multi-config ticks only), above the code's price
 * trend (shared SignalCodeTrendChart) with the FULL signal history drawn
 * as buy/sell markers, above the code's history-signals table
 * (history_signals ∪ live_signals, one per config-day) scoped to the
 * LAST 20 TRADING DAYS ending on the viewed day. Split out of
 * LiveDataTradingSignalsPage.tsx (2026-10-20, >900-line split).
 */
import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Chip,
  CircularProgress,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
  useTheme,
} from "@mui/material";
import { SignalActionChip } from "@/shared/components/signal-action";
import {
  fetchTradingSignalHistory,
  type TradingSignal,
  type TradingSignalHistoryResponse,
} from "@/lib/api-client";
import {
  SignalCodeTrendChart,
  SignalPopulationToggles,
  SignalRecordsTable,
} from "@/shared/components/signal-code-trend";
import type { CodeTrendSecType } from "@/ui/OhlcChart";
import { regimeAccentColor } from "@/shared/charts/regimeBands";
import { rowKey, TREND_SEC_TYPES } from "./signalsShared";

export function SignalExpansion({
  signal,
  group,
}: {
  signal: TradingSignal;
  /** Every config that breached the parent row's (code, date, time) tick,
   *  confidence DESC — the rows the parent table's PK grouping collapsed
   *  into it. */
  group: TradingSignal[];
}) {
  const [history, setHistory] =
    useState<TradingSignalHistoryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  // The code's FULL signal history (every date), anchored on the viewed
  // day — drives both the trend chart's buy/sell markers (ALL of it) and
  // the history table (scoped to the last 20 trading days). fetchJson's
  // cache makes re-expansions of the same (code, day) instant.
  useEffect(() => {
    let cancelled = false;
    setHistory(null);
    setError(null);
    fetchTradingSignalHistory(
      signal.sec_type, signal.code, null, null, signal.date,
    )
      .then((resp) => {
        if (!cancelled) setHistory(resp);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [signal.sec_type, signal.code, signal.date]);

  // The code's events — BOTH the trend chart's markers and the history
  // table below draw them (one view; one population since the boost
  // twins' retirement).
  const events: TradingSignal[] = useMemo(
    () => history?.signals ?? [],
    [history],
  );

  // Table rows: only the last 20 trading days ending on the viewed day —
  // (windowStart, anchor], the count_20d window. The anchor is ALSO the
  // upper bound: an old-date view must not leak later rows into the table
  // (the chart plots through today). Falls back to everything when the
  // boundary is missing (fewer than 20 baseline closes).
  const recentRows: TradingSignal[] = useMemo(() => {
    const windowStart = history?.recent_window_start;
    if (!windowStart) return events;
    return events.filter(
      (h) => h.date > windowStart && h.date <= signal.date,
    );
  }, [events, history?.recent_window_start, signal.date]);

  if (error) {
    return (
      <Alert severity="error" sx={{ m: 1.5 }}>
        Failed to load {signal.code} history: {error}
      </Alert>
    );
  }
  if (history === null) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
        <CircularProgress size={24} />
      </Box>
    );
  }
  return (
    <Stack spacing={2} sx={{ p: 1.5 }}>
      {group.length > 1 && (
        <SignalRecordsTable
          caption={`All signals at this tick · ${group[0].date} ${group[0].time} · ${group.length} config${group.length === 1 ? "" : "s"}`}
          rows={group}
        />
      )}
      {TREND_SEC_TYPES.has(signal.sec_type) && (
        <SignalCodeTrendChart
          secType={signal.sec_type as CodeTrendSecType}
          code={signal.code}
          name={signal.code_name ?? undefined}
          anchorDate={signal.date}
          history={history}
          population={events}
          height={380}
        />
      )}
      <SignalPopulationToggles count={events.length} />
      <HistorySignalsTable
        rows={recentRows}
        windowed={history.recent_window_start !== null}
      />
    </Stack>
  );
}

/** The code's history signals scoped to the recent window (last 20 trading
 *  days when `windowed`, the count_20d window), newest first
 *  (server-ordered). */
function HistorySignalsTable({
  rows,
  windowed,
}: {
  rows: TradingSignal[];
  windowed: boolean;
}) {
  const theme = useTheme();
  return (
    <Paper variant="outlined" sx={{ p: 1.25 }}>
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ display: "block", mb: 0.75, px: 0.5 }}
      >
        {windowed ? "Last 20 trading days" : "History signals"} ·{" "}
        {rows.length} record{rows.length === 1 ? "" : "s"}
      </Typography>
      {rows.length === 0 ? (
        <Typography variant="body2" color="text.secondary" sx={{ px: 0.5, pb: 1 }}>
          No recorded signals for this code.
        </Typography>
      ) : (
        <TableContainer sx={{ maxHeight: 300 }}>
          <Table size="small" stickyHeader>
            <TableHead>
              <TableRow>
                <TableCell>Date</TableCell>
                <TableCell>Time</TableCell>
                <TableCell>Signal</TableCell>
                <TableCell>Action</TableCell>
                <TableCell align="right">Signal value</TableCell>
                <TableCell align="right">Threshold</TableCell>
                <TableCell align="right">Excess</TableCell>
                <Tooltip
                  title={
                    "The strategy's expected favorable move (the chosen " +
                    "entry rung's sign-aligned dir_ave) as a % of price — " +
                    "the breach record's confidence in basis points / 100"
                  }
                  arrow
                >
                  <TableCell align="right">Confidence</TableCell>
                </Tooltip>
                <Tooltip
                  title={
                    "The breach day's market regime (stats.market_regimes: " +
                    "calm / hot / panic / quiet) — recorded context, never a " +
                    "gate; the same split the strategy itself registered under"
                  }
                  arrow
                >
                  <TableCell align="center">regime</TableCell>
                </Tooltip>
              </TableRow>
            </TableHead>
            <TableBody>
              {rows.map((s) => (
                <TableRow key={rowKey(s)}>
                  <TableCell sx={{ whiteSpace: "nowrap" }}>
                    {s.date}
                    {s.is_day_close_trigger && (
                      <Tooltip
                        title="Day-close trigger — analysis run (15:00 close)"
                        arrow
                      >
                        <Chip
                          size="small"
                          label="close"
                          variant="outlined"
                          sx={{
                            ml: 0.5,
                            height: 18,
                            fontSize: "0.65rem",
                            verticalAlign: "middle",
                          }}
                        />
                      </Tooltip>
                    )}
                  </TableCell>
                  <TableCell sx={{ whiteSpace: "nowrap" }}>{s.time}</TableCell>
                  <TableCell>
                    {s.signal_type} · {s.signal_sub_type}
                  </TableCell>
                  <TableCell>
                    <SignalActionChip
                      action={s.action}
                      confidence={s.confidence}
                      rejected={s.is_rejection === true}
                    />
                  </TableCell>
                  <TableCell align="right">{s.signal.toFixed(4)}</TableCell>
                  <TableCell align="right">
                    {s.signal_threshold.toFixed(4)}
                  </TableCell>
                  <TableCell
                    align="right"
                    sx={{
                      whiteSpace: "nowrap",
                      color: s.action === "buy"
                        ? theme.palette.success.main
                        : theme.palette.error.main,
                      fontWeight: 600,
                    }}
                  >
                    {s.signal_excess >= 0 ? "▲ " : "▼ "}
                    {s.signal_excess.toFixed(4)}
                  </TableCell>
                  <TableCell align="right" sx={{ color: "text.secondary", whiteSpace: "nowrap" }}>
                    {s.confidence_pct.toFixed(2)}%
                  </TableCell>
                  <TableCell align="center">
                    <Box
                      component="span"
                      sx={{
                        color:
                          regimeAccentColor(s.regime_state) ??
                          "text.disabled",
                        fontWeight: 600,
                      }}
                    >
                      {s.regime_state === "calm" ? "·" : s.regime_state}
                    </Box>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
    </Paper>
  );
}
