/**
 * UsageDayBreakdown — the selected GMT day's llm.llm_requests buckets,
 * one row per (service, model): request / failed counts, the usage-block
 * token sums (input / output / total) and whole-call durations. Rows
 * arrive from the page's usage payload (already pivoted once), sorted
 * here by total tokens descending so the day's heaviest callers lead.
 */
import { useMemo } from "react";
import {
  Box,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import type { AiUsageEntry } from "@/lib/api-client/monitor";

function fmtMs(ms: number | null): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

function fmtTokens(n: number): string {
  return n.toLocaleString();
}

export default function UsageDayBreakdown({
  date,
  rows,
}: {
  date: string;
  rows: AiUsageEntry[];
}) {
  const sorted = useMemo(
    () => [...rows].sort((a, b) => b.totalTokens - a.totalTokens),
    [rows],
  );
  const totalRequests = rows.reduce((a, r) => a + r.requestCount, 0);
  const totalFailed = rows.reduce((a, r) => a + r.failedCount, 0);
  const totalTokens = rows.reduce((a, r) => a + r.totalTokens, 0);
  return (
    <Paper sx={{ overflowX: "auto" }}>
      <Box
        sx={{
          px: 2,
          pt: 1.5,
          pb: 0.5,
          display: "flex",
          alignItems: "center",
          gap: 2,
          flexWrap: "wrap",
        }}
      >
        <Typography variant="subtitle2">Breakdown — {date} (GMT)</Typography>
        <Typography variant="caption" color="text.secondary">
          GMT day — runs Beijing 08:00 → next 08:00 · {rows.length} bucket(s)
          · {totalRequests.toLocaleString()} requests ·{" "}
          {totalFailed.toLocaleString()} failed ·{" "}
          {fmtTokens(totalTokens)} tokens
        </Typography>
      </Box>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Service</TableCell>
            <TableCell>Model</TableCell>
            <TableCell align="right">Requests</TableCell>
            <TableCell align="right">Failed</TableCell>
            <TableCell align="right">Input tokens</TableCell>
            <TableCell align="right">Output tokens</TableCell>
            <TableCell align="right">Total tokens</TableCell>
            <TableCell align="right">Avg duration</TableCell>
            <TableCell align="right">Max duration</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {sorted.map((r) => (
            <TableRow key={`${r.service}|${r.model ?? "none"}`}>
              <TableCell sx={{ fontFamily: "monospace" }}>{r.service}</TableCell>
              <TableCell sx={{ fontFamily: "monospace", fontSize: "0.75rem" }}>
                {r.model ?? "—"}
              </TableCell>
              <TableCell align="right">{r.requestCount}</TableCell>
              <TableCell
                align="right"
                sx={{ color: r.failedCount > 0 ? "error.main" : undefined }}
              >
                {r.failedCount}
              </TableCell>
              <TableCell align="right">{fmtTokens(r.inputTokens)}</TableCell>
              <TableCell align="right">{fmtTokens(r.outputTokens)}</TableCell>
              <TableCell align="right">{fmtTokens(r.totalTokens)}</TableCell>
              <TableCell align="right">{fmtMs(r.avgDurationMs)}</TableCell>
              <TableCell align="right">{fmtMs(r.maxDurationMs)}</TableCell>
            </TableRow>
          ))}
          {rows.length === 0 && (
            <TableRow>
              <TableCell colSpan={9} align="center">
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ my: 2, display: "inline-block" }}
                >
                  No LLM requests on this date.
                </Typography>
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </Paper>
  );
}
