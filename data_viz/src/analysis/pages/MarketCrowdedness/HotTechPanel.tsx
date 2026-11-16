/**
 * HotTechPanel — the second card per code: the hot stocks' typical
 * technical profile (analysis.market_crowdedness_hot_tech) — per date,
 * the mean + median over the day's rolling hot set of ONE selectable
 * stat, each read also carried as its percentile vs the pool's own
 * trailing-year history in the tooltip.
 *
 * The card reacts to the shared controls owned by CrowdednessCodeGroup:
 *   • hotYear (the crowdedness card's Hot set select) — a pinned year
 *     slices the rows to that calendar year (a display-window slice;
 *     the hot-tech table has no year-pinned variant, so the aggregates
 *     stay the ROLLING hot set's) — keeping the date axis aligned with
 *     the crowdedness card above.
 *   • group (the shared echarts.connect id) — the crowdedness card's
 *     dataZoom time slider and hover sync onto this chart.
 *
 * The stat switcher: eight stat buttons on the left plus the SHARED
 * OhlcModeToggle (the ma-spread header's Absolute | % Change read
 * toggle, used verbatim) pushed to the strip's right edge — Absolute
 * shows the metric's own scale (scale: true), while the "% Change"
 * button here drives the own-history percentile read plotted 0..100
 * with a "%" axis (the ma-spread percentage-mode setup; comparable
 * across metrics and pools).
 *
 * Rows exist only where the pool's active size >= 10 and the hot set is
 * non-empty; the *_pct columns are NULL during the pool's percentile
 * warm-up. Codes outside the pipeline scope plot close only.
 */
import { useMemo, useState } from "react";
import { BaseChart, useChartThemeMode, useChartData } from "@/shared/charts/base-chart";
import type { BaseChartProps } from "@/shared/charts/base-chart";
import { fetchMarketCrowdednessHotTech } from "@/lib/api-client";
import type {
  MarketCrowdednessHotTechMetric,
  MarketCrowdednessHotTechResponse,
} from "@shared/types";
import { Box, ToggleButton, ToggleButtonGroup, Typography } from "@mui/material";
import OhlcModeToggle from "@/shared/components/OhlcModeToggle";
import { buildHotTechCurveOption } from "./hotTechCurveOption";
import { HOT_TECH_METRIC_ITEMS } from "./constants";
import type { HotTechView, PanelProps } from "./types";

interface Props extends BaseChartProps, PanelProps {
  /** The shared hot-set year (0 = rolling) — slices the rows to that
   *  calendar year (display window only; the aggregates stay rolling). */
  hotYear?: number;
}

export default function HotTechPanel({ code, name, secType, hotYear = 0, ...base }: Props) {
  const themeMode = useChartThemeMode();
  // The selected stat (button group; default = the swing-trend position)
  // and the read mode (the metric's own scale vs its 0..100 history
  // percentiles).
  const [metric, setMetric] = useState<MarketCrowdednessHotTechMetric>(
    "px_vs_ma20",
  );
  const [view, setView] = useState<HotTechView>("abs");
  const { data, loading, error } = useChartData<MarketCrowdednessHotTechResponse>(
    () => fetchMarketCrowdednessHotTech(code, secType),
    [code, secType],
  );

  // A pinned hot-set year slices the display window to that calendar
  // year so the axis stays aligned with the crowdedness card (which the
  // API serves year-sliced) — the echarts.connect dataZoom sync needs
  // equal-length category axes.
  const sliced = useMemo<MarketCrowdednessHotTechResponse | null>(() => {
    if (!data) return null;
    if (!hotYear) return data;
    return {
      ...data,
      rows: data.rows.filter((r) => r.date.slice(0, 4) === String(hotYear)),
    };
  }, [data, hotYear]);

  const option = useMemo(
    () =>
      sliced && sliced.rows.length > 0
        ? buildHotTechCurveOption(sliced, metric, view, themeMode)
        : null,
    [sliced, metric, view, themeMode],
  );

  const label = name || code;
  const rows = sliced?.rows ?? [];
  const hasMetric = rows.some((r) => r[`mean_${metric}`] != null);
  // The latest hot-set size with a defined row — the "who is aggregated"
  // evidence for the subtitle.
  let lastHotN: number | null = null;
  for (let i = rows.length - 1; i >= 0; i--) {
    if (rows[i].hot_n != null) {
      lastHotN = rows[i].hot_n;
      break;
    }
  }
  const subtitle =
    rows.length > 0
      ? `${rows.length} daily bars · close${hasMetric ? ` · hot set ${lastHotN ?? "—"} names` : " (no hot-tech rows for this code)"}${hotYear ? ` · sliced to ${hotYear}` : ""}`
      : undefined;

  return (
    <BaseChart
      title={`${label} — Hot-stock tech profile`}
      subtitle={subtitle}
      loading={loading}
      error={error ? `Failed to load hot-tech stats: ${error}` : null}
      option={option}
      emptyText={`No close history for ${label} in stats — nothing to plot.`}
      height={360}
      {...base}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1,
          px: 0.5,
          pb: 0.5,
          flexWrap: "wrap",
        }}
      >
        <Typography variant="caption" sx={{ fontSize: "0.72rem", fontWeight: 600 }}>
          Stat:
        </Typography>
        <ToggleButtonGroup
          size="small"
          exclusive
          value={metric}
          onChange={(_, v: MarketCrowdednessHotTechMetric | null) => {
            if (v) setMetric(v);
          }}
        >
          {HOT_TECH_METRIC_ITEMS.map((m) => (
            <ToggleButton
              key={m.key}
              value={m.key}
              sx={{ px: 1, py: 0.25, fontSize: "0.7rem" }}
            >
              {m.label}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
        {/* The shared ma-spread read toggle (Absolute | % Change — used
            verbatim per the user's pick), pushed to the strip's right
            edge; the OhlcMode maps onto the panel's abs/pct read. */}
        <Box sx={{ ml: "auto" }}>
          <OhlcModeToggle
            value={view === "pct" ? "percentage" : "absolute"}
            onChange={(m) => setView(m === "percentage" ? "pct" : "abs")}
          />
        </Box>
      </Box>
    </BaseChart>
  );
}
