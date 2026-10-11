/**
 * Source Data Audit page — per-security coverage of the raw source
 * datasets (monitor.src_data_audit): one row per (sec_type, code) with
 * exchange + board, n_days and first_date / last_date, upserted by the
 * classification build (index rows from stats.index_identity, stock rows
 * from stats.stock_identity) and the ETF OHLCV build (etf rows). Migrated
 * out of the classification JSON cache, whose hand-editable shards should
 * never have carried build-time data.
 *
 * Filter + check tool: the sec_type dropdown (SMALLINT label-code filter,
 * defaults to Index — the classification family this audit was migrated
 * from) narrows server-side, the search box matches code substring or
 * display name (debounced — the full universe is ~20k rows). Rows show
 * their audit refresh stamp (updated_at); a last_date far behind today
 * flags stale or dead source data, n_days = 0 means none at all.
 * Table chrome mirrors the Service Config page (Paper → named header box
 * with the visible/total count + filter + search, small dense table).
 *
 * Data: GET /api/monitor/src-data-audit (read replica). No auto-refresh —
 * the table only changes when a build runs; the header button refetches.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Box,
  CircularProgress,
  IconButton,
  InputAdornment,
  MenuItem,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { Clear, Refresh, Search } from "@mui/icons-material";
import { fmtDateTimeShanghai } from "@/shared/datetime";
import {
  fetchSrcDataAudit,
  type SrcDataAuditResponse,
} from "@/lib/api-client/monitor";

/** Debounce for the server-side search box (code / name substring). */
const SEARCH_DEBOUNCE_MS = 300;
/** Server-side row cap (API clamps to 20000) — sits above the full
 *  universe (~20k), so each type renders complete. */
const ROW_LIMIT = 20000;
/** Default filter: the index family — the audit's original scope. */
const DEFAULT_SEC_TYPE = "index";

const TYPE_OPTIONS = ["index", "etf", "stock"] as const;

const TYPE_LABEL: Record<string, string> = {
  index: "Index",
  etf: "ETF",
  stock: "Stock",
};

export default function SrcDataAuditPage() {
  const [secType, setSecType] = useState<string>(DEFAULT_SEC_TYPE);
  const [search, setSearch] = useState("");
  const [data, setData] = useState<SrcDataAuditResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  // The in-flight request's filter set — guards a stale response from
  // overwriting a newer one when the dropdown / debounced search race.
  const reqSeqRef = useRef(0);

  const load = useCallback(async (type: string, q: string) => {
    const seq = ++reqSeqRef.current;
    setLoading(true);
    try {
      const resp = await fetchSrcDataAudit(type, q, ROW_LIMIT);
      if (seq !== reqSeqRef.current) return;
      setData(resp);
      setError(null);
    } catch (e) {
      if (seq === reqSeqRef.current) setError(String(e));
    } finally {
      if (seq === reqSeqRef.current) setLoading(false);
    }
  }, []);

  // Debounced search: every keystroke restarts the timer; the fetch fires
  // once typing settles. The sec_type dropdown fetches immediately.
  const [debouncedSearch, setDebouncedSearch] = useState(search);
  useEffect(() => {
    const id = setTimeout(() => setDebouncedSearch(search), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(id);
  }, [search]);
  useEffect(() => {
    load(secType, debouncedSearch.trim());
  }, [secType, debouncedSearch, load]);

  const totalCount = useMemo(
    () => Object.values(data?.counts ?? {}).reduce((a, b) => a + b, 0),
    [data],
  );
  const typedTotal =
    secType === "all" ? totalCount : (data?.counts[secType] ?? 0);
  const searching = debouncedSearch.trim().length > 0;

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <Box sx={{ display: "flex", alignItems: "center", gap: 2, flexWrap: "wrap" }}>
        <Typography variant="h6">Source Data Audit</Typography>
        <Typography variant="caption" color="text.secondary">
          {data
            ? `${totalCount.toLocaleString()} securities audited (` +
              ["index", "etf", "stock"]
                .filter((t) => data.counts[t] != null)
                .map((t) => `${data.counts[t].toLocaleString()} ${TYPE_LABEL[t] ?? t}`)
                .join(" · ") +
              ") · refreshed " + fmtDateTimeShanghai(data.generatedAt)
            : "loading…"}
        </Typography>
        <Box sx={{ flexGrow: 1 }} />
        <Tooltip title="Refetch from the read replica" arrow>
          <span>
            <IconButton
              size="small"
              aria-label="refresh source data audit"
              onClick={() => load(secType, debouncedSearch.trim())}
              disabled={loading}
              sx={{ bgcolor: "action.hover", "&:hover": { bgcolor: "action.selected" } }}
            >
              <Refresh fontSize="small" />
            </IconButton>
          </span>
        </Tooltip>
      </Box>

      {error && <Alert severity="error">{error}</Alert>}

      <Paper sx={{ overflowX: "auto" }}>
        <Box
          sx={{
            px: 2,
            pt: 1.5,
            pb: 1,
            display: "flex",
            alignItems: "center",
            gap: 2,
            flexWrap: "wrap",
          }}
        >
          <Typography variant="subtitle2">Source data coverage</Typography>
          <Typography variant="caption" color="text.secondary">
            {data != null
              ? `${data.rows.length.toLocaleString()} of ${typedTotal.toLocaleString()} rows`
              : "loading…"}
          </Typography>
          {loading && <CircularProgress size={14} />}
          <Box sx={{ flexGrow: 1 }} />
          <TextField
            select
            size="small"
            label="Type"
            value={secType}
            onChange={(e) => setSecType(e.target.value)}
            sx={{ minWidth: 140 }}
          >
            {TYPE_OPTIONS.map((t) => (
              <MenuItem key={t} value={t}>
                {TYPE_LABEL[t]}
              </MenuItem>
            ))}
            <MenuItem value="all">All types</MenuItem>
          </TextField>
          <TextField
            size="small"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search code or name…"
            sx={{ width: 260 }}
            InputProps={{
              startAdornment: (
                <InputAdornment position="start">
                  <Search fontSize="small" sx={{ color: "text.secondary" }} />
                </InputAdornment>
              ),
              endAdornment: searching ? (
                <InputAdornment position="end">
                  <IconButton
                    size="small"
                    aria-label="clear search"
                    onClick={() => setSearch("")}
                  >
                    <Clear fontSize="small" />
                  </IconButton>
                </InputAdornment>
              ) : undefined,
            }}
          />
        </Box>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Type</TableCell>
              <TableCell>Code</TableCell>
              <TableCell>Name</TableCell>
              <TableCell>Exchange</TableCell>
              <TableCell>Board</TableCell>
              <TableCell align="right">Days</TableCell>
              <TableCell>First date</TableCell>
              <TableCell>Last date</TableCell>
              <TableCell>Audit refreshed</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {(data?.rows ?? []).map((r) => (
              <TableRow key={`${r.secType}:${r.code}`} hover>
                <TableCell>{TYPE_LABEL[r.secType] ?? r.secType}</TableCell>
                <TableCell sx={{ fontFamily: "monospace" }}>{r.code}</TableCell>
                <TableCell>{r.name ?? "—"}</TableCell>
                <TableCell>{r.exchange ?? "—"}</TableCell>
                <TableCell>{r.board ?? "—"}</TableCell>
                <TableCell align="right">{r.nDays.toLocaleString()}</TableCell>
                <TableCell>{r.firstDate ?? "—"}</TableCell>
                <TableCell>{r.lastDate ?? "—"}</TableCell>
                <TableCell>{fmtDateTimeShanghai(r.updatedAt)}</TableCell>
              </TableRow>
            ))}
            {!data && !error && (
              <TableRow>
                <TableCell colSpan={9} align="center">
                  <CircularProgress size={20} sx={{ my: 2 }} />
                </TableCell>
              </TableRow>
            )}
            {data && data.rows.length === 0 && (
              <TableRow>
                <TableCell colSpan={9} align="center">
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{ my: 2, display: "inline-block" }}
                  >
                    {searching
                      ? `No audit row matches “${debouncedSearch.trim()}”`
                      : "No source-data audit rows yet — run builds.classification (index + stock) or the ETF build"}
                  </Typography>
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </Paper>
    </Box>
  );
}
