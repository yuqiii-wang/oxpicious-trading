/**
 * SignalHistoryTable — the Recent Movements signal-history table beneath
 * the code trend: ONE row per SIGNAL DATE (the day's highest-confidence
 * config as the representative, a "+N" chip for its collapsed siblings —
 * the live Trading Signals main-table grouping), each row CLICKABLE to
 * expand the date's full records (the shared SignalRecordsTable panel,
 * the live tick-panel style). One date expanded at a time; a chart
 * marker click drives `expandedDate` from outside and scrolls the row
 * into view.
 */
import { Fragment, useEffect, useMemo, useRef } from "react";
import {
  Box,
  Chip,
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
import { SignalRecordsTable } from "@/shared/components/signal-code-trend";
import { regimeAccentColor } from "@/shared/charts/regimeBands";
import type { TradingSignal } from "@/lib/api-client";

export interface SignalHistoryTableProps {
  /** The code's FULL signal history, newest first (server-ordered). */
  signals: TradingSignal[];
  /** The currently expanded signal date (one at a time), or null. */
  expandedDate: string | null;
  /** Row click / outside marker click — set or clear the expansion. */
  onExpandedDateChange: (date: string | null) => void;
}

export default function SignalHistoryTable({
  signals,
  expandedDate,
  onExpandedDateChange,
}: SignalHistoryTableProps) {
  const theme = useTheme();
  const containerRef = useRef<HTMLDivElement | null>(null);

  // One group per signal date, insertion-ordered (newest first — the
  // server order); the representative is the day's highest-confidence
  // config (the +N siblings surface in the expansion panel).
  const dateGroups = useMemo(() => {
    const groups = new Map<string, TradingSignal[]>();
    for (const s of signals) {
      const group = groups.get(s.date);
      if (group) group.push(s);
      else groups.set(s.date, [s]);
    }
    return groups;
  }, [signals]);

  // An outside expansion (chart marker click) brings its row into view.
  useEffect(() => {
    if (!expandedDate) return;
    containerRef.current
      ?.querySelector(`[data-signal-date="${expandedDate}"]`)
      ?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [expandedDate]);

  return (
    <Paper variant="outlined" sx={{ p: 1.25 }}>
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ display: "block", mb: 0.75, px: 0.5 }}
      >
        History signals · {dateGroups.size} day{dateGroups.size === 1 ? "" : "s"} ·{" "}
        {signals.length} record{signals.length === 1 ? "" : "s"} — click a row
        (or a chart marker) to expand the date's signals
      </Typography>
      {dateGroups.size === 0 ? (
        <Typography variant="body2" color="text.secondary" sx={{ px: 0.5, pb: 1 }}>
          No recorded signals for this code.
        </Typography>
      ) : (
        <TableContainer ref={containerRef} sx={{ maxHeight: 300 }}>
          <Table size="small" stickyHeader>
            <TableHead>
              <TableRow>
                <TableCell>Date</TableCell>
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
                    "calm / hot / panic / quiet) — recorded context, never " +
                    "a gate; the same split the strategy itself registered " +
                    "under"
                  }
                  arrow
                >
                  <TableCell align="center">regime</TableCell>
                </Tooltip>
              </TableRow>
            </TableHead>
            <TableBody>
              {[...dateGroups.entries()].map(([date, group]) => {
                const rep = group.reduce(
                  (a, b) => (b.confidence > a.confidence ? b : a),
                  group[0],
                );
                const expanded = expandedDate === date;
                return (
                  <Fragment key={date}>
                    <TableRow
                      data-signal-date={date}
                      hover
                      onClick={() =>
                        onExpandedDateChange(expanded ? null : date)
                      }
                      sx={{
                        cursor: "pointer",
                        ...(expanded && { backgroundColor: "action.hover" }),
                      }}
                    >
                      <TableCell sx={{ whiteSpace: "nowrap" }}>{date}</TableCell>
                      <TableCell sx={{ whiteSpace: "nowrap" }}>
                        {rep.signal_type} · {rep.signal_sub_type}
                        {group.length > 1 && (
                          <Tooltip
                            title={
                              `+${group.length - 1} more config signalled ` +
                              "this date — expand for all: " +
                              group
                                .filter((g) => g !== rep)
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
                          action={rep.action}
                          confidence={rep.confidence}
                        />
                      </TableCell>
                      <TableCell align="right">{rep.signal.toFixed(4)}</TableCell>
                      <TableCell align="right">
                        {rep.signal_threshold.toFixed(4)}
                      </TableCell>
                      <TableCell
                        align="right"
                        sx={{
                          whiteSpace: "nowrap",
                          color: rep.action === "buy"
                            ? theme.palette.success.main
                            : theme.palette.error.main,
                          fontWeight: 600,
                        }}
                      >
                        {rep.signal_excess >= 0 ? "▲ " : "▼ "}
                        {rep.signal_excess.toFixed(4)}
                        {rep.signal_excess_pct !== null &&
                          rep.signal_excess_pct !== undefined && (
                            <Box
                              component="span"
                              sx={{
                                color: theme.palette.text.secondary,
                                fontWeight: 400,
                                ml: 0.5,
                              }}
                            >
                              ({rep.signal_excess_pct >= 0 ? "+" : ""}
                              {rep.signal_excess_pct.toFixed(2)}%)
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
                        {rep.confidence_pct.toFixed(2)}%
                      </TableCell>
                      <TableCell align="center">
                        <Box
                          component="span"
                          sx={{
                            color:
                              regimeAccentColor(rep.regime_state) ??
                              "text.disabled",
                            fontWeight: 600,
                          }}
                        >
                          {rep.regime_state === "calm" ? "·" : rep.regime_state}
                        </Box>
                      </TableCell>
                    </TableRow>
                    {expanded && (
                      <TableRow>
                        <TableCell
                          colSpan={8}
                          sx={{
                            p: 0,
                            border: "none",
                            bgcolor: "background.default",
                          }}
                        >
                          <Box sx={{ p: 1 }}>
                            <SignalRecordsTable
                              caption={`All signals on ${date} · ${group.length} record${group.length === 1 ? "" : "s"}`}
                              rows={group}
                              showTime
                            />
                          </Box>
                        </TableCell>
                      </TableRow>
                    )}
                  </Fragment>
                );
              })}
            </TableBody>
          </Table>
        </TableContainer>
      )}
    </Paper>
  );
}
