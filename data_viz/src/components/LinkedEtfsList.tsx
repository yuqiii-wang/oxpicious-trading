/**
 * LinkedEtfsList — paginated table of ETFs tracking the given index.
 *
 * Shown on the Index Baseline page beside the Composition pie chart.
 * Mirrors the CompositionPieChart open/toggle pattern: the parent
 * (IndexPanel) controls `open` / `onToggle` so the parent card can expand
 * to fit the table.
 *
 * Pagination: 10 ETFs per page (MUI Pagination control below the table).
 *
 * Data source: /api/sec-composition/linked-etfs?code=<index code>, which
 * queries stats.sec_classification (type='etf', parent_index_code = code)
 * JOIN stats.v_etf_margin for latest close + n_days.
 */
import { useEffect, useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Pagination,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
  useTheme,
} from "@mui/material";
import { Link as LinkIcon } from "@mui/icons-material";
import RefreshButton from "@/components/RefreshButton";
import { fetchLinkedEtfs, invalidateCacheForUrl } from "@/lib/api-client";
import { fmtNum } from "@/lib/series";
import type { LinkedEtfsResponse } from "../../shared/types";
import {
  expandedTableAggCellSx,
  expandedTableBodyCellSx,
  expandedTableBodyRowSx,
  expandedTableContainerSx,
  expandedTableHeadCellSx,
} from "@/shared/styles/expanded-table-styles";

interface Props {
  /** Bare index code (e.g. "000300") — passed through to the API. */
  code: string;
  /** Controlled open state — lifted to parent so it can expand the card. */
  open: boolean;
  onToggle: () => void;
  /** When true, the toggle + refresh buttons are NOT rendered — the parent
   *  renders them in a shared button row. Defaults to false. */
  hideButton?: boolean;
  /** External refresh key — when provided, the parent owns the refresh state.
   *  Bumping this value triggers a refetch. */
  refreshKey?: number;
  /** Notifies the parent of loading-state changes so the parent's refresh
   *  button can show a spinner. Only meaningful when hideButton=true. */
  onLoadingChange?: (loading: boolean) => void;
}

const PAGE_SIZE = 10;

export default function LinkedEtfsList({
  code,
  open,
  onToggle,
  hideButton = false,
  refreshKey: externalRefreshKey,
  onLoadingChange,
}: Props) {
  const theme = useTheme();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<LinkedEtfsResponse | null>(null);
  const [page, setPage] = useState(1);
  // Plot-level refresh key — bumped by the refresh button to force a cache
  // bypass + refetch of this index's linked ETFs.
  const [internalRefreshKey, setInternalRefreshKey] = useState(0);
  // When the parent provides an external refresh key (hideButton mode), it
  // owns the refresh state; otherwise the internal key is used.
  const refreshKey = externalRefreshKey ?? internalRefreshKey;

  // Ref so onLoadingChange can be called from the fetch effect without
  // being added to the effect's dependency array (avoids re-run loops).
  const onLoadingChangeRef = useRef(onLoadingChange);
  onLoadingChangeRef.current = onLoadingChange;

  useEffect(() => {
    if (!open || !code) return;
    let cancelled = false;
    setLoading(true);
    onLoadingChangeRef.current?.(true);
    setError(null);
    fetchLinkedEtfs(code)
      .then((d) => {
        if (cancelled) return;
        setData(d);
        setLoading(false);
        onLoadingChangeRef.current?.(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message);
        setLoading(false);
        onLoadingChangeRef.current?.(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, code, refreshKey]);

  // Reset to page 1 whenever the data source changes (new code or refresh).
  useEffect(() => {
    setPage(1);
  }, [code, refreshKey]);

  const handleRefresh = () => {
    invalidateCacheForUrl(`/api/sec-composition/linked-etfs?code=${code}`);
    setInternalRefreshKey((k) => k + 1);
  };

  const totalEtfs = data?.etfs.length ?? 0;
  const totalPages = Math.max(1, Math.ceil(totalEtfs / PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);
  const pagedEtfs = (data?.etfs ?? []).slice(
    (currentPage - 1) * PAGE_SIZE,
    currentPage * PAGE_SIZE,
  );

  // ---- Aggregation row values (computed over ALL linked ETFs, not just the
  //      current page). total_etf_trading_amount / total_etf_trading_amount_ma5 come from
  //      stats.index_exts (Σ ETF turnover tracking the index, SSE+SZSE, yuan);
  //      the per-ETF "Trading Amt" column is SZSE-only (v_etf_margin), so its
  //      sum is reported separately and generally ≤ total_etf_trading_amount.
  const sumTradingAmtYi = (() => {
    if (!data) return null;
    let sum = 0;
    let any = false;
    for (const e of data.etfs) {
      if (e.latest_trading_amount != null) {
        // latest_trading_amount is in yuan (from v_etf_margin.trading_amount);
        // convert to 亿元 (/1e8) to match the "Trading Amt (亿)" column.
        sum += e.latest_trading_amount / 1e8;
        any = true;
      }
    }
    return any ? sum : null;
  })();
  const sumAumYi = (() => {
    if (!data) return null;
    let sum = 0;
    let any = false;
    for (const e of data.etfs) {
      if (e.aum_yi != null) {
        sum += e.aum_yi;
        any = true;
      }
    }
    return any ? sum : null;
  })();
  const totalEtfAmtYi =
    data?.total_etf_trading_amount != null ? data.total_etf_trading_amount / 1e8 : null;
  const totalEtfAmtMa5Yi =
    data?.total_etf_trading_amount_ma5 != null ? data.total_etf_trading_amount_ma5 / 1e8 : null;
  const aggDate =
    data?.total_etf_trading_amount_date ||
    data?.etfs.reduce((m, e) => (e.latest_date > m ? e.latest_date : m), "") ||
    "";

  // Sticky aggregation-row cell style — frozen top pane. position: sticky on
  // <td> pins the row to the top of the scrollable TableContainer so the
  // index-level totals stay visible while the per-ETF rows scroll beneath.
  const AGG_SX = expandedTableAggCellSx(theme);

  return (
    <Box>
      {!hideButton && (
        <Box sx={{ display: "flex", alignItems: "center", gap: 0.5, flexWrap: "wrap" }}>
          <Button
            size="small"
            variant="outlined"
            startIcon={<LinkIcon />}
            onClick={onToggle}
            sx={{ fontSize: "0.7rem", textTransform: "none", mt: 0.5 }}
          >
            {open ? "Hide Linked ETFs" : "Linked ETFs"}
          </Button>
          {open && (
            <RefreshButton
              onClick={handleRefresh}
              loading={loading}
              size="tiny"
              tooltip={`Refresh linked ETFs for ${code}`}
            />
          )}
        </Box>
      )}

      {open && (
        <Box sx={{ mt: 1 }}>
          {loading && (
            <Stack direction="row" spacing={1} alignItems="center">
              <CircularProgress size={16} />
              <Typography variant="caption" color="text.secondary">
                Loading linked ETFs…
              </Typography>
            </Stack>
          )}
          {error && (
            <Alert severity="error" sx={{ py: 0.5 }}>
              {error}
            </Alert>
          )}
          {data && !loading && (
            <>
              {data.etfs.length === 0 ? (
                <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.7rem" }}>
                  No ETFs track this index ({code}).
                </Typography>
              ) : (
                <>
                  <Stack
                    direction="row"
                    spacing={1}
                    alignItems="center"
                    sx={{ mb: 0.5 }}
                    flexWrap="wrap"
                    useFlexGap
                  >
                    <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.7rem" }}>
                      {data.etfs.length} ETF{data.etfs.length === 1 ? "" : "s"} tracking {data.index_code}
                    </Typography>
                  </Stack>
                  <TableContainer sx={expandedTableContainerSx(460)}>
                    <Table size="small" sx={{ minWidth: 880 }}>
                      <TableHead>
                        <TableRow>
                          <TableCell sx={expandedTableHeadCellSx}>Code</TableCell>
                          <TableCell sx={expandedTableHeadCellSx}>Name</TableCell>
                          <TableCell sx={expandedTableHeadCellSx}>Exch</TableCell>
                          <TableCell sx={expandedTableHeadCellSx} align="right">Close</TableCell>
                          <TableCell sx={expandedTableHeadCellSx} align="right">Trading Amt (亿)</TableCell>
                          <TableCell sx={expandedTableHeadCellSx} align="right">Total ETF Amt (亿)</TableCell>
                          <TableCell sx={expandedTableHeadCellSx} align="right">Amt MA5 (亿)</TableCell>
                          <TableCell sx={expandedTableHeadCellSx} align="right">Valuation Amt (亿)</TableCell>
                          <TableCell sx={expandedTableHeadCellSx}>Latest Date</TableCell>
                          <TableCell sx={expandedTableHeadCellSx} align="right">Days</TableCell>
                          <TableCell sx={expandedTableHeadCellSx}>Industry</TableCell>
                        </TableRow>
                      </TableHead>
                      <TableBody>
                        {/* Frozen aggregation row — index-level totals from
                            stats.index_exts (total_etf_trading_amount, total_etf_trading_amount_ma5)
                            plus client-side sums over all linked ETFs.
                            position: sticky pins it to the top of the
                            scrollable container so it stays visible. */}
                        <TableRow>
                          <TableCell sx={AGG_SX}>Σ</TableCell>
                          <TableCell sx={AGG_SX}>{totalEtfs} ETF{totalEtfs === 1 ? "" : "s"}</TableCell>
                          <TableCell sx={AGG_SX}>—</TableCell>
                          <TableCell sx={{ ...AGG_SX }} align="right">—</TableCell>
                          <TableCell sx={{ ...AGG_SX, fontFamily: "monospace" }} align="right">
                            {sumTradingAmtYi != null ? fmtNum(sumTradingAmtYi) : "—"}
                          </TableCell>
                          <TableCell sx={{ ...AGG_SX, fontFamily: "monospace" }} align="right">
                            {totalEtfAmtYi != null ? fmtNum(totalEtfAmtYi) : "—"}
                          </TableCell>
                          <TableCell sx={{ ...AGG_SX, fontFamily: "monospace" }} align="right">
                            {totalEtfAmtMa5Yi != null ? fmtNum(totalEtfAmtMa5Yi) : "—"}
                          </TableCell>
                          <TableCell sx={{ ...AGG_SX, fontFamily: "monospace" }} align="right">
                            {sumAumYi != null ? fmtNum(sumAumYi) : "—"}
                          </TableCell>
                          <TableCell sx={AGG_SX}>{aggDate || "—"}</TableCell>
                          <TableCell sx={{ ...AGG_SX, fontFamily: "monospace" }} align="right">—</TableCell>
                          <TableCell sx={AGG_SX}>—</TableCell>
                        </TableRow>
                        {pagedEtfs.map((e, idx) => (
                          <TableRow key={e.code} sx={expandedTableBodyRowSx(idx)}>
                            <TableCell sx={{ ...expandedTableBodyCellSx, fontWeight: 600 }}>{e.code}</TableCell>
                            <TableCell sx={expandedTableBodyCellSx}>{e.name || "—"}</TableCell>
                            <TableCell sx={expandedTableBodyCellSx}>
                              {e.exchange ? (
                                <Chip
                                  label={e.exchange}
                                  size="small"
                                  variant="outlined"
                                  sx={{ fontSize: "0.6rem", height: 16 }}
                                />
                              ) : (
                                "—"
                              )}
                            </TableCell>
                            <TableCell sx={{ ...expandedTableBodyCellSx, fontFamily: "monospace" }} align="right">
                              {e.latest_close != null ? fmtNum(e.latest_close) : "—"}
                            </TableCell>
                            <TableCell sx={{ ...expandedTableBodyCellSx, fontFamily: "monospace" }} align="right">
                              {e.latest_trading_amount != null ? fmtNum(e.latest_trading_amount / 1e8) : "—"}
                            </TableCell>
                            {/* Index-level metrics — not applicable per-ETF. */}
                            <TableCell sx={{ ...expandedTableBodyCellSx, color: "text.disabled" }} align="right">—</TableCell>
                            <TableCell sx={{ ...expandedTableBodyCellSx, color: "text.disabled" }} align="right">—</TableCell>
                            <TableCell sx={{ ...expandedTableBodyCellSx, fontFamily: "monospace" }} align="right">
                              {e.aum_yi != null ? fmtNum(e.aum_yi) : "—"}
                            </TableCell>
                            <TableCell sx={expandedTableBodyCellSx}>{e.latest_date || "—"}</TableCell>
                            <TableCell sx={{ ...expandedTableBodyCellSx, fontFamily: "monospace" }} align="right">
                              {e.n_days > 0 ? e.n_days : "—"}
                            </TableCell>
                            <TableCell sx={expandedTableBodyCellSx}>{e.industry_label || "—"}</TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </TableContainer>
                  {totalPages > 1 && (
                    <Box sx={{ display: "flex", justifyContent: "center", pt: 1 }}>
                      <Pagination
                        count={totalPages}
                        page={currentPage}
                        onChange={(_e, v) => setPage(v)}
                        color="primary"
                        size="small"
                        showFirstButton
                        showLastButton
                      />
                    </Box>
                  )}
                </>
              )}
            </>
          )}
        </Box>
      )}
    </Box>
  );
}
