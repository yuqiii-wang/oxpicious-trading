/**
 * Compact stat table for options snapshot stats — mirrors the bottom-row
 * summary table in plot_szse_options.py's market-interest figure.
 */
import {
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
  useTheme,
} from "@mui/material";
import type { SnapshotStats } from "@/lib/options-stats";
import { fmtNum, fmtPct, fmtMil } from "@/lib/series";
import { PRICE_SCALE } from "@/theme/chart-palette";

interface StatTableProps {
  statsList: Array<{ label: string; date: string; stats: SnapshotStats | null }>;
}

const COLS: Array<{
  label: string;
  align: "left" | "right" | "center";
  get: (s: SnapshotStats | null) => string;
}> = [
  { label: "Snapshot", align: "left", get: () => "" },
  { label: "Date", align: "left", get: () => "" },
  { label: "Spot", align: "right", get: (s) => (s ? fmtNum(s.S) : "—") },
  {
    label: "Call Wall",
    align: "right",
    get: (s) => (s && s.callWall ? fmtNum(s.callWall / PRICE_SCALE) : "—"),
  },
  {
    label: "Put Wall",
    align: "right",
    get: (s) => (s && s.putWall ? fmtNum(s.putWall / PRICE_SCALE) : "—"),
  },
  {
    label: "Max Pain",
    align: "right",
    get: (s) => (s && s.maxPain ? fmtNum(s.maxPain / PRICE_SCALE) : "—"),
  },
  {
    label: "P/C Ratio",
    align: "right",
    get: (s) => (s && Number.isFinite(s.pcRatio) ? fmtNum(s.pcRatio) : "—"),
  },
  {
    label: "ATM IV",
    align: "right",
    get: (s) => (s && s.atmIv != null ? fmtPct(s.atmIv * 100) : "—"),
  },
  {
    label: "IV Skew",
    align: "right",
    get: (s) =>
      s && s.ivSkew != null && Number.isFinite(s.ivSkew)
        ? (s.ivSkew * 100 >= 0 ? "+" : "") + fmtPct(s.ivSkew * 100)
        : "—",
  },
  {
    label: "Smile Skew",
    align: "right",
    get: (s) => {
      if (!s || s.smileSkewness.length === 0) return "—";
      const front = s.smileSkewness[0];
      const v = front.overallSkew;
      if (v == null || !Number.isFinite(v)) return "—";
      return (v >= 0 ? "+" : "") + fmtNum(v, 2);
    },
  },
  {
    label: "ATM Δ",
    align: "right",
    get: (s) =>
      s && s.atmGreeks.delta != null && Number.isFinite(s.atmGreeks.delta)
        ? (s.atmGreeks.delta >= 0 ? "+" : "") + fmtNum(s.atmGreeks.delta)
        : "—",
  },
  {
    label: "ATM Γ",
    align: "right",
    get: (s) =>
      s && s.atmGreeks.gamma != null && Number.isFinite(s.atmGreeks.gamma)
        ? fmtNum(s.atmGreeks.gamma)
        : "—",
  },
  {
    label: "ATM Θ",
    align: "right",
    get: (s) =>
      s && s.atmGreeks.theta != null && Number.isFinite(s.atmGreeks.theta)
        ? (s.atmGreeks.theta >= 0 ? "+" : "") + fmtNum(s.atmGreeks.theta)
        : "—",
  },
  {
    label: "ATM ν",
    align: "right",
    get: (s) =>
      s && s.atmGreeks.vega != null && Number.isFinite(s.atmGreeks.vega)
        ? (s.atmGreeks.vega >= 0 ? "+" : "") + fmtNum(s.atmGreeks.vega)
        : "—",
  },
  {
    label: "ATM ρ",
    align: "right",
    get: (s) =>
      s && s.atmGreeks.rho != null && Number.isFinite(s.atmGreeks.rho)
        ? (s.atmGreeks.rho >= 0 ? "+" : "") + fmtNum(s.atmGreeks.rho)
        : "—",
  },
  {
    label: "Net GEX",
    align: "right",
    get: (s) =>
      s ? (s.netGex >= 0 ? "+" : "") + fmtMil(s.netGex) : "—",
  },
  {
    label: "Net OI",
    align: "right",
    get: (s) => (s ? (s.netPos >= 0 ? "+" : "") + fmtNum(s.netPos) : "—"),
  },
  {
    label: "OI Conc.",
    align: "right",
    get: (s) => (s ? fmtNum(s.concentration) : "—"),
  },
];

export default function StatTable({ statsList }: StatTableProps) {
  const theme = useTheme();
  return (
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
      <Table size="small" sx={{ minWidth: 1180 }}>
        <TableHead>
          <TableRow sx={{ bgcolor: theme.palette.primary.main }}>
            {COLS.map((c) => (
              <TableCell
                key={c.label}
                align={c.align}
                sx={{
                  color: "#fff",
                  fontWeight: 600,
                  fontSize: "0.75rem",
                  whiteSpace: "nowrap",
                }}
              >
                {c.label}
              </TableCell>
            ))}
          </TableRow>
        </TableHead>
        <TableBody>
          {statsList.map((row, idx) => (
            <TableRow
              key={row.label}
              sx={{
                bgcolor: idx % 2 === 0 ? "background.paper" : "action.hover",
                "&:hover": { bgcolor: "action.selected" },
              }}
            >
              <TableCell sx={{ fontWeight: 600, fontSize: "0.78rem" }}>
                {row.label}
              </TableCell>
              <TableCell sx={{ fontSize: "0.75rem", color: "text.secondary" }}>
                {row.date || "—"}
              </TableCell>
              {COLS.slice(2).map((c, i) => (
                <TableCell
                  key={i}
                  align={c.align}
                  sx={{
                    fontSize: "0.75rem",
                    fontFamily: "monospace",
                    whiteSpace: "nowrap",
                  }}
                >
                  {c.get(row.stats)}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  );
}
