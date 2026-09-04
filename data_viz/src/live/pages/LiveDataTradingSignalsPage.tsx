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
 *   • Refresh button — force-triggers `python -m live.live_signals
 *     --sec-type <selection>` (the same run the 13:30 scheduler fires),
 *     then reloads the list. On an OLD date (no intraday bars exist) it
 *     first confirms, then runs the analysis day-close job instead
 *     (`python -m analyze.analysis_signals --live`), which records every
 *     not-yet-recorded signal day as one day-close observation (15:00).
 */
import { useEffect, useMemo, useState } from "react";
import {
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
  type Theme,
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

// ---------------------------------------------------------------------------
//  Buy/Sell color helpers — MUI palette.success (green) for buy,
//  palette.error (red) for sell, visualized as a NO-FILL outlined chip
//  so text never gets hidden. Confidence tunes *border width + text
//  alpha* as the intensity knob: 100 → 2px solid full color, 1 → 1px
//  still-visible tint (minimum alpha = 0.55 so never too faint).
// ---------------------------------------------------------------------------

/** Minimum border/text alpha even at confidence=1 — keeps the chip
 *  clearly visible (never a ghost outline). */
const MIN_CONF_ALPHA = 0.55;

/** Map confidence (1..100) to a border/text alpha. Linear ramp, clamped
 *  at MIN_CONF_ALPHA on the low end. */
function confAlpha(confidence: number): number {
  const c = Math.max(1, Math.min(100, confidence));
  return MIN_CONF_ALPHA + (c / 100) * (1 - MIN_CONF_ALPHA);
}

/** Map confidence (1..100) to border width in px. Linear ramp: 1px at 1,
 *  2px at 100 — the thicker outline is the "intensity" signal when no
 *  fill is present. */
function confBorderWidth(confidence: number): number {
  const c = Math.max(1, Math.min(100, confidence));
  return 1 + (c / 100); // 1.00 → 2.00
}

/** Convert a theme palette "main" hex (#RRGGBB) into an rgba string with
 *  the given alpha — used for confidence-tinted border + text. */
function hexWithAlpha(hex: string, alpha: number): string {
  const m = /^#([0-9a-fA-F]{6})$/.exec(hex.trim());
  if (!m) return hex;
  const n = parseInt(m[1]!, 16);
  const r = (n >> 16) & 0xff;
  const g = (n >> 8) & 0xff;
  const b = n & 0xff;
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

/** Chip sx overrides — NO fill (outlined by default), so text is never
 *  hidden. Border width + border/text alpha ramp with confidence. */
function actionChipSx(
  action: string,
  confidence: number,
  theme: Theme,
): Record<string, unknown> {
  const buy = action === "buy";
  const main = buy ? theme.palette.success.main : theme.palette.error.main;
  const alpha = confAlpha(confidence);
  return {
    fontWeight: 600,
    backgroundColor: "transparent",
    borderColor: hexWithAlpha(main, alpha),
    borderWidth: confBorderWidth(confidence),
    borderStyle: "solid",
    color: hexWithAlpha(main, alpha),
    "&:hover": {
      backgroundColor: hexWithAlpha(main, 0.08),
      borderColor: main,
      color: main,
    },
  };
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
        <Tooltip
          title={selectedDate ? selectedDate : `Biz today (${resolvedDate})`}
          arrow
        >
          <Autocomplete
            size="small"
            sx={{ minWidth: 150 }}
            disableClearable
            options={dateOptions}
            value={selectedDate ?? resolvedDate}
            onChange={(_e, v) => {
              if (!v) return;
              // Picking the resolved biz-today entry returns to "live"
              // mode; a historical date freezes the page.
              setSelectedDate(v === resolvedDate ? null : v);
            }}
            renderInput={(params) => (
              <TextField
                {...params}
                label="Date"
                variant="outlined"
                size="small"
              />
            )}
          />
        </Tooltip>
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

/** The day's triggered signals, confidence DESC (server-ordered). */
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
  if (loading && rows.length === 0) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
        <CircularProgress size={28} />
      </Box>
    );
  }
  if (rows.length === 0) {
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
            <TableCell align="right">Confidence</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {rows.map((s) => (
            <TableRow key={`${s.code}-${s.signal_type}-${s.signal_sub_type}-${s.time}`}>
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
              <TableCell>
                {s.signal_type} · {s.signal_sub_type}
              </TableCell>
              <TableCell>
                <Chip
                  size="small"
                  label={s.action.toUpperCase()}
                  variant="outlined"
                  sx={actionChipSx(s.action, s.confidence, theme)}
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
                {s.signal_excess_pct !== null && s.signal_excess_pct !== undefined && (
                  <span
                    sx={{
                      color: theme.palette.text.secondary,
                      fontWeight: 400,
                      ml: 0.5,
                    }}
                  >
                    ({s.signal_excess_pct >= 0 ? "+" : ""}
                    {s.signal_excess_pct.toFixed(2)}%)
                  </span>
                )}
              </TableCell>
              <TableCell
                align="right"
                sx={{
                  color: theme.palette.text.secondary,
                }}
              >
                {s.confidence}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  );
}
