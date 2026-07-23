/**
 * ETF + Margin page — interactive mirror of plot_szse_sse_etf_and_margin.py.
 *
 *   • ThemeSelector — two-level cascade (L1 sector → L2 industry), loads from
 *     /api/etf-margin/themes which returns the precomputed taxonomy tree.
 *   • Stack of EtfMarginPanel cards (one per row, full width) — rebased close %,
 *     MA20/MA60/MA120, RZ/RQ margin fills, volume bars
 *   • Pagination — 6 ETFs per page, each page triggers one API request
 *
 * Defaults to the "BROAD" sector (broad-based index ETFs).
 */
import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  CircularProgress,
  Pagination,
  Stack,
  Typography,
} from "@mui/material";
import ThemeSelector from "@/components/ThemeSelector";
import CodeSearchBar, { findCodeInThemes } from "@/components/CodeSearchBar";
import EtfMarginPanel from "@/features/etf-margin/EtfMarginPanel";
import { fetchEtfMarginCombined, fetchThemes } from "@/lib/api-client";
import { useStore } from "@/store/filters";
import type {
  EtfMarginCombinedResponse,
  SectorNode,
} from "../../../shared/types";

const PAGE_SIZE = 6;

export default function EtfMarginPage() {
  const sectorId = useStore((s) => s.sectorId);
  const setSectorId = useStore((s) => s.setSectorId);
  const industrySlug = useStore((s) => s.industrySlug);
  const setIndustrySlug = useStore((s) => s.setIndustrySlug);

  const [sectors, setSectors] = useState<SectorNode[]>([]);
  const [data, setData] = useState<EtfMarginCombinedResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  // Active exact-code search (null = browsing mode). When set, only the
  // matching ETF is fetched and displayed; sector/industry chips are
  // highlighted to show where the code belongs.
  const [searchCode, setSearchCode] = useState<string | null>(null);

  // Load themes (two-level taxonomy tree) once
  useEffect(() => {
    fetchThemes()
      .then(setSectors)
      .catch((e: Error) => setError(e.message));
  }, []);

  // Reset to page 1 whenever sector or industry changes
  useEffect(() => {
    setPage(1);
  }, [sectorId, industrySlug]);

  // Load ETF data whenever sector/industry, page, or search code changes.
  // When searchCode is set, fetch only that one ETF (bypassing sector/pagination).
  useEffect(() => {
    let cancelled = false;
    if (!sectorId && !searchCode) return;
    setLoading(true);
    setError(null);
    const promise = searchCode
      ? fetchEtfMarginCombined(null, null, null, null, undefined, 1, 1, searchCode)
      : fetchEtfMarginCombined(sectorId, industrySlug, null, null, undefined, page, PAGE_SIZE);
    promise
      .then((d) => {
        if (cancelled) return;
        setData(d);
        setLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message);
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [sectorId, industrySlug, page, searchCode]);

  // Resolve a searched code against the themes tree: update sector/industry
  // highlights + activate single-result mode. Shows an error if not found.
  const handleSearch = (code: string) => {
    const found = findCodeInThemes(sectors, code);
    if (!found) {
      setError(`ETF code not found: ${code}`);
      setSearchCode(null);
      return;
    }
    setError(null);
    setSectorId(found.sectorId);
    setIndustrySlug(found.industrySlug);
    setSearchCode(code);
    setPage(1);
  };

  // Clearing the search returns to the normal paginated view for the
  // currently highlighted sector/industry.
  const handleClearSearch = () => {
    setSearchCode(null);
  };

  // Clicking a sector/industry chip exits search mode and browses normally.
  const handleSectorChange = (id: string | null) => {
    setSearchCode(null);
    setSectorId(id);
  };
  const handleIndustryChange = (slug: string | null) => {
    setSearchCode(null);
    setIndustrySlug(slug);
  };

  const activeSector = sectors.find((s) => s.sector_id === sectorId);
  const activeIndustry = activeSector?.industries.find(
    (i) => i.industry_slug === industrySlug,
  );
  const headerLabel = activeIndustry
    ? `${activeSector?.sector_label ?? ""} / ${activeIndustry.industry_label}`
    : activeSector
      ? `${activeSector.sector_label} (All)`
      : "Select a sector";
  const totalPages = data?.total_pages ?? 1;

  // Compute the common (intersection) date range across all ETFs on this page.
  // Each panel's slider defaults to this window so plots are aligned to the
  // shortest time range plot (max of first dates → min of last dates).
  const commonRange = useMemo(() => {
    if (!data || data.etfs.length === 0) return null;
    let maxStart = "";
    let minEnd = "";
    for (const etf of data.etfs) {
      if (etf.rows.length === 0) continue;
      const first = etf.rows[0].date;
      const last = etf.rows[etf.rows.length - 1].date;
      if (first > maxStart) maxStart = first;
      if (minEnd === "" || last < minEnd) minEnd = last;
    }
    if (!maxStart || !minEnd || maxStart > minEnd) return null;
    return { start: maxStart, end: minEnd };
  }, [data]);

  return (
    <Box>
      <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 2, flexWrap: "wrap" }}>
        <Box>
          <Typography variant="h5" sx={{ fontWeight: 700 }}>
            ETF + Margin
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {headerLabel} — interactive mirror of plot_szse_sse_etf_and_margin.py
          </Typography>
        </Box>
        <CodeSearchBar
          activeCode={searchCode}
          onSearch={handleSearch}
          onClear={handleClearSearch}
          placeholder="ETF code (e.g. 510050)"
        />
      </Box>

      <ThemeSelector
        sectors={sectors}
        sectorId={sectorId}
        industrySlug={industrySlug}
        onSectorChange={handleSectorChange}
        onIndustryChange={handleIndustryChange}
      />

      {loading && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
          <CircularProgress size={32} />
        </Box>
      )}
      {error && (
        <Alert severity="error" variant="filled">
          Failed to load ETF data: {error}
        </Alert>
      )}
      {!loading && !error && data && (
        <>
          {data.etfs.length === 0 ? (
            <Alert severity="warning">
              {searchCode
                ? `No data available for ETF code: ${searchCode}`
                : "No ETFs in this sector/industry for the selected date range."}
            </Alert>
          ) : (
            <>
              <Typography variant="caption" color="text.secondary">
                {searchCode
                  ? `Search result for ${searchCode} · ${data.dates[0] ?? "—"} → ${data.dates[data.dates.length - 1] ?? "—"}`
                  : `${data.etfs.length} ETFs on this page · ${data.total_etfs} total · page ${data.page}/${data.total_pages} · ${data.dates[0] ?? "—"} → ${data.dates[data.dates.length - 1] ?? "—"}`}
              </Typography>
              <Stack spacing={1.5}>
                {data.etfs.map((etf) => (
                  <EtfMarginPanel
                    key={etf.code}
                    etf={etf}
                    defaultStartDate={commonRange?.start}
                    defaultEndDate={commonRange?.end}
                  />
                ))}
              </Stack>
              {!searchCode && totalPages > 1 && (
                <Box sx={{ display: "flex", justifyContent: "center", pt: 2, pb: 1 }}>
                  <Pagination
                    count={totalPages}
                    page={page}
                    onChange={(_e, v) => setPage(v)}
                    color="primary"
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
  );
}
