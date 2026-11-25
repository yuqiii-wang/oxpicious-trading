/**
 * SqlAuditPanel — the statement-level half of the DB tab: monitor.sql_audit
 * via GET /api/monitor/db/sql — one row per DISTINCT statement executed on
 * the cluster (pg_stat_statements sampled on both instances by
 * database/sql_audit.py), keyed by sha256 of the normalized text (literals
 * -> $n) with delta-accumulated call/time/row totals. Sort selector (by
 * total time / calls / recency / avg) + debounced search over the
 * statement text or the hash prefix.
 */
import { useEffect, useState } from "react";
import {
  Alert,
  Box,
  Chip,
  CircularProgress,
  InputAdornment,
  Paper,
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
} from "@mui/material";
import { Search } from "@mui/icons-material";
import { useChartData } from "@/shared/charts/base-chart";
import { fetchDbSql } from "@/lib/api-client/monitor";
import { fmtStamp } from "@/shared/datetime/display";
import { fmtCount, fmtMs } from "./fmt";

type SqlOrder = "total_ms" | "calls" | "recent" | "avg_ms";

const ORDERS: SqlOrder[] = ["total_ms", "calls", "recent", "avg_ms"];

// UI-wide display stamp — keeps the ISO string's own (UTC) date + time parts,
// matching the neighboring TableWorkloadTable's UTC-honest treatment.
function shortTime(iso: string | null): string {
  if (!iso) return "—";
  return fmtStamp(iso.slice(0, 19));
}

export default function SqlAuditPanel() {
  const [order, setOrder] = useState<SqlOrder>("total_ms");
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  // debounce the search box — every keystroke would re-fire the fetch
  useEffect(() => {
    const t = setTimeout(() => setQ(search.trim()), 300);
    return () => clearTimeout(t);
  }, [search]);

  const { data, loading, error } = useChartData(
    () => fetchDbSql(order, 100, q),
    [order, q],
  );
  const rows = data?.rows ?? [];

  return (
    <Paper variant="outlined" sx={{ p: 2 }}>
      <Typography variant="subtitle2" gutterBottom>
        SQL audit
      </Typography>
      <Typography variant="caption" color="text.secondary" sx={{ mb: 1, display: "block" }}>
        monitor.sql_audit · one row per DISTINCT statement (sha256 of the normalized
        text, literals → $n) · calls / time / rows cumulative since first seen ·
        sampled on both instances from pg_stat_statements
      </Typography>
      <TextField
        size="small"
        placeholder="Search statement text or hash prefix…"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        sx={{ width: 340, mb: 1, display: "block" }}
        InputProps={{
          startAdornment: (
            <InputAdornment position="start">
              <Search fontSize="small" />
            </InputAdornment>
          ),
        }}
      />
      {error != null && <Alert severity="error">Failed to load the SQL audit: {error}</Alert>}
      {!error && loading && rows.length === 0 && (
        <Typography variant="body2" color="text.secondary">loading…</Typography>
      )}
      {!error && !loading && rows.length === 0 && (
        <Typography variant="body2" color="text.secondary">
          No audited statements match — the sampler (database.sql_audit) seeds
          on its next pass.
        </Typography>
      )}
      {rows.length > 0 && (
        <>
          {/* Sort refetch feedback: stale rows stay on screen while the
              new ordering loads — the spinner marks the in-flight fetch. */}
          <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 1 }}>
            <ToggleButtonGroup
              size="small"
              exclusive
              value={order}
              onChange={(_, v) => {
                if (v != null) setOrder(v);
              }}
            >
              {ORDERS.map((o) => (
                <ToggleButton key={o} value={o}>
                  {o === "total_ms" ? "by total time" : o === "calls" ? "by calls" : o === "recent" ? "by recent" : "by avg time"}
                </ToggleButton>
              ))}
            </ToggleButtonGroup>
            {loading && <CircularProgress size={16} aria-label="loading sql audit" />}
          </Box>
          <TableContainer sx={{ maxHeight: 420 }}>
            <Table stickyHeader size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Statement</TableCell>
                  <TableCell align="right">Calls</TableCell>
                  <TableCell align="right">Total</TableCell>
                  <TableCell align="right">Avg</TableCell>
                  <TableCell align="right">Max</TableCell>
                  <TableCell align="right">Rows</TableCell>
                  <TableCell>Where</TableCell>
                  <TableCell align="right">Last active</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {rows.map((r) => (
                  <TableRow key={r.sqlHash}>
                    <Tooltip
                      title={
                        <>
                          <Typography variant="caption" sx={{ fontFamily: "monospace" }}>
                            {r.sqlHash.slice(0, 16)}…
                          </Typography>
                          <Typography variant="caption" component="pre" sx={{ fontFamily: "monospace", whiteSpace: "pre-wrap", maxWidth: 640, maxHeight: 320, overflow: "auto", m: 0 }}>
                            {r.sqlText}
                          </Typography>
                        </>
                      }
                    >
                      <TableCell
                        sx={{
                          fontFamily: "monospace",
                          fontSize: 11,
                          maxWidth: 460,
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                      >
                        {r.sqlText.replace(/\s+/g, " ").slice(0, 90)}
                      </TableCell>
                    </Tooltip>
                    <TableCell align="right" sx={{ fontVariantNumeric: "tabular-nums" }}>
                      {fmtCount(r.callCount)}
                    </TableCell>
                    <TableCell align="right" sx={{ fontVariantNumeric: "tabular-nums" }}>
                      {fmtMs(r.totalExecMs)}
                    </TableCell>
                    <TableCell align="right" sx={{ fontVariantNumeric: "tabular-nums" }}>
                      {fmtMs(r.avgExecMs)}
                    </TableCell>
                    <TableCell align="right" sx={{ fontVariantNumeric: "tabular-nums" }}>
                      {fmtMs(r.maxExecMs)}
                    </TableCell>
                    <TableCell align="right" sx={{ fontVariantNumeric: "tabular-nums" }}>
                      {fmtCount(r.rowsTotal)}
                    </TableCell>
                    <TableCell sx={{ whiteSpace: "nowrap" }}>
                      <Tooltip title={`roles: ${r.roles.join(", ")}`}>
                        <Chip
                          size="small"
                          variant="outlined"
                          label={r.instances.join("+")}
                        />
                      </Tooltip>
                    </TableCell>
                    <TableCell align="right" sx={{ fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" }}>
                      {shortTime(r.lastActiveAt)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        </>
      )}
    </Paper>
  );
}
