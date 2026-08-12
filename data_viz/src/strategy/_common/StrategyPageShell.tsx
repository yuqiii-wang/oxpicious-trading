/**
 * StrategyPageShell — shared layout for strategy pages.
 *
 * Provides the common page structure every strategy page needs:
 *   1. Header — back button + title + subtitle + sec_type toggle + CodeSearchBar
 *   2. SecClassificationNav — wired from useStrategyNav
 *   3. Loading / error states
 *   4. Content area (render prop) — strategy-specific chart + controls
 *
 * Usage:
 *   <StrategyPageShell nav={nav} title="MA-Spread Strategy" subtitle="...">
 *     {(searchCode) => (
 *       // strategy-specific content: run button, chart, decision table
 *     )}
 *   </StrategyPageShell>
 */
import type { ReactNode } from "react";
import {
  Alert,
  Box,
  CircularProgress,
  IconButton,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import { ArrowBack } from "@mui/icons-material";
import { useNavigate } from "react-router-dom";
import CodeSearchBar from "@/components/CodeSearchBar";
import SecClassificationNav from "@/shared/components/sec-classification/SecClassificationNav";
import type { MaSpreadSecType } from "../../../shared/types";
import type { StrategyNavState } from "./useStrategyNav";

interface StrategyPageShellProps {
  nav: StrategyNavState;
  title: string;
  subtitle: string;
  /** Where the back button navigates to. Default: "/strategy/commons". */
  backPath?: string;
  /** Render prop — receives the selected searchCode (or null). */
  children: (searchCode: string | null) => ReactNode;
}

export default function StrategyPageShell({
  nav,
  title,
  subtitle,
  backPath = "/strategy/commons",
  children,
}: StrategyPageShellProps) {
  const navigate = useNavigate();
  const itemKind =
    nav.secType === "etf" ? "ETF" : nav.secType === "index" ? "Index" : "Stock";
  const searchPlaceholder =
    nav.secType === "etf"
      ? "ETF code (e.g. 510050)"
      : nav.secType === "index"
        ? "Index code (e.g. 000300)"
        : "Stock code (e.g. 600000)";

  return (
    <Box>
      {/* Header */}
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
            <IconButton
              onClick={() => navigate(backPath)}
              size="small"
              aria-label="back to strategy commons"
            >
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
          <ToggleButtonGroup
            value={nav.secType}
            exclusive
            size="small"
            onChange={(_, v) => {
              if (v) nav.setSecType(v as MaSpreadSecType);
            }}
          >
            <ToggleButton value="index">Index</ToggleButton>
            <ToggleButton value="etf">ETF</ToggleButton>
            <ToggleButton value="stock">Stock</ToggleButton>
          </ToggleButtonGroup>
          <CodeSearchBar
            activeCode={nav.searchCode}
            onSearch={nav.handleSearch}
            onClear={() => nav.setSearchCode(null)}
            placeholder={searchPlaceholder}
          />
        </Box>
      </Box>

      {/* Classification Nav */}
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
        itemKind={itemKind}
        selectedItemCode={nav.searchCode}
        onItemSelected={nav.onItemSelected}
        onClearItemSelection={nav.onClearItemSelection}
        loading={nav.loading}
      />

      {nav.loading && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
          <CircularProgress size={32} />
        </Box>
      )}
      {nav.error && (
        <Alert severity="error" variant="filled" sx={{ mb: 2 }}>
          {nav.error}
        </Alert>
      )}

      {/* Strategy-specific content */}
      {!nav.loading && children(nav.searchCode)}
    </Box>
  );
}
