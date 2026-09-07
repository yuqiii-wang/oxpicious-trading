/**
 * SecNavShell — shared layout for analysis pages built on useSecNav.
 *
 * Provides the common page structure every analysis item needs:
 *   1. Header — back button + title + subtitle (active sector/industry label)
 *      + controls row (sec_type toggle · CodeSearchBar · Refresh · extras)
 *   2. SecClassificationNav — two-level cascade (L1 sector → L2 industry) +
 *      parallel strategy column + exchange filter row + L3 security chips,
 *      fully wired from the SecNavState returned by useSecNav
 *   3. Loading spinner + load/search error alert
 *   4. Content area — the page-specific panels/charts
 *
 * Usage:
 *   const nav = useSecNav({ themesSources: THEMES_SOURCES, ... });
 *   return (
 *     <SecNavShell nav={nav} title="MA-Spread" backPath="/analysis/commons"
 *       subtitle={`${nav.headerLabel} — ...`} secTypes={["etf", "index", "stock"]}>
 *       ...page-specific content...
 *     </SecNavShell>
 *   );
 */
import type { ReactNode } from "react";
import { Alert, Box, CircularProgress, IconButton, Typography } from "@mui/material";
import { ArrowBack } from "@mui/icons-material";
import { useNavigate } from "react-router-dom";
import CodeSearchBar from "@/components/CodeSearchBar";
import RefreshButton from "@/components/RefreshButton";
import SecClassificationNav from "@/shared/components/sec-classification/SecClassificationNav";
import SecTypeToggle from "./SecTypeToggle";
import type { SecNavSecType, SecNavState } from "./types";

interface SecNavShellProps {
  nav: SecNavState;
  title: string;
  /** Page subtitle — usually starts with `${nav.headerLabel} — ...`. */
  subtitle: ReactNode;
  backPath: string;
  /** aria-label for the back IconButton. Default: "back". */
  backLabel?: string;
  /** Sec types shown in the toggle. Omit for single-sec-type pages (no toggle). */
  secTypes?: SecNavSecType[];
  /** Disable the toggle (pages locked to one sec_type). Default false. */
  disableToggle?: boolean;
  /** Show the CodeSearchBar. Default true. */
  showSearch?: boolean;
  /** Disable the CodeSearchBar (pages whose selection model has no search). Default false. */
  disableSearch?: boolean;
  /** Custom search placeholder; default derived from the active sec_type. */
  searchPlaceholder?: string;
  /** Show the RefreshButton. Default true. */
  showRefresh?: boolean;
  refreshTooltip?: string;
  /** Page-specific controls appended to the header controls row. */
  headerExtra?: ReactNode;
  /** Replace the default single-select SecClassificationNav with a custom
   *  navigator (e.g. SecClassificationNavMulti for multi-select pages). The
   *  custom node receives no props — wire it from the page's own state. */
  navSlot?: ReactNode;
  /** Error alert reads "Failed to load {errorPrefix}: {error}". Default "data". */
  errorPrefix?: string;
  /** Keep page content mounted (no spinner swap) while the nav reloads. Default true. */
  hideContentWhileLoading?: boolean;
  children: ReactNode;
}

function searchPlaceholderFor(secType: SecNavSecType): string {
  return secType === "etf"
    ? "ETF code (e.g. 510050)"
    : secType === "index"
      ? "Index code (e.g. 000300)"
      : "Stock code (e.g. 600000)";
}

export default function SecNavShell({
  nav,
  title,
  subtitle,
  backPath,
  backLabel = "back",
  secTypes,
  disableToggle = false,
  showSearch = true,
  disableSearch = false,
  searchPlaceholder,
  showRefresh = true,
  refreshTooltip,
  headerExtra,
  navSlot,
  errorPrefix = "data",
  hideContentWhileLoading = true,
  children,
}: SecNavShellProps) {
  const navigate = useNavigate();

  return (
    <Box>
      {/* ---- Header ---- */}
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          gap: 2,
          flexWrap: "wrap",
        }}
      >
        <Box>
          <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
            <IconButton onClick={() => navigate(backPath)} size="small" aria-label={backLabel}>
              <ArrowBack />
            </IconButton>
            <Typography variant="h5" sx={{ fontWeight: 700 }}>
              {title}
            </Typography>
          </Box>
          <Typography variant="body2" color="text.secondary">
            {subtitle}
          </Typography>
        </Box>
        <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
          {secTypes && secTypes.length > 0 && (
            <SecTypeToggle
              value={nav.secType}
              onChange={nav.setSecType}
              options={secTypes}
              disabled={disableToggle}
            />
          )}
          {showSearch && (
            <CodeSearchBar
              activeCode={nav.searchCode}
              onSearch={nav.handleSearch}
              onClear={nav.handleClearSearch}
              placeholder={searchPlaceholder ?? searchPlaceholderFor(nav.secType)}
              disabled={disableSearch}
            />
          )}
          {showRefresh && (
            <RefreshButton
              onClick={nav.refresh}
              loading={nav.loading}
              label="Refresh"
              tooltip={refreshTooltip ?? "Refresh classification trees (bypass cache)"}
            />
          )}
          {headerExtra}
        </Box>
      </Box>

      {/* ---- Classification Nav (or a page-provided custom navigator) ---- */}
      {navSlot ?? (
        <SecClassificationNav
          sectors={nav.sectors}
          sectorId={nav.sectorId}
          industrySlug={nav.industrySlug}
          exchange={nav.exchange}
          onSectorChange={nav.handleSectorChange}
          onIndustryChange={nav.handleIndustryChange}
          onExchangeChange={nav.handleExchangeChange}
          strategies={nav.strategies}
          strategyId={nav.strategyId}
          themeSlug={nav.themeSlug}
          onStrategyChange={nav.handleStrategyChange}
          onThemeChange={nav.handleThemeChange}
          itemKind={nav.secType === "etf" ? "ETF" : nav.secType === "index" ? "Index" : "Stock"}
          selectedItemCode={nav.searchCode}
          onItemSelected={nav.onItemSelected}
          onClearItemSelection={nav.onClearItemSelection}
          loading={nav.loading}
        />
      )}

      {/* ---- Loading / error ---- */}
      {nav.loading && hideContentWhileLoading && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
          <CircularProgress size={32} />
        </Box>
      )}
      {nav.error && (
        <Alert severity="error" variant="filled" sx={{ mb: 2 }}>
          Failed to load {errorPrefix}: {nav.error}
        </Alert>
      )}

      {/* ---- Page content ---- */}
      {!nav.error && (!nav.loading || !hideContentWhileLoading) && children}
    </Box>
  );
}
