/**
 * buildMaSpreadAiAskSpec — the panel's AI Ask spec, describing every
 * in-plot control's CURRENT value so the modal / LLM see the view on
 * screen. Pure function over the control state; the panel keeps the
 * useMemo and its dependency list.
 */
import type { AiAskSpec } from "@/shared/ai-ask";
import { INSIGHT_PAGES } from "@/shared/ai-ask";
import type { OhlcMode } from "@/lib/ohlc";
import type { MovAveSpreadPairSeries } from "@shared/types";
import {
  TREND_TYPES,
  TREND_TYPE_LABELS,
  type DerivTrendSpec,
  type ShadeOverlay,
  type TrendType,
} from "./chartOption";

export interface MaSpreadAiAskArgs {
  code: string;
  name: string;
  /** sec_type label ("etf" | "index" | "stock" | "industry") — rides the
   *  instrument's assetClass so the insight metrics table resolves. */
  secType: string;
  selectedPair: MovAveSpreadPairSeries | undefined;
  amtPairSelected: boolean;
  shortName: string;
  longName: string;
  bollingerK: number;
  roofFloorWindow: number | null;
  rfEstOn: boolean;
  rfEstMethod: string;
  streakLabel: string;
  pxVolShade: ShadeOverlay | null;
  ohlcMode: OhlcMode;
  rsiDays: number[];
  rsiLinesActive: boolean;
  derivTrend: DerivTrendSpec | null;
  anchorDate: string | undefined;
  rsiTopPct: number | null;
  trendPicks: Set<TrendType>;
}

export function buildMaSpreadAiAskSpec(args: MaSpreadAiAskArgs): AiAskSpec {
  const {
    code,
    name,
    secType,
    selectedPair,
    amtPairSelected,
    shortName,
    longName,
    bollingerK,
    roofFloorWindow,
    rfEstOn,
    rfEstMethod,
    streakLabel,
    pxVolShade,
    ohlcMode,
    rsiDays,
    rsiLinesActive,
    derivTrend,
    anchorDate,
    rsiTopPct,
    trendPicks,
  } = args;
  return {
    insight: INSIGHT_PAGES.maSpread,
    asOfDate: anchorDate ?? undefined,
    intro:
      `MA-spread study for ${code} — pair ${selectedPair?.pair_label ?? "—"}: short curve vs ` +
      "long MA, green fill when short > long, red when short < long; tooltips report each " +
      "series' slope and curvature. Control overlays: Bollinger ±kσ envelope on the long " +
      "MA, click-anchored rolling High/Low trendlines (roof/floor), extreme↔2nd-extreme " +
      "shade zones (all roof/floor windows at once), break-streak zones, " +
      "Px-Vol state shading (price speed × amount state). % mode: OHLC + MAs rebased to " +
      "% change from the first valid close; Amt pairs switch to an amount-envelope view " +
      "with a lowkey price reference. Indication: green fill = momentum regime long; " +
      "roof/floor breach = breakout; a deep streak = stretched — fade-prone.",
    instruments: [
      {
        code,
        name: name || undefined,
        assetClass: secType as "etf" | "index" | "stock" | "industry",
      },
    ],
    series: amtPairSelected
      ? [
          { name: "Price", description: "lowkey (high+low)/2 close reference for the amt-envelope view" },
          { name: "Amt Above", unit: "亿", description: "daily trading amount at/above the selected Amt MA" },
          { name: "Amt Below", unit: "亿", description: "daily trading amount below the selected Amt MA" },
          ...[5, 20, 60, 120, 255].map((w) => ({
            name: `Amt MA${w}`,
            unit: "亿",
            description: `${w}-day trading-amount moving average (selected window emphasized)`,
          })),
        ]
      : [
          { name: shortName, description: "the pair's short leg (price or short MA/EMA)" },
          { name: longName, description: "the pair's long moving average" },
          ...(rsiLinesActive
            ? rsiDays.slice().sort((a, b) => a - b).map((w) => ({
                name: `RSI${w}`,
                description: `${w}-day Wilder RSI trend line on the offset right axis — RSI 0-100 hugs the bottom quarter of the shared plot (dashed 70/30 guides)`,
              }))
            : derivTrend != null
              ? derivTrend.lines.map((l) => ({
                  name: l.name,
                  description:
                    `${l.name.replace(/ (Slope|Curv)$/, "")} ${derivTrend.mode === "slope" ? "1st derivative — daily diff of the price / MA / EMA line" : "2nd derivative — diff of that daily diff"}, DB-stored direct diff, raw price units per trading day, dashed zero guide`,
                }))
              : roofFloorWindow != null
                ? [
                    { name: `Δ Low(${roofFloorWindow}d)`, description: "close − the LAST low anchor's value (change_since_last_low): floor-blue bars ABOVE the dashed zero central line, positive = price above the floor anchor; crossing below it = floor break" },
                    { name: `Δ High(${roofFloorWindow}d)`, description: "close − the LAST high anchor's value (change_since_last_high): roof-orange bars BELOW the zero central line, negative = price below the roof anchor; crossing above it = roof breakout" },
                  ]
                : [
                    { name: "Amt Up", unit: "亿", description: "daily trading amount on up days (lowkey bars)" },
                    { name: "Amt Down", unit: "亿", description: "daily trading amount on down days (lowkey bars)" },
                  ]),
          { name: `Upper (+${bollingerK}σ)`, description: "long MA + k×σ Bollinger band edge" },
          { name: `Lower (−${bollingerK}σ)`, description: "long MA − k×σ Bollinger band edge" },
          ...(roofFloorWindow != null
            ? [
                { name: `High(${roofFloorWindow}d)`, description: "rolling OHLC window high" },
                { name: `Low(${roofFloorWindow}d)`, description: "rolling OHLC window low" },
                { name: `Roof(${roofFloorWindow}d)`, description: "trendline through the window's top+2nd highs, stopping at the anchor date" },
                { name: `Floor(${roofFloorWindow}d)`, description: "trendline through the window's top+2nd lows, stopping at the anchor date" },
              ]
            : []),
          ...(roofFloorWindow != null && rfEstOn
            ? [
                { name: `Est roof (${rfEstMethod})`, description: `estimated resistance level AT the chosen date under the ${rfEstMethod} method (analysis.mov_ave_spreads_roof_floor_estimates; median = the cross-method consensus of trendline/donchian/quantile/channel/boll) — horizontal dashed roof-orange line` },
                { name: `Est floor (${rfEstMethod})`, description: "the mirror estimated support level — horizontal dashed floor-blue line at the same chosen date" },
              ]
            : []),
          ...(streakLabel
            ? [
                { name: `High ${streakLabel}`, description: "merged span of closes above the anchor window's static top pct zone" },
                { name: `Low ${streakLabel}`, description: "merged span of closes below the anchor window's static bottom pct zone" },
              ]
            : []),
          ...(pxVolShade ? [{ name: pxVolShade.label, description: "dates matching the selected price-speed × amount-state combo" }] : []),
          ...(trendPicks.size > 0
            ? TREND_TYPES.filter((t) => trendPicks.has(t)).map((t) => ({
                name: `Trend(${TREND_TYPE_LABELS[t]})`,
                description:
                  "the signals tier's trend spans (analysis.trend_days: RSI re-entry momentum episodes + MA255-gap extremes) shaded in the phase's light fill (top red / bottom green / rise blue / fall amber)",
              }))
            : []),
        ],
    state: {
      pair: selectedPair?.pair_label ?? "—",
      ohlc_mode: ohlcMode === "percentage" ? "% (rebased to first valid close)" : "absolute",
      bollinger_k: bollingerK,
      lower_overlay:
        (rsiLinesActive
          ? `rsi trends (${rsiDays.slice().sort((a, b) => a - b).join("/")}d window lines replace the amt bars)`
          : derivTrend != null
            ? `${derivTrend.mode === "slope" ? "slopes" : "curvatures"} (Price + ${selectedPair?.kind === "ema" ? "EMA6/EMA20" : "MA5/MA20"} DB-stored derivative lines — value[t] − value[t−1], no averaging — replace the amt bars; the MA/EMA curves follow the selected pair chip)`
            : roofFloorWindow != null
              ? "change-since-last-anchor plot (the enabled roof/floor window's Δ Low / Δ High bars around a dashed zero central line — Δ Low positive above = price off the floor anchor, Δ High negative below = price off the roof anchor — replace the amt bars)"
              : "amt (lowkey bars)") +
        (rsiTopPct != null
          ? ` + RSI top-${Math.round(rsiTopPct * 100)}% history-rank shades split evenly: green where the RSI ranked within the top ${Math.round(rsiTopPct * 500) / 10}% of its own ${rsiDays.length > 0 ? `${rsiDays.slice().sort((a, b) => a - b).join("/")}d window` : "history (any window)"} (rises), red where within the bottom ${Math.round(rsiTopPct * 500) / 10}% (drops); base fill per qualifying window, darker where multiple picked windows' extremes overlap`
          : "") +
        (trendPicks.size > 0
          ? ` + signals-tier trend phase shades (${TREND_TYPES.filter((t) => trendPicks.has(t)).map((t) => TREND_TYPE_LABELS[t]).join("/")})`
          : ""),
      ohlc_window: roofFloorWindow ?? "off",
      anchor_date: anchorDate ?? "none",
      rf_estimate:
        roofFloorWindow != null && rfEstOn
          ? `${rfEstMethod} @ ${anchorDate ?? "latest date"}`
          : "off",
      streak_combo: streakLabel || "off",
      px_vol: pxVolShade?.label ?? "off",
      trend:
        trendPicks.size > 0
          ? TREND_TYPES.filter((t) => trendPicks.has(t))
              .map((t) => TREND_TYPE_LABELS[t])
              .join("/")
          : "off",
    },
    notes: [
      "All 9 pairs share one date axis; the in-chart dataZoom windows the view.",
      "Break streaks are detected client-side against the anchor window's static band edge (≤5-day in-band gaps bridged).",
      "Shade families are mutually exclusive — one shade source at a time (trend / RSI pct / streaks / px-vol / OHLC days / regimes); a pick in one clears the others.",
    ],
  };
}
