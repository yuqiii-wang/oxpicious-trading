/**
 * LinkedEtfsFeedersTable — the ONE unified expansion table for the whole
 * index → ETF → feeder-fund chain (merges the former LinkedEtfsList +
 * LinkedFeederFundsList pair, both retired):
 *
 *   index ← parent_index_code ← ETF ← sec_etf_links.target_etf_code ← feeder (联接基金)
 *
 * One flat table under the title "Index → ETF → Feeder Fund (联接基金)",
 * with ONE shared column set and the expanded-table style: per-column header
 * filters → sticky Σ aggregation row → zebra rows → 10-per-page pagination.
 * Feeder sub-rows REUSE the same columns beneath their ETF (no separate
 * feeder column group): feeder code/name/NAV map onto Code/Name/Close-NAV,
 * feeder AUM onto AUM; the feeder-only details (发起式, manager, inception)
 * live in the name tooltip and the 日涨跌 rides the NAV cell.
 *
 * Feeder sub-rows are HIDDEN by default. Clicking an ETF row that HAS
 * feeders (feeder_count > 0, signaled by the ▶ chevron before its Code)
 * expands its feeder sub-rows beneath it — fetched lazily from
 * /api/sec-composition/linked-feeders (cached per ETF until the table
 * scope/refresh changes) — and opens the ETF's OWN daily trend beneath the
 * table at the same time (collapsing the feeders closes that trend again).
 * ETF rows WITHOUT feeders keep the plain row-click daily-trend expansion;
 * feeder sub-rows open the feeder's OWN close-only NAV trend (bare code
 * through the secType "etf" baseline — builds.etf.feeder_nav loads the NAV
 * series).
 *
 * Data source: /api/sec-composition/linked-etfs?code=<index> (rows + Σ meta
 * + per-row feeder counts). Hosted twice with the same contract:
 *   · IndexPanel     — code = the index shown on the panel
 *   · EtfMarginPanel — code = the ETF's tracking index, focusCode = the ETF
 *     (highlighted + paged into view, so its feeders stay one click away)
 */
import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
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
  Tooltip,
  Typography,
  useTheme,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import {
  KeyboardArrowDown,
  KeyboardArrowRight,
  Link as LinkIcon,
} from "@mui/icons-material";
import RefreshButton from "@/components/RefreshButton";
import CodeTrendExpansionChart from "@/components/CodeTrendExpansionChart";
import useTableHeaderFilters, { type HeaderFilterDef } from "@/hooks/table-header-filters";
import {
  fetchLinkedEtfs,
  fetchLinkedFeeders,
  invalidateCacheForPrefix,
  invalidateCacheForUrl,
} from "@/lib/api-client";
import { fmtNum } from "@/lib/series";
import type { CodeTrendSecType } from "@/ui/OhlcChart";
import type { LinkedEtfRow, LinkedEtfsResponse, LinkedFeederRow } from "@shared/types";
import {
  expandedTableAggCellSx,
  expandedTableBodyCellSx,
  expandedTableBodyRowSx,
  expandedTableContainerSx,
  expandedTableHeadCellSx,
} from "@/shared/styles/expanded-table-styles";

const EMPTY_RESPONSE: LinkedEtfsResponse = {
  index_code: "",
  index_name: "",
  etfs: [],
  total_etf_trading_amount: null,
  total_etf_trading_amount_ma5: null,
  total_etf_trading_amount_date: "",
};

const PAGE_SIZE = 10;

/** Row-click daily-trend target (CodeTrendChart via /api/sec-baseline). */
interface RowTrend {
  code: string;
  name: string;
  secType: CodeTrendSecType;
}

/** One shared column for both row kinds — ETF rows and expanded feeder
 *  sub-rows render into the SAME grid. Columns without a feederCell (ETF-only
 *  metrics) render empty cells for feeder sub-rows. Header filters stay
 *  ETF-only — feeder sub-rows are drill-down detail, not an independently
 *  filterable set (filtering hidden rows is confusing). */
interface ChainColumn {
  key: string;
  label: string;
  align?: "left" | "right";
  filter?: HeaderFilterDef<LinkedEtfRow>;
  cell: (e: LinkedEtfRow) => ReactNode;
  feederCell?: (f: LinkedFeederRow) => ReactNode;
}

/** Shorten the feeder legal name to its theme label: strip the boilerplate
 *  "交易型开放式指数证券投资基金(发起式)联接基金" tail, keep "…联接". */
function feederShortName(name: string): string {
  const m = name.match(/^(.*?)交易型开放式指数证券投资基金/);
  return `${m && m[1] ? m[1] : name}联接`;
}

/** Feeder name tooltip — the full legal name plus the feeder-only details
 *  that have no dedicated column now that feeders reuse the ETF columns
 *  (发起式, manager, inception). */
function feederDetail(f: LinkedFeederRow): ReactNode {
  const meta = [
    f.is_initiator ? "发起式" : null,
    f.manager || null,
    f.inception_date ? `Inception ${f.inception_date}` : null,
  ].filter((s): s is string => s != null);
  return (
    <Stack sx={{ gap: 0.25 }}>
      <Typography variant="caption" sx={{ fontSize: "0.7rem" }}>{f.feeder_name}</Typography>
      {meta.length > 0 && (
        <Typography variant="caption" sx={{ fontSize: "0.65rem", opacity: 0.85 }}>
          {meta.join(" · ")}
        </Typography>
      )}
    </Stack>
  );
}

/** A股 up/down coloring for the 日涨跌 cell (red = up, green = down). */
function pctColor(v: number | null): string | undefined {
  if (v == null || v === 0) return undefined;
  return v > 0 ? "#d32f2f" : "#2e7d32";
}

/** Loose security-code equality — ignores any exchange suffix so codes
 *  from mixed sources still match (ETF codes are bare everywhere since
 *  2026-09-30; the strip also covers stock codes from shared callers). */
function sameSecCode(a: string, b: string): boolean {
  const strip = (c: string) => c.replace(/\.(SS|SZ|BJ|HK)$/i, "");
  return strip(a) === strip(b);
}

/** The shared column set — ETF rows and expanded feeder sub-rows reuse the
 *  same grid. ETF-only metrics (trading amt, days, industry) have no
 *  feederCell, so feeder sub-rows leave them empty. The Code column's ETF
 *  cell is rendered inline in the row map (it carries the feeder-expansion
 *  chevron, which needs the per-row expansion state). */
const COLUMNS: ChainColumn[] = [
  {
    key: "code", label: "Code",
    cell: (e) => <span style={{ fontWeight: 600 }}>{e.code}</span>,
    feederCell: (f) => (
      <span style={{ whiteSpace: "nowrap" }}>
        <span style={{ color: "text.secondary" }}>↳ </span>
        <span style={{ fontWeight: 600 }}>{f.feeder_code}</span>
      </span>
    ),
  },
  {
    key: "name", label: "Name",
    cell: (e) => e.name || "—",
    feederCell: (f) => (
      <Tooltip title={feederDetail(f)} arrow>
        <span>{feederShortName(f.feeder_name)}</span>
      </Tooltip>
    ),
  },
  {
    key: "exchange", label: "Exch",
    filter: { key: "exchange", label: "Exch", type: "ticks", value: (e) => e.exchange || null },
    cell: (e) =>
      e.exchange ? (
        <Chip label={e.exchange} size="small" variant="outlined" sx={{ fontSize: "0.6rem", height: 16 }} />
      ) : (
        "—"
      ),
    // Feeders are OTC funds — no exchange listing.
    feederCell: () => (
      <Chip label="OTC" size="small" variant="outlined" sx={{ fontSize: "0.6rem", height: 16 }} />
    ),
  },
  {
    // ETFs show the latest close; feeders show the latest 单位净值 with the
    // 日涨跌 as a colored suffix (red = up, green = down; tooltip = NAV date).
    key: "latest_close", label: "Close / NAV",
    filter: { key: "latest_close", label: "Close", type: "range", value: (e) => e.latest_close },
    align: "right",
    cell: (e) => (e.latest_close != null ? <span style={{ fontFamily: "monospace" }}>{fmtNum(e.latest_close)}</span> : "—"),
    feederCell: (f) =>
      f.latest_nav != null ? (
        <Tooltip title={f.latest_date || undefined} arrow>
          <span style={{ fontFamily: "monospace" }}>
            {fmtNum(f.latest_nav)}
            {f.day_pct != null && (
              <span style={{ color: pctColor(f.day_pct) }}>
                {` ${f.day_pct > 0 ? "+" : ""}${f.day_pct.toFixed(2)}%`}
              </span>
            )}
          </span>
        </Tooltip>
      ) : (
        "—"
      ),
  },
  {
    key: "trading_amt", label: "Trading Amt (亿)",
    // Filter on the 亿元 value the cell displays.
    filter: {
      key: "trading_amt", label: "Trading Amt (亿)", type: "range",
      value: (e) => (e.latest_trading_amount != null ? e.latest_trading_amount / 1e8 : null),
    },
    align: "right",
    cell: (e) => (e.latest_trading_amount != null ? <span style={{ fontFamily: "monospace" }}>{fmtNum(e.latest_trading_amount / 1e8)}</span> : "—"),
  },
  {
    key: "aum", label: "AUM (亿)",
    filter: { key: "aum", label: "AUM (亿)", type: "range", value: (e) => e.aum_yi },
    align: "right",
    cell: (e) => (e.aum_yi != null ? <span style={{ fontFamily: "monospace" }}>{fmtNum(e.aum_yi)}</span> : "—"),
    feederCell: (f) => (f.aum_yi != null && f.aum_yi > 0 ? <span style={{ fontFamily: "monospace" }}>{fmtNum(f.aum_yi)}</span> : "—"),
  },
  {
    key: "latest_date", label: "Latest Date",
    filter: { key: "latest_date", label: "Latest Date", type: "date", value: (e) => e.latest_date || null },
    cell: (e) => e.latest_date || "—",
    feederCell: (f) => f.latest_date || "—",
  },
  {
    key: "n_days", label: "Days",
    filter: { key: "n_days", label: "Days", type: "range", value: (e) => (e.n_days > 0 ? e.n_days : null) },
    align: "right",
    cell: (e) => (e.n_days > 0 ? <span style={{ fontFamily: "monospace" }}>{e.n_days}</span> : "—"),
  },
  {
    key: "industry", label: "Industry",
    filter: { key: "industry", label: "Industry", type: "ticks", value: (e) => e.industry_label || null },
    cell: (e) => e.industry_label || "—",
  },
];

interface Props {
  /** Index code (bare, e.g. "000300") — the ETFs listed track this index. */
  code: string;
  /** ETF code (bare) to highlight and page into view once per scope —
   *  EtfMarginPanel passes its own ETF so the row is easy to find. */
  focusCode?: string;
  /** Overrides the default empty-state text (default distinguishes an empty
   *  index from a MISSING tracking index). */
  emptyText?: (code: string) => ReactNode;
  loadingText?: string;

  // ---- shared expansion-button contract (same as the retired lists) ----
  open: boolean;
  onToggle: () => void;
  /** When true, the toggle + refresh buttons are NOT rendered — the parent
   *  renders them in a shared button row. */
  hideButton?: boolean;
  /** External refresh key — when provided, the parent owns the refresh state. */
  refreshKey?: number;
  /** Parent loading-state notifications (hideButton mode). */
  onLoadingChange?: (loading: boolean) => void;
}

export default function LinkedEtfsFeedersTable({
  code,
  focusCode,
  emptyText,
  loadingText = "Loading the index → ETF → feeder fund chain…",
  open,
  onToggle,
  hideButton = false,
  refreshKey: externalRefreshKey,
  onLoadingChange,
}: Props) {
  const theme = useTheme();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [payload, setPayload] = useState<LinkedEtfsResponse | null>(null);
  const [page, setPage] = useState(1);
  const [internalRefreshKey, setInternalRefreshKey] = useState(0);
  // When the parent provides an external refresh key (hideButton mode), it
  // owns the refresh state; otherwise the internal key is used.
  const refreshKey = externalRefreshKey ?? internalRefreshKey;

  // Feeder expansion state — all keyed by ETF code (bare).
  const [expandedKeys, setExpandedKeys] = useState<ReadonlySet<string>>(() => new Set());
  const [feedersByEtf, setFeedersByEtf] = useState<Record<string, LinkedFeederRow[]>>({});
  const [feederErrors, setFeederErrors] = useState<Record<string, string>>({});
  const [feederLoadingKeys, setFeederLoadingKeys] = useState<ReadonlySet<string>>(() => new Set());

  // Row-click trend expansion: the code whose daily trend is expanded beneath
  // the table (an ETF without feeders, or a feeder fund — one at a time).
  const [selectedTrend, setSelectedTrend] = useState<RowTrend | null>(null);

  // Generation guard — feeder fetches in flight when the scope/refresh
  // changes are dropped instead of repopulating the cleared cache.
  const feederGenRef = useRef(0);
  // One-shot focus page-jump per scope (do not yank the user's page when
  // they filter or page manually afterwards).
  const jumpedRef = useRef(false);

  const onLoadingChangeRef = useRef(onLoadingChange);
  onLoadingChangeRef.current = onLoadingChange;
  const setLoad = (v: boolean) => {
    setLoading(v);
    onLoadingChangeRef.current?.(v);
  };

  useEffect(() => {
    if (!open) return;
    if (!code) {
      setPayload(EMPTY_RESPONSE);
      return;
    }
    let cancelled = false;
    setLoad(true);
    setError(null);
    fetchLinkedEtfs(code)
      .then((d) => {
        if (cancelled) return;
        setPayload(d);
        setLoad(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message);
        setLoad(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, code, refreshKey]);

  // Reset the expansion state whenever the data source changes (new index
  // code or refresh): page 1, trend closed, all feeder rows collapsed and
  // the per-ETF feeder cache dropped.
  useEffect(() => {
    setPage(1);
    setSelectedTrend(null);
    setExpandedKeys(new Set());
    setFeedersByEtf({});
    setFeederErrors({});
    setFeederLoadingKeys(new Set());
    feederGenRef.current += 1;
    jumpedRef.current = false;
  }, [code, refreshKey]);

  const handleRefresh = () => {
    invalidateCacheForUrl(`/api/sec-composition/linked-etfs?code=${code}`);
    invalidateCacheForPrefix("/api/sec-composition/linked-feeders");
    setInternalRefreshKey((k) => k + 1);
  };

  const rows = payload?.etfs ?? [];
  const total = rows.length;
  const totalFeeders = rows.reduce((s, e) => s + e.feeder_count, 0);

  // Header filters apply BEFORE pagination; the pager and the table show
  // only rows matching every enabled column's filter.
  const filterDefs = COLUMNS
    .map((c) => (c.filter ? { ...c.filter } : null))
    .filter((d): d is HeaderFilterDef<LinkedEtfRow> => d !== null);
  const { filtered, menuFor, anyActive } = useTableHeaderFilters(
    filterDefs,
    rows,
    [code, refreshKey],
  );
  const visible = filtered.length;
  const totalPages = Math.max(1, Math.ceil(visible / PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);
  const pagedRows = filtered.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);

  // Page the focused ETF into view once per scope (EtfMarginPanel usage).
  useEffect(() => {
    if (!focusCode || !payload || jumpedRef.current) return;
    jumpedRef.current = true;
    const idx = filtered.findIndex((e) => sameSecCode(e.code, focusCode));
    if (idx >= PAGE_SIZE) setPage(Math.floor(idx / PAGE_SIZE) + 1);
  }, [filtered, focusCode, payload]);

  const fetchFeedersFor = (etfCode: string) => {
    const gen = feederGenRef.current;
    setFeederLoadingKeys((prev) => new Set(prev).add(etfCode));
    fetchLinkedFeeders(etfCode)
      .then((d) => {
        if (gen !== feederGenRef.current) return;
        setFeedersByEtf((prev) => ({ ...prev, [etfCode]: d.feeders }));
      })
      .catch((e: Error) => {
        if (gen !== feederGenRef.current) return;
        setFeederErrors((prev) => ({ ...prev, [etfCode]: e.message }));
      })
      .finally(() => {
        if (gen !== feederGenRef.current) return;
        setFeederLoadingKeys((prev) => {
          const n = new Set(prev);
          n.delete(etfCode);
          return n;
        });
      });
  };

  // ETF row click: with feeders → toggle its feeder sub-rows (lazy fetch) AND
  // open the ETF's own daily trend beneath the table (collapsing the feeders
  // closes it again); without feeders → toggle the ETF's own daily trend.
  const handleEtfRowClick = (e: LinkedEtfRow) => {
    if (e.feeder_count > 0) {
      const willOpen = !expandedKeys.has(e.code);
      setExpandedKeys((prev) => {
        const n = new Set(prev);
        if (willOpen) n.add(e.code);
        else n.delete(e.code);
        return n;
      });
      if (willOpen && !(e.code in feedersByEtf)) {
        // Clear a previous error so this click retries the fetch.
        setFeederErrors((prev) => {
          if (!(e.code in prev)) return prev;
          const next = { ...prev };
          delete next[e.code];
          return next;
        });
        fetchFeedersFor(e.code);
      }
      setSelectedTrend((prev) =>
        willOpen
          ? { code: e.code, name: e.name || e.code, secType: "etf" }
          : prev?.code === e.code
            ? null
            : prev,
      );
    } else {
      setSelectedTrend((prev) =>
        prev?.code === e.code ? null : { code: e.code, name: e.name || e.code, secType: "etf" },
      );
    }
  };

  // Feeder sub-row click → the FEEDER'S OWN code trend beneath the table
  // (close-only NAV series through the etf baseline — v_etf_margin).
  const handleFeederRowClick = (f: LinkedFeederRow) => {
    setSelectedTrend((prev) =>
      prev?.code === f.feeder_code
        ? null
        : { code: f.feeder_code, name: feederShortName(f.feeder_name), secType: "etf" },
    );
  };

  const indexLabel =
    payload != null ? `${payload.index_code} ${payload.index_name}`.trim() : "";

  const AGG_SX = expandedTableAggCellSx(theme);
  // Feeder sub-row tint — a faint primary wash distinct from the parent
  // zebra rows and from the selected/expanded highlight.
  const feederRowSx = {
    bgcolor: alpha(theme.palette.primary.main, theme.palette.mode === "dark" ? 0.16 : 0.07),
    "&:hover": {
      bgcolor: alpha(theme.palette.primary.main, theme.palette.mode === "dark" ? 0.26 : 0.14),
    },
    cursor: "pointer",
  };

  const resolveEmptyText = (): ReactNode =>
    emptyText ? emptyText(code) : code
      ? `No ETFs track this index (${code}).`
      : "No tracking index for this security — feeder lookup unavailable.";

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
            {open ? "Hide Index → ETF → Feeder Fund" : "Index → ETF → Feeder Fund"}
          </Button>
          {open && (
            <RefreshButton
              onClick={handleRefresh}
              loading={loading}
              size="tiny"
              tooltip={`Refresh the index → ETF → feeder fund chain for ${code}`}
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
                {loadingText}
              </Typography>
            </Stack>
          )}
          {error && (
            <Alert severity="error" sx={{ py: 0.5 }}>
              {error}
            </Alert>
          )}
          {payload && !loading && (
            <>
              {total === 0 ? (
                <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.7rem" }}>
                  {resolveEmptyText()}
                </Typography>
              ) : (
                <>
                  <Stack
                    direction="row"
                    spacing={1}
                    alignItems="baseline"
                    sx={{ mb: 0.5 }}
                    flexWrap="wrap"
                    useFlexGap
                  >
                    {/* Proper table name — the chain this table covers. */}
                    <Typography variant="subtitle2" sx={{ fontSize: "0.78rem", fontWeight: 700 }}>
                      Index → ETF → Feeder Fund (联接基金)
                    </Typography>
                    <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.7rem" }}>
                      {anyActive
                        ? `${visible} of ${total} ETF${total === 1 ? "" : "s"} (filtered) tracking ${indexLabel}`
                        : `${total} ETF${total === 1 ? "" : "s"} tracking ${indexLabel} · ${totalFeeders} feeder fund${totalFeeders === 1 ? "" : "s"} — click an ETF row to expand its feeders & trend`}
                    </Typography>
                  </Stack>
                  <TableContainer sx={expandedTableContainerSx(460)}>
                    <Table size="small" sx={{ minWidth: 1000 }}>
                      <TableHead>
                        <TableRow>
                          {COLUMNS.map((col) => (
                            <TableCell
                              key={col.key}
                              sx={expandedTableHeadCellSx}
                              align={col.align ?? "left"}
                            >
                              {col.filter ? menuFor(col.filter) : col.label}
                            </TableCell>
                          ))}
                        </TableRow>
                      </TableHead>
                      <TableBody>
                        {/* Frozen aggregation row — computed over ALL rows.
                            position: sticky pins it to the top of the
                            scrollable container so it stays visible. */}
                        <TableRow>
                          {aggCells({ rows, payload }).map((c, i) => (
                            <TableCell key={aggKeys[i] ?? i} sx={AGG_SX}>
                              {c}
                            </TableCell>
                          ))}
                        </TableRow>
                        {pagedRows.map((e, idx) => {
                          const expandable = e.feeder_count > 0;
                          const expanded = expandable && expandedKeys.has(e.code);
                          const focused = focusCode != null && sameSecCode(e.code, focusCode);
                          const trendSelected = selectedTrend?.code === e.code;
                          const highlighted = expanded || focused || trendSelected;
                          return (
                            <Fragment key={e.code}>
                              <TableRow
                                onClick={() => handleEtfRowClick(e)}
                                sx={{
                                  ...expandedTableBodyRowSx(idx),
                                  cursor: "pointer",
                                  ...(highlighted ? { bgcolor: "action.selected" } : {}),
                                }}
                              >
                                {COLUMNS.map((col) => (
                                  <TableCell
                                    key={col.key}
                                    sx={expandedTableBodyCellSx}
                                    align={col.align ?? "left"}
                                  >
                                    {/* Code col — rendered inline so expandable
                                        rows carry the feeder-expansion chevron. */}
                                    {col.key === "code" ? (
                                      <Box component="span" sx={{ display: "inline-flex", alignItems: "center" }}>
                                        {expandable && (expanded
                                          ? <KeyboardArrowDown sx={{ fontSize: 15 }} />
                                          : <KeyboardArrowRight sx={{ fontSize: 15 }} />)}
                                        <span style={{ fontWeight: 600 }}>{e.code}</span>
                                      </Box>
                                    ) : (
                                      col.cell(e)
                                    )}
                                  </TableCell>
                                ))}
                              </TableRow>
                              {expanded && renderFeederSubRows(e.code)}
                            </Fragment>
                          );
                        })}
                      </TableBody>
                    </Table>
                  </TableContainer>
                  {selectedTrend != null && (
                    <CodeTrendExpansionChart
                      secType={selectedTrend.secType}
                      code={selectedTrend.code}
                      name={selectedTrend.name}
                      onClose={() => setSelectedTrend(null)}
                      closeLabel="close row trend chart"
                    />
                  )}
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

  /** Feeder sub-rows beneath an expanded ETF row: one placeholder row while
   *  loading / on error / when the count drifted to zero, else one row per
   *  feeder fund — rendered in the SAME columns as the ETF rows (columns
   *  without a feederCell stay empty). */
  function renderFeederSubRows(etfCode: string): ReactNode {
    if (feederLoadingKeys.has(etfCode)) {
      return (
        <TableRow sx={feederRowSx}>
          <TableCell colSpan={COLUMNS.length} sx={expandedTableBodyCellSx}>
            <Stack direction="row" spacing={1} alignItems="center">
              <CircularProgress size={12} />
              <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.7rem" }}>
                Loading feeder funds for {etfCode}…
              </Typography>
            </Stack>
          </TableCell>
        </TableRow>
      );
    }
    const err = feederErrors[etfCode];
    if (err != null) {
      return (
        <TableRow sx={feederRowSx}>
          <TableCell colSpan={COLUMNS.length} sx={expandedTableBodyCellSx}>
            <Typography variant="caption" sx={{ fontSize: "0.7rem", color: "error.main" }}>
              Feeder funds for {etfCode} failed to load: {err}
            </Typography>
          </TableCell>
        </TableRow>
      );
    }
    const feeders = feedersByEtf[etfCode];
    if (feeders == null) return null;
    if (feeders.length === 0) {
      return (
        <TableRow sx={feederRowSx}>
          <TableCell colSpan={COLUMNS.length} sx={expandedTableBodyCellSx}>
            <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.7rem" }}>
              No feeder funds (联接基金) target {etfCode}.
            </Typography>
          </TableCell>
        </TableRow>
      );
    }
    return feeders.map((f) => (
      <TableRow key={f.feeder_code} onClick={() => handleFeederRowClick(f)} sx={feederRowSx}>
        {COLUMNS.map((col) => (
          <TableCell key={col.key} sx={expandedTableBodyCellSx} align={col.align ?? "left"}>
            {col.feederCell ? col.feederCell(f) : null}
          </TableCell>
        ))}
      </TableRow>
    ));
  }
}

/** Stable keys for the sticky Σ row cells (column order as rendered). */
const aggKeys = COLUMNS.map((c) => `etf:${c.key}`);

/** Σ aggregation row — client-side sums over ALL linked ETFs. Cells map 1:1
 *  onto the rendered columns. (The index-level total ETF trading amount from
 *  stats.index_exts is not shown — the Trading Amt Σ already aggregates
 *  turnover; only its date is reused as the Σ row's date.) */
function aggCells({
  rows,
  payload,
}: {
  rows: LinkedEtfRow[];
  payload: LinkedEtfsResponse | null;
}): ReactNode[] {
  let sumTradingAmtYi: number | null = null;
  let sumAumYi: number | null = null;
  {
    let s1 = 0, a1 = false, s2 = 0, a2 = false;
    for (const e of rows) {
      if (e.latest_trading_amount != null) { s1 += e.latest_trading_amount / 1e8; a1 = true; }
      if (e.aum_yi != null) { s2 += e.aum_yi; a2 = true; }
    }
    sumTradingAmtYi = a1 ? s1 : null;
    sumAumYi = a2 ? s2 : null;
  }
  const aggDate =
    payload?.total_etf_trading_amount_date ||
    rows.reduce((m, e) => (e.latest_date > m ? e.latest_date : m), "") ||
    "";
  const n = rows.length;
  return [
    `Σ ${n} ETF${n === 1 ? "" : "s"}`,
    "—",
    "—",
    "—",
    sumTradingAmtYi != null ? <span style={{ fontFamily: "monospace" }}>{fmtNum(sumTradingAmtYi)}</span> : "—",
    sumAumYi != null ? <span style={{ fontFamily: "monospace" }}>{fmtNum(sumAumYi)}</span> : "—",
    aggDate || "—",
    "—",
    "—",
  ];
}
