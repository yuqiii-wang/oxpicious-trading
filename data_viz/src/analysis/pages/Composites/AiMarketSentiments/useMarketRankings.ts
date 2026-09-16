/**
 * Market-mode feed scope for the Market Sentiments by AI and News page —
 * the LATEST month's top-N HYPE + top-N DRAIN industries from
 * analysis.industry_hypes_and_drains (the same seasonal rankings the
 * Market Trend plot's "Hypes & Drains" sub-view renders). Fetched lazily
 * on first entry into market mode (and on Refresh) with the plot's default
 * parameters.
 */
import { useEffect, useMemo, useState } from "react";
import { fetchIndustryHypesAndDrains } from "@/lib/api-client";
import { DEFAULT_ROLLING_DAYS } from "@/analysis/pages/IndustrySentiments/constants";
import type { IndustryHypesAndDrainsResponse } from "@shared/types";
import { MARKET_TOP_BENCHMARK } from "./constants";

/** One seasonal_rankings row. */
type HdRow = IndustryHypesAndDrainsResponse["seasonal_rankings"][number];

/** The latest season's top-N rows, both sides. Null while the rankings
 *  haven't loaded (the feed fetch is deferred — never silently unscoped);
 *  a loaded-but-empty ranking yields ids: [] which scopes the feed to an
 *  EMPTY set (present-but-empty industry_ids matches nothing). */
export interface MarketRanked {
  season: string | null;
  hypes: HdRow[];
  drains: HdRow[];
  ids: string[];
}

export function useMarketRankings(
  mode: "market" | "industry",
  marketTopN: 3 | 5,
  refreshKey: number,
): {
  marketRanked: MarketRanked | null;
  hdLoading: boolean;
  hdError: string | null;
  /** Feed scope label — "市场 Top…" until the rankings resolve, then
   *  "市场 Top N · <season> · <labels>". */
  marketLabel: string;
} {
  const [hdData, setHdData] = useState<IndustryHypesAndDrainsResponse | null>(null);
  const [hdLoading, setHdLoading] = useState(false);
  const [hdError, setHdError] = useState<string | null>(null);
  useEffect(() => {
    if (mode !== "market") return;
    let cancelled = false;
    setHdLoading(true);
    setHdError(null);
    fetchIndustryHypesAndDrains(MARKET_TOP_BENCHMARK, DEFAULT_ROLLING_DAYS, "equal")
      .then((d) => {
        if (cancelled) return;
        setHdData(d);
        setHdLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setHdError(e.message);
        setHdLoading(false);
      });
    return () => { cancelled = true; };
  }, [mode, refreshKey]);

  const marketRanked = useMemo<MarketRanked | null>(() => {
    if (mode !== "market" || !hdData) return null;
    const seasons = hdData.seasons.map((s) => s.season_qkey).sort();
    const season = seasons[seasons.length - 1] ?? null;
    const rows = hdData.seasonal_rankings.filter(
      (r) => r.season_qkey === season && r.rank <= marketTopN,
    );
    const hypes = rows
      .filter((r) => r.rank_side === "HYPE")
      .sort((a, b) => a.rank - b.rank);
    const drains = rows
      .filter((r) => r.rank_side === "DRAIN")
      .sort((a, b) => a.rank - b.rank);
    return {
      season,
      hypes,
      drains,
      ids: [...new Set([...hypes, ...drains].map((r) => r.industry_id))],
    };
  }, [mode, hdData, marketTopN]);

  const marketLabel = useMemo(() => {
    if (!marketRanked) return "市场 Top…";
    const labels = [
      ...new Set(
        [...marketRanked.hypes, ...marketRanked.drains].map(
          (r) => r.industry_label.split("  ")[0] || r.industry_label,
        ),
      ),
    ];
    return [
      `市场 Top ${marketTopN}`,
      marketRanked.season ?? "",
      labels.join(" + "),
    ]
      .filter(Boolean)
      .join(" · ");
  }, [marketRanked, marketTopN]);

  return { marketRanked, hdLoading, hdError, marketLabel };
}
