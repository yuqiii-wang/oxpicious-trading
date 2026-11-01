/**
 * Market-mode feed scope for the Market Sentiments pages —
 * the LATEST month's top-N HYPE + top-N DRAIN industries from
 * analysis.industry_hypes_and_drains (the same seasonal rankings the
 * Market Trend plot's "Hypes & Drains" sub-view renders), PLUS the
 * broad-market tag the daily ai_daily summary QA stores under (see
 * MARKET_FEED_BROAD_INDUSTRY_ID — the rankings never contain it). Fetched
 * lazily on first entry into market mode (and on Refresh) with the plot's
 * default parameters.
 */
import { useEffect, useMemo, useState } from "react";
import { fetchIndustryHypesAndDrains } from "@/lib/api-client";
import { DEFAULT_ROLLING_DAYS } from "@/analysis/pages/MarketBeta/constants";
import type { IndustryHypesAndDrainsResponse } from "@shared/types";
import {
  MARKET_FEED_BROAD_INDUSTRY_ID,
  MARKET_TOP_BENCHMARK,
} from "./constants";

/** One seasonal_rankings row. */
type HdRow = IndustryHypesAndDrainsResponse["seasonal_rankings"][number];

/** The latest season's top-N rows, both sides (ids + the broad-market
 *  tag). Null while the rankings haven't loaded (the feed fetch is
 *  deferred — never silently unscoped); a loaded-but-empty ranking still
 *  carries the broad tag, so the feed shows the daily broad-market QA
 *  rather than the whole corpus. */
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
      ids: [
        ...new Set([...hypes, ...drains].map((r) => r.industry_id)),
        MARKET_FEED_BROAD_INDUSTRY_ID,
      ],
    };
  }, [mode, hdData, marketTopN]);

  const marketLabel = useMemo(() => {
    if (!marketRanked) return "市场 Top…";
    // 大盘 has no ranking row but IS in scope (the daily broad-market QA).
    const labels = [
      ...new Set(
        [...marketRanked.hypes, ...marketRanked.drains].map(
          (r) => r.industry_label.split("  ")[0] || r.industry_label,
        ),
      ),
      "大盘",
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
