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
 *     collapsed siblings reappear inside the row's expansion panel (the
 *     signals-at-this-tick table).
 *   • Row expansion — every row is CLICKABLE: clicking toggles a panel
 *     below the row with (1) the tick's collapsed signals (only when
 *     several configs breached the same tick), (2) the code's daily price
 *     trend (shared CodeTrendChart over the sec_type's baseline endpoint)
 *     carrying the code's FULL signal history as buy (green ▲ below the
 *     low) / sell (red ▼ above the high) markers, and (3) the code's
 *     history-signals table (GET /api/live-data/trading-signals/history —
 *     every live.live_signals row of the code, newest first). One row
 *     expanded at a time; re-expanding a code reuses the cached history
 *     fetch.
 *   • Refresh button — force-triggers `python -m live.live_signals
 *     --sec-type <selection>` (the same run the 13:30 scheduler fires),
 *     then reloads the list. On an OLD date (no intraday bars exist) it
 *     first confirms, then runs the analysis day-close job instead
 *     (`python -m analyze.analysis_signals --live`), which records every
 *     not-yet-recorded signal day as one day-close observation (15:00).
 */
import { Fragment, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Autocomplete,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
  useTheme,
} from "@mui/material";
import { SignalActionChip } from "@/shared/components/signal-action";
import {
  fetchTradingSignalConfigs,
  fetchTradingSignals,
  fetchTradingSignalHistory,
  runTradingSignals,
  runTradingSignalsAnalysis,
  fetchTradingSignalsRunStatus,
  invalidateCacheForPrefix,
  type TradingSignal,
  type TradingSignalConfig,
  type TradingSignalsResponse,
} from "@/lib/api-client";
import RefreshButton from "@/components/RefreshButton";
import CodeTrendChart, {
  type CodeTrendSecType,
} from "@/components/CodeTrendChart";
import type { OhlcTradeSignal } from "@/components/StockOhlcChart";
import { DateSelector } from "@/shared/components/date-selector";
import { regimeAccentColor } from "@/shared/charts/regimeBands";

type SignalsMode = "analysis" | "strategy";
type SecType = "index" | "etf" | "stock";

const SEC_TYPES: SecType[] = ["index", "etf", "stock"];
const SEC_TYPES_KEY = "trading-signals:sec-types";

/** Restore the persisted sec-type selection (shared with the 13:30
 *  scheduler); falls back to "index". */
function readPersistedSecType(): SecType {
  try {
    const raw = localStorage.getItem(SEC_TYPES_KEY);
    if (raw) {
      const parsed: unknown = JSON.parse(raw);
      if (Array.isArray(parsed) && parsed.length > 0) {
        const first = parsed[0];
        if (first === "index" || first === "etf" || first === "stock") {
          return first;
        }
      }
    }
  } catch {
    // corrupted storage → default
  }
  return "index";
}

function persistSecType(st: SecType): void {
  try {
    localStorage.setItem(SEC_TYPES_KEY, JSON.stringify([st]));
  } catch {
    // storage unavailable — the scheduler just uses its default
  }
}

/** Short menu label: "rsi14" → "mov_rsi · rsi14". */
function configLabel(c: TradingSignalConfig): string {
  return `${c.signal_type} · ${c.signal_sub_type}`;
}

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
  // `python -m analyze.analysis_signals --live --sec-type <selection>`),
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
        <SignalTable
          rows={rows}
          loading={loading}
          resolvedDate={resolvedDate}
          total={data?.signals.length ?? 0}
        />
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

/** Stable React key + expansion identity for one signal row. */
function rowKey(s: TradingSignal): string {
  return `${s.code}-${s.signal_type}-${s.signal_sub_type}-${s.date}-${s.time}`;
}

/** Tick-group identity: the live_signals PK minus the config columns —
 *  several configs breaching at the same (code, date, time) collapse into
 *  ONE parent row; the siblings surface in the expansion panel. */
function tickKey(s: TradingSignal): string {
  return `${s.code}-${s.date}-${s.time}`;
}

/** The day's triggered signals, confidence DESC (server-ordered), grouped
 *  to ONE parent row per (code, date, time) tick — the representative is
 *  the group's highest-confidence signal (server orders confidence DESC
 *  and the signal-menu filter preserves order), and a "+N" chip on the
 *  Signal cell marks the collapsed siblings. Every row is CLICKABLE:
 *  clicking toggles an expansion below the row with (1) the tick's
 *  collapsed signals (multi-config ticks only), (2) the code's daily price
 *  trend (shared CodeTrendChart) marked with the code's full history of
 *  buy/sell signals and (3) the code's history-signals table. One row
 *  expanded at a time. */
function SignalTable({
  rows,
  loading,
  resolvedDate,
  total,
}: {
  rows: TradingSignalsResponse["signals"];
  loading: boolean;
  resolvedDate: string;
  total: number;
}) {
  const theme = useTheme();
  const [expandedKey, setExpandedKey] = useState<string | null>(null);

  // PK (code, date, time) grouping: one parent row per tick, the rest of
  // each group withheld into the row's expansion. Insertion-ordered Map —
  // parentRows keeps the server's confidence-DESC order.
  const tickGroups = useMemo(() => {
    const groups = new Map<string, TradingSignal[]>();
    for (const s of rows) {
      const k = tickKey(s);
      const group = groups.get(k);
      if (group) group.push(s);
      else groups.set(k, [s]);
    }
    return groups;
  }, [rows]);
  const parentRows = useMemo(
    () => [...tickGroups.values()].map((group) => group[0]),
    [tickGroups],
  );

  if (loading && rows.length === 0) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
        <CircularProgress size={28} />
      </Box>
    );
  }
  if (parentRows.length === 0) {
    return (
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          alignItems: "center",
          py: 10,
          gap: 1,
        }}
      >
        <Typography variant="body2" color="text.secondary">
          No triggered signals for {resolvedDate || "biz today"}
          {total === 0 ? " (run the check to populate)" : " for this filter"}.
        </Typography>
      </Box>
    );
  }
  return (
    <TableContainer component={Paper} variant="outlined">
      <Table size="small" stickyHeader>
        <TableHead>
          <TableRow>
            <TableCell>Time</TableCell>
            <TableCell>Code</TableCell>
            <TableCell>Name</TableCell>
            <TableCell>Signal</TableCell>
            <TableCell>Action</TableCell>
            <TableCell align="right">Signal value</TableCell>
            <TableCell align="right">Threshold</TableCell>
            <TableCell align="right">Excess</TableCell>
            <Tooltip
              title={
                "The strategy's expected favorable move (the chosen entry " +
                "rung's sign-aligned dir_ave) as a % of price — the breach " +
                "record's confidence in basis points / 100"
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
            <Tooltip
              title={
                "Signal DAYS for this code over the last 20 trading days " +
                "— one day counts once, through its highest-confidence " +
                "signal that day (all signal types)"
              }
              arrow
            >
              <TableCell align="right">20d count</TableCell>
            </Tooltip>
          </TableRow>
        </TableHead>
        <TableBody>
          {parentRows.map((s) => {
            const key = tickKey(s);
            const group = tickGroups.get(key) ?? [s];
            const expanded = expandedKey === key;
            return (
              <Fragment key={key}>
                <TableRow
                  hover
                  onClick={() => setExpandedKey(expanded ? null : key)}
                  sx={{
                    cursor: "pointer",
                    ...(expanded && { backgroundColor: "action.hover" }),
                  }}
                >
                  <TableCell sx={{ whiteSpace: "nowrap" }}>
                    {s.time}
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
                  <TableCell>{s.code}</TableCell>
                  <TableCell sx={{ color: "text.secondary", whiteSpace: "nowrap" }}>
                    {s.code_name ?? s.code}
                  </TableCell>
                  <TableCell sx={{ whiteSpace: "nowrap" }}>
                    {s.signal_type} · {s.signal_sub_type}
                    {group.length > 1 && (
                      <Tooltip
                        title={
                          `+${group.length - 1} more config breached this ` +
                          "tick — expand for all: " +
                          group
                            .slice(1)
                            .map((g) => `${g.signal_type} · ${g.signal_sub_type}`)
                            .join(", ")
                        }
                        arrow
                      >
                        <Chip
                          size="small"
                          label={`+${group.length - 1}`}
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
                  <TableCell>
                    <SignalActionChip action={s.action} confidence={s.confidence} />
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
                    {s.signal_excess_pct !== null && s.signal_excess_pct !== undefined && (
                      <Box
                        component="span"
                        sx={{
                          color: theme.palette.text.secondary,
                          fontWeight: 400,
                          ml: 0.5,
                        }}
                      >
                        ({s.signal_excess_pct >= 0 ? "+" : ""}
                        {s.signal_excess_pct.toFixed(2)}%)
                      </Box>
                    )}
                  </TableCell>
                  <TableCell
                    align="right"
                    sx={{
                      color: theme.palette.text.secondary,
                    }}
                  >
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
                  <TableCell
                    align="right"
                    sx={{
                      whiteSpace: "nowrap",
                      color: s.count_20d > 1
                        ? theme.palette.warning.main
                        : theme.palette.text.secondary,
                      fontWeight: s.count_20d > 1 ? 600 : 400,
                    }}
                  >
                    {s.count_20d}
                  </TableCell>
                </TableRow>
                {expanded && (
                  <TableRow>
                    <TableCell
                      colSpan={11}
                      sx={{ p: 0, border: "none", bgcolor: "background.default" }}
                    >
                      <SignalExpansion signal={s} group={group} />
                    </TableCell>
                  </TableRow>
                )}
              </Fragment>
            );
          })}
        </TableBody>
      </Table>
    </TableContainer>
  );
}

// ---------------------------------------------------------------------------
//  Row expansion — the tick's collapsed signals (multi-config ticks), above
//  the code's price trend (shared CodeTrendChart) with the FULL signal
//  history drawn as buy/sell markers, above the code's history-signals
//  table (live.live_signals, newest first).
// ---------------------------------------------------------------------------

/** The sec_types CodeTrendChart knows how to fetch. */
const TREND_SEC_TYPES: ReadonlySet<string> = new Set(["index", "etf", "stock"]);

function SignalExpansion({
  signal,
  group,
}: {
  signal: TradingSignal;
  /** Every config that breached the parent row's (code, date, time) tick,
   *  confidence DESC — the rows the parent table's PK grouping collapsed
   *  into it. */
  group: TradingSignal[];
}) {
  const [history, setHistory] = useState<TradingSignal[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  // The code's FULL signal history (every date) — drives both the trend
  // chart's buy/sell markers and the history table. fetchJson's cache makes
  // re-expansions of the same code instant.
  useEffect(() => {
    let cancelled = false;
    setHistory(null);
    setError(null);
    fetchTradingSignalHistory(signal.sec_type, signal.code)
      .then((resp) => {
        if (!cancelled) setHistory(resp.signals);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [signal.sec_type, signal.code]);

  // live_signals rows → chart markers (one marker per buy/sell day).
  const markers: OhlcTradeSignal[] = useMemo(
    () =>
      (history ?? []).map((h) => ({
        date: h.date,
        action: h.action,
        signal_type: h.signal_type,
        signal_sub_type: h.signal_sub_type,
        confidence: h.confidence_pct,
      })),
    [history],
  );

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
      {group.length > 1 && <TickSignalsTable group={group} />}
      {TREND_SEC_TYPES.has(signal.sec_type) && (
        <CodeTrendChart
          secType={signal.sec_type as CodeTrendSecType}
          code={signal.code}
          name={signal.code_name ?? undefined}
          height={380}
          chartOptions={{ tradeSignals: markers }}
        />
      )}
      <HistorySignalsTable rows={history} />
    </Stack>
  );
}

/** Every config that breached the expanded row's (code, date, time) tick —
 *  the rows the parent table's PK grouping withheld, confidence DESC (the
 *  first is the parent row itself). */
function TickSignalsTable({ group }: { group: TradingSignal[] }) {
  const theme = useTheme();
  return (
    <Paper variant="outlined" sx={{ p: 1.25 }}>
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ display: "block", mb: 0.75, px: 0.5 }}
      >
        All signals at this tick · {group[0].date} {group[0].time} ·{" "}
        {group.length} config{group.length === 1 ? "" : "s"}
      </Typography>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Signal</TableCell>
            <TableCell>Action</TableCell>
            <TableCell align="right">Signal value</TableCell>
            <TableCell align="right">Threshold</TableCell>
            <TableCell align="right">Excess</TableCell>
            <Tooltip
              title={
                "The strategy's expected favorable move (the chosen entry " +
                "rung's sign-aligned dir_ave) as a % of price — the breach " +
                "record's confidence in basis points / 100"
              }
              arrow
            >
              <TableCell align="right">Confidence</TableCell>
            </Tooltip>
          </TableRow>
        </TableHead>
        <TableBody>
          {group.map((s) => (
            <TableRow key={rowKey(s)}>
              <TableCell sx={{ whiteSpace: "nowrap" }}>
                {s.signal_type} · {s.signal_sub_type}
              </TableCell>
              <TableCell>
                <SignalActionChip action={s.action} confidence={s.confidence} />
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
                {s.signal_excess_pct !== null &&
                  s.signal_excess_pct !== undefined && (
                    <Box
                      component="span"
                      sx={{
                        color: theme.palette.text.secondary,
                        fontWeight: 400,
                        ml: 0.5,
                      }}
                    >
                      ({s.signal_excess_pct >= 0 ? "+" : ""}
                      {s.signal_excess_pct.toFixed(2)}%)
                    </Box>
                  )}
              </TableCell>
              <TableCell align="right" sx={{ color: "text.secondary" }}>
                {s.confidence_pct.toFixed(2)}%
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Paper>
  );
}

/** The code's full live_signals history, newest first (server-ordered). */
function HistorySignalsTable({ rows }: { rows: TradingSignal[] }) {
  const theme = useTheme();
  return (
    <Paper variant="outlined" sx={{ p: 1.25 }}>
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ display: "block", mb: 0.75, px: 0.5 }}
      >
        History signals · {rows.length} record{rows.length === 1 ? "" : "s"}
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
                    <SignalActionChip action={s.action} confidence={s.confidence} />
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
                  <TableCell align="right" sx={{ color: "text.secondary" }}>
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
