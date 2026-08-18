/**
 * useStrategyNav — shared hook for strategy pages' classification navigation.
 *
 * Encapsulates the state + handlers that every strategy page needs to wire up
 * SecClassificationNav:
 *   • sec_type toggle (index / etf / stock)
 *   • L1 sector / L2 industry selection
 *   • strategy-theme navigation (BROAD / sector strategies)
 *   • exchange filter
 *   • code search (finds a code in themes or strategy-themes)
 *   • selected security code
 *
 * Any new strategy page can call `const nav = useStrategyNav()` and spread
 * the returned props onto `<SecClassificationNav {...nav.navProps} />`.
 */
import { useEffect, useState } from "react";
import CodeSearchBar, {
  findCodeInThemes,
  findCodeInStrategyThemes,
} from "@/components/CodeSearchBar";
import {
  fetchMovAveSpreadCodes,
  fetchMovAveSpreadThemes,
  fetchMovAveSpreadStrategyThemes,
} from "@/lib/api-client";
import type {
  MaSpreadSecType,
  MovAveSpreadCodesResponse,
  SectorNode,
  StrategyNode,
} from "@shared/types";

export interface StrategyNavState {
  // Classification data
  sectors: SectorNode[];
  strategies: StrategyNode[];
  codesData: MovAveSpreadCodesResponse | null;
  loading: boolean;
  error: string | null;

  // Selection state
  secType: MaSpreadSecType;
  sectorId: string | null;
  industrySlug: string | null;
  exchange: string | null;
  strategyId: string | null;
  themeSlug: string | null;
  searchCode: string | null;

  // Setters
  setSecType: (t: MaSpreadSecType) => void;
  setSectorId: (id: string | null) => void;
  setIndustrySlug: (slug: string | null) => void;
  setExchange: (ex: string | null) => void;
  setStrategyId: (id: string | null) => void;
  setThemeSlug: (slug: string | null) => void;
  setSearchCode: (code: string | null) => void;

  // Code search
  handleSearch: (code: string) => void;

  // Nav handlers (clear searchCode on change)
  handleSectorChange: (id: string | null) => void;
  handleIndustryChange: (slug: string | null) => void;
  handleStrategyChange: (id: string | null) => void;
  handleThemeChange: (slug: string | null) => void;
  handleExchangeChange: (ex: string | null) => void;

  // Item selection from L3 chips
  onItemSelected: (code: string) => void;
  onClearItemSelection: () => void;
}

export function useStrategyNav(
  onCodeChange?: (code: string | null) => void,
): StrategyNavState {
  const [secType, setSecType] = useState<MaSpreadSecType>("index");
  const [sectorId, setSectorId] = useState<string | null>(null);
  const [industrySlug, setIndustrySlug] = useState<string | null>(null);
  const [exchange, setExchange] = useState<string | null>("PRIMARY");
  const [strategies, setStrategies] = useState<StrategyNode[]>([]);
  const [strategyId, setStrategyId] = useState<string | null>(null);
  const [themeSlug, setThemeSlug] = useState<string | null>(null);
  const [sectors, setSectors] = useState<SectorNode[]>([]);
  const [codesData, setCodesData] = useState<MovAveSpreadCodesResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searchCode, setSearchCode] = useState<string | null>(null);

  // Reset nav state when secType changes
  useEffect(() => {
    setSectors([]);
    setCodesData(null);
    setError(null);
    setSectorId(null);
    setIndustrySlug(null);
    setExchange("PRIMARY");
    setStrategies([]);
    setStrategyId(null);
    setThemeSlug(null);
    setSearchCode(null);
    onCodeChange?.(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [secType]);

  // Load themes + codes
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([
      fetchMovAveSpreadThemes(secType, exchange),
      fetchMovAveSpreadCodes(secType, exchange),
      fetchMovAveSpreadStrategyThemes(secType, exchange),
    ])
      .then(([t, c, st]) => {
        if (cancelled) return;
        setSectors(t);
        setCodesData(c);
        setStrategies(st);
        if (sectorId && !t.some((s) => s.sector_id === sectorId)) {
          setSectorId(null);
          setIndustrySlug(null);
        }
        if (strategyId && !st.some((s) => s.sector_id === strategyId)) {
          setStrategyId(null);
          setThemeSlug(null);
        }
        // No page-level default — SecClassificationNav handles auto-selecting
        // the default per sec_type via its DEFAULTS_BY_KIND map.
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [secType, exchange]);

  // Code search handler
  const handleSearch = (code: string) => {
    const foundIndustry = findCodeInThemes(sectors, code);
    if (foundIndustry) {
      setError(null);
      setStrategyId(null);
      setThemeSlug(null);
      setSectorId(foundIndustry.sectorId);
      setIndustrySlug(foundIndustry.industrySlug);
      setSearchCode(code);
      onCodeChange?.(code);
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
      onCodeChange?.(code);
      return;
    }
    setError(`Code not found in ${secType.toUpperCase()} data: ${code}`);
    setSearchCode(null);
    onCodeChange?.(null);
  };

  const handleSectorChange = (id: string | null) => {
    setSearchCode(null);
    setSectorId(id);
    if (id) {
      setStrategyId(null);
      setThemeSlug(null);
    }
    onCodeChange?.(null);
  };
  const handleIndustryChange = (slug: string | null) => {
    setSearchCode(null);
    setIndustrySlug(slug);
    onCodeChange?.(null);
  };
  const handleStrategyChange = (id: string | null) => {
    setSearchCode(null);
    setStrategyId(id);
    if (id) {
      setSectorId(null);
      setIndustrySlug(null);
    }
    onCodeChange?.(null);
  };
  const handleThemeChange = (slug: string | null) => {
    setSearchCode(null);
    setThemeSlug(slug);
    onCodeChange?.(null);
  };
  const handleExchangeChange = (ex: string | null) => {
    setSearchCode(null);
    setExchange(ex);
    onCodeChange?.(null);
  };

  const onItemSelected = (code: string) => {
    setError(null);
    setSearchCode(code);
    onCodeChange?.(code);
  };
  const onClearItemSelection = () => {
    setSearchCode(null);
    onCodeChange?.(null);
  };

  return {
    sectors,
    strategies,
    codesData,
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
    setSectorId,
    setIndustrySlug,
    setExchange,
    setStrategyId,
    setThemeSlug,
    setSearchCode,
    handleSearch,
    handleSectorChange,
    handleIndustryChange,
    handleStrategyChange,
    handleThemeChange,
    handleExchangeChange,
    onItemSelected,
    onClearItemSelection,
  };
}
