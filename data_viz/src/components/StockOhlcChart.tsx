/**
 * StockOhlcChart — shared daily OHLC chart for a single stock.
 *
 * Single source of truth for the stock daily OHLC plot. Used by:
 *   • StockPanel (Stock Baseline page) — wrapped in a ChartCard with a
 *     date-range slider and return badges.
 *   • StockOhlcExpansionChart (composition pie expansion) — wrapped in a Card
 *     with a close button; data fetched on demand.
 *
 * Renders OHLC bars (shared `ohlcSeries`) + MA5/MA20/MA60/MA120 (computed
 * client-side from close — the stock baseline view does not carry precomputed
 * MA columns) + PE ratio on a twin axis (when available, with estimated PE
 * drawn as a faint series). Falls back to a close line when OHLC components
 * are sparse. OHLC + MAs are rebased to % change from the first valid close
 * in "percentage" mode (the default).
 *
 * Margin + liquidity overlays (mirror EtfMarginPanel):
 *   • RZ (融资 cash borrow) green fill UP from middle on a hidden twin axis.
 *   • RQ (融券 sec borrow) red fill DOWN from middle on the same hidden axis.
 *   • Trading-turnover bars (成交金额, 亿元) on a visible right axis, colored
 *     by price-up/down.
 *
 * Dividend events (利润分配/分红) from stats.stock_dividends are overlaid as
 * gold diamond markPoints on the ex-dividend date. The dividend amount is
 * shown in the axis tooltip when the user hovers the event day.
 */
import React, { useMemo } from "react";
import { renderReactElement, tooltipComponents } from "@/lib/react-tooltip-renderer";
import EChart from "@/components/EChart";
import { useStore } from "@/store/filters";
import { breakArraysAtGaps, fmtNum, fmtMil, safeMa } from "@/lib/series";
import {
  ohlcSeries,
  rebasePriceArrays,
  formatPriceValue,
  type OhlcMode,
} from "@/lib/ohlc";
import { computeMarginScores } from "@/lib/margin-score";
import {
  MA5_COLOR,
  MA20_COLOR,
  MA60_COLOR,
  MA120_COLOR,
  MUTED_PALETTE,
  PE_COLOR,
  DIVIDEND_COLOR,
  UP_COLOR,
  DOWN_COLOR,
  axisColors,
  commonLegend,
  commonGrid,
  commonDataZoom,
} from "@/theme/chart-palette";
import type { StockBaselineRow, StockDividend } from "@shared/types";
import type { EChartsOption } from "echarts";

interface Props {
  /** Daily OHLC + PE rows for one stock (already windowed by the caller). */
  rows: StockBaselineRow[];
  /** OHLC display mode — "percentage" rebases OHLC + MAs to % change from the
   *  first valid close; "absolute" shows raw prices. */
  ohlcMode: OhlcMode;
  /** Chart height in px. Defaults to 250 (matches StockPanel). */
  height?: number;
  /** Dividend events for this stock (all dates — not windowed). When
   *  provided, gold diamond markers are drawn on ex-dividend dates that fall
   *  inside the visible rows. Defaults to [] (no markers). */
  dividends?: StockDividend[];
  /** Initial dataZoom start % (0–100). When provided, an in-chart dataZoom
   *  (inside + slider) is rendered and `rows` should be the FULL history (the
   *  dataZoom owns windowing). When omitted, no dataZoom is rendered and the
   *  caller owns windowing (e.g. StockOhlcExpansionChart). */
  dataZoomStart?: number;
  /** Initial dataZoom end % (0–100). Defaults to 100 when dataZoomStart is set. */
  dataZoomEnd?: number;
  /** Optional callback fired when the user clicks any date on the chart.
   *  Used by the PE & Dividend analysis page to highlight the matching
   *  month-end row in the stats table. */
  onDateClick?: (date: string) => void;
}

export default function StockOhlcChart({ rows, ohlcMode, height = 250, dividends = [], dataZoomStart, dataZoomEnd, onDateClick }: Props) {
  const themeMode = useStore((s) => s.themeMode);

  // Chart x-axis dates (with gap-break inserts) — used by the onCanvasClick
  // handler to map a click index back to a date string. Mirrors the broken
  // dates computed inside the option useMemo.
  const chartDates = useMemo(
    () => breakArraysAtGaps(rows.map((r) => r.date), [rows.map(() => null)]).dates,
    [rows],
  );

  const option = useMemo<EChartsOption>(() => {
    const c = axisColors(themeMode);
    const enableDataZoom = dataZoomStart !== undefined;
    const dates = rows.map((r) => r.date);
    const open = rows.map((r) => r.open);
    const high = rows.map((r) => r.high);
    const low = rows.map((r) => r.low);
    const close = rows.map((r) => r.close);
    const pe = rows.map((r) => r.pe);
    const isPeEstimatedNum = rows.map((r) => (r.is_pe_estimated ? 1 : 0));
    // Liquidity + margin data (from stock_liquidity_margin via v_stock_baseline)
    const tradingAmount = rows.map((r) => r.trading_amount);
    // Compute MA client-side — the stock baseline view does not carry
    // precomputed MA columns (only OHLC + pct_change + PE).
    const ma5 = safeMa(close, 5);
    const ma20 = safeMa(close, 20);
    const ma60 = safeMa(close, 60);
    const ma120 = safeMa(close, 120);

    // Detect whether OHLC is available — when most rows have all four
    // components, render an OHLC chart; otherwise fall back to a close line.
    const hasOhlc = (() => {
      if (rows.length === 0) return false;
      const ohlcCount = rows.filter(
        (r) => r.open != null && r.high != null && r.low != null && r.close != null,
      ).length;
      return ohlcCount > 0 && ohlcCount >= rows.length * 0.5;
    })();

    // PE is rendered only when at least one non-null, non-zero sample exists
    // (0 is treated as a placeholder, not a real PE).
    const hasPe = rows.some((r) => r.pe != null && r.pe !== 0);

    // Margin availability — any row with non-null, non-zero rz_balance.
    const hasMargin = rows.some(
      (r) => r.rz_balance != null && r.rz_balance !== 0,
    );

    // Rebase price-derived arrays (OHLC + MAs) to % change in percentage mode.
    // pe and isPeEstimatedNum are NOT price-derived — kept in absolute units.
    const { rebased } = rebasePriceArrays(
      { open, high, low, close, ma5, ma20, ma60, ma120 },
      ohlcMode,
    );

    const broken = breakArraysAtGaps(dates, [
      rebased.open, rebased.high, rebased.low, rebased.close,
      rebased.ma5, rebased.ma20, rebased.ma60, rebased.ma120,
      pe, isPeEstimatedNum,
    ]);

    // Data order: [open, close, low, high] (low before high — matches the
    // shared ohlcRenderItem destructuring `const [o, cl, l, h] = value`).
    const candleData: Array<Array<number | null>> = broken.dates.map((_, i) => [
      broken.arrays[0][i],
      broken.arrays[3][i],
      broken.arrays[2][i],
      broken.arrays[1][i],
    ]);

    // --- Margin scores (RZ up, RQ down) ---------------------------------
    const marginRows = rows.map((r) => ({
      date: r.date,
      rz_balance: r.rz_balance,
      rq_balance_amt: r.rq_balance_amt,
    }));
    const marginScores = computeMarginScores(marginRows);
    const rzScore = marginScores.map((m) => m.rz_score);
    const rqScore = marginScores.map((m) => m.rq_score);

    // Date → raw balance (yuan) lookups for tooltip display. The plotted
    // series values are shifted/clipped scores (not raw yuan), so the tooltip
    // must re-resolve the actual balance by date to show a meaningful figure.
    const rzByDate = new Map<string, number | null>(
      rows.map((r) => [r.date, r.rz_balance]),
    );
    const rqByDate = new Map<string, number | null>(
      rows.map((r) => [r.date, r.rq_balance_amt]),
    );
    // Date → EPS (yuan/share) lookup for tooltip display. EPS is a per-row
    // attribute (close / pe), not a plotted series, so the tooltip resolves it
    // by date — mirroring the rz/rq balance lookups above.
    const epsByDate = new Map<string, number | null>(
      rows.map((r) => [r.date, r.eps]),
    );

    // Dynamic axis limits for hidden margin axis — matches Python's
    // ax_rzrq.set_ylim(-max_of * 1.15, max_of * 1.15)
    const marginVals = [...rzScore, ...rqScore].filter(
      (v): v is number => v != null && Number.isFinite(v),
    );
    const maxAbs = marginVals.length > 0 ? Math.max(...marginVals.map(Math.abs)) : 0;
    const marginAxisRange = Math.max(1e-6, maxAbs) * 1.15;

    // --- Trading-turnover bars (亿元) -----------------------------------
    // trading_amount is stored in yuan — convert to 亿元 (/1e8) for display.
    // Bar color: green when close >= open (price-up), red otherwise.
    const amtData = tradingAmount.map((v, i) => {
      const o = open[i];
      const cl = close[i];
      const up = o != null && cl != null && cl >= o;
      return {
        value: v / 1e8,
        itemStyle: { color: up ? UP_COLOR : DOWN_COLOR, opacity: 0.4 },
      };
    });

    // --- Dividend event markers ------------------------------------------
    // Build a date → rebased-close lookup so we can place each marker at the
    // close price of the ex-dividend day. Markers ride on the MA20 line series
    // (the custom OHLC renderItem cannot host markPoint). Only dividends whose
    // ex_dividend_date falls inside the visible window are drawn.
    const dateToCloseIdx = new Map<string, number>();
    broken.dates.forEach((d, i) => dateToCloseIdx.set(d, i));
    const dividendMarkPointData: Array<{
      name: string;
      coord: [string, number];
      itemStyle: { color: string };
      symbol: string;
      symbolSize: number;
    }> = [];
    const dividendByDate = new Map<string, StockDividend>();
    for (const d of dividends) {
      const idx = dateToCloseIdx.get(d.ex_dividend_date);
      if (idx === undefined) continue; // ex-div date not in visible window
      const y = broken.arrays[3][idx]; // rebased close (arrays[3] = close)
      if (y == null || !Number.isFinite(y)) continue;
      dividendMarkPointData.push({
        name: "Dividend",
        coord: [d.ex_dividend_date, y],
        itemStyle: { color: DIVIDEND_COLOR },
        symbol: "diamond",
        symbolSize: 11,
      });
      dividendByDate.set(d.ex_dividend_date, d);
    }
    const dividendMarkPoint = dividendMarkPointData.length
      ? { data: dividendMarkPointData, label: { show: false } }
      : undefined;

    // --- Y axes ----------------------------------------------------------
    // Axis layout (index → role):
    //   0  left     price (% or yuan)
    //   1  hidden   margin scores (RZ ≥0 up, RQ ≤0 down) — only when hasMargin
    //   2  right    trading amount (亿元) — always present (0 when no data)
    //   3  right    PE (offset) — only when hasPe
    //
    // When margin is absent the hidden axis is omitted so indices shift down
    // by one. The series yAxisIndex values below account for this.
    const yAxis: EChartsOption["yAxis"] = [
      {
        type: "value",
        scale: true,
        name: ohlcMode === "percentage" ? "%" : "Price",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: {
          color: c.textColor,
          fontSize: 9,
          formatter: (v: number) => formatPriceValue(v, ohlcMode),
        },
        splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
      },
    ];
    // Hidden margin axis (index 1 when present)
    let marginAxisIdx = -1;
    if (hasMargin) {
      marginAxisIdx = (yAxis as Array<unknown>).length;
      (yAxis as Array<unknown>).push({
        type: "value",
        scale: true,
        show: false,
        min: -marginAxisRange,
        max: marginAxisRange,
      });
    }
    // Trading-amount axis (right, visible). Index = 1 when no margin, else 2.
    const amtAxisIdx = (yAxis as Array<unknown>).length;
    (yAxis as Array<unknown>).push({
      type: "value",
      scale: true,
      name: "Amt (亿)",
      nameTextStyle: { color: c.textColor, fontSize: 9 },
      axisLine: { lineStyle: { color: c.axisLineColor } },
      axisLabel: {
        color: c.textColor,
        fontSize: 9,
        formatter: (v: number) => fmtNum(v) + " 亿",
      },
      splitLine: { show: false },
    });
    // PE axis (right, offset). Index follows amount axis.
    let peAxisIdx = -1;
    if (hasPe) {
      peAxisIdx = (yAxis as Array<unknown>).length;
      (yAxis as Array<unknown>).push({
        type: "value",
        scale: true,
        name: "PE",
        nameTextStyle: { color: PE_COLOR, fontSize: 9 },
        axisLine: { lineStyle: { color: PE_COLOR } },
        axisLabel: { color: PE_COLOR, fontSize: 9, formatter: (v: number) => fmtNum(v) },
        splitLine: { show: false },
        offset: 40,
      });
    }

    const series: EChartsOption["series"] = [
      ...(hasOhlc
        ? [ohlcSeries(candleData, { name: "OHLC", yAxisIndex: 0, z: 5 })]
        : [{
            type: "line" as const,
            name: "Close",
            yAxisIndex: 0,
            data: broken.arrays[3],
            smooth: false,
            symbol: "none",
            lineStyle: { color: MUTED_PALETTE[0], width: 1.3 },
            z: 5,
          }]),
      {
        type: "line",
        name: "MA5",
        yAxisIndex: 0,
        data: broken.arrays[4],
        smooth: false,
        symbol: "none",
        lineStyle: { color: MA5_COLOR, width: 0.8 },
        z: 4,
      },
      // MA20 carries the dividend markPoint — the custom OHLC series cannot
      // host markPoint, so a standard line series must carry it. MA20 is a
      // good visual anchor (always present, sits near the price).
      {
        type: "line",
        name: "MA20",
        yAxisIndex: 0,
        data: broken.arrays[5],
        smooth: false,
        symbol: "none",
        lineStyle: { color: MA20_COLOR, width: 0.9 },
        z: 4,
        markPoint: dividendMarkPoint,
      },
      {
        type: "line",
        name: "MA60",
        yAxisIndex: 0,
        data: broken.arrays[6],
        smooth: false,
        symbol: "none",
        lineStyle: { color: MA60_COLOR, width: 0.8, type: "dashed" },
        z: 4,
      },
      {
        type: "line",
        name: "MA120",
        yAxisIndex: 0,
        data: broken.arrays[7],
        smooth: false,
        symbol: "none",
        lineStyle: { color: MA120_COLOR, width: 0.7, type: "dotted" },
        z: 4,
      },
    ];

    // --- Margin fills (RZ up green, RQ down red) ------------------------
    if (hasMargin && marginAxisIdx >= 0) {
      const brokenM = breakArraysAtGaps(dates, [rzScore, rqScore]);
      series.push({
        type: "line",
        name: "cash borrow balance",
        yAxisIndex: marginAxisIdx,
        data: brokenM.arrays[0],
        smooth: false,
        symbol: "none",
        lineStyle: { color: UP_COLOR, width: 0.6, opacity: 0.7 },
        areaStyle: { color: UP_COLOR, opacity: 0.36 },
        z: 3,
      });
      series.push({
        type: "line",
        name: "sec borrow balance",
        yAxisIndex: marginAxisIdx,
        data: brokenM.arrays[1],
        smooth: false,
        symbol: "none",
        lineStyle: { color: DOWN_COLOR, width: 0.6, opacity: 0.7 },
        areaStyle: { color: DOWN_COLOR, opacity: 0.36 },
        z: 3,
      });
    }

    // --- Trading-turnover bars (visible right axis) ---------------------
    series.push({
      type: "bar",
      name: "Amount",
      yAxisIndex: amtAxisIdx,
      data: amtData,
      barWidth: "90%",
      z: 1,
    });

    if (hasPe && peAxisIdx >= 0) {
      // Separate PE into actual (solid) and estimated (faint) series. Null or
      // 0 values are suppressed so missing/placeholder PE samples do not
      // render on the chart.
      const peActual = broken.arrays[8].map((val, i) =>
        broken.arrays[9][i] === 1 || val == null || val === 0 ? null : val
      );
      const peEstimated = broken.arrays[8].map((val, i) =>
        broken.arrays[9][i] === 1 && val != null && val !== 0 ? val : null
      );
      series.push({
        type: "line",
        name: "PE",
        yAxisIndex: peAxisIdx,
        data: peActual,
        smooth: false,
        symbol: "none",
        lineStyle: { color: PE_COLOR, width: 1.1, opacity: 0.85 },
        z: 6,
      });
      series.push({
        type: "line",
        name: "PE (est)",
        yAxisIndex: peAxisIdx,
        data: peEstimated,
        smooth: false,
        symbol: "none",
        connectNulls: false,
        lineStyle: { color: PE_COLOR, width: 1.1, opacity: 0.4 },
        z: 6,
      });
    }

    return {
      backgroundColor: "transparent",
      animation: false,
      grid: commonGrid({ left: 50, right: hasPe ? 60 : 50, bottom: enableDataZoom ? 50 : 28 }),
      dataZoom: enableDataZoom ? commonDataZoom({}, dataZoomStart, dataZoomEnd ?? 100) : undefined,
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "cross", snap: true },
        backgroundColor: c.tooltipBg,
        borderColor: c.splitLineColor,
        textStyle: { color: c.textColor, fontSize: 11 },
        formatter: (params: unknown) => {
          const arr = (Array.isArray(params) ? params : [params]) as Array<{
            axisValue?: string;
            marker?: string;
            seriesName?: string;
            value?: Array<number | null> | number;
          }>;
          if (arr.length === 0) return "";
          const dateStr = (arr[0].axisValue as string) || "";

          const makeHeader = (text: string) =>
            React.createElement(tooltipComponents.Header, null, text);
          const makeRow = (children: React.ReactNode, style?: React.CSSProperties) =>
            React.createElement(tooltipComponents.Row, { style }, children);
          const makeTextRow = (marker: string, name: string, text: React.ReactNode) =>
            makeRow([marker, " ", name, ": ", text]);
          const makeBoldRow = (marker: string, name: string, vstr: string) =>
            makeTextRow(marker, name, React.createElement(tooltipComponents.Bold, null, vstr));
          const makeOhlcRow = (marker: string, name: string, o: number | null, h: number | null, l: number | null, c: number | null) =>
            makeTextRow(marker, name,
              `O=${formatPriceValue(o, ohlcMode)} H=${formatPriceValue(h, ohlcMode)} L=${formatPriceValue(l, ohlcMode)} C=${formatPriceValue(c, ohlcMode)}`);

          const children: React.ReactNode[] = [];
          children.push(makeHeader(dateStr));

          const div = dividendByDate.get(dateStr);
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

          const isPriceSeries = (name: string) =>
            name === "OHLC" || name === "Close" || name.startsWith("MA");
          for (const p of arr) {
            if (p.value == null) continue;
            const name = p.seriesName ?? "";
            if (Array.isArray(p.value)) {
              const [o, cl, l, h] = p.value;
              if (o == null && cl == null && l == null && h == null) continue;
              children.push(makeOhlcRow(p.marker ?? "", name, o, h, l, cl));
            } else {
              const v = p.value as number;
              if (!Number.isFinite(v)) continue;
              let vstr: string;
              if (name === "Amount") {
                vstr = fmtNum(v) + " 亿";
              } else if (name === "cash borrow balance") {
                vstr = fmtMil(rzByDate.get(dateStr) ?? null);
              } else if (name === "sec borrow balance") {
                vstr = fmtMil(rqByDate.get(dateStr) ?? null);
              } else if (name.includes("remained")) {
                vstr = fmtMil(v);
              } else if (name.includes("RZ") || name.includes("RQ")) {
                vstr = fmtNum(v);
              } else if (isPriceSeries(name)) {
                vstr = formatPriceValue(v, ohlcMode);
              } else if (name === "PE" || name === "PE (est)") {
                vstr = fmtNum(v, 2);
              } else {
                vstr = formatPriceValue(v, ohlcMode);
              }
              children.push(makeBoldRow(p.marker ?? "", name, vstr));
            }
          }

          const epsVal = epsByDate.get(dateStr);
          if (epsVal != null && Number.isFinite(epsVal)) {
            children.push(makeRow([
              React.createElement("span", { style: { color: PE_COLOR } }, "●"),
              " ",
              "EPS: ",
              React.createElement(tooltipComponents.Bold, null, `¥${fmtNum(epsVal, 4)}`),
            ]));
          }

          return renderReactElement(React.createElement(React.Fragment, null, children));
        },
      },
      legend: commonLegend(themeMode, { type: "scroll" }),
      xAxis: {
        type: "category",
        data: broken.dates,
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: {
          color: c.textColor,
          fontSize: 8,
          formatter: (v: string) => v.slice(0, 7),
          interval: Math.max(1, Math.floor(broken.dates.length / 8)),
        },
        splitLine: { show: false },
      },
      yAxis,
      series,
    };
  }, [rows, themeMode, ohlcMode, dividends, dataZoomStart, dataZoomEnd]);

  return (
    <EChart
      option={option}
      height={height}
      onCanvasClick={onDateClick ? (idx) => {
        const date = chartDates[idx];
        if (date) onDateClick(date);
      } : undefined}
    />
  );
}
