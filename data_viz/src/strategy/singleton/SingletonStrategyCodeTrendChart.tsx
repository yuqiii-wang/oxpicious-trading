/**
 * SingletonStrategyCodeTrendChart — the singleton strategy page's trend
 * chart: the SHARED CodeTrendChart with the strategy's OWN data layered
 * on top (user-directed 2026-11-18 — inherit the code trend plot, then
 * add the singleton data, so the page's market rows are the same synced
 * sec-baseline data every other code-trend consumer renders).
 *
 * Added on top of the inherited base (the ORIGINAL hand-rolled backtest
 * chart's overlays, restored through the OhlcChart extras seam):
 *   • BUY/SELL markers for the displayed run's trade_decision rows
 *     (the LAST DAY SELL excluded — it gets its own series below)
 *   • the LAST DAY SELL (final liquidation) — the purple oversized sell
 *     triangle + "LDS" label at the day's high, the original chart's
 *     look (signal_reason starts "LAST DAY SELL")
 *   • the Total P&L curve on its own right axis (from strategy_daily,
 *     aligned to the gap-broken category axis)
 *   • the selected risk-analytics period shaded green (gain) / red
 *     (loss) with its label — the RiskPanel bar-click shading
 *   • the per-day tooltip block: the day's decision (side/#, fill,
 *     signal reason) + the daily P&L rows (unrealized / total /
 *     position / qty / return / hold / Sharpes)
 *   • the selected-decision emphasis (grow + glow via selectedSignalDate)
 *     and the pan-to-marker request (focusDateRequest) driven by the
 *     page's DecisionTable row click
 *   • the visible-window reporting (onVisibleRangeChange) that drives
 *     the DecisionTable's "slider window" mirroring
 *   • the SYNC-GAP band + subtitle line: market dates the displayed run
 *     does not cover (strategy_daily ends before the baseline rows do —
 *     the stale-run case: strategy through 2026-11-13, market through
 *     2026-11-16 shades the uncovered day and says so in the subtitle),
 *     plus the run's Total Return in the sign color
 *
 * Everything else — the sec-baseline fetch lifecycle, OHLC/MA/PE/amount
 * rendering, the mode / regimes / trend toggles, the range slider, the
 * AI Ask button, loading / error / empty states — is the inherited
 * CodeTrendChart behavior, unchanged.
 */
import React from "react";
import { Box, Typography } from "@mui/material";
import CodeTrendChart from "@/ui/OhlcChart/CodeTrendChart";
import type {
  CodeTrendChartProps,
  CodeTrendRow,
  HighlightSpan,
  OhlcChartProps,
  OhlcTradeSignal,
} from "@/ui/OhlcChart";
import type { EChartsOption } from "echarts";
import { rebasePriceArrays, type OhlcMode } from "@/lib/ohlc";
import { breakArraysAtGaps, fmtNum } from "@/lib/series";
import { signalMarkerSpec } from "@/shared/charts/signalMarker";
import { DOWN_COLOR, PE_COLOR, UP_COLOR } from "@/theme/chart-palette";
import {
  escapeTooltipText,
  tooltipComponents,
} from "@/lib/react-tooltip-renderer";
import { stripMixPrefix } from "../_common/mixReason";
import type { SelectedPeriod } from "../_common/types";
import type {
  StrategyBacktestResponse,
  StrategyPeriodType,
} from "@shared/types";

/** The LAST DAY SELL purple — the original backtest chart's constant
 *  (final-liquidation marker; not a palette token there either). */
const LAST_DAY_SELL_COLOR = "#9575cd";

/** Stable EMPTY identities — same identity-stability rule as the base's
 *  NO_SPANS / NO_DIVIDENDS: a fresh [] per render would dirty the inner
 *  OhlcChart's option memo and force a full notMerge rebuild. */
const NO_TRADE_SIGNALS: OhlcTradeSignal[] = [];
const NO_HIGHLIGHT_SPANS: HighlightSpan[] = [];

export interface SingletonStrategyCodeTrendChartProps
  extends CodeTrendChartProps {
  /** The displayed singleton run (already loaded by the page's
   *  useStrategyData — no extra fetch here). Its decisions render as
   *  B/S markers; its daily rows define the covered span AND the
   *  Total P&L curve. */
  backtest: StrategyBacktestResponse | null;
  /** The selected Trade-Decisions row (null = none) — its marker draws
   *  with the emphasis look. */
  selectedDecisionNo?: number | null;
  /** The risk-analytics period selected in the RiskPanel (null = none)
   *  — its date range shades green (gain) / red (loss) on the trend. */
  selectedPeriod?: SelectedPeriod | null;
  /** Pan the dataZoom window to this date (fresh {date, seq} per
   *  request — the page bumps seq on every row click). */
  focusDateRequest?: { date: string; seq: number } | null;
  /** Visible-window reports for the DecisionTable mirroring (pass a
   *  stable useCallback — identity changes re-bind the listener). */
  onVisibleRangeChange?: (range: { start: string; end: string } | null) => void;
}

/** True when a SELL decision is the run's final liquidation row. */
function isLastDaySell(d: { side: string; signal_reason: string }): boolean {
  return d.side === "SELL" && !!d.signal_reason?.startsWith("LAST DAY SELL");
}

/** Period label of a date for the selected risk period ("2024",
 *  "2024-06", "2024-Q3"). Mirrors the retired hand-rolled builder. */
function dateToPeriodValue(dateStr: string, periodType: StrategyPeriodType): string {
  const y = dateStr.slice(0, 4);
  if (periodType === "year") return y;
  if (periodType === "month") return dateStr.slice(0, 7);
  const m = parseInt(dateStr.slice(5, 7), 10);
  return `${y}-Q${Math.floor((m - 1) / 3) + 1}`;
}

/** Apply alpha transparency to a #RRGGBB hex color → rgba() string. */
function withAlphaHex(hex: string, alpha: number): string {
  const m = /^#([0-9a-fA-F]{6})$/.exec(hex.trim());
  if (!m) return hex;
  const r = parseInt(m[1].slice(0, 2), 16);
  const g = parseInt(m[1].slice(2, 4), 16);
  const b = parseInt(m[1].slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

/** The derived sync state of one (backtest, fetched-rows) pair. */
interface SyncState {
  /** The uncovered-trailing-window span ([] when the run covers the
   *  full market span or no run is displayed). */
  spans: HighlightSpan[];
  /** Last strategy_daily trade_date of the displayed run. */
  lastStrategyDate: string | null;
  /** Last baseline row date (the market span's end). */
  lastMarketDate: string | null;
  /** Baseline rows after lastStrategyDate (0 = no gap). */
  uncoveredDays: number;
}

/** The strategy overlays for one (run, rows, ohlcMode, period) tuple —
 *  everything appended to the inner OhlcChart through the extras seam. */
interface StrategyExtras {
  extraYAxis: EChartsOption["yAxis"];
  extraSeries: EChartsOption["series"];
  tooltipExtraRows: ((date: string) => React.ReactNode) | null;
}

export default class SingletonStrategyCodeTrendChart extends CodeTrendChart<SingletonStrategyCodeTrendChartProps> {
  /** Cached marker mapping — keyed by the displayed run's seq_id so the
   *  OhlcChart option memo sees a stable identity across re-renders. */
  private signalsCache: { key: string; signals: OhlcTradeSignal[] } | null = null;
  /** Cached sync state — keyed by the (run, market-span) pair. */
  private syncCache: { key: string; state: SyncState } | null = null;
  /** Cached strategy overlays — keyed by the (run, rows-span, mode,
   *  period) tuple: the PnL curve data is mode-independent but the LDS
   *  anchor prices rebase with the mode, and the period markArea moves
   *  with the RiskPanel selection. */
  private extrasCache: { key: string; extras: StrategyExtras } | null = null;

  private get decisionSignals(): OhlcTradeSignal[] {
    const bt = this.props.backtest;
    if (!bt) return NO_TRADE_SIGNALS;
    const key = String(bt.seq_id ?? "none");
    if (this.signalsCache?.key !== key) {
      this.signalsCache = {
        key,
        signals: bt.decisions
          .filter((d) => !isLastDaySell(d))
          .map((d) => ({
            date: d.exec_date,
            action: d.side.toLowerCase(),
            signal_type: "singleton",
            signal_sub_type: `#${d.decision_no} ${d.side} ${d.qty}u @ ${d.fill_price}`,
            confidence: Math.abs(d.signal_value ?? 0),
          })),
      };
    }
    return this.signalsCache.signals;
  }

  private get selectedSignalDate(): string | null {
    const no = this.props.selectedDecisionNo;
    if (no == null) return null;
    return (
      this.props.backtest?.decisions.find((d) => d.decision_no === no)
        ?.exec_date ?? null
    );
  }

  /** The displayed run vs the fetched baseline rows: where the strategy
   *  data stops short of the market data (the sync gap). */
  private get sync(): SyncState {
    const bt = this.props.backtest;
    const rows = this.state.data?.rows ?? [];
    const lastStrategyDate =
      bt && bt.daily.length > 0 ? bt.daily[bt.daily.length - 1].trade_date : null;
    const lastMarketDate = rows.length > 0 ? rows[rows.length - 1].date : null;
    const key = `${bt?.seq_id ?? "none"}|${lastStrategyDate}|${lastMarketDate}|${rows.length}`;
    if (this.syncCache?.key === key) return this.syncCache.state;
    let state: SyncState;
    if (
      lastStrategyDate != null &&
      lastMarketDate != null &&
      lastStrategyDate < lastMarketDate
    ) {
      // First baseline row strictly after the strategy's last day — the
      // uncovered run starts there and runs to the market span's end.
      const firstIdx = rows.findIndex((r) => r.date > lastStrategyDate);
      const uncoveredDays = rows.length - firstIdx;
      state = {
        spans: [
          { start: rows[firstIdx].date, end: lastMarketDate, days: uncoveredDays },
        ],
        lastStrategyDate,
        lastMarketDate,
        uncoveredDays,
      };
    } else {
      state = { spans: [], lastStrategyDate, lastMarketDate, uncoveredDays: 0 };
    }
    this.syncCache = { key, state };
    return state;
  }

  /** The strategy overlays: Total P&L curve + axis, LAST DAY SELL
   *  marker, selected-period markArea, per-day tooltip block. Data
   *  arrays align to the SAME gap-broken category axis the base
   *  builder inserts (>4-day calendar breaks); marker anchors rebase
   *  with the active OHLC mode (rebasePriceArrays, the base's own
   *  convention). Only THEME-CONSTANT palette colors are used so a
   *  light/dark toggle never leaves stale colors here. */
  private strategyExtras(ohlcMode: OhlcMode): StrategyExtras {
    const bt = this.props.backtest;
    const period = this.props.selectedPeriod ?? null;
    const rows = this.state.data?.rows ?? [];
    const key =
      `${bt?.seq_id ?? "none"}|${ohlcMode}|${rows.length}|` +
      `${rows[rows.length - 1]?.date ?? ""}|` +
      `${period ? `${period.periodType}:${period.periodValue}:${period.isGain}` : "-"}`;
    if (this.extrasCache?.key === key) return this.extrasCache.extras;

    const dailyByDate = new Map(
      (bt ? bt.daily : []).map((d) => [d.trade_date, d]),
    );
    const decisionByDate = new Map(
      (bt ? bt.decisions : []).map((d) => [d.exec_date, d]),
    );

    // The SAME gap-broken category axis the base option builds — extra
    // series must align to it (NaN slots at >4-day calendar gaps).
    const broken = breakArraysAtGaps(rows.map((r) => r.date), [
      rows.map(() => null),
    ]);
    const modePrices = rebasePriceArrays(
      {
        open: rows.map((r) => r.open),
        high: rows.map((r) => r.high),
        low: rows.map((r) => r.low),
        close: rows.map((r) => r.close),
      },
      ohlcMode,
    ).rebased;

    // ---- Total P&L curve + axis (normalized money, own right axis —
    // a different scale from both the price axis and the amount axis).
    const hasPnl = !!bt && bt.daily.length > 0;
    const pnlByDate = new Map(
      (bt ? bt.daily : []).map((d) => [d.trade_date, d.total_pnl]),
    );
    const pnlData: Array<number | null> = broken.dates.map((d) => {
      const v = pnlByDate.get(d);
      return v != null && Number.isFinite(v) ? v : null;
    });

    // ---- LAST DAY SELL (the run's final-liquidation row) — purple
    // oversized sell triangle at the day's high (mode-rebased), the
    // original backtest chart's look.
    const ldsData: Array<{ value: [number, number | null] }> = [];
    if (bt) {
      for (const d of bt.decisions) {
        if (!isLastDaySell(d)) continue;
        const rowsIdx = rows.findIndex((r) => r.date === d.exec_date);
        if (rowsIdx < 0) continue;
        const slot = broken.dates.indexOf(d.exec_date);
        if (slot < 0) continue;
        ldsData.push({ value: [slot, modePrices.high[rowsIdx]] });
      }
    }

    // ---- Selected risk period shading (gain green / loss red) — the
    // RiskPanel bar-click range, labeled with the period value.
    let periodSeries: Record<string, unknown> | null = null;
    if (bt && period && rows.length > 0) {
      let start = -1;
      let end = -1;
      for (let i = 0; i < rows.length; i++) {
        if (dateToPeriodValue(rows[i].date, period.periodType) === period.periodValue) {
          if (start < 0) start = i;
          end = i;
        }
      }
      if (start >= 0) {
        const shade = period.isGain ? UP_COLOR : DOWN_COLOR;
        periodSeries = {
          name: "Period",
          type: "line",
          data: [],
          yAxisIndex: 0,
          silent: true,
          emphasis: { disabled: true },
          markArea: {
            silent: true,
            z: 0,
            itemStyle: { color: withAlphaHex(shade, 0.22) },
            label: {
              show: true,
              formatter: period.periodValue,
              color: shade,
              fontSize: 10,
              fontWeight: 700,
              position: "insideTopLeft",
            },
            data: [[
              { xAxis: rows[start].date },
              { xAxis: rows[end].date },
            ]],
          },
        };
      }
    }

    const extraYAxis: EChartsOption["yAxis"] = hasPnl
      ? [
          {
            type: "value",
            name: "Total P&L",
            position: "right",
            offset: 60,
            scale: true,
            axisLine: { lineStyle: { color: PE_COLOR } },
            axisLabel: { fontSize: 10 },
            splitLine: { show: false },
            nameTextStyle: { color: PE_COLOR, fontSize: 10 },
          },
        ]
      : null;

    const extraSeries: NonNullable<EChartsOption["series"]> = [
      ...(hasPnl
        ? [{
            name: "Total P&L",
            type: "line" as const,
            data: pnlData,
            yAxisIndex: -1,
            smooth: false,
            symbol: "none",
            lineStyle: { color: PE_COLOR, width: 1.5 },
            emphasis: { disabled: true },
            z: 6,
          }]
        : []),
      ...(ldsData.length > 0
        ? [{
            name: "LAST DAY SELL",
            type: "scatter" as const,
            data: ldsData,
            xAxisIndex: 0,
            yAxisIndex: 0,
            ...signalMarkerSpec("sell", {
              placement: "onPoint",
              scale: 1.5,
              color: LAST_DAY_SELL_COLOR,
            }),
            z: 30,
            emphasis: { disabled: true },
            label: {
              show: true,
              position: "top" as const,
              formatter: "LDS",
              fontSize: 10,
              fontWeight: 700,
              color: LAST_DAY_SELL_COLOR,
              distance: -4,
            },
          }]
        : []),
      ...(periodSeries ? [periodSeries as never] : []),
    ];

    // ---- Per-day tooltip block: the day's decision + the daily P&L
    // rows (the original chart's tooltip strategy half). Null on days
    // with neither — the formatter appends nothing.
    const tooltipExtraRows =
      bt && (bt.decisions.length > 0 || bt.daily.length > 0)
        ? (date: string): React.ReactNode => {
            const decision = decisionByDate.get(date);
            const daily = dailyByDate.get(date);
            if (!decision && !daily) return null;
            const lines: React.ReactNode[] = [];
            if (decision) {
              const color = decision.side === "BUY"
                ? UP_COLOR
                : isLastDaySell(decision)
                  ? LAST_DAY_SELL_COLOR
                  : DOWN_COLOR;
              lines.push(
                React.createElement(
                  tooltipComponents.Bold,
                  { key: "dec", style: { color } },
                  `${decision.side} #${decision.decision_no}`,
                ),
              );
              lines.push(`Fill: ${fmtNum(decision.fill_price, 4)}`);
              const reason = stripMixPrefix(decision.signal_reason);
              if (reason) {
                lines.push(`Signal: ${escapeTooltipText(reason)}`);
              }
            }
            if (daily) {
              const colored = (label: string, v: number, suffix = "") =>
                React.createElement(
                  React.Fragment,
                  { key: label },
                  `${label}: `,
                  React.createElement(
                    tooltipComponents.Bold,
                    { style: { color: v >= 0 ? UP_COLOR : DOWN_COLOR } },
                    `${v >= 0 ? "+" : ""}${fmtNum(v, 2)}${suffix}`,
                  ),
                );
              lines.push(colored("Unrealized", daily.unrealized_pnl));
              lines.push(colored("Total P&L", daily.total_pnl));
              lines.push(`Position: ${fmtNum(daily.position_value, 2)}`);
              lines.push(`Total Qty: ${fmtNum(daily.total_qty, 1)}`);
              lines.push(colored("Return", daily.return_rate * 100, "%/yr"));
              lines.push(`Hold: ${fmtNum(daily.normalized_mean_buy_period, 1)}d`);
              lines.push(`Sharpe (full): ${fmtNum(daily.sharpe_ratio, 2)}`);
              lines.push(`Sharpe (252d): ${fmtNum(daily.sharpe_ratio_252d, 2)}`);
              lines.push(`Sharpe (504d): ${fmtNum(daily.sharpe_ratio_504d, 2)}`);
            }
            return React.createElement(
              React.Fragment,
              null,
              lines.map((l, i) => React.createElement("div", { key: i }, l)),
            );
          }
        : null;

    const extras: StrategyExtras = { extraYAxis, extraSeries, tooltipExtraRows };
    this.extrasCache = { key, extras };
    return extras;
  }

  /** The inherited security-mode OhlcChart props + the strategy's own
   *  overlays. Added AFTER the super spread — the base's explicit props
   *  cannot clobber them. */
  protected ohlcChartProps(
    rows: CodeTrendRow[],
    ohlcMode: OhlcMode,
    height: number,
  ): OhlcChartProps {
    const sync = this.sync;
    const extras = this.strategyExtras(ohlcMode);
    return {
      ...super.ohlcChartProps(rows, ohlcMode, height),
      // The singleton plot replaces the valuation context with the
      // strategy overlays — suppress the PE twin axis even when the
      // baseline rows carry PE (user-directed 2026-11-18).
      showPe: false,
      tradeSignals: this.decisionSignals,
      selectedSignalDate: this.selectedSignalDate,
      highlightSpans: sync.spans.length > 0 ? sync.spans : NO_HIGHLIGHT_SPANS,
      focusDateRequest: this.props.focusDateRequest ?? null,
      onVisibleRangeChange: this.props.onVisibleRangeChange,
      extraYAxis: extras.extraYAxis,
      extraSeries: extras.extraSeries,
      tooltipExtraRows: extras.tooltipExtraRows,
    };
  }

  /** The inherited subtitle (bars + date span + extras) + the run/sync
   *  line: which run is displayed, its Total Return, where its rows
   *  end, and (warning colored) how far the market data runs ahead. */
  protected renderSubtitle(): React.ReactNode {
    const bt = this.props.backtest;
    const { spans, lastStrategyDate, lastMarketDate, uncoveredDays } = this.sync;
    const lagging = spans.length > 0;
    const retPct = bt?.summary.total_return_pct ?? null;
    return (
      <>
        {super.renderSubtitle()}
        <Typography
          variant="caption"
          component="div"
          sx={{ display: "block", lineHeight: 1.4, mt: -0.25 }}
        >
          {bt ? (
            <>
              strategy run seq {bt.seq_no ?? "—"} ·{" "}
              <Box
                component="span"
                sx={{
                  fontWeight: 600,
                  color: (retPct ?? 0) >= 0 ? "success.main" : "error.main",
                }}
              >
                Total Return {retPct != null && retPct >= 0 ? "+" : ""}
                {retPct != null ? `${fmtNum(retPct, 1)}%` : "—"}
              </Box>
              {" · "}
              {bt.summary.n_buys}B / {bt.summary.n_sells}S · strategy rows
              through{" "}
              <Box component="span" sx={{ fontWeight: 600 }}>
                {lastStrategyDate ?? "—"}
              </Box>
              {lagging && (
                <>
                  {" · "}
                  <Box component="span" sx={{ color: "warning.main" }}>
                    market through {lastMarketDate} (+{uncoveredDays} trading{" "}
                    {uncoveredDays === 1 ? "day" : "days"} not covered — run
                    again to extend)
                  </Box>
                </>
              )}
            </>
          ) : (
            "No strategy run displayed — showing the shared code trend."
          )}
        </Typography>
      </>
    );
  }
}
