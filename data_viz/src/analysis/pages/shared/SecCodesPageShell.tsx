/**
 * SecCodesPageShell — shared layout for analysis pages that show ONE panel
 * per code over the classification nav (the PE / Dividends / MA-Spread
 * page shape).
 *
 * Owns everything those pages duplicated verbatim (2026-09-29 extraction;
 * Dividends/index.tsx and MaSpread/index.tsx were byte-for-byte copies of
 * Pe/index.tsx minus the panel component and strings):
 *   • useSecNav wiring (trees via the page-supplied themes sources)
 *   • the codes-list fetch on sec_type/exchange/refresh change
 *   • client-side filtering (sector/industry OR strategy/theme, or exact
 *     code search) + back-to-page-1 resets
 *   • pagination (pageSize codes per page) + SecNavShell chrome
 *
 * The codes fetcher is STRUCTURAL ({ codes: {code, name}[] }) — any codes
 * endpoint fits (valuation, mov-ave-spread, …); extra row fields ride
 * along untouched. A page supplies its own UI strings, its codes fetcher
 * (backing endpoint), and the per-code panel renderer:
 *   export default function MyPage() {
 *     return (
 *       <SecCodesPageShell
 *         title="My Metric" dataLabel="My Metric"
 *         themesSources={THEMES_SOURCES}
 *         onInvalidateCache={() => invalidateCacheForPrefix("/api/analysis/my/")}
 *         fetchCodes={fetchMyCodes}
 *         emptyNoun="My Metric data"
 *         refreshTooltip="Refresh My Metric themes + codes + chart data (bypass cache)"
 *         errorPrefix="My Metric data"
 *         subtitle={(headerLabel) => `${headerLabel} — ...`}
 *         renderCode={(c) => <MyPanel code={c.code} name={c.name} secType={c.secType} />}
 *       />
 *     );
 *   }
 */
import { useEffect, useMemo, useState } from "react";
import { Fragment } from "react";
import type { ReactNode } from "react";
import {
  Alert,
  Box,
  Pagination,
  Stack,
  Typography,
} from "@mui/material";
import { SecNavShell, useSecNav } from "@/shared/components/sec-nav";
import type {
  SecNavSecType,
  SecNavThemesSource,
} from "@/shared/components/sec-nav";
import type { SectorNode, StrategyNode } from "@shared/types";

/**
 * The minimal structural shape the shell consumes from ANY codes endpoint —
 * ValuationCodesResponse, MovAveSpreadCodesResponse, … all satisfy it
 * (rows may carry extra fields; the shell only filters on code/name).
 */
export interface SecCodesListResponse {
  codes: { code: string; name: string }[];
}

/** Codes fetcher backing the page (e.g. fetchValuationPageCodes). */
export type FetchSecCodes = (
  secType: SecNavSecType,
  exchange: string | null,
) => Promise<SecCodesListResponse>;

/** The nav selection fields the codes filtering needs. */
interface NavFilterState {
  searchCode: string | null;
  sectorId: string | null;
  industrySlug: string | null;
  strategyId: string | null;
  themeSlug: string | null;
  sectors: SectorNode[];
  strategies: StrategyNode[];
}

export interface SecCodesPageShellProps {
  title: string;
  /** Page subtitle builder — receives nav.headerLabel. */
  subtitle: (headerLabel: string) => ReactNode;
  /** Themes endpoints per sec_type (passed to useSecNav). */
  themesSources: Partial<Record<SecNavSecType, SecNavThemesSource>>;
  /** useSecNav dataLabel (code-not-found message). Same as title usually. */
  dataLabel: string;
  /** Called on refresh before the trees reload — invalidate the page's API cache. */
  onInvalidateCache?: () => void;
  /** Codes list endpoint. */
  fetchCodes: FetchSecCodes;
  /** Noun in the empty-state warning, e.g. "PE data" / "dividends data". */
  emptyNoun: string;
  /** SecNavShell props passthrough. */
  backPath: string;
  backLabel?: string;
  refreshTooltip?: string;
  errorPrefix?: string;
  /** sec_types shown in the toggle. Default: all three. */
  secTypes?: SecNavSecType[];
  /** Initial sec_type for the toggle. Default "index". */
  defaultSecType?: SecNavSecType;
  /** Codes per page. Default 1 (full-height baseline charts per code). */
  pageSize?: number;
  /** One panel card per visible code. */
  renderCode: (c: { code: string; name: string; secType: SecNavSecType }) => ReactNode;
}

export default function SecCodesPageShell({
  title,
  subtitle,
  themesSources,
  dataLabel,
  onInvalidateCache,
  fetchCodes,
  emptyNoun,
  backPath,
  backLabel,
  refreshTooltip,
  errorPrefix,
  secTypes = ["etf", "index", "stock"],
  defaultSecType,
  pageSize = 1,
  renderCode,
}: SecCodesPageShellProps) {
  // Shared nav: sec_type toggle + code search + classification selection.
  const nav = useSecNav({
    themesSources,
    dataLabel,
    defaultSecType,
    onInvalidateCache,
  });

  // Page-specific state: the codes list + pagination.
  const [codesData, setCodesData] = useState<SecCodesListResponse | null>(null);
  const [page, setPage] = useState(1);

  // Fetch the codes list on sec_type/exchange change or refresh (the trees
  // themselves are loaded inside useSecNav).
  useEffect(() => {
    let cancelled = false;
    fetchCodes(nav.secType, nav.exchange)
      .then((c) => {
        if (!cancelled) setCodesData(c);
      })
      .catch(() => {
        if (!cancelled) setCodesData(null);
      });
    return () => {
      cancelled = true;
    };
  }, [nav.secType, nav.exchange, nav.refreshKey, fetchCodes]);

  // Back to page 1 whenever sec_type, selection, or search changes.
  useEffect(() => {
    setPage(1);
  }, [nav.secType, nav.searchCode, nav.sectorId, nav.industrySlug, nav.strategyId, nav.themeSlug, nav.exchange]);

  // ---- Filter codes by sector/industry OR strategy/theme, or by exact code
  //      search. Exchange filtering is applied at the BACKEND (both the
  //      themes tree and the codes list are filtered via matchesExchange),
  //      so no client-side exchange filtering is needed here. ----
  const {
    searchCode, sectorId, industrySlug, strategyId, themeSlug,
    sectors, strategies,
  } = nav;
  const { pageCodes, totalCodes } = useMemo(
    () => filterCodesByNav(codesData, {
      searchCode, sectorId, industrySlug, strategyId, themeSlug,
      sectors, strategies,
    }),
    [codesData, searchCode, sectorId, industrySlug, strategyId, themeSlug, sectors, strategies],
  );

  const totalPages = Math.max(1, Math.ceil(totalCodes / pageSize));
  const safePage = Math.min(page, totalPages);
  const visibleCodes = pageCodes.slice((safePage - 1) * pageSize, safePage * pageSize);

  return (
    <SecNavShell
      nav={nav}
      title={title}
      backPath={backPath}
      backLabel={backLabel}
      subtitle={subtitle(nav.headerLabel)}
      secTypes={secTypes}
      refreshTooltip={refreshTooltip}
      errorPrefix={errorPrefix}
    >
      {visibleCodes.length === 0 ? (
        <Alert severity="warning">
          {nav.searchCode
            ? `No data available for code: ${nav.searchCode}`
            : `No ${nav.secType.toUpperCase()} ${emptyNoun} in this sector/industry. (Run the Python populator for sec_type="${nav.secType}" first.)`}
        </Alert>
      ) : (
        <>
          <Typography variant="caption" color="text.secondary">
            {nav.searchCode
              ? `Search result for ${nav.searchCode}`
              : `${visibleCodes.length} of ${totalCodes} ${nav.secType.toUpperCase()}s on this page · page ${safePage}/${totalPages}`}
          </Typography>
          <Stack spacing={1.5} sx={{ mt: 0.5 }}>
            {visibleCodes.map((c) => (
              <Fragment key={c.code}>
                {renderCode({ code: c.code, name: c.name, secType: nav.secType })}
              </Fragment>
            ))}
          </Stack>
          {!nav.searchCode && totalPages > 1 && (
            <Box sx={{ display: "flex", justifyContent: "center", pt: 2, pb: 1 }}>
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
        </>
      )}
    </SecNavShell>
  );
}

/** The codes-filtering memo body — exact code search (suffix-insensitive),
 *  else strategy/theme tree intersection, else sector/industry tree
 *  intersection. */
function filterCodesByNav(
  codesData: SecCodesListResponse | null,
  nav: NavFilterState,
): { pageCodes: { code: string; name: string }[]; totalCodes: number } {
  const all = codesData?.codes ?? [];
  if (nav.searchCode) {
    const norm = nav.searchCode.toUpperCase().replace(/\.(SS|SZ|SH|BJ|HK)$/i, "");
    const match = all.find(
      (c) => c.code.toUpperCase().replace(/\.(SS|SZ|SH|BJ|HK)$/i, "") === norm,
    );
    return { pageCodes: match ? [match] : [], totalCodes: match ? 1 : 0 };
  }
  if (nav.strategyId) {
    const strategyCodes = new Set<string>();
    const strat = nav.strategies.find((s) => s.sector_id === nav.strategyId);
    if (strat) {
      for (const theme of strat.industries) {
        if (!nav.themeSlug || theme.industry_slug === nav.themeSlug || theme.industry_id === nav.themeSlug) {
          for (const item of theme.items) {
            strategyCodes.add(item.code);
          }
        }
      }
    }
    const wanted = all.filter((c) => strategyCodes.has(c.code));
    return { pageCodes: wanted, totalCodes: wanted.length };
  }
  const wantedSet = new Set<string>();
  for (const s of nav.sectors) {
    if (nav.sectorId && s.sector_id !== nav.sectorId) continue;
    for (const ind of s.industries) {
      if (nav.industrySlug && ind.industry_slug !== nav.industrySlug) continue;
      for (const item of ind.items) {
        wantedSet.add(item.code);
      }
    }
  }
  const wanted = all.filter((c) => wantedSet.has(c.code));
  return { pageCodes: wanted, totalCodes: wanted.length };
}
