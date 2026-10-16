/**
 * SignalRecordsTable — THE shared per-config signal-records table (the
 * live Trading Signals tick panel's style): one row per signal record —
 * Signal | Action | Signal value | Threshold | Excess | Confidence —
 * inside outlined Paper under the muted caption line. Consumed by the
 * live row expansion (all configs at one tick) and the Recent Movements
 * history table's per-date expansion (all configs of one date), so the
 * two read identically.
 */
import {
  Box,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
  useTheme,
} from "@mui/material";
import { SignalActionChip } from "@/shared/components/signal-action";
import { signalRecordName } from "./families";
import type { TradingSignal } from "@/lib/api-client";

export interface SignalRecordsTableProps {
  /** The signal records of ONE scope (tick or date), server-ordered. */
  rows: TradingSignal[];
  /** The muted caption line above the table. */
  caption: string;
  /** Prepend a Time column (a date's records can span several ticks;
   *  one tick's cannot). Default false. */
  showTime?: boolean;
}

/** Stable React key for one signal record inside a shared scope. */
function recordKey(s: TradingSignal): string {
  return `${s.date}-${s.time}-${s.signal_type}-${s.signal_sub_type}`;
}

export default function SignalRecordsTable({
  rows,
  caption,
  showTime = false,
}: SignalRecordsTableProps) {
  const theme = useTheme();
  return (
    <Paper variant="outlined" sx={{ p: 1.25 }}>
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ display: "block", mb: 0.75, px: 0.5 }}
      >
        {caption}
      </Typography>
      <Table size="small">
        <TableHead>
          <TableRow>
            {showTime && <TableCell>Time</TableCell>}
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
          {rows.map((s) => (
            <TableRow key={recordKey(s)}>
              {showTime && (
                <TableCell sx={{ whiteSpace: "nowrap" }}>{s.time}</TableCell>
              )}
              <TableCell sx={{ whiteSpace: "nowrap" }}>
                {signalRecordName(s.signal_type, s.signal_sub_type)}
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
              <TableCell
                align="right"
                sx={{ color: "text.secondary", whiteSpace: "nowrap" }}
              >
                {s.confidence_pct.toFixed(2)}%
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Paper>
  );
}
