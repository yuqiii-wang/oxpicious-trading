/**
 * MarketParticipationChart — the "Market Participation" view of the
 * Market Beta page.
 *
 * Whole-market participation gauges, one row per trading day
 * (analysis.market_participation, built by the
 * analysis.market_participation daily pipeline):
 *
 *   Plot 1 "Market Turnover & SSE Share" — total SS+SZ proxy turnover
 *   stacked bars (亿, left axis) + the raw daily SSE share line (right
 *   axis; the "SSE Share" toggle hides it).
 *   PROXY: each exchange is proxied by ONE representative broad index
 *   (SS -> 000001 上证指数, SZ -> 399001 深证成指), NOT a true
 *   exchange-wide sum.
 *
 *   Plot 2 "Advance / Decline Breadth" — adv (+) / dec (−) name counts
 *   as diverging stacked bars around zero + the day's median % change
 *   line + the A/D ratio line (the "A/D Ratio" toggle hides it).
 *
 * Data: GET /api/analysis/market-beta/participation?days=2000 — a
 * display-only panel; the daily pipeline keeps the table fresh.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { ToggleButton, ToggleButtonGroup } from "@mui/material";
import ChartCard from "@/shared/components/ChartCard";
import { BaseChart, useChartThemeMode } from "@/shared/charts/base-chart";
import { useAiAskAddon, INSIGHT_PAGES } from "@/shared/ai-ask";
import { useDocs, compactDefined } from "@/shared/docs";
import { fetchMarketParticipation } from "@/lib/api-client";
import type { MarketParticipationDay } from "@/lib/api-client";
import * as echarts from "echarts";
import type { ECharts } from "echarts";
import {
  buildTurnoverShareOption,
  buildBreadthOption,
  turnoverShareGauges,
} from "./participationOption";

export function MarketParticipationChart() {
  const themeMode = useChartThemeMode();
  const [rows, setRows] = useState<MarketParticipationDay[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // On/off toggles for the right-axis overlay lines (default ON).
  const [showShare, setShowShare] = useState(true);
  const [showRatio, setShowRatio] = useState(true);

  // Cross-chart sync (the PerfAttrPanel echarts.connect pattern): both
  // charts share one connect group so the datetime dataZoom slider and
  // the hover tooltip move together. The two options build from the SAME
  // rows array — identical category axes, so index-based sync is exact.
  // connect() only binds instances registered AT CALL TIME, and BaseChart
  // mounts the ECharts only once loading clears — so re-connect on every
  // loading flip (connect is idempotent; the toggles never dispose the
  // instances, the group membership persists across option rebuilds).
  const chartGroup = "market-participation";
  useEffect(() => {
    if (loading) return;
    echarts.connect(chartGroup);
  }, [loading, chartGroup]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    // FULL history from the table's 2020 inception — the participation
    // gauges are market-level context, most useful across whole cycles
    // (the dataZoom windows any sub-range; 2000 covers ~8 years).
    fetchMarketParticipation(2000)
      .then((data) => {
        if (cancelled) return;
        setRows(data);
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
  }, []);

  const turnoverOption = useMemo(
    () =>
      rows.length > 0
        ? buildTurnoverShareOption(rows, themeMode, showShare)
        : null,
    [rows, themeMode, showShare],
  );
  const breadthOption = useMemo(
    () =>
      rows.length > 0
        ? buildBreadthOption(rows, themeMode, showRatio)
        : null,
    [rows, themeMode, showRatio],
  );

  const g = useMemo(() => turnoverShareGauges(rows), [rows]);
  const subtitle = loading
    ? "Loading market participation…"
    : rows.length === 0
      ? "No participation rows"
      : `${rows.length} trading days · latest ${rows[rows.length - 1].date}` +
        (g.latestTotalYi != null ? ` · total ${Math.round(g.latestTotalYi).toLocaleString()} 亿` : "") +
        (g.latestPctRank != null ? ` · 252d pct ${(g.latestPctRank * 100).toFixed(0)}%` : "") +
        (g.latestSharePct != null ? ` · SSE share ${g.latestSharePct.toFixed(1)}%` : "");

  const turnoverRef = useRef<ECharts | null>(null);
  const breadthRef = useRef<ECharts | null>(null);
  const docs = useDocs();

  const aiAskAddon = useAiAskAddon({
    title: "Market Participation",
    subtitle,
    option: turnoverOption,
    spec: {
      insight: INSIGHT_PAGES.marketBeta,
      intro: docs.text("ui.market-beta.participation"),
      series: compactDefined([
        docs.seriesEntry("ui.market-beta.participation", "total_trading_amount", { name: "Market total (亿)" }),
        docs.seriesEntry("ui.market-beta.participation", "ss_share", { name: "SSE share (%)" }),
        docs.seriesEntry("ui.market-beta.participation", "adv_count", { name: "Advancing names" }),
        docs.seriesEntry("ui.market-beta.participation", "dec_count", { name: "Declining names" }),
        docs.seriesEntry("ui.market-beta.participation", "ad_ratio", { name: "A/D ratio" }),
      ]),
      state: {
        days: rows.length,
        sse_share: showShare ? "shown" : "hidden",
        ad_ratio: showRatio ? "shown" : "hidden",
      },
      notes: docs.notes("ui.market-beta.participation"),
    },
    getInstance: () => turnoverRef.current,
  });

  const errorText = error ? `Failed to load market participation: ${error}` : null;

  return (
    <ChartCard
      title="Market Participation"
      subtitle={subtitle}
      titleAddon={aiAskAddon}
    >
      {/* --- Plot 1: turnover + SSE share, with the share toggle --- */}
      <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 1 }}>
        <ToggleButtonGroup
          size="small"
          exclusive
          value={showShare ? "share" : ""}
          onChange={(_, v: string | null) => {
            if (v === "share" || v === null) setShowShare(v === "share");
          }}
        >
          <ToggleButton value="share" sx={{ height: 28, px: 1.5, fontSize: "0.72rem" }}>
            SSE Share
          </ToggleButton>
        </ToggleButtonGroup>
      </div>
      <BaseChart
        variant="bare"
        option={turnoverOption}
        group={chartGroup}
        loading={loading}
        error={errorText}
        emptyText="No market participation rows"
        height={300}
        onReady={(chart) => {
          turnoverRef.current = chart;
        }}
      />

      {/* --- Plot 2: advance/decline breadth, with the A/D toggle --- */}
      <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 2, marginBottom: 1 }}>
        <ToggleButtonGroup
          size="small"
          exclusive
          value={showRatio ? "ratio" : ""}
          onChange={(_, v: string | null) => {
            if (v === "ratio" || v === null) setShowRatio(v === "ratio");
          }}
        >
          <ToggleButton value="ratio" sx={{ height: 28, px: 1.5, fontSize: "0.72rem" }}>
            A/D Ratio
          </ToggleButton>
        </ToggleButtonGroup>
      </div>
      <BaseChart
        variant="bare"
        option={breadthOption}
        group={chartGroup}
        loading={loading}
        error={errorText}
        emptyText="No breadth rows"
        height={300}
        onReady={(chart) => {
          breadthRef.current = chart;
        }}
      />
    </ChartCard>
  );
}
