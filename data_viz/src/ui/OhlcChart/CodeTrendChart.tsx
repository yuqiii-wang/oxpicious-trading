/**
 * CodeTrendChart — the globally shared per-security daily price-trend chart.
 *
 * Given (sec_type, code), fetches the code's daily baseline rows from the
 * sec_type's endpoint and renders the shared `OhlcChart` (OHLC candles +
 * MA5/20/60/120 + PE / margin / turnover overlays where the data has them)
 * inside a Card with an OhlcModeToggle and a date-range slider.
 *
 * Configuration (props) — every part of the default setup is switchable:
 *   • sources            — per-sec_type data-source override (fetch + map)
 *   • chartOptions       — pass-through OhlcChart options (dividends,
 *                          dataZoomStart/End, onDateClick, …) for full
 *                          chart-option compatibility
 *   • variant            — "card" (default: Card chrome + header) | "bare"
 *                          (chart + slider only, for embedding in a parent
 *                          that already provides the card)
 *   • height / defaultOhlcMode / showModeToggle / showRangeSlider
 *   • title / subtitleExtra / headerAction — header customization
 *   • onLoaded / onOhlcModeChange — callbacks
 *
 * Inheritance — for features beyond what props cover, extend the class and
 * override the protected hooks instead of forking the file:
 *   class IntradayTrendChart extends CodeTrendChart {
 *     protected fetchSource(code: string) { return fetchIntradayTrend(code); }
 *     protected renderChart(rows, ohlcMode, height) { …custom EChart… }
 *     protected renderHeaderActions() { return <MyToggle …/>; }
 *   }
 * The base class owns the fetch lifecycle, range-slider windowing, loading /
 * error / empty states and the card chrome; subclasses only swap the pieces
 * they need.
 *
 * Split (this directory) — this file is the class shell + fetch lifecycle:
 *   • codeTrendTypes.ts    props / state / data-shape types
 *   • codeTrendSources.ts  default per-sec_type fetchers + row mappers
 *   • codeTrendHeader.tsx  title / subtitle / header-actions renderers
 *   • codeTrendAiAsk.tsx   the AI Ask "?" button of the card header
 */
import React from "react";
import {
  Alert,
  Box,
  Card,
  CardContent,
  CardHeader,
  CircularProgress,
} from "@mui/material";
import TimeSlider from "@/shared/components/time-slider/TimeSlider";
import OhlcChart from "./OhlcChart";
import type { ECharts } from "echarts";
import { fetchMarketRegimeSpans, fetchSecBoardMap } from "@/lib/api-client";
import { shadedRegimeSpans } from "@/shared/charts/regimeBands";
import type { OhlcMode } from "@/lib/ohlc";
import type { MarketRegimeSpans, StockDividend } from "@shared/types";
import type {
  CodeTrendChartProps,
  CodeTrendChartState,
  CodeTrendData,
  CodeTrendRow,
  CodeTrendSource,
} from "./codeTrendTypes";
import { CODE_TREND_SOURCES } from "./codeTrendSources";
import {
  CodeTrendHeaderActions,
  CodeTrendSubtitle,
  CodeTrendTitle,
} from "./codeTrendHeader";
import { CodeTrendAiAsk } from "./codeTrendAiAsk";

/** Stable EMPTY identities for the chart props' fallbacks. A fresh []
 * per render would dirty OhlcChart's option memo on EVERY parent
 * re-render (spinner flips, chip mounts, row selection...), forcing a
 * full ~1 s notMerge rebuild of the 1700-candle chart each time
 * (measured 2026-09) — the props must stay identity-stable when their
 * CONTENT is unchanged. */
const NO_SPANS: Partial<MarketRegimeSpans> = {};
const NO_DIVIDENDS: StockDividend[] = [];

export default class CodeTrendChart extends React.Component<
  CodeTrendChartProps,
  CodeTrendChartState
> {
  /** Monotonic token — only the newest fetch may settle into state. */
  private loadSeq = 0;
  /** Separate token for the board-tag fetch (it must not be invalidated
   *  by the shared loadSeq bumps from the regimes toggle). */
  private boardSeq = 0;
  /** Live inner-chart instance — captured via the OhlcChart
   *  onChartReady passthrough for the AI Ask screenshot. */
  private chartInstance: ECharts | null = null;

  state: CodeTrendChartState = {
    data: null,
    loading: false,
    error: null,
    ohlcMode: "percentage",
    range: [0, 0],
    showRegimes: false,
    regimeSpans: null,
    regimesLoading: false,
    boardTags: [],
  };

  componentDidMount() {
    this.load();
  }

  componentDidUpdate(prev: CodeTrendChartProps) {
    if (prev.secType !== this.props.secType || prev.code !== this.props.code) {
      this.setState({
        ohlcMode: this.props.defaultOhlcMode ?? "percentage",
        showRegimes: false,
        regimeSpans: null,
        regimesLoading: false,
      });
      this.load();
    }
  }

  // ---- Overridable hooks (inheritance extension points) -------------------

  /** Resolve the effective source for the active sec_type. */
  protected getSource(): CodeTrendSource {
    const { secType, sources } = this.props;
    return sources?.[secType] ?? CODE_TREND_SOURCES[secType];
  }

  /** Fetch + map the active code's daily rows. Override for custom data. */
  protected fetchSource(code: string): Promise<CodeTrendData> {
    return this.getSource().fetch(code);
  }

  /** The inner chart. Override to render a custom ECharts option. */
  protected renderChart(
    rows: CodeTrendRow[],
    ohlcMode: OhlcMode,
    height: number,
  ): React.ReactNode {
    const { chartOptions } = this.props;
    // Default windowing: the in-chart ECharts dataZoom slider (shared
    // commonDataZoom — same style as the MA-Spread chart) over the FULL
    // history. Only suppressed when the caller opted into the MUI range
    // slider below the chart (which owns windowing via this.state.range).
    const dataZoom =
      (this.props.showRangeSlider ?? false) || chartOptions?.dataZoomStart !== undefined
        ? {}
        : { dataZoomStart: 0, dataZoomEnd: 100 };
    return (
      <OhlcChart
        rows={rows}
        ohlcMode={ohlcMode}
        height={height}
        {...chartOptions}
        {...dataZoom}
        dividends={chartOptions?.dividends ?? this.state.data?.dividends ?? NO_DIVIDENDS}
        regimeSpans={
          this.state.showRegimes
            ? shadedRegimeSpans(this.state.regimeSpans)
            : NO_SPANS
        }
        onChartReady={(c) => {
          this.chartInstance = c;
          chartOptions?.onChartReady?.(c);
        }}
      />
    );
  }

  /** Header title (card variant). */
  protected renderTitle(): React.ReactNode {
    return (
      <CodeTrendTitle
        code={this.props.code}
        name={this.props.name}
        secType={this.props.secType}
        title={this.props.title}
        data={this.state.data}
        boardTags={this.state.boardTags}
      />
    );
  }

  /** Header subtitle (card variant) — bars + date span + extras. */
  protected renderSubtitle(): React.ReactNode {
    return (
      <CodeTrendSubtitle
        subtitleExtra={this.props.subtitleExtra}
        data={this.state.data}
      />
    );
  }

  /** Header actions (card variant) — mode toggle + Regimes toggle +
   *  prop extras (see codeTrendHeader.tsx). */
  protected renderHeaderActions(): React.ReactNode {
    return (
      <CodeTrendHeaderActions
        showModeToggle={this.props.showModeToggle}
        headerAction={this.props.headerAction}
        ohlcMode={this.state.ohlcMode}
        onModeChange={this.handleModeChange}
        showRegimes={this.state.showRegimes}
        regimesLoading={this.state.regimesLoading}
        onRegimesToggle={this.handleRegimesToggle}
      />
    );
  }

  protected renderLoading(): React.ReactNode {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
        <CircularProgress size={24} />
      </Box>
    );
  }

  protected renderError(message: string): React.ReactNode {
    return <Alert severity="error" sx={{ mb: 1 }}>{message}</Alert>;
  }

  protected renderEmpty(): React.ReactNode {
    return (
      <Alert severity="info" sx={{ py: 0.5 }}>
        No daily data available for {this.props.code}.
      </Alert>
    );
  }

  // ---- Lifecycle -----------------------------------------------------------

  private load() {
    const { code, secType, onLoaded } = this.props;
    if (!code) return;
    const seq = ++this.loadSeq;
    const boardSeq = ++this.boardSeq;
    this.setState({
      loading: true, error: null, data: null, range: [0, 0], boardTags: [],
    });
    // Board tag — fetched in parallel with the trend rows, guarded by its
    // own token. A failure (endpoint missing / code without board data)
    // just leaves the tag absent; it must never block or fail the chart.
    fetchSecBoardMap(code, secType)
      .then((d) => {
        if (boardSeq !== this.boardSeq) return;
        this.setState({ boardTags: d.boards });
      })
      .catch(() => {
        if (boardSeq !== this.boardSeq) return;
        this.setState({ boardTags: [] });
      });
    this.fetchSource(code)
      .then((data) => {
        if (seq !== this.loadSeq) return;
        this.setState({ data, loading: false, range: [0, data.rows.length - 1] });
        onLoaded?.(data);
      })
      .catch((e: Error) => {
        if (seq !== this.loadSeq) return;
        this.setState({ loading: false, error: e.message });
      });
  }

  private handleModeChange = (mode: OhlcMode) => {
    this.setState({ ohlcMode: mode });
    this.props.onOhlcModeChange?.(mode);
  };

  /** Toggle the market-regime shading; the first enable fetches the
   *  code's spans once and caches them (per code — reset on code
   *  change). */
  private handleRegimesToggle = () => {
    const showRegimes = !this.state.showRegimes;
    this.setState({ showRegimes });
    if (showRegimes && this.state.regimeSpans == null && !this.state.regimesLoading) {
      const seq = ++this.loadSeq;
      this.setState({ regimesLoading: true });
      fetchMarketRegimeSpans(this.props.code, this.props.secType)
        .then((d) => {
          if (seq !== this.loadSeq) return;
          this.setState({ regimeSpans: d.spans, regimesLoading: false });
        })
        .catch(() => {
          if (seq !== this.loadSeq) return;
          // No regime data (endpoint error / never built) — toggle stays
          // on with empty shading; clearing regimeSpans refetches on
          // retry.
          this.setState({ regimeSpans: {}, regimesLoading: false });
        });
    }
  };

  private handleRangeChange = (range: [number, number]) => {
    this.setState({ range });
  };

  // ---- Render ---------------------------------------------------------------

  render() {
    const { height = 300, variant = "card", showRangeSlider } = this.props;
    const { data, loading, error, ohlcMode, range } = this.state;

    // MUI range slider: opt-in only (default windowing is the in-chart
    // ECharts dataZoom — the chart then needs the FULL history, so rows
    // must stay unwindowed here).
    const sliderOn = showRangeSlider ?? false;

    const allRows = data?.rows ?? [];
    const maxIdx = allRows.length - 1;
    // Filter rows to the selected date window (full history otherwise).
    const windowedRows = sliderOn ? allRows.slice(range[0], range[1] + 1) : allRows;
    // Without the external slider the chart owns the full card height (its
    // in-chart dataZoom slider occupies the reserved bottom space).
    const chartHeight = sliderOn ? height - 60 : height;

    const slider = sliderOn && maxIdx > 0 && (
      <TimeSlider
        dates={allRows.map((r) => r.date)}
        value={range}
        onChange={this.handleRangeChange}
        sx={{ mt: 0.25 }}
      />
    );

    // AI Ask — the "?" beside the title (codeTrendAiAsk.tsx).
    const { code, name, secType } = this.props;
    const displayName = name || data?.name || "—";
    const aiAskButton = (
      <CodeTrendAiAsk
        code={code}
        displayName={displayName}
        secType={secType}
        allRows={allRows}
        windowedRows={windowedRows}
        ohlcMode={ohlcMode}
        showRegimes={this.state.showRegimes}
        sliderOn={sliderOn}
        aiSearchKeywords={this.props.aiSearchKeywords}
        getInstance={() => this.chartInstance}
      />
    );

    const body = (
      <Box sx={{ width: "100%" }}>
        {loading && this.renderLoading()}
        {error && this.renderError(error)}
        {!loading && !error && data && allRows.length === 0 && this.renderEmpty()}
        {!loading && !error && data && allRows.length > 0 && (
          <>
            {this.renderChart(windowedRows, ohlcMode, chartHeight)}
            {slider}
          </>
        )}
      </Box>
    );

    if (variant === "bare") return body;

    return (
      <Card>
        <CardHeader
          title={(
            <>
              {this.renderTitle()}
              {aiAskButton}
            </>
          )}
          subheader={this.renderSubtitle()}
          action={this.renderHeaderActions()}
          sx={{ pb: 0.5, "& .MuiCardHeader-content": { overflow: "hidden" } }}
        />
        <CardContent sx={{ pt: 0.5, pb: 1.5, height }}>{body}</CardContent>
      </Card>
    );
  }
}
