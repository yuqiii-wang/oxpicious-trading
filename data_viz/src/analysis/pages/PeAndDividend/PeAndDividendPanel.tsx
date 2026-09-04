/**
 * PeAndDividendPanel — one card per code: the EXACT data-viz baseline plot
 * for the security on top, monthly PE & Dividend stats table beneath.
 *
 * The plot is NOT reimplemented here — it delegates to the shared baseline
 * panel used across the app:
 *   • sec_type=index → IndexPanel  (OHLC + MAs + Trading Amt + PE twin axis)
 *   • sec_type=etf   → EtfMarginPanel (rebased OHLC + MAs + RZ/RQ + Amt +
 *                     corp-action markers; dividends already shown as gold
 *                     diamond markPoints on ex-dividend dates)
 *   • sec_type=stock → StockPanel (OHLC + MAs + PE; dividends already shown
 *                     as gold diamond markPoints on ex-dividend dates)
 *
 * Clicking any date on the plot fires onDateClick → the monthly stats table
 * beneath highlights the row whose month-end contains the clicked date and
 * scrolls it into view.
 *
 * The monthly PE & Dividend stats table (analysis.pe_and_dividend_stats) is
 * rendered beneath the plot: one row per month-end snapshot, most recent
 * first. is_active row is tagged with a "latest" chip.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Box,
  Chip,
  CircularProgress,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import ChartCard from "@/components/ChartCard";
import AnalysisRunButton from "@/components/AnalysisRunButton";
import IndexPanel from "@/dataviz/features/index-baseline/IndexPanel";
import EtfMarginPanel from "@/dataviz/features/etf-margin/EtfMarginPanel";
import StockPanel from "@/dataviz/features/stock-baseline/StockPanel";
import { DIVIDEND_COLOR, PE_COLOR } from "@/theme/chart-palette";
import { fmtNum, fmtPct } from "@/lib/series";
import {
  fetchIndicesCombined,
  fetchEtfMarginCombined,
  fetchStocksCombined,
  fetchPeAndDividendStats,
  invalidateCacheForUrl,
} from "@/lib/api-client";
import type {
  IndexBundle,
  EtfBundle,
  StockBundle,
  PeAndDividendStatsResponse,
  PeAndDividendStatsRow,
} from "@shared/types";
import type { PanelProps } from "./types";
import {
  expandedTableBodyCellSx,
  expandedTableBodyRowSx,
  expandedTableContainerSx,
  expandedTableHeadCellSx,
  expandedTableNumCellSx,
} from "@/shared/styles/expanded-table-styles";
import useTableHeaderFilters, { type HeaderFilterDef } from "@/hooks/table-header-filters";

/** Format a YYYY-MM-DD date as a short YYYY-MM string for month display. */
function fmtMonth(dateStr: string): string {
  return dateStr.length >= 7 ? dateStr.slice(0, 7) : dateStr;
}

/** Return the YYYY-MM key for a YYYY-MM-DD date string. */
function monthKey(dateStr: string): string {
  return dateStr.slice(0, 7);
}

/** Opt-in per-column header filters — Month is a date-range selector (month
 *  granularity over the month-end snapshot dates), Active is a discrete
 *  label (ticks), the rolling 5y metrics are continuous magnitudes
 *  (numeric range). */
const FILTER_DEFS: HeaderFilterDef<PeAndDividendStatsRow>[] = [
  { key: "month", label: "Month", type: "date", granularity: "month", value: (r) => r.date.slice(0, 7) },
  { key: "active", label: "Active", type: "ticks", value: (r) => (r.is_active ? "latest" : "earlier") },
  { key: "min_pe", label: "Min PE 5y", type: "range", value: (r) => r.min_pe_5y },
  { key: "max_pe", label: "Max PE 5y", type: "range", value: (r) => r.max_pe_5y },
  { key: "div_var", label: "Div Var 5y", type: "range", value: (r) => r.dividend_var_5y },
  { key: "last_div", label: "Last Div", type: "range", value: (r) => r.last_dividend_per_share },
  { key: "div_stab", label: "Div Stability 5y", type: "range", value: (r) => r.dividend_stability_5y },
];
const DEF_BY_KEY = new Map(FILTER_DEFS.map((d) => [d.key, d]));

export function PeAndDividendPanel({
  code,
  secType,
  themeMode,
}: PanelProps) {
  // ---- Security baseline bundle (IndexBundle | EtfBundle | StockBundle) ---
  const [bundle, setBundle] = useState<IndexBundle | EtfBundle | StockBundle | null>(null);
  const [bundleLoading, setBundleLoading] = useState(false);
  const [bundleError, setBundleError] = useState<string | null>(null);

  // ---- Stats data ---------------------------------------------------------
  const [statsData, setStatsData] = useState<PeAndDividendStatsResponse | null>(null);
  const [statsLoading, setStatsLoading] = useState(false);
  const [statsError, setStatsError] = useState<string | null>(null);

  // Bumped by the per-security AnalysisRunButton after a rebuild run —
  // retriggers the stats fetch (the cache entry is invalidated first in
  // the completion handler).
  const [refreshKey, setRefreshKey] = useState(0);

  // Clicked date from the plot — drives the table highlight + scroll-into-view.
  const [clickedDate, setClickedDate] = useState<string | null>(null);

  // Ref to the table row that should scroll into view when the highlight
  // changes (set by the chart click handler).
  const highlightedRowRef = useRef<HTMLTableRowElement | null>(null);

  // Fetch the security baseline bundle on mount and whenever code/sec_type
  // changes. Uses the SAME combined endpoints as /dataviz/index-baseline,
  // /dataviz/etf-margin, /dataviz/stock-baseline — page_size=1 + code filter
  // returns just the one security.
  useEffect(() => {
    let cancelled = false;
    setBundleLoading(true);
    setBundleError(null);
    setBundle(null);
    const p =
      secType === "index"
        ? fetchIndicesCombined(null, null, null, null, 1, 1, code, null).then((r) => r.indices[0] ?? null)
        : secType === "etf"
          ? fetchEtfMarginCombined(null, null, null, null, undefined, 1, 1, code, null).then((r) => r.etfs[0] ?? null)
          : fetchStocksCombined(null, null, null, null, 1, 1, code, null).then((r) => r.stocks[0] ?? null);
    p.then((b) => {
      if (cancelled) return;
      setBundle(b);
      setBundleLoading(false);
    }).catch((e: Error) => {
      if (cancelled) return;
      setBundleError(e.message);
      setBundleLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [code, secType]);

  // Fetch stats data on mount and whenever code/sec_type changes.
  useEffect(() => {
    let cancelled = false;
    setStatsLoading(true);
    setStatsError(null);
    fetchPeAndDividendStats(code, secType)
      .then((data) => {
        if (cancelled) return;
        setStatsData(data);
        setStatsLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setStatsError(e.message);
        setStatsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [code, secType, refreshKey]);

  // Reset clicked date when the code changes.
  useEffect(() => {
    setClickedDate(null);
  }, [code, secType]);

  // ---- Stats table: highlight + scroll-into-view --------------------------
  // Find the stats row whose month-end is the latest one <= clickedDate.
  const statsRows: PeAndDividendStatsRow[] = statsData?.rows ?? [];

  // Opt-in header filters over the monthly snapshots (reset on scope change).
  const { filtered: visibleStatsRows, menuFor } = useTableHeaderFilters(
    FILTER_DEFS,
    statsRows,
    [code, secType, refreshKey],
  );

  // Whether this security has PE & dividend analysis rows — drives the bold
  // highlight of the per-security build button (AnalysisRunButton). Loading
  // counts as "present" so the button doesn't bold-flicker.
  const hasAnalysisData = statsLoading || statsRows.length > 0;

  // Refetch after a per-security analysis rebuild (AnalysisRunButton):
  // drop the cached stats response, then bump the refresh key.
  const handleAnalysisRunCompleted = useCallback(() => {
    invalidateCacheForUrl(
      `/api/analysis/pe-and-dividend/stats?code=${code}&sec_type=${secType}`,
    );
    setRefreshKey((k) => k + 1);
  }, [code, secType]);
  const highlightedStatsRowDate = useMemo(() => {
    if (!clickedDate || statsRows.length === 0) return null;
    for (const r of statsRows) {
      if (r.date <= clickedDate) return r.date;
    }
    return null;
  }, [clickedDate, statsRows]);

  useEffect(() => {
    if (!highlightedStatsRowDate) return;
    const t = setTimeout(() => {
      highlightedRowRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 50);
    return () => clearTimeout(t);
  }, [highlightedStatsRowDate]);

  // ---- Render: baseline plot ---------------------------------------------
  const plotContent = (() => {
    if (bundleLoading) {
      return (
        <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
          <CircularProgress size={28} />
        </Box>
      );
    }
    if (bundleError) {
      return (
        <Alert severity="error" variant="filled">
          Failed to load {secType} baseline: {bundleError}
        </Alert>
      );
    }
    if (!bundle) {
      return (
        <Alert severity="warning">
          No {secType.toUpperCase()} baseline data for {code}.
        </Alert>
      );
    }
    // Delegate to the exact same plot component used in /dataviz/*.
    // onDateClick fires for any click on the chart → highlights the matching
    // month-end row in the stats table below.
    if (secType === "index") {
      return <IndexPanel index={bundle as IndexBundle} themeMode={themeMode} onDateClick={setClickedDate} />;
    }
    if (secType === "etf") {
      return <EtfMarginPanel etf={bundle as EtfBundle} onDateClick={setClickedDate} />;
    }
    return <StockPanel stock={bundle as StockBundle} onDateClick={setClickedDate} />;
  })();

  return (
    <Stack spacing={1.5}>
      {/*
        The baseline panels (IndexPanel / EtfMarginPanel / StockPanel) render
        their own ChartCard with title + subtitle + controls, so we don't wrap
        them in another ChartCard here — just render them directly.
      */}
      {plotContent}

      {/* ---- Monthly PE & Dividend stats table ---- */}
      <ChartCard
        title="Monthly PE & Dividend Stats (5y rolling)"
        subtitle={
          statsData
            ? `${statsRows.length} month-end snapshots · click a date on the chart above to highlight the matching month`
            : undefined
        }
        action={
          <AnalysisRunButton
            module="pe_and_dividends"
            secType={secType}
            code={code}
            hasData={hasAnalysisData}
            onCompleted={handleAnalysisRunCompleted}
          />
        }
        height={undefined}
      >
        {statsLoading && (
          <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
            <CircularProgress size={28} />
          </Box>
        )}
        {statsError && (
          <Alert severity="error" variant="filled">
            Failed to load stats: {statsError}
          </Alert>
        )}
        {!statsLoading && !statsError && statsRows.length === 0 && (
          <Alert severity="warning">
            No monthly stats for {code}. (Stats are computed monthly by the
            Python build script — run it once a month after the 5y window
            updates.)
          </Alert>
        )}
        {!statsLoading && !statsError && statsRows.length > 0 && (
          <TableContainer
            component={Box}
            sx={expandedTableContainerSx(360)}
          >
            <Table size="small" stickyHeader>
              <TableHead>
                <TableRow>
                  <TableCell sx={expandedTableHeadCellSx}>
                    {menuFor(DEF_BY_KEY.get("month")!)}
                  </TableCell>
                  <TableCell sx={expandedTableHeadCellSx} align="center">
                    {menuFor(DEF_BY_KEY.get("active")!)}
                  </TableCell>
                  <TableCell sx={expandedTableHeadCellSx} align="right">
                    {menuFor(DEF_BY_KEY.get("min_pe")!)}
                  </TableCell>
                  <TableCell sx={expandedTableHeadCellSx} align="right">
                    {menuFor(DEF_BY_KEY.get("max_pe")!)}
                  </TableCell>
                  <TableCell sx={expandedTableHeadCellSx} align="right">
                    {menuFor(DEF_BY_KEY.get("div_var")!)}
                  </TableCell>
                  <TableCell sx={expandedTableHeadCellSx} align="right">
                    {menuFor(DEF_BY_KEY.get("last_div")!)}
                  </TableCell>
                  <TableCell sx={expandedTableHeadCellSx} align="right">
                    {menuFor(DEF_BY_KEY.get("div_stab")!)}
                  </TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {visibleStatsRows.map((r, idx) => {
                  const isHighlighted = r.date === highlightedStatsRowDate;
                  const isActive = r.is_active;
                  // Bold the Last Div cell when a dividend was issued in
                  // this month — surfaces dividend-event months at a glance.
                  const boldDiv = r.dividend_issued_this_month === true;
                  return (
                    <TableRow
                      key={r.date}
                      ref={isHighlighted ? highlightedRowRef : undefined}
                      sx={{
                        ...expandedTableBodyRowSx(idx),
                        ...(isHighlighted
                          ? { bgcolor: "action.selected" }
                          : isActive
                            ? { bgcolor: "action.hover" }
                            : {}),
                      }}
                    >
                      <TableCell sx={{ ...expandedTableBodyCellSx, fontWeight: isHighlighted ? 700 : 500 }}>
                        {fmtMonth(r.date)}
                      </TableCell>
                      <TableCell align="center" sx={{ ...expandedTableBodyCellSx, py: 0.5 }}>
                        {isActive && (
                          <Chip
                            label="latest"
                            size="small"
                            color="primary"
                            sx={{ height: 18, fontSize: "0.65rem" }}
                          />
                        )}
                      </TableCell>
                      <TableCell align="right" sx={expandedTableNumCellSx}>{fmtNum(r.min_pe_5y)}</TableCell>
                      <TableCell align="right" sx={expandedTableNumCellSx}>{fmtNum(r.max_pe_5y)}</TableCell>
                      <TableCell align="right" sx={expandedTableNumCellSx}>{fmtPct(r.dividend_var_5y)}</TableCell>
                      <TableCell
                        align="right"
                        sx={{
                          ...expandedTableNumCellSx,
                          color: DIVIDEND_COLOR,
                          fontWeight: boldDiv ? 700 : 400,
                        }}
                      >
                        {fmtNum(r.last_dividend_per_share, 4)}
                      </TableCell>
                      <TableCell align="right" sx={{ ...expandedTableNumCellSx, color: PE_COLOR }}>
                        {r.dividend_stability_5y != null
                          ? fmtNum(r.dividend_stability_5y, 1)
                          : "—"}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </TableContainer>
        )}
        {clickedDate && (
          <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: "block" }}>
            Clicked date <b>{clickedDate}</b> → highlighted month{" "}
            <b>{highlightedStatsRowDate ? monthKey(highlightedStatsRowDate) : "(none — before earliest stats)"}</b>
          </Typography>
        )}
      </ChartCard>
    </Stack>
  );
}
