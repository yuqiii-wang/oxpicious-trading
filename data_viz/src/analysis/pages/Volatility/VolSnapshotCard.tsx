/**
 * VolSnapshotCard — one code's latest-day snapshot of the gauges the
 * four vol charts do not surface directly: the ATR family, the band
 * width / vol-of-vol / short-long structure trio, the remaining
 * percentiles + z-scores, and the 5-min rv quality pair. A compact
 * dense MUI table (label · value · trailing-year percentile where the
 * gauge has one), not a chart.
 */
import { useMemo } from "react";
import {
  Box,
  Card,
  CardContent,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableRow,
  Typography,
} from "@mui/material";
import { fmtDate } from "@/shared/datetime";
import type { VolatilityRow } from "@shared/types";

interface Props {
  code: string;
  name: string;
  rows: VolatilityRow[];
}

interface SnapshotItem {
  label: string;
  value: string;
  pct?: number | null;
  hint?: string;
}

const fmt = (v: number | null | undefined, digits = 2): string =>
  v == null ? "—" : v.toFixed(digits);

export default function VolSnapshotCard({ code, name, rows }: Props) {
  const label = name || code;
  const items: SnapshotItem[] = useMemo(() => {
    const last = rows.length > 0 ? rows[rows.length - 1] : null;
    if (!last) return [];
    return [
      { label: "ATR(14)", value: fmt(last.atr_14), hint: "price level" },
      { label: "NATR(14)", value: `${fmt(last.natr_14)}%`, pct: last.natr_14_pct_rank_252 },
      { label: "Bollinger width 20d", value: `${fmt(last.boll_bw_20)}%` },
      { label: "Vol-of-vol 20d", value: fmt(last.vol_of_vol_20), hint: "vol points" },
      { label: "Vol ratio 20/252", value: fmt(last.vol_ratio_20_252) },
      { label: "HV 10d", value: `${fmt(last.hv_10)}%` },
      { label: "HV 120d", value: `${fmt(last.hv_120)}%` },
      { label: "YZ 20d percentile", value: fmt(last.yang_zhang_vol_20_pct_rank_252), pct: last.yang_zhang_vol_20_pct_rank_252 },
      { label: "EWMA z-score", value: fmt(last.ewma_vol_z_252) },
      { label: "RV 5-min bars", value: last.rv_bar_count == null ? "—" : String(last.rv_bar_count), hint: "quality gauge" },
    ];
  }, [rows]);

  const last = rows.length > 0 ? rows[rows.length - 1] : null;
  if (!last || items.length === 0) return null;

  return (
    <Card variant="outlined">
      <CardContent sx={{ pb: 1 }}>
        <Box sx={{ display: "flex", alignItems: "baseline", gap: 1, mb: 0.5 }}>
          <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>
            {label} — latest snapshot
          </Typography>
          <Typography variant="caption" color="text.secondary">
            as of {fmtDate(last.date)}
          </Typography>
        </Box>
        <TableContainer>
          <Table size="small">
            <TableBody>
              {items.map((it) => (
                <TableRow key={it.label}>
                  <TableCell sx={{ py: 0.3, width: "45%" }}>
                    <Typography variant="caption" sx={{ fontSize: "0.72rem" }}>
                      {it.label}
                      {it.hint ? (
                        <Box component="span" sx={{ color: "text.disabled", ml: 0.5 }}>
                          ({it.hint})
                        </Box>
                      ) : null}
                    </Typography>
                  </TableCell>
                  <TableCell align="right" sx={{ py: 0.3 }}>
                    <Typography variant="caption" sx={{ fontSize: "0.72rem", fontWeight: 600 }}>
                      {it.value}
                    </Typography>
                  </TableCell>
                  <TableCell align="right" sx={{ py: 0.3, width: "30%" }}>
                    <Typography variant="caption" sx={{ fontSize: "0.7rem", color: "text.secondary" }}>
                      {it.pct != null ? `1y pct ${(it.pct * 100).toFixed(0)}%` : ""}
                    </Typography>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      </CardContent>
    </Card>
  );
}
