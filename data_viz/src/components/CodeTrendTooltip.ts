/**
 * CodeTrendTooltip — the ONE shared axis tooltip for every code-trend
 * chart (per-security daily OHLC + MA + overlays): CodeTrendChart →
 * StockOhlcChart (Stock / ETF baseline pages) and the Index Baseline
 * panel's chart. Grep "code trend" to find the whole family.
 *
 * Rendered through React.createElement + the shared element-to-HTML
 * renderer (react-tooltip-renderer), like the other ECharts tooltips.
 *
 * Every stat row carries its DAILY MOVEMENT beside the hovered tick's
 * value: a signed "(+/-change)" suffix vs the previous plotted tick,
 * green for gains / red for declines / dim for exactly zero. The suffix
 * is omitted where there is no valid previous tick — the series start,
 * the first tick after a gap break (comparing across a data gap would
 * pair non-adjacent trading days), and PE right after a 0 placeholder.
 *
 * The Open / High / Low / Close rows are built from the gap-broken OHLC
 * arrays at the hovered index, not from tooltip params: the OHLC bars are
 * an ECharts custom series, and custom series never reach the axis-trigger
 * params (the data carries no x dimension for the nearest-index lookup),
 * so a params-driven candle row would never render.
 */
import React from "react";
import { renderReactElement, tooltipComponents } from "@/lib/react-tooltip-renderer";
import { fmtNum, fmtMil } from "@/lib/series";
import { formatPriceValue, type OhlcMode } from "@/lib/ohlc";
import {
  DIVIDEND_COLOR,
  DOWN_COLOR,
  PE_COLOR,
  TRIGGER_STREAK_COLOR,
  UP_COLOR,
} from "@/theme/chart-palette";
import type { StockDividend } from "@shared/types";
import type { OhlcForecastAction, OhlcTradeSignal } from "./StockOhlcChart";

/** One forecast signal streak period, as reported on hover — the
 *  [start, end] qualifying run behind a merged signal's mid day, plus
 *  its trading-day count (forecast_results streak_days). */
export interface CodeTrendStreakInfo {
  days: number | null;
  start: string;
  end: string;
}

/** Per-chart state the tooltip resolves at hover time. The `broken`
 *  arrays are the PLOTTED series (gap-broken, index-aligned with the
 *  category axis) so previous-tick deltas share the displayed scale —
 *  in percentage mode the change is in %-points, matching the axis. */
export interface CodeTrendTooltipContext {
  ohlcMode: OhlcMode;
  broken: {
    dates: string[];
    open: Array<number | null>;
    high: Array<number | null>;
    low: Array<number | null>;
    close: Array<number | null>;
    ma5: Array<number | null>;
    ma20: Array<number | null>;
    ma60: Array<number | null>;
    ma120: Array<number | null>;
    pe: Array<number | null>;
    /** Trading amount in the plotted 亿 (1e8 yuan) scale — the same values
     *  the Amount / Trading Amt bars plot, so deltas need no rescaling. */
    amount: Array<number | null>;
  };
  /** Date → raw balance (yuan) lookups — the plotted margin series are
   *  shifted/clipped scores, so the tooltip re-resolves real balances
   *  (and their daily change) by date. Stock-baseline charts only. */
  rzByDate?: Map<string, number | null>;
  rqByDate?: Map<string, number | null>;
  /** Date → EPS (yuan/share) — a per-row attribute, not a plotted series. */
  epsByDate?: Map<string, number | null>;
  dividendByDate?: Map<string, StockDividend>;
  signalsByDate?: Map<string, OhlcTradeSignal[]>;
  /** Live ref — refreshed by the trigger overlay memo (no option rebuild). */
  streakInfoByDateRef?: React.MutableRefObject<Map<string, CodeTrendStreakInfo>>;
  /** Live ref — the trigger overlay's forecast-action labels (the clicked
   *  Recent Movements bucket's registered buy/sell per highlighted day),
   *  refreshed by the overlay memo (no option rebuild). */
  forecastActionByDateRef?: React.MutableRefObject<Map<string, OhlcForecastAction>>;
}

interface AxisTooltipParam {
  axisValue?: string;
  marker?: string;
  seriesName?: string;
  dataIndex?: number;
  value?: number | Array<number | null>;
}

/** Signed daily-change suffix node: green "+", red "−", dim "±0".
 *  `format` renders the magnitude with the row's own unit/scale. */
function DeltaNode({ delta, format }: { delta: number; format: (v: number) => string }) {
  const sign = delta > 0 ? "+" : delta < 0 ? "-" : "±";
  const colored = delta !== 0;
  return React.createElement(
    "span",
    {
      style: {
        color: colored ? (delta > 0 ? UP_COLOR : DOWN_COLOR) : undefined,
        opacity: colored ? 0.85 : 0.65,
      },
    },
    ` (${sign}${format(Math.abs(delta))})`,
  );
}

/**
 * Change vs the previous plotted tick of `arr` (the category index before
 * `dataIndex`). Null at index 0 and right after a gap break (previous slot
 * is null/NaN) — never compare across a data gap.
 */
function tickDelta(
  arr: Array<number | null> | undefined,
  dataIndex: number | null | undefined,
  cur: number,
): number | null {
  if (!arr || dataIndex == null || dataIndex <= 0) return null;
  const prev = arr[dataIndex - 1];
  if (prev == null || !Number.isFinite(prev)) return null;
  return cur - prev;
}

export function makeCodeTrendTooltipFormatter(ctx: CodeTrendTooltipContext) {
  const { ohlcMode, broken } = ctx;
  return (params: unknown): string => {
    const arr = (Array.isArray(params) ? params : [params]) as AxisTooltipParam[];
    if (arr.length === 0) return "";
    const dateStr = (arr[0].axisValue as string) || "";

    const makeHeader = (text: string) =>
      React.createElement(tooltipComponents.Header, null, text);
    const makeRow = (children: React.ReactNode, style?: React.CSSProperties) =>
      React.createElement(tooltipComponents.Row, { style }, children);
    const makeTextRow = (marker: React.ReactNode, name: string, text: React.ReactNode) =>
      makeRow([marker, " ", name, ": ", text]);
    const makeBold = (vstr: string) =>
      React.createElement(tooltipComponents.Bold, null, vstr);
    /** Value row with the stat's daily-change suffix (when resolvable). */
    const makeBoldRow = (
      marker: React.ReactNode,
      name: string,
      vstr: string,
      delta?: React.ReactElement | null,
    ) =>
      makeTextRow(
        marker,
        name,
        delta
          ? [makeBold(vstr), delta]
          : makeBold(vstr),
      );
    const priceDelta = (delta: number | null) =>
      delta == null
        ? null
        : React.createElement(DeltaNode, {
            delta,
            format: (v: number) => formatPriceValue(v, ohlcMode),
          });

    const children: React.ReactNode[] = [];
    children.push(makeHeader(dateStr));

    const div = ctx.dividendByDate?.get(dateStr);
    if (div) {
      const dps = div.dividend_per_share_pre_tax;
      const dpsStr = dps != null ? `¥${fmtNum(dps, 4)}/share` : "n/a";
      const totStr = div.total_dividend_wan != null
        ? ` · ¥${fmtNum(div.total_dividend_wan, 0)}万 total`
        : "";
      children.push(makeRow([
        React.createElement("span", { style: { color: DIVIDEND_COLOR } }, "◆"),
        " ",
        React.createElement(tooltipComponents.Bold, { style: { color: DIVIDEND_COLOR } }, "Dividend"),
        ` · ${dpsStr}${totStr}`,
      ], { marginBottom: 4 }));
    }

    // Trade-signal rows — one per live_signals record of the day.
    const daySignals = ctx.signalsByDate?.get(dateStr) ?? [];
    for (const s of daySignals) {
      const buy = s.action === "buy";
      const color = buy ? UP_COLOR : DOWN_COLOR;
      children.push(makeRow([
        React.createElement("span", { style: { color } }, buy ? "▲" : "▼"),
        " ",
        React.createElement(
          tooltipComponents.Bold,
          { style: { color } },
          buy ? "BUY" : "SELL",
        ),
        ` · ${s.signal_type} · ${s.signal_sub_type}`,
        ` (conf ${s.confidence}%)`,
      ]));
    }

    // Forecast-action row — the hovered day is one of the trigger
    // overlay's highlighted days AND the clicked forecast bucket
    // registered as a signal strategy: report the bucket's own buy/sell
    // (the same action the triangle marker carries) with its expected
    // blended move. The map is refreshed by the trigger overlay memo —
    // reading the ref here keeps the option build independent of the
    // highlight state.
    const forecastAction = ctx.forecastActionByDateRef?.current.get(dateStr);
    if (forecastAction) {
      const buy = forecastAction.action === "buy";
      const color = buy ? UP_COLOR : DOWN_COLOR;
      const conf = forecastAction.confidence;
      const confStr = conf != null && Number.isFinite(conf)
        ? ` (exp ${conf >= 0 ? "+" : ""}${(conf * 100).toFixed(2)}%)`
        : "";
      children.push(makeRow([
        React.createElement("span", { style: { color } }, buy ? "▲" : "▼"),
        " ",
        React.createElement(
          tooltipComponents.Bold,
          { style: { color } },
          buy ? "BUY" : "SELL",
        ),
        ` · forecast${confStr}`,
      ]));
    }

    // Forecast streak row — when the hovered day sits inside one of
    // the trigger overlay's dark-purple streak spans (the qualifying
    // run behind a merged signal), report the run: its trading-day
    // count (forecast_results streak_days) and the span. The map is
    // refreshed by the trigger overlay memo — reading the ref here
    // keeps the option build independent of the highlight state.
    const streak = ctx.streakInfoByDateRef?.current.get(dateStr);
    if (streak) {
      const daysStr = streak.days != null ? ` · ${streak.days}d` : "";
      children.push(makeRow([
        React.createElement("span", { style: { color: TRIGGER_STREAK_COLOR } }, "▬"),
        " ",
        React.createElement(
          tooltipComponents.Bold,
          { style: { color: TRIGGER_STREAK_COLOR } },
          "Streak",
        ),
        ` · ${streak.start} → ${streak.end}${daysStr}`,
      ]));
    }

    // --- OHLC rows (Open / High / Low / Close, one row each) -----------------
    // Built from the broken arrays at the hovered index, NOT from tooltip
    // params: the OHLC bars are an ECharts custom series whose data carries
    // no x dimension, so the axis-trigger tooltip never includes it — only
    // the line/bar series reach the formatter's params. The hovered
    // dataIndex comes from whichever param has one (all axis params share
    // it), and the same index drives the per-series deltas below.
    const hoveredIdx = arr.find((p) => p.dataIndex != null)?.dataIndex ?? null;
    if (hoveredIdx != null && hoveredIdx >= 0 && hoveredIdx < broken.dates.length) {
      const o = broken.open[hoveredIdx];
      const h = broken.high[hoveredIdx];
      const l = broken.low[hoveredIdx];
      const cl = broken.close[hoveredIdx];
      const finite = (v: number | null) => v != null && Number.isFinite(v);
      if (finite(o) || finite(h) || finite(l) || finite(cl)) {
        // Square marker tinted like the day's candle (close >= open = up) —
        // one per row so the OHLC block aligns with the marker-led rows below.
        const marker = React.createElement(
          "span",
          { style: { color: o != null && cl != null && cl < o ? DOWN_COLOR : UP_COLOR } },
          "▪",
        );
        const row = (label: string, cur: number | null, prevArr: Array<number | null>) => {
          if (!finite(cur)) return;
          children.push(makeBoldRow(
            marker,
            label,
            formatPriceValue(cur, ohlcMode),
            priceDelta(tickDelta(prevArr, hoveredIdx, cur)),
          ));
        };
        row("Open", o, broken.open);
        row("High", h, broken.high);
        row("Low", l, broken.low);
        row("Close", cl, broken.close);
      }
    }

    const isPriceSeries = (name: string) =>
      name === "Close" || name.startsWith("MA");
    for (const p of arr) {
      if (p.value == null) continue;
      const name = p.seriesName ?? "";
      // The candle row above already reports OHLC from the broken arrays —
      // skip the custom-series params should ECharts ever deliver them
      // (it currently does not), so the row never renders twice.
      if (name === "OHLC") continue;
      const di = p.dataIndex;
      if (!Array.isArray(p.value)) {
        const v = p.value as number;
        if (!Number.isFinite(v)) continue;
        let vstr: string;
        let delta: React.ReactElement | null = null;
        if (name === "Amount" || name === "Trading Amt") {
          vstr = fmtNum(v) + " 亿";
          // broken.amount is the plotted 亿-scale array — deltas compare 1:1
          const d = tickDelta(broken.amount, di, v);
          if (d != null) {
            delta = React.createElement(DeltaNode, {
              delta: d,
              format: (x: number) => fmtNum(x) + " 亿",
            });
          }
        } else if (name === "cash borrow balance" || name === "sec borrow balance") {
          // Balances resolve raw yuan by date (plotted values are scores) —
          // the daily change compares the raw balances of adjacent ticks.
          const byDate = name === "cash borrow balance" ? ctx.rzByDate : ctx.rqByDate;
          if (byDate) {
            const cur = byDate.get(dateStr) ?? null;
            const prevDate = di != null && di > 0 ? broken.dates[di - 1] : undefined;
            const prev = prevDate != null ? byDate.get(prevDate) ?? null : null;
            vstr = fmtMil(cur);
            if (cur != null && Number.isFinite(cur) && prev != null && Number.isFinite(prev)) {
              delta = React.createElement(DeltaNode, {
                delta: cur - prev,
                format: (x: number) => fmtMil(x),
              });
            }
          } else {
            vstr = fmtMil(v);
          }
        } else if (name.includes("remained")) {
          vstr = fmtMil(v);
        } else if (name.includes("RZ") || name.includes("RQ")) {
          vstr = fmtNum(v);
        } else if (isPriceSeries(name)) {
          vstr = formatPriceValue(v, ohlcMode);
          const prevArr = name === "Close" ? broken.close
            : name === "MA5" ? broken.ma5
            : name === "MA20" ? broken.ma20
            : name === "MA60" ? broken.ma60
            : name === "MA120" ? broken.ma120
            : undefined;
          delta = priceDelta(tickDelta(prevArr, di, v));
        } else if (name === "PE" || name === "PE (est)") {
          vstr = fmtNum(v, 2);
          // 0 is a PE placeholder (not a real sample) — never diff against it
          if (broken.pe && di != null && di > 0) {
            const prevPe = broken.pe[di - 1];
            if (prevPe != null && Number.isFinite(prevPe) && prevPe !== 0) {
              delta = React.createElement(DeltaNode, {
                delta: v - prevPe,
                format: (x: number) => fmtNum(x, 2),
              });
            }
          }
        } else {
          vstr = formatPriceValue(v, ohlcMode);
        }
        children.push(makeBoldRow(p.marker ?? "", name, vstr, delta));
      }
    }

    const epsVal = ctx.epsByDate?.get(dateStr);
    if (epsVal != null && Number.isFinite(epsVal)) {
      children.push(makeRow([
        React.createElement("span", { style: { color: PE_COLOR } }, "●"),
        " ",
        "EPS: ",
        React.createElement(tooltipComponents.Bold, null, `¥${fmtNum(epsVal, 4)}`),
      ]));
    }

    return renderReactElement(React.createElement(React.Fragment, null, children));
  };
}
