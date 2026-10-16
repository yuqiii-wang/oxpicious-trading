/**
 * chart/trendTooltip — the rich axis tooltip for the composite Margin
 * Trends chart (extracted from the old monolithic chartOptions.ts).
 *
 * Sections, in order:
 *   date header
 *   margin values (top grid, 亿) — selected + muted lines, top 8 by value
 *   margin intensity — per selected code: 买入占比 (buyShare ‱) + tercile
 *                     regime tag (LOW/MID/HIGH = drained/mid/hyped)
 *   price action   — per selected code: close · 日涨跌% · fwd1/fwd5/fwd20%
 *                     (forward close→close FROM the hovered date) ·
 *                     20日波动%
 *
 * Renders React elements via renderReactElement + tooltipComponents
 * (the repo's tooltip convention — no ad-hoc HTML strings).
 */
import React from "react";
import type { MarginAnalytics } from "./types";
import { RET_SUFFIX, VOL_SUFFIX, fmtShare } from "./types";
import { fmtNum } from "@/lib/series";
import { renderReactElement, tooltipComponents } from "@/lib/react-tooltip-renderer";
import { UP_COLOR, DOWN_COLOR, axisColors } from "@/theme/chart-palette";
import type { ThemeMode } from "@/store/filters";

const CLOSE_SUFFIX = " · Close";
/** buyShare tercile display tags (index = tercile code, -1 → "—"). */
const TERCILE_TAGS = ["LOW", "MID", "HIGH"] as const;

export interface TrendTooltipArgs {
  dates: string[];
  analytics: MarginAnalytics;
  codeToLabel: Map<string, string>;
  effectiveSelectedSet: Set<string>;
  unit: string;
  themeMode: ThemeMode;
}

export function buildTrendTooltip(args: TrendTooltipArgs) {
  const { dates, analytics, codeToLabel, effectiveSelectedSet, unit } = args;
  const c = axisColors(args.themeMode);

  return {
    trigger: "axis" as const,
    backgroundColor: c.tooltipBg,
    borderColor: c.splitLineColor,
    textStyle: { color: c.textColor, fontSize: 11 },
    formatter: (params: unknown) => {
      const arr = (Array.isArray(params) ? params : [params]) as Array<{
        dataIndex?: number;
        seriesName?: string;
        value?: number | null | Array<number | string>;
        color?: string;
      }>;
      if (arr.length === 0) return "";
      const idx = arr[0].dataIndex ?? 0;
      const dateStr = dates[idx] ?? "";
      const label = (code: string) => codeToLabel.get(code) ?? code;
      const selectedCodes = Array.from(effectiveSelectedSet);

      // -- margin rows (top grid values; return/vol series excluded) ------
      const marginRows = arr.filter(
        (p) => p.seriesName
          && !p.seriesName.endsWith(CLOSE_SUFFIX)
          && !p.seriesName.endsWith(RET_SUFFIX)
          && !p.seriesName.endsWith(VOL_SUFFIX)
          && typeof p.value === "number",
      )
        .filter((p) => p.value != null && Number.isFinite(p.value as number))
        .sort((a, b) => (b.value as number) - (a.value as number))
        .slice(0, 8);

      const children: React.ReactNode[] = [];
      children.push(React.createElement(tooltipComponents.Header, null, dateStr));
      for (const p of marginRows) {
        const v = p.value as number;
        children.push(React.createElement(tooltipComponents.Row, null, [
          React.createElement("span", { style: { color: p.color ?? "" } }, "●"),
          ` ${p.seriesName}: `,
          React.createElement(tooltipComponents.Bold, null, fmtNum(v, 2)),
          ` ${unit}`,
        ]));
      }

      // -- margin intensity + price action per selected code --------------
      const intensityRows: React.ReactNode[] = [];
      const priceRows: React.ReactNode[] = [];
      for (const code of selectedCodes) {
        const derived = analytics.byCode.get(code);
        const p = derived?.points.get(dateStr);
        const closeVal = derived?.close.get(dateStr) ?? null;
        if (p) {
          const tag = p.tercile >= 0 ? TERCILE_TAGS[p.tercile] : "—";
          intensityRows.push(React.createElement(tooltipComponents.Row, null, [
            ` ${label(code)} buy share: `,
            p.buyShare != null
              ? React.createElement(tooltipComponents.Bold, null, fmtShare(p.buyShare))
              : "—",
            React.createElement(
              "span",
              { style: { opacity: 0.7 } },
              ` (${tag})`,
            ),
          ]));
          priceRows.push(React.createElement(tooltipComponents.Row, null, [
            ` ${label(code)}: `,
            closeVal != null ? fmtNum(closeVal, 2) : "—",
            " · ret ",
            p.ret != null
              ? React.createElement(
                  "span",
                  { style: { color: p.ret >= 0 ? UP_COLOR : DOWN_COLOR, fontWeight: 600 } },
                  `${p.ret >= 0 ? "+" : ""}${p.ret.toFixed(2)}%`,
                )
              : "—",
            ...([["fwd1", p.fwd1], ["fwd5", p.fwd5], ["fwd20", p.fwd20]] as const).flatMap(
              ([tag, v]) => [
                ` · ${tag} `,
                v != null
                  ? React.createElement(
                      "span",
                      { style: { color: v >= 0 ? UP_COLOR : DOWN_COLOR } },
                      `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`,
                    )
                  : "—",
              ],
            ),
            " · vol20d ",
            p.vol20 != null ? `${p.vol20.toFixed(2)}%` : "—",
          ]));
        }
      }

      const divider = React.createElement("div", {
        style: { borderTop: `1px solid ${c.splitLineColor}`, margin: "3px 0" },
      });
      if (marginRows.length > 0 && (intensityRows.length > 0 || priceRows.length > 0)) {
        children.push(divider);
      }
      if (intensityRows.length > 0) {
        children.push(React.createElement(
          "div",
          { style: { opacity: 0.8, fontSize: 10 } },
          "Margin Intensity (RZ-buy share · tercile)",
        ));
        children.push(...intensityRows);
      }
      if (intensityRows.length > 0 && priceRows.length > 0) children.push(divider);
      if (priceRows.length > 0) {
        children.push(React.createElement(
          "div",
          { style: { opacity: 0.8, fontSize: 10 } },
          "Price Action",
        ));
        children.push(...priceRows);
      }

      return renderReactElement(React.createElement(React.Fragment, null, children));
    },
  };
}
