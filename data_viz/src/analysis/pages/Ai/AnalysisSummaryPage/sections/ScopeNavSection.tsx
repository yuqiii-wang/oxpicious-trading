/** The page's scope picker strip + classification nav section. */
import { Alert, Box, Typography } from "@mui/material";
import CodeSearchBar from "@/components/CodeSearchBar";
import RefreshButton from "@/components/RefreshButton";
import SecClassificationNav from "@/shared/components/sec-classification/SecClassificationNav";
import { SecTypeToggle } from "@/shared/components/sec-nav";
import { SUMMARY_SEC_TYPES } from "../../constants";
import { itemKindFor } from "../helpers";
import type { ScopeSubjects } from "../useScopeSubjects";

export default function ScopeNavSection({
  scope,
}: {
  /** The useScopeSubjects bag — nav, tags, mutators, caption. */
  scope: ScopeSubjects;
}) {
  const { nav } = scope;
  return (
    <>
      {/* ---- Scope picker: sec_type toggle + code search + refresh ---- */}
      <Box sx={{ display: "flex", alignItems: "center", gap: 1.5, flexWrap: "wrap", mb: 1 }}>
        <SecTypeToggle
          value={nav.secType}
          onChange={scope.handleSecTypeChange}
          options={SUMMARY_SEC_TYPES}
        />
        {nav.secType !== "all" && (
          <CodeSearchBar
            activeCode={nav.searchCode}
            onSearch={scope.handleScopeSearch}
            onClear={nav.handleClearSearch}
            placeholder={
              nav.secType === "industry"
                ? "Industry id (e.g. BANKS) — find & tag"
                : nav.secType === "etf"
                  ? "ETF code (e.g. 510050) — find & tag"
                  : nav.secType === "stock"
                    ? "Stock code (e.g. 600000) — find & tag"
                    : "Index code (e.g. 000300) — find & tag"
            }
          />
        )}
        <RefreshButton
          onClick={nav.refresh}
          loading={nav.loading}
          label="Refresh"
          tooltip="Refresh classification trees (bypass cache)"
        />
        <Typography variant="caption" color="text.secondary" sx={{ ml: "auto" }}>
          Summary scope: {scope.scopeLabel}
        </Typography>
      </Box>

      {/* ---- Classification nav ("all" = the locked exchange-only row;
           otherwise the L3 row is MULTI-SELECT — every tick lands in the
           Scopes panel's subject tags) ---- */}
      <SecClassificationNav
        allMode={nav.secType === "all"}
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
        itemKind={itemKindFor(nav.secType)}
        multiSelectItems
        selectedItemCodes={scope.scopeCodes}
        onMultiItemSelected={scope.setScopeCodes}
        loading={nav.loading}
      />
      {nav.error && (
        <Alert severity="error" variant="filled" sx={{ mb: 2 }}>
          Failed to load classification: {nav.error}
        </Alert>
      )}
    </>
  );
}
