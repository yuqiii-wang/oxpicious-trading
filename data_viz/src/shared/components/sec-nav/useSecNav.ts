/**
 * useSecNav — shared hook for analysis pages' classification navigation.
 *
 * Encapsulates the state + handlers that every analysis page needs to wire up
 * SecClassificationNav (the same role useStrategyNav plays for strategy pages):
 *   • sec_type toggle state (etf / index / stock)
 *   • L1 sector / L2 industry selection (LEFT column)
 *   • parallel strategy → theme selection (RIGHT column, mutually exclusive
 *     with the LEFT column)
 *   • exchange filter
 *   • code search (resolves a code against BOTH trees)
 *   • L3 security-chip selection
 *   • loading of the two trees via the page-supplied themes endpoints, with
 *     pruning of stale selections and a refresh cycle
 *
 * A page only supplies its own themes endpoints per sec_type:
 *   const nav = useSecNav({ themesSources: THEMES_SOURCES, ... });
 * then renders <SecNavShell nav={nav} ...>{content}</SecNavShell>.
 */
import { useEffect, useMemo, useState } from "react";
import { findCodeInThemes, findCodeInStrategyThemes } from "@/components/CodeSearchBar";
import type { SectorNode, StrategyNode } from "@shared/types";
import type { SecNavSecType, SecNavState, UseSecNavOptions } from "./types";

/** Suffix-insensitive code match (000001.SZ ≙ 000001). */
function normCode(code: string): string {
  return code.toUpperCase().replace(/\.(SS|SZ|SH|BJ|HK)$/i, "");
}

export function useSecNav(options: UseSecNavOptions): SecNavState {
  const {
    themesSources,
    defaultSecType = "index",
    dataLabel = "classification",
    onInvalidateCache,
    mutuallyExclusive = true,
  } = options;

  const [secType, setSecType] = useState<SecNavSecType>(defaultSecType);
  const [sectorId, setSectorId] = useState<string | null>(null);
  const [industrySlug, setIndustrySlug] = useState<string | null>(null);
  const [exchange, setExchange] = useState<string | null>("PRIMARY");
  const [strategies, setStrategies] = useState<StrategyNode[]>([]);
  const [strategyId, setStrategyId] = useState<string | null>(null);
  const [themeSlug, setThemeSlug] = useState<string | null>(null);
  const [sectors, setSectors] = useState<SectorNode[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searchCode, setSearchCode] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  // ---- Reset selection when secType changes -------------------------------
  // Wipes the trees + selection so the user never sees stale data from the
  // other sec_type while the new sec_type's trees are loading.
  useEffect(() => {
    setSectors([]);
    setStrategies([]);
    setError(null);
    setSectorId(null);
    setIndustrySlug(null);
    setExchange("PRIMARY");
    setStrategyId(null);
    setThemeSlug(null);
    setSearchCode(null);
  }, [secType]);

  // ---- Load the two navigation trees whenever secType/exchange changes or
  //      refresh is bumped. Both are fetched in parallel; stale sector/
  //      strategy selections that no longer exist in the filtered tree are
  //      pruned (e.g. switching exchange may drop the active sector). No
  //      page-level default — SecClassificationNav handles auto-selecting the
  //      default per sec_type via its own DEFAULTS_BY_KIND map.
  useEffect(() => {
    const source = themesSources[secType];
    if (!source) {
      setSectors([]);
      setStrategies([]);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([source.themes(exchange), source.strategyThemes(exchange)])
      .then(([t, st]) => {
        if (cancelled) return;
        setSectors(t);
        setStrategies(st);
        if (sectorId && !t.some((s) => s.sector_id === sectorId)) {
          setSectorId(null);
          setIndustrySlug(null);
        }
        if (strategyId && !st.some((s) => s.sector_id === strategyId)) {
          setStrategyId(null);
          setThemeSlug(null);
        }
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
    // themesSources is a module-level constant per page; sectorId/strategyId
    // are read only for the stale-selection prune (same idiom as the pages).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [secType, exchange, refreshKey]);

  // ---- Code search: resolve against BOTH the industry tree (LEFT column)
  //      and the strategy tree (RIGHT column). If found in the industry tree,
  //      sector/industry highlights are set; if found in the strategy tree,
  //      strategy/theme highlights are set. Shows an error if not found.
  const handleSearch = (code: string) => {
    const foundIndustry = findCodeInThemes(sectors, code);
    if (foundIndustry) {
      setError(null);
      setStrategyId(null);
      setThemeSlug(null);
      setSectorId(foundIndustry.sectorId);
      setIndustrySlug(foundIndustry.industrySlug);
      setSearchCode(code);
      return;
    }
    const foundStrategy = findCodeInStrategyThemes(strategies, code);
    if (foundStrategy) {
      setError(null);
      setSectorId(null);
      setIndustrySlug(null);
      setStrategyId(foundStrategy.strategyId);
      setThemeSlug(foundStrategy.themeSlug);
      setSearchCode(code);
      return;
    }
    setError(`Code not found in ${secType.toUpperCase()} ${dataLabel} data: ${code}`);
    setSearchCode(null);
  };

  const handleClearSearch = () => {
    setSearchCode(null);
  };

  /** Set the active code DIRECTLY, without classification-tree
   *  resolution — for external jumps (e.g. a forecast-id search that
   *  resolves a code which exists in the data universe but not in the
   *  nav trees; the chart / table panels key on the code alone). Clears
   *  the nav error; leaves the tree highlights untouched. */
  const handleCodeJump = (code: string) => {
    setError(null);
    setSearchCode(code);
  };

  // Mutual exclusivity: selecting in the LEFT column clears the RIGHT column
  // (and vice versa) unless mutuallyExclusive is off. Clicking a chip exits
  // search mode and browses normally.
  const handleSectorChange = (id: string | null) => {
    setSearchCode(null);
    setSectorId(id);
    if (id && mutuallyExclusive) {
      setStrategyId(null);
      setThemeSlug(null);
    }
  };
  const handleIndustryChange = (slug: string | null) => {
    setSearchCode(null);
    setIndustrySlug(slug);
  };
  const handleStrategyChange = (id: string | null) => {
    setSearchCode(null);
    setStrategyId(id);
    if (!id) setThemeSlug(null);
    if (id && mutuallyExclusive) {
      setSectorId(null);
      setIndustrySlug(null);
    }
  };
  const handleThemeChange = (slug: string | null) => {
    setSearchCode(null);
    setThemeSlug(slug);
  };
  const handleExchangeChange = (ex: string | null) => {
    setSearchCode(null);
    setExchange(ex);
  };

  const onItemSelected = (code: string) => {
    setError(null);
    setSearchCode(code);
  };
  const onClearItemSelection = () => {
    setSearchCode(null);
  };

  const refresh = () => {
    onInvalidateCache?.();
    setRefreshKey((k) => k + 1);
  };

  // ---- Active selection label (used in page subtitles) ---------------------
  const headerLabel = useMemo(() => {
    const activeSector = sectors.find((s) => s.sector_id === sectorId);
    const activeIndustry = activeSector?.industries.find(
      (i) => i.industry_slug === industrySlug,
    );
    const activeStrategy = strategies.find((s) => s.sector_id === strategyId);
    const activeTheme = activeStrategy?.industries.find(
      (t) => t.industry_slug === themeSlug,
    );
    return activeIndustry
      ? `${activeSector?.sector_label ?? ""} / ${activeIndustry.industry_label}`
      : activeSector
        ? `${activeSector.sector_label} (All)`
        : activeTheme
          ? `${activeStrategy?.sector_label ?? ""} / ${activeTheme.industry_label}`
          : activeStrategy
            ? `${activeStrategy.sector_label} (All)`
            : "Select a sector or strategy";
  }, [sectors, strategies, sectorId, industrySlug, strategyId, themeSlug]);

  // Display name for a code across BOTH trees (industry tree first).
  const findItemName = (code: string): string | undefined => {
    const want = normCode(code);
    for (const s of sectors) {
      for (const ind of s.industries) {
        const hit = ind.items.find((it) => normCode(it.code) === want);
        if (hit) return hit.name;
      }
    }
    for (const strat of strategies) {
      for (const th of strat.industries) {
        const hit = th.items.find((it) => normCode(it.code) === want);
        if (hit) return hit.name;
      }
    }
    return undefined;
  };

  return {
    sectors,
    strategies,
    loading,
    error,
    secType,
    sectorId,
    industrySlug,
    exchange,
    strategyId,
    themeSlug,
    searchCode,
    setSecType,
    handleSearch,
    handleClearSearch,
    handleCodeJump,
    handleSectorChange,
    handleIndustryChange,
    handleStrategyChange,
    handleThemeChange,
    handleExchangeChange,
    onItemSelected,
    onClearItemSelection,
    refresh,
    refreshKey,
    headerLabel,
    findItemName,
  };
}
