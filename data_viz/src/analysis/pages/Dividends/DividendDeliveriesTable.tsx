/**
 * DividendDeliveriesTable — the Dividends page's per-security delivery
 * audit card: the rolling 5-year (1-year stride) windows of
 * analysis.dividend_deliveries, newest first.
 *
 * Each row audits ONE window (anchor = the year's last data date):
 * how many ex-dividend deliveries landed inside it, across how many
 * calendar years, at what per-event yield (D / close at the ex-date) and
 * eps (close/pe), the window's annualized yield (Σ per-event yield /
 * window years), the trailing-1-year yield at the anchor (Σ per-event
 * yield over the final 365d), and how STABLE the schedule was — an event
 * is stable when a delivery also landed within ±20 days of the same date
 * one year earlier (last-year ±20d repeat rule); stability = stable /
 * deliveries.
 *
 * Sits UNDER the shared baseline code-trend chart, where the renamed
 * dividend_est estimation curve stays as the dashed "Div Est" overlay.
 * Hidden for non-payers and for index (no rows) — the fetch is LRU
 * cached so page flips are cheap.
 */
import { useEffect, useState } from "react";
import {
  Alert,
  Box,
  Skeleton,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
  Typography,
  useTheme,
} from "@mui/material";
import { fetchDividendDeliveries } from "@/lib/api-client";
import { fmtNum, fmtPct } from "@/lib/series";
import type {
  DividendDeliveryRow,
  PeSecType,
} from "@shared/types";

interface DeliveriesState {
  rows: DividendDeliveryRow[];
  loading: boolean;
  error: string | null;
}

function useDividendDeliveries(
  code: string,
  secType: PeSecType,
): DeliveriesState {
  const [state, setState] = useState<DeliveriesState>({
    rows: [],
    loading: true,
    error: null,
  });

  useEffect(() => {
    let cancelled = false;
    setState({ rows: [], loading: true, error: null });
    fetchDividendDeliveries(code, secType)
      .then((r) => {
        if (cancelled) return;
        setState({ rows: r.rows, loading: false, error: null });
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setState({ rows: [], loading: false, error: e.message });
      });
    return () => {
      cancelled = true;
    };
  }, [code, secType]);

  return state;
}

const NUM_SX = {
  fontSize: "0.75rem",
  fontFamily: "monospace",
  whiteSpace: "nowrap",
} as const;

/** Stability cell: share text + a light inline bar for quick scanning. */
function StabilityCell({ row }: { row: DividendDeliveryRow }) {
  const theme = useTheme();
  if (row.stability == null) {
    return <TableCell align="right" sx={NUM_SX}>—</TableCell>;
  }
  const pct = row.stability * 100;
  return (
    <TableCell align="right" sx={NUM_SX}>
      <Box sx={{ display: "flex", alignItems: "center", gap: 0.75, justifyContent: "flex-end" }}>
        <Box
          sx={{
            width: 42,
            height: 5,
            borderRadius: 0.5,
            bgcolor: "action.hover",
            overflow: "hidden",
            flexShrink: 0,
          }}
        >
          <Box
            sx={{
              width: `${Math.min(100, Math.max(0, pct))}%`,
              height: "100%",
              bgcolor:
                pct >= 75
                  ? theme.palette.success.main
                  : pct >= 40
                    ? theme.palette.warning.main
                    : theme.palette.error.main,
            }}
          />
        </Box>
        {fmtPct(pct, 1)}
      </Box>
    </TableCell>
  );
}

export function DividendDeliveriesTable({
  code,
  secType,
}: {
  code: string;
  secType: PeSecType;
}) {
  const { rows, loading, error } = useDividendDeliveries(code, secType);

  if (error) {
    return (
      <Alert severity="error" variant="filled">
        Failed to load dividend deliveries: {error}
      </Alert>
    );
  }
  if (loading) {
    return (
      <Box sx={{ px: 1, py: 0.5 }}>
        <Skeleton variant="rounded" height={26} />
        <Skeleton variant="rounded" height={26} sx={{ mt: 0.75 }} />
        <Skeleton variant="rounded" height={26} sx={{ mt: 0.75 }} />
      </Box>
    );
  }
  // Non-payers and index have no delivery rows — the audit card only
  // exists for securities with a delivery track record.
  if (rows.length === 0) return null;

  return (
    <Box sx={{ mt: 2 }}>
      <Typography variant="subtitle2" sx={{ mb: 0.75 }}>
        Dividend Deliveries — rolling 5y audit (stride 1y)
      </Typography>
      <TableContainer
        component={Paper}
        elevation={0}
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1.5,
          overflow: "auto",
        }}
      >
        <Table size="small" sx={{ minWidth: 700 }}>
          <TableHead>
            <TableRow sx={{ bgcolor: "primary.main" }}>
              {[
                "Window (end ← start)",
                "Deliveries",
                "Years",
                "Avg Yield",
                "Ann. Yield",
                "Latest 1Y Yield",
                "Avg EPS (¥)",
                "Stable",
                "Stability",
              ].map((label, i) => (
                <TableCell
                  key={label}
                  align={i === 0 ? "left" : "right"}
                  sx={{
                    color: "#fff",
                    fontWeight: 600,
                    fontSize: "0.75rem",
                    whiteSpace: "nowrap",
                  }}
                >
                  {label}
                </TableCell>
              ))}
            </TableRow>
          </TableHead>
          <TableBody>
            {rows.map((row, idx) => (
              <TableRow
                key={row.window_end_date}
                sx={{
                  bgcolor: idx % 2 === 0 ? "background.paper" : "action.hover",
                  "&:hover": { bgcolor: "action.selected" },
                }}
              >
                <TableCell sx={{ fontSize: "0.75rem", whiteSpace: "nowrap" }}>
                  {row.window_end_date}
                  <Box component="span" sx={{ color: "text.secondary" }}>
                    {" ← "}
                    {row.window_start_date}
                  </Box>
                </TableCell>
                <TableCell align="right" sx={NUM_SX}>{row.deliveries}</TableCell>
                <TableCell align="right" sx={NUM_SX}>{row.years_with_delivery}</TableCell>
                <TableCell align="right" sx={NUM_SX}>
                  {row.avg_yield != null ? fmtPct(row.avg_yield * 100, 2) : "—"}
                </TableCell>
                <TableCell align="right" sx={NUM_SX}>
                  {row.annualized_yield != null ? fmtPct(row.annualized_yield * 100, 2) : "—"}
                </TableCell>
                <TableCell align="right" sx={NUM_SX}>
                  {row.latest_year_yield != null ? fmtPct(row.latest_year_yield * 100, 2) : "—"}
                </TableCell>
                <TableCell align="right" sx={NUM_SX}>
                  {row.avg_eps != null ? fmtNum(row.avg_eps, 3) : "—"}
                </TableCell>
                <TableCell align="right" sx={NUM_SX}>
                  {row.stable_events}
                  {row.deliveries > 0
                    ? ` / ${row.deliveries}`
                    : ""}
                </TableCell>
                <StabilityCell row={row} />
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>
      <Typography variant="caption" sx={{ color: "text.secondary", display: "block", mt: 0.5 }}>
        Anchor = the year&apos;s last data date (current year = latest close — open
        window). Yield = D / close at each ex-date; EPS = close/pe then
        averaged. Ann. Yield = window Σ(D/close) per year; Latest 1Y =
        Σ(D/close) over the final 365d of the window. Stable = a delivery
        within ±20 days of the same date one year earlier; stability =
        stable / deliveries.
      </Typography>
    </Box>
  );
}
