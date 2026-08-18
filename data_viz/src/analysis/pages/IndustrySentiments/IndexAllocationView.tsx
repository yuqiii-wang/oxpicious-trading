/**
 * IndexAllocationView — the "Index Allocation" view mode on the Industry
 * Sentiments page (renders when viewMode === "index_allocation").
 *
 * Migrated from the standalone "Sec Allocation Perf Attribution" commons
 * analysis. Unlike that page (which had its OWN themes tree + secType
 * toggle + pagination), this view REUSES the Industry Sentiments
 * classification-nav selection — no separate nav, secType fixed to "index".
 *
 * Layout (top → bottom):
 *   1. TOP plot — close-price curves for every selected member index,
 *      rebased to 100 at the start of the visible (zoom) window. Plain
 *      style (lines only; no trading-amount bars / MA / cascading rebase).
 *      Gives an overview of the selected indices' relative close trends.
 *   2. Per-index PerfAttrPanel cards (paginated, 1 per page) — each shows
 *      the Fluctuation Attribution (shared-weight contribution per
 *      benchmark) + expandable time-series charts for one index code.
 *
 * ---- Mapping: classification-nav selection → target indices ----------
 * The Industry Sentiments page fetches `chartDataList` (one
 * IndustrySentimentsChartResponse per selected industry, including
 * strategy-only codes fetched by code). Each response carries `indices[]`
 * with `{ code, name, rows:[{date, close}] }`. The target index set for
 * this view is resolved as:
 *   • L3 multi-select non-empty (selectedItemCodes) → narrow to JUST those
 *     codes (looked up across all chartDataList responses).
 *   • Otherwise → ALL member indices of the selected industries.
 * Codes are de-duplicated (an index may carry multiple industry tags).
 * Each target index → one line in the top plot + one PerfAttrPanel card.
 *
 * NOTE: the target set is the FULL set of selected member indices (matching
 * the L2/L3 chip counts exactly) — it is NOT narrowed to indices that have
 * sec_alloc_perf_attribution rows. Indices without attribution data still
 * appear in the top plot (close curve) and pagination; their PerfAttrPanel
 * gracefully reports "No benchmark data for {code}." The perf-attr codes
 * list is fetched only to SORT (indices WITH allocation data first, so the
 * first paginated panels show real attribution bars) and to surface the
 * allocation-data count in the subtitle.
 */
import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Pagination,
  Stack,
  Typography,
} from "@mui/material";
import ChartCard from "@/components/ChartCard";
import EChart from "@/components/EChart";
import { buildGroupColorScheme } from "@/theme/group-colors";
import { fetchPerfAttrCodes } from "@/lib/api-client";
import type {
  IndustrySentimentsChartResponse,
  PerfAttrCodeRow,
} from "@shared/types";
import { PerfAttrPanel } from "../PerfAttr/PerfAttrPanel";
import type { IndexAllocationViewProps } from "./types";
import {
  buildIndexAllocationPriceOption,
  type IndexAllocationSeries,
} from "./indexAllocationPriceOption";

/** Page size — one PerfAttrPanel per page (each panel is chart-heavy). */
const PAGE_SIZE = 1;

interface TargetIndex {
  code: string;
  name: string;
  /** Source industry_id — used as the group key for per-index coloring. */
  industryId: string;
  /** Source industry display label — used to prefix the legend when more
   *  than one industry contributes indices. */
  industryLabel: string;
  rows: { date: string; close: number | null }[];
}

export function IndexAllocationView({
  themeMode,
  chartDataList,
  selectedItemCodes,
}: IndexAllocationViewProps) {
  const [page, setPage] = useState(1);

  // Fetch the list of index codes that have rows in
  // analysis.sec_alloc_perf_attribution (once, on mount). Used ONLY for
  // SORTING (indices WITH allocation data are paginated first, so the user
  // sees real attribution bars before "No benchmark data" panels) and for the
  // subtitle count. The target set itself is NOT filtered by this — every
  // selected member index appears in both the top plot and the pagination,
  // matching the L2/L3 chip counts exactly.
  const [perfAttrCodes, setPerfAttrCodes] = useState<PerfAttrCodeRow[] | null>(null);
  const [codesError, setCodesError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    fetchPerfAttrCodes("index")
      .then((resp) => {
        if (cancelled) return;
        setPerfAttrCodes(resp.codes);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setCodesError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const multiIndustry = chartDataList.length > 1;

  // Resolve the target index set from the IS classification-nav selection.
  // This is the FULL set of selected member indices (de-duplicated by code).
  // See the mapping comment at the top of this file.
  const targetIndices = useMemo<TargetIndex[]>(() => {
    const selectedSet = new Set(
      selectedItemCodes.map((c) => c.toUpperCase()),
    );
    const hasL3 = selectedSet.size > 0;
    const seen = new Set<string>();
    const out: TargetIndex[] = [];
    for (const d of chartDataList) {
      for (const idx of d.indices) {
        const key = idx.code.toUpperCase();
        if (seen.has(key)) continue;
        if (hasL3 && !selectedSet.has(key)) continue;
        seen.add(key);
        out.push({
          code: idx.code,
          name: idx.name,
          industryId: d.industry_id,
          industryLabel: d.industry_label,
          rows: idx.rows.map((r) => ({ date: r.date, close: r.close })),
        });
      }
    }
    return out;
  }, [chartDataList, selectedItemCodes]);

  // Count how many target indices have allocation attribution data (for the
  // subtitle). Derived from the perf-attr codes list; 0 while loading.
  const allocationCount = useMemo(() => {
    if (perfAttrCodes == null) return 0;
    const set = new Set(perfAttrCodes.map((c) => c.code.toUpperCase()));
    return targetIndices.filter((t) => set.has(t.code.toUpperCase())).length;
  }, [perfAttrCodes, targetIndices]);

  // Sort target indices so those WITH allocation data come first (paginated
  // first → real attribution bars before "No benchmark data" panels), then by
  // n_dates DESC (most data-rich first), then code ASC for stable order.
  // Uses a stable sort key so the order is deterministic. The sort is applied
  // to a COPY so the original targetIndices (used for the top plot) keeps its
  // natural industry-grouped order.
  const sortedForPagination = useMemo(() => {
    if (perfAttrCodes == null) return targetIndices;
    const ndatesByCode = new Map<string, number>();
    for (const c of perfAttrCodes) {
      ndatesByCode.set(c.code.toUpperCase(), c.n_dates);
    }
    return [...targetIndices].sort((a, b) => {
      const ka = a.code.toUpperCase();
      const kb = b.code.toUpperCase();
      const aHas = ndatesByCode.has(ka) ? 1 : 0;
      const bHas = ndatesByCode.has(kb) ? 1 : 0;
      if (aHas !== bHas) return bHas - aHas; // has-data first
      const na = ndatesByCode.get(ka) ?? 0;
      const nb = ndatesByCode.get(kb) ?? 0;
      if (na !== nb) return nb - na; // n_dates DESC
      return a.code.localeCompare(b.code);
    });
  }, [targetIndices, perfAttrCodes]);

  // Reset to page 1 whenever the target set changes.
  useEffect(() => {
    setPage(1);
  }, [targetIndices]);

  // Shared date axis — sorted union of all target indices' dates.
  const allDates = useMemo(() => {
    const set = new Set<string>();
    for (const idx of targetIndices) for (const r of idx.rows) set.add(r.date);
    return Array.from(set).sort();
  }, [targetIndices]);

  const lastIdx = Math.max(0, allDates.length - 1);

  // Per-index color: variant of the source industry's major color so indices
  // from the same industry share a hue (mirrors the IndustrySentimentsPlot
  // convention). When only one industry is selected, every line is a variant
  // of a single major color.
  const series = useMemo<IndexAllocationSeries[]>(() => {
    const scheme = buildGroupColorScheme(
      targetIndices.map((t) => t.industryId),
    );
    const counters = new Map<string, number>();
    return targetIndices.map((t) => {
      const i = counters.get(t.industryId) ?? 0;
      counters.set(t.industryId, i + 1);
      const closeByDate = new Map<string, number | null>();
      for (const r of t.rows) closeByDate.set(r.date, r.close);
      // Prefix the industry short label in multi-industry mode so the legend
      // identifies which industry each curve came from.
      const shortLabel =
        (t.industryLabel || t.industryId).split("  ")[0] || t.industryId;
      const name = multiIndustry ? `[${shortLabel}] ${t.name}` : t.name;
      return {
        code: t.code,
        name,
        color: scheme.variantColor(t.industryId, i),
        closes: allDates.map((d) => closeByDate.get(d) ?? null),
      };
    });
  }, [targetIndices, allDates, multiIndustry]);

  const option = useMemo(
    () =>
      series.length > 0 && allDates.length > 0
        ? buildIndexAllocationPriceOption(
            allDates,
            series,
            0,
            lastIdx,
            themeMode,
          )
        : null,
    [series, allDates, lastIdx, themeMode],
  );

  const totalPages = Math.max(1, Math.ceil(sortedForPagination.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const visibleIndices = sortedForPagination.slice(
    (safePage - 1) * PAGE_SIZE,
    safePage * PAGE_SIZE,
  );

  return (
    <Stack spacing={1.5}>
      {codesError && (
        <Alert severity="error" sx={{ py: 0.5 }}>
          Failed to load allocation index list: {codesError}
        </Alert>
      )}
      {/* TOP plot — close-price curves for ALL selected member indices
          (matches the L2/L3 chip counts exactly, NOT narrowed to allocation
          data). Rebased to 100 at the visible-window start. */}
      <ChartCard
        title="Index Close Price"
        subtitle={
          targetIndices.length === 0
            ? "No indices selected"
            : perfAttrCodes == null
              ? `${targetIndices.length} index${targetIndices.length === 1 ? "" : "es"} · rebased to 100 at window start · actual close in tooltip`
              : `${targetIndices.length} index${targetIndices.length === 1 ? "" : "es"} (${allocationCount} with allocation data) · rebased to 100 at window start · actual close in tooltip`
        }
      >
        {targetIndices.length === 0 ? (
          <Typography
            variant="body2"
            color="text.secondary"
            sx={{ py: 2, textAlign: "center" }}
          >
            Select one or more industries (or L3 index chips) to see their close-price curves.
          </Typography>
        ) : (
          <>
            <EChart option={option ?? {}} height={340} />
          </>
        )}
      </ChartCard>

      {/* Per-index attribution panels (paginated). Each PerfAttrPanel fetches
          its own attribution + chart data for one index code (secType=index).
          Paginates through ALL selected indices (matching the top plot count);
          indices without allocation data render a "No benchmark data" panel.
          Sorted so allocation-data indices come first. */}
      {visibleIndices.map((t) => (
        <PerfAttrPanel
          key={t.code}
          code={t.code}
          name={t.name}
          secType="index"
          themeMode={themeMode}
        />
      ))}
      {totalPages > 1 && (
        <Box sx={{ display: "flex", justifyContent: "center", pt: 1, pb: 1 }}>
          <Pagination
            count={totalPages}
            page={safePage}
            onChange={(_e, v) => setPage(v)}
            color="primary"
            showFirstButton
            showLastButton
          />
        </Box>
      )}
    </Stack>
  );
}
