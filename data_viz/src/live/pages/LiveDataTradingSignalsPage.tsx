/**
 * Live Data — Trading Signals page.
 *
 * Lists TODAY'S triggered breach records from live.live_signals (the
 * analysis_signals threshold set), ordered by confidence DESC:
 *
 *   • Scheme toggle (Analysis / Strategy) — only Analysis is wired (the
 *     analysis_signals scheme); Strategy stays a placeholder until a
 *     strategy-sourced threshold set exists.
 *   • Sec-type toggle (Index / ETF / Stock), default Index — persisted to
 *     localStorage so the app-root 13:30 scheduler (useTradingSignalsSchedule)
 *     runs the SAME selection.
 *   • Date selector — null = biz today (Asia/Shanghai, the same biz day the
 *     live markets pages use; the server resolves it); a concrete date
 *     freezes the page on that historical day. Roster = dates present in
 *     live_signals (newest first).
 *   • Signal menu — the ACTIVE analysis_signals configs (signal_type /
 *     signal_sub_type) for the sec_type, default ALL; filters the list.
 *   • PK grouping — the parent table shows ONE row per (code, date, time)
 *     tick (the live_signals PK minus the config columns): several configs
 *     breaching at the same tick collapse into the group's highest-
 *     confidence signal, marked with a "+N" chip on the Signal cell. The
 *     collapsed siblings reappear inside the row's expansion panel
 *     (SignalTable + SignalExpansion, ./tradingSignals/ — 2026-10-20
 *     >900-line split).
 *   • Row expansion — every row is CLICKABLE: clicking toggles a panel
 *     below the row with (1) the tick's collapsed signals (only when
 *     several configs breached the same tick), (2) the code's daily price
 *     trend (shared SignalCodeTrendChart over the sec_type's baseline
 *     endpoint) carrying the code's FULL signal history as buy (green ▲
 *     below the low) / sell (red ▼ above the high) markers — every
 *     recorded event —
 *     and (3) the code's history-signals table (GET
 *     /api/live-data/trading-signals/history — analysis_signals
 *     .history_signals UNION live.live_signals, one record per config-day)
 *     scoped to the LAST 20 TRADING DAYS ending on the viewed day: the
 *     chart plots all of it, the table only the recent window. One row
 *     expanded at a time; re-expanding a code reuses the cached history
 *     fetch.
 *   • Refresh button — force-triggers `python -m live.live_signals
 *     --sec-type <selection>` (the same run the 13:30 scheduler fires),
 *     then reloads the list. On an OLD date (no intraday bars exist) it
 *     first confirms, then runs the analysis day-close job instead
 *     (`python -m analysis.analysis_signals --live`), which records every
 *     not-yet-recorded signal day as one day-close observation (15:00).
 */
import { useEffect, useMemo, useState } from "react";
import {
  Autocomplete,
  Box,
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import {
  fetchTradingSignalConfigs,
  fetchTradingSignals,
  runTradingSignals,
  runTradingSignalsAnalysis,
  fetchTradingSignalsRunStatus,
  invalidateCacheForPrefix,
  type TradingSignalConfig,
  type TradingSignalsResponse,
} from "@/lib/api-client";
import RefreshButton from "@/components/RefreshButton";
import { SignalPopulationToggles } from "@/shared/components/signal-code-trend";
import { DateSelector } from "@/shared/components/date-selector";
import {
  configLabel,
  persistSecType,
  readPersistedSecType,
  SEC_TYPES,
  type SecType,
  type SignalsMode,
} from "./tradingSignals/signalsShared";
import { SignalTable } from "./tradingSignals/SignalTable";

export default function LiveDataTradingSignalsPage() {
  const [mode, setMode] = useState<SignalsMode>("analysis");
  const [secType, setSecType] = useState<SecType>(readPersistedSecType);
  // Date selector: null = biz today (server resolves Asia/Shanghai today);
  // a concrete date freezes the page on that historical day.
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [data, setData] = useState<TradingSignalsResponse | null>(null);
  const [configs, setConfigs] = useState<TradingSignalConfig[]>([]);
  // null = ALL configs selected (menu untouched default).
  const [selectedConfigs, setSelectedConfigs] = useState<
    TradingSignalConfig[] | null
  >(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [runRunning, setRunRunning] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  // Old-date refresh: shown when the selected historical date has no
  // intraday records — confirms before the analysis day-close run.
  const [analysisConfirmOpen, setAnalysisConfirmOpen] = useState(false);

  // Persist the sec-type selection so the app-root 13:30 scheduler runs
  // exactly what this page shows.
  useEffect(() => {
    persistSecType(secType);
    // Reset the signal menu when the universe changes.
    setSelectedConfigs(null);
  }, [secType]);

  // The ACTIVE signal configs (menu source) for the sec_type.
  useEffect(() => {
    let cancelled = false;
    fetchTradingSignalConfigs(secType)
      .then((resp) => {
        if (!cancelled) setConfigs(resp.configs);
      })
      .catch(() => {
        if (!cancelled) setConfigs([]);
      });
    return () => {
      cancelled = true;
    };
  }, [secType]);

  // The day's triggered signals (confidence DESC — server-ordered).
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchTradingSignals(secType, selectedDate)
      .then((resp) => {
        if (!cancelled) setData(resp);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [secType, selectedDate, refreshKey]);

  // Restore the spinning state when a run (e.g. the 13:30 scheduler fire)
  // was already in flight before this page mounted.
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = () => {
      fetchTradingSignalsRunStatus()
        .then((running) => {
          if (cancelled) return;
          setRunRunning(running);
          if (running) {
            timer = setTimeout(poll, 3_000);
          } else {
            // The in-flight run just finished — pick up its records.
            setRefreshKey((k) => k + 1);
          }
        })
        .catch(() => {});
    };
    poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, []);

  // Refresh = force-trigger the signal run (same as the 13:30 fire, for
  // the CURRENT sec-type selection), then reload the list. On an OLD date
  // (no intraday bars exist) confirm first, then run the analysis
  // day-close job instead.
  const handleRefresh = async () => {
    if (selectedDate !== null) {
      const hasIntraday = (data?.signals ?? []).some(
        (s) => !s.is_day_close_trigger,
      );
      if (hasIntraday) {
        // The day was recorded intraday — a reload is all it needs.
        invalidateCacheForPrefix("/api/live-data/trading-signals");
        setRefreshKey((k) => k + 1);
        return;
      }
      setAnalysisConfirmOpen(true);
      return;
    }
    setRunRunning(true);
    try {
      await runTradingSignals([secType]);
    } finally {
      setRunRunning(false);
    }
    invalidateCacheForPrefix("/api/live-data/trading-signals");
    setRefreshKey((k) => k + 1);
  };

  // Confirmed old-date refresh → the analysis day-close run (spawns
  // `python -m analysis.analysis_signals --live --sec-type <selection>`),
  // then reload the list.
  const handleAnalysisConfirm = async () => {
    setAnalysisConfirmOpen(false);
    setRunRunning(true);
    try {
      await runTradingSignalsAnalysis([secType]);
    } finally {
      setRunRunning(false);
    }
    invalidateCacheForPrefix("/api/live-data/trading-signals");
    setRefreshKey((k) => k + 1);
  };

  // Client-side signal-menu filter (default = all).
  const rows = useMemo(() => {
    const signals = data?.signals ?? [];
    if (selectedConfigs === null || selectedConfigs.length === configs.length) {
      return signals;
    }
    const keys = new Set(selectedConfigs.map(configLabel));
    return signals.filter((s) =>
      keys.has(`${s.signal_type} · ${s.signal_sub_type}`),
    );
  }, [data, selectedConfigs, configs]);

  // The day-table rows: one population per event (the boost twins were
  // retired 2026-10-16 — the boost's tuned confidence lives on the rule
  // cells, rendered by the rules panels).
  const populationRows = rows;

  // Date roster: biz today (resolved by the server) + dates present in
  // live_signals, newest first, de-duplicated.
  const dateOptions = useMemo(() => {
    const opts = new Set<string>(data ? [data.date] : []);
    for (const d of data?.available_dates ?? []) opts.add(d);
    return [...opts];
  }, [data]);

  const resolvedDate = data?.date ?? "";

  return (
    <Stack spacing={2}>
      {/* Control bar: scheme toggle + sec-type toggle + date + signal menu + refresh */}
      <Stack
        direction="row"
        spacing={2}
        alignItems="center"
        flexWrap="wrap"
        useFlexGap
      >
        <ToggleButtonGroup
          size="small"
          exclusive
          value={mode}
          onChange={(_, v: SignalsMode | null) => {
            if (v) setMode(v);
          }}
          sx={{ height: 32 }}
        >
          <ToggleButton value="analysis" sx={{ px: 1.5, fontSize: "0.75rem" }}>
            Analysis
          </ToggleButton>
          <ToggleButton value="strategy" sx={{ px: 1.5, fontSize: "0.75rem" }}>
            Strategy
          </ToggleButton>
        </ToggleButtonGroup>
        <ToggleButtonGroup
          size="small"
          exclusive
          value={secType}
          onChange={(_, v: SecType | null) => {
            if (v) setSecType(v);
          }}
          sx={{ height: 32 }}
        >
          {SEC_TYPES.map((st) => (
            <ToggleButton
              key={st}
              value={st}
              sx={{ px: 1.5, fontSize: "0.75rem", textTransform: "capitalize" }}
            >
              {st}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
        <DateSelector
          dates={dateOptions}
          value={selectedDate}
          defaultDate={resolvedDate || undefined}
          tooltip={selectedDate ? selectedDate : `Biz today (${resolvedDate})`}
          onChange={(v) => {
            // Picking the resolved biz-today entry returns to "live"
            // mode (null); a historical date freezes the page.
            setSelectedDate(v);
          }}
        />
        <Autocomplete
          size="small"
          multiple
          sx={{ minWidth: 320 }}
          limitTags={2}
          options={configs}
          getOptionLabel={configLabel}
          groupBy={(c) => c.signal_type}
          value={selectedConfigs ?? configs}
          onChange={(_e, v) => {
            // Empty selection = show nothing (explicit user choice);
            // the ALL state is the untouched default (null).
            setSelectedConfigs(v);
          }}
          renderInput={(params) => (
            <TextField
              {...params}
              label="Signal types"
              variant="outlined"
              size="small"
              placeholder={selectedConfigs === null ? "All" : ""}
            />
          )}
        />
        <RefreshButton
          onClick={() => void handleRefresh()}
          loading={loading || runRunning}
          tooltip={
            "Force-run the live signal check (python -m live.live_signals " +
            "--sec-type <selection>), then reload today's triggered signals"
          }
          label={runRunning ? "Running…" : "Run & Refresh"}
        />
      </Stack>

      {mode === "strategy" ? (
        <Box
          sx={{
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
            py: 10,
          }}
        >
          <Typography variant="body2" color="text.secondary">
            Trading Signals — Strategy view coming soon.
          </Typography>
        </Box>
      ) : error ? (
        <Typography variant="body2" color="error">
          {error}
        </Typography>
      ) : (
        <>
          <SignalPopulationToggles count={populationRows.length} />
          <SignalTable
            rows={populationRows}
            loading={loading}
            resolvedDate={resolvedDate}
            total={data?.signals.length ?? 0}
          />
        </>
      )}

      {/* Old-date refresh confirmation: no intraday bars exist for a past
          date, so the refresh runs the analysis day-close job instead. */}
      <Dialog
        open={analysisConfirmOpen}
        onClose={() => setAnalysisConfirmOpen(false)}
      >
        <DialogTitle>No intraday data for {selectedDate}</DialogTitle>
        <DialogContent>
          <DialogContentText>
            A past date has no intraday bars to check. Refresh will run the
            analysis day-close job instead, which records every
            not-yet-recorded signal day as one day-close observation (15:00,
            close price vs the signal thresholds). Continue?
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setAnalysisConfirmOpen(false)}>Cancel</Button>
          <Button
            onClick={() => void handleAnalysisConfirm()}
            variant="contained"
            autoFocus
          >
            Run analysis
          </Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
}
