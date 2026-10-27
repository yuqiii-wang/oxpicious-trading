/**
 * buildMaSpreadAiAskSpec — the panel's AI Ask spec. The authored text
 * lives in static.docs (doc `ui.ma-spread.panel`, seeded by
 * database/sql/static/docs/static/10_ui_ma_spread.sql); this builder
 * only wires the runtime params and picks the series entries matching
 * the ACTIVE overlays. Pure function over the control state; the panel
 * keeps the useMemo and its dependency list.
 */
import type { AiAskSpec } from "@/shared/ai-ask";
import { INSIGHT_PAGES } from "@/shared/ai-ask";
import { compactDefined, type Docs, type DocsParams } from "@/shared/docs";
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

export function buildMaSpreadAiAskSpec(
  docs: Docs,
  args: MaSpreadAiAskArgs,
): AiAskSpec {
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
  const S = (key: string, params?: DocsParams) =>
    docs.seriesEntry("ui.ma-spread.panel", key, params);
  return {
    insight: INSIGHT_PAGES.maSpread,
    asOfDate: anchorDate ?? undefined,
    intro: docs.text("ui.ma-spread.panel", {
      code,
      pair: selectedPair?.pair_label ?? "—",
    }),
    instruments: [
      {
        code,
        name: name || undefined,
        assetClass: secType as "etf" | "index" | "stock" | "industry",
      },
    ],
    series: amtPairSelected
      ? compactDefined([
          S("price"),
          S("amtAbove"),
          S("amtBelow"),
          ...[5, 20, 60, 120, 255].map((w) => S("amtMa", { w })),
        ])
      : compactDefined([
          S("shortLeg", { name: shortName }),
          S("longLeg", { name: longName }),
          ...(rsiLinesActive
            ? rsiDays.slice().sort((a, b) => a - b).map((w) => S("rsi", { w }))
            : derivTrend != null
              ? derivTrend.lines.flatMap((l) => [
                  S(
                    derivTrend.mode === "slope" ? "derivSlope" : "derivCurv",
                    { name: l.name, base: l.name.replace(/ (Slope|Curv)$/, "") },
                  ),
                ])
              : roofFloorWindow != null
                ? [
                    S("deltaLow", { w: roofFloorWindow }),
                    S("deltaHigh", { w: roofFloorWindow }),
                  ]
                : [S("amtUp"), S("amtDown")]),
          S("bollUpper", { k: bollingerK }),
          S("bollLower", { k: bollingerK }),
          ...(roofFloorWindow != null
            ? [
                S("highW", { w: roofFloorWindow }),
                S("lowW", { w: roofFloorWindow }),
                S("roof", { w: roofFloorWindow }),
                S("floor", { w: roofFloorWindow }),
              ]
            : []),
          ...(roofFloorWindow != null && rfEstOn
            ? [
                S("estRoof", { method: rfEstMethod }),
                S("estFloor", { method: rfEstMethod }),
              ]
            : []),
          ...(streakLabel
            ? [S("streakHigh", { label: streakLabel }), S("streakLow", { label: streakLabel })]
            : []),
          ...(pxVolShade ? [S("pxVol", { label: pxVolShade.label })] : []),
          ...(trendPicks.size > 0
            ? TREND_TYPES.filter((t) => trendPicks.has(t)).map((t) =>
                S("trend", { label: TREND_TYPE_LABELS[t] }),
              )
            : []),
        ]),
    state: {
      pair: selectedPair?.pair_label ?? "—",
      ohlc_mode: ohlcMode === "percentage" ? "% (rebased to first valid close)" : "absolute",
      bollinger_k: bollingerK,
      lower_overlay:
        (rsiLinesActive
          ? `RSI trend lines (${rsiDays.slice().sort((a, b) => a - b).join("/")}d windows)`
          : derivTrend != null
            ? `${derivTrend.mode === "slope" ? "slope" : "curvature"} lines of the price + ${selectedPair?.kind === "ema" ? "EMA6/EMA20" : "MA5/MA20"} pair`
            : roofFloorWindow != null
              ? `change-since-last-anchor bars (Δ Low / Δ High of the ${roofFloorWindow}d roof/floor anchors)`
              : "up/down-day trading amount") +
        (rsiTopPct != null
          ? ` + RSI extreme ranks: shades where the RSI sits in its own top ${Math.round(rsiTopPct * 500) / 10}% (rises) or bottom ${Math.round(rsiTopPct * 500) / 10}% (drops) — the rare-tail episodes the RSI families trade`
          : "") +
        (trendPicks.size > 0
          ? ` + trend phases (${TREND_TYPES.filter((t) => trendPicks.has(t)).map((t) => TREND_TYPE_LABELS[t]).join("/")})`
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
    notes: docs.notes("ui.ma-spread.panel"),
  };
}
