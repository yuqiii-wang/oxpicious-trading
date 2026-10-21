/**
 * SignalTable — the day's triggered signals, confidence DESC
 * (server-ordered), grouped to ONE parent row per (code, date, time) tick
 * — the representative is the group's highest-confidence signal (server
 * orders confidence DESC and the signal-menu filter preserves order), and
 * a "+N" chip on the Signal cell marks the collapsed siblings. Every row
 * is CLICKABLE: clicking toggles an expansion below the row with (1) the
 * tick's collapsed signals (multi-config ticks only), (2) the code's
 * daily price trend (shared SignalCodeTrendChart) marked with the code's
 * full history of buy/sell signals and (3) the code's history-signals
 * table. One row expanded at a time. Split out of
 * LiveDataTradingSignalsPage.tsx (2026-10-20, >900-line split).
 */
import { Fragment, useMemo, useState } from "react";
import {
  Box,
  Chip,
  CircularProgress,
  Paper,
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
import type { TradingSignal, TradingSignalsResponse } from "@/lib/api-client";
import { regimeAccentColor } from "@/shared/charts/regimeBands";
import { tickKey } from "./signalsShared";
import { SignalExpansion } from "./SignalExpansion";

export function SignalTable({
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
                            .map(
                              (g) =>
                                `${g.signal_type} · ${g.signal_sub_type}`,
                            )
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
                      whiteSpace: "nowrap",
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
