/**
 * CodeTrendChart — the globally shared per-security daily price-trend chart.
 *
 * Given (sec_type, code), fetches the code's daily baseline rows from the
 * sec_type's endpoint and renders the shared `StockOhlcChart` (OHLC candles +
 * MA5/20/60/120 + PE / margin / turnover overlays where the data has them)
 * inside a Card with an OhlcModeToggle and a date-range slider.
 *
 * Endpoints per sec_type (each supports an exact-code filter):
 *   stock → GET /api/stock-baseline?code=…        (fetchStockBaseline —
 *           carries PE + dividends, passed through untouched)
 *   etf   → GET /api/etf-margin/combined?code=…   (fetchEtfMarginCombined —
 *           adjusted OHLC preferred, margin balances overlay RZ/RQ)
 *   index → GET /api/index-baseline/combined?code=… (fetchIndicesCombined —
 *           precomputed PE, no margin data)
 *
 * ETF / index rows are mapped onto the StockBaselineRow shape the shared
 * chart consumes (see mapEtfRows / mapIndexRows); fields the source doesn't
 * carry (EPS, dividends, …) are null and their overlays simply don't render.
 *
 * Configuration (props) — every part of the default setup is switchable:
 *   • sources            — per-sec_type data-source override (fetch + map)
 *   • chartOptions       — pass-through StockOhlcChart options (dividends,
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
 */
import React from "react";
import {
  Alert,
  Box,
  Card,
  CardContent,
  CardHeader,
  CircularProgress,
  ToggleButton,
} from "@mui/material";
import TimeSlider from "@/shared/components/time-slider/TimeSlider";
import OhlcModeToggle from "@/components/OhlcModeToggle";
import StockOhlcChart from "@/components/StockOhlcChart";
import { AiAskButton, derivePlotInfo } from "@/shared/ai-ask";
import type { ECharts } from "echarts";
import {
  fetchEtfMarginCombined,
  fetchIndicesCombined,
  fetchSecBoardMap,
  fetchStockBaseline,
} from "@/lib/api-client";
import { fetchMarketHypes } from "@/lib/api-client";
import { mergeHypeEpisodesAllWindows, HYPE_ACCENT_COLOR } from "@/shared/charts/hypeBands";
import type { OhlcMode } from "@/lib/ohlc";
import type {
  MovAveSpreadHypeEpisodes,
  EtfMarginRow,
  IndexBaselineRow,
  SecBoardTag,
  StockBaselineRow,
  StockDividend,
} from "@shared/types";

/** Security type the chart knows how to fetch a trend for. */
export type CodeTrendSecType = "etf" | "index" | "stock";

/** Normalized per-day row shape the base chart consumes (StockBaselineRow). */
export type CodeTrendRow = StockBaselineRow;

/** Result of one code-trend fetch, already mapped to CodeTrendRow[]. */
export interface CodeTrendData {
  rows: CodeTrendRow[];
  /** Display name as reported by the API (null when the API has none). */
  name: string | null;
  /** Dividend events (stock only) — drawn as ex-div date markers. */
  dividends: StockDividend[];
}

/** One sec_type's data source: fetch a code's daily rows + map them. */
export interface CodeTrendSource {
  fetch: (code: string) => Promise<CodeTrendData>;
}

/** Strip the exchange suffix — the combined endpoints match bare codes. */
export function stripCodeSuffix(code: string): string {
  return code.toUpperCase().replace(/\.(SS|SZ|SH|BJ|HK)$/i, "");
}

/** ETF margin rows → the shared chart's row shape (adjusted OHLC preferred). */
export function mapEtfRows(rows: EtfMarginRow[]): CodeTrendRow[] {
  return rows.map((r) => ({
    date: r.date,
    // Prefer adjusted OHLC — corp actions (dividend/split) otherwise draw
    // artificial cliffs into the trend.
    open: r.adj_open ?? r.open,
    high: r.adj_high ?? r.high,
    low: r.adj_low ?? r.low,
    close: r.adj_close ?? r.close,
    prev_close: r.prev_close,
    pct_change: null,
    pe: null,
    eps: null,
    is_pe_estimated: false,
    has_intraday_5mins: false,
    trading_shares: 0,
    trading_amount: r.trading_amount,
    rz_balance: r.rz_balance,
    rz_buy: null,
    rq_balance_qty: r.rq_balance_qty,
    rq_balance_amt: r.rq_balance_amt,
    total_balance: r.total_balance,
  }));
}

/** Index baseline rows → the shared chart's row shape (PE carried over). */
export function mapIndexRows(rows: IndexBaselineRow[]): CodeTrendRow[] {
  return rows.map((r) => ({
    date: r.date,
    open: r.open,
    high: r.high,
    low: r.low,
    close: r.close,
    prev_close: null,
    pct_change: r.change_pct,
    pe: r.pe,
    eps: null,
    is_pe_estimated: false,
    has_intraday_5mins: r.has_intraday_5mins,
    trading_shares: r.trading_shares ?? 0,
    trading_amount: r.trading_amount ?? 0,
    rz_balance: null,
    rz_buy: null,
    rq_balance_qty: null,
    rq_balance_amt: null,
    total_balance: null,
  }));
}

/** Default per-sec_type sources — override via the `sources` prop. */
/** Stable EMPTY identities for the chart props' fallbacks. A fresh []
 * per render would dirty StockOhlcChart's option memo on EVERY parent
 * re-render (spinner flips, chip mounts, row selection...), forcing a
 * full ~1 s notMerge rebuild of the 1700-candle chart each time
 * (measured 2026-09) — the props must stay identity-stable when their
 * CONTENT is unchanged. */
const NO_EPISODES: import("@shared/types").MovAveSpreadHypeEpisode[] = [];
const NO_DIVIDENDS: StockDividend[] = [];

/** One dim outline chip of the header's board tag ("MAIN", or
 *  "MAIN 40%" for ETF/index mixes). Percent formatting: integers at
 *  10%+, one decimal below — shares under 0.5% are dropped by the
 *  caller so a rounded 0% never renders. */
function BoardChip({ tag, showPct }: { tag: SecBoardTag; showPct: boolean }) {
  const pctStr = tag.pct >= 10 ? Math.round(tag.pct).toString()
    : (Math.round(tag.pct * 10) / 10).toString();
  return (
    <span style={{
      fontSize: "0.62rem",
      fontWeight: 500,
      lineHeight: "15px",
      padding: "0 6px",
      border: "1px solid var(--chart-subtitle)",
      borderRadius: 8,
      color: "var(--chart-subtitle)",
      whiteSpace: "nowrap",
    }}>
      {tag.board}{showPct ? ` ${pctStr}%` : ""}
    </span>
  );
}

export const CODE_TREND_SOURCES: Record<CodeTrendSecType, CodeTrendSource> = {
  stock: {
    fetch: async (code) => {
      const d = await fetchStockBaseline(stripCodeSuffix(code));
      return { rows: d.rows, name: d.name || null, dividends: d.dividends ?? [] };
    },
  },
  etf: {
    fetch: async (code) => {
      // code filter bypasses sector/industry filters + pagination.
      const d = await fetchEtfMarginCombined(
        null, null, null, null, undefined, undefined, undefined, stripCodeSuffix(code),
      );
      const etf = d.etfs[0];
      return {
        rows: etf ? mapEtfRows(etf.rows) : [],
        name: etf?.name || null,
        dividends: [],
      };
    },
  },
  index: {
    fetch: async (code) => {
      const d = await fetchIndicesCombined(
        null, null, null, null, undefined, undefined, stripCodeSuffix(code),
      );
      const idx = d.indices[0];
      return {
        rows: idx ? mapIndexRows(idx.rows) : [],
        name: idx?.name || null,
        dividends: [],
      };
    },
  },
};

/**
 * Pass-through options for the inner StockOhlcChart — everything except the
 * pieces this component owns (rows / ohlcMode / height). This keeps full
 * compatibility with the shared chart's option surface: dataZoom windowing,
 * dividend markers, date-click callbacks, etc.
 */
export type CodeTrendChartOptions = Partial<
  Omit<React.ComponentProps<typeof StockOhlcChart>, "rows" | "ohlcMode" | "height">
>;

export interface CodeTrendChartProps {
  /** Security type — selects the baseline endpoint + row mapping. */
  secType: CodeTrendSecType;
  /** Security code — suffixed ("000001.SZ") or bare ("000001"). */
  code: string;
  /** Display name (e.g. from a nav tree); falls back to the API's name. */
  name?: string;
  /** Content height in px (chart gets height − 60, slider fits below). */
  height?: number;
  /** Initial OHLC display mode. Defaults to "percentage" (% change rebased). */
  defaultOhlcMode?: OhlcMode;
  /** Show the Absolute / %-change toggle in the header. Default true. */
  showModeToggle?: boolean;
  /** Show the MUI date-range slider below the chart. Default false — the
   *  default windowing is the in-chart ECharts dataZoom slider (shared
   *  commonDataZoom, same style as the MA-Spread chart), which needs the
   *  FULL unwindowed row history to work. */
  showRangeSlider?: boolean;
  /** "card" (default) wraps in Card chrome with header; "bare" renders just
   *  chart + slider for embedding under an existing header. */
  variant?: "card" | "bare";
  /** Header title override (defaults to "code · name"). */
  title?: React.ReactNode;
  /** Extra text appended to the default subtitle (e.g. "3.21% holding"). */
  subtitleExtra?: string;
  /** Extra header control rendered after the mode toggle (close button, …). */
  headerAction?: React.ReactNode;
  /** Per-sec_type source overrides (merged over CODE_TREND_SOURCES). */
  sources?: Partial<Record<CodeTrendSecType, CodeTrendSource>>;
  /** Extra online-search seed keywords for the AI Ask "?" — the page's
   *  items of interest that live OUTSIDE this chart (e.g. the forecast
   *  family buttons beneath it: RSI, Bollinger, …). Merged ahead of the
   *  auto-derived instrument/series keywords; identity-stable (memoized)
   *  like every other chart input. */
  aiSearchKeywords?: string[];
  /** Inner-chart option pass-through (dividends, dataZoom, onDateClick, …). */
  chartOptions?: CodeTrendChartOptions;
  /** Fired after each load settles (success or empty; not on error). */
  onLoaded?: (data: CodeTrendData | null) => void;
  /** Fired when the user switches the OHLC display mode. */
  onOhlcModeChange?: (mode: OhlcMode) => void;
}

interface CodeTrendChartState {
  data: CodeTrendData | null;
  loading: boolean;
  error: string | null;
  /** OHLC display mode — "percentage" rebases OHLC + MAs to % change from
   *  the first valid close; "absolute" shows raw prices. */
  ohlcMode: OhlcMode;
  /** Date-range slider window (row indices) — [0, len-1] = full history. */
  range: [number, number];
  /** Market-hype EPISODE shading toggle (stats.mov_ave_market_hypes) —
   *  the light-purple hyped-period bands beside the Absolute/% Change
   *  toggle. Off by default; the first enable fetches the code's episodes
   *  once (GET /api/analysis/market-hypes) and caches them per code. */
  showHypes: boolean;
  hypeEpisodes: MovAveSpreadHypeEpisodes | null;
  hypesLoading: boolean;
  /** Board tag(s) beside the header title (stats.sec_board_map via
   *  GET /api/sec-board): a stock's single listing board, or an
   *  ETF/index's composition-weighted board mix. Fetched in parallel
   *  with the trend rows; empty when no data / fetch failure. */
  boardTags: SecBoardTag[];
}

export default class CodeTrendChart extends React.Component<
  CodeTrendChartProps,
  CodeTrendChartState
> {
  /** Monotonic token — only the newest fetch may settle into state. */
  private loadSeq = 0;
  /** Separate token for the board-tag fetch (it must not be invalidated
   *  by the shared loadSeq bumps from the hypes toggle). */
  private boardSeq = 0;
  /** Live inner-chart instance — captured via the StockOhlcChart
   *  onChartReady passthrough for the AI Ask screenshot. */
  private chartInstance: ECharts | null = null;

  state: CodeTrendChartState = {
    data: null,
    loading: false,
    error: null,
    ohlcMode: "percentage",
    range: [0, 0],
    showHypes: false,
    hypeEpisodes: null,
    hypesLoading: false,
    boardTags: [],
  };

  componentDidMount() {
    this.load();
  }

  componentDidUpdate(prev: CodeTrendChartProps) {
    if (prev.secType !== this.props.secType || prev.code !== this.props.code) {
      this.setState({
        ohlcMode: this.props.defaultOhlcMode ?? "percentage",
        showHypes: false,
        hypeEpisodes: null,
        hypesLoading: false,
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
      <StockOhlcChart
        rows={rows}
        ohlcMode={ohlcMode}
        height={height}
        {...chartOptions}
        {...dataZoom}
        dividends={chartOptions?.dividends ?? this.state.data?.dividends ?? NO_DIVIDENDS}
        hypeEpisodes={
          this.state.showHypes
            ? mergeHypeEpisodesAllWindows(this.state.hypeEpisodes)
            : NO_EPISODES
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
    const { code, name, secType, title } = this.props;
    if (title !== undefined) return title;
    const displayName = name || this.state.data?.name || "—";
    // Board tag beside the code title: a stock shows its single listing
    // board; an ETF/index shows the board mix of its composition
    // (pct DESC, shares < 0.5% dropped so a rounded 0% never renders).
    const boardTags = this.state.boardTags.filter((t) => t.pct >= 0.5);
    const showPct = secType !== "stock";
    const boardTip = showPct
      ? "Listing-board mix of the latest composition constituents, weighted by index weight (stats.sec_board_map)"
      : "Listing board (stats.sec_board_map)";
    return (
      <span style={{
        fontSize: "0.9rem", fontWeight: 600,
        display: "inline-flex", alignItems: "center", gap: 6,
      }}>
        <span>{code} · {displayName}</span>
        {boardTags.length > 0 && (
          <span
            title={boardTip}
            style={{ display: "inline-flex", alignItems: "center", gap: 3 }}
          >
            {boardTags.map((t) => <BoardChip key={t.board} tag={t} showPct={showPct} />)}
          </span>
        )}
      </span>
    );
  }

  /** Header subtitle (card variant) — bars + date span + extras. */
  protected renderSubtitle(): React.ReactNode {
    const { subtitleExtra } = this.props;
    const { data } = this.state;
    const n = data?.rows.length ?? 0;
    const span =
      data && n > 0 ? ` · ${data.rows[0].date} → ${data.rows[n - 1].date}` : "";
    return (
      <span style={{ fontSize: "0.7rem", color: "var(--chart-subtitle)" }}>
        {data ? `${n} bars${span}${subtitleExtra ? ` · ${subtitleExtra}` : ""}` : "Loading…"}
      </span>
    );
  }

  /** Header actions (card variant) — mode toggle + Hypes toggle + prop
   *  extras. The Hypes toggle sits beside the Absolute / % Change toggle
   *  so every page's code trend can shade the market-hype episodes. */
  protected renderHeaderActions(): React.ReactNode {
    const { showModeToggle = true, headerAction } = this.props;
    return (
      <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
        {showModeToggle && (
          <OhlcModeToggle
            value={this.state.ohlcMode}
            onChange={this.handleModeChange}
          />
        )}
        <ToggleButton
          value="hypes"
          size="small"
          selected={this.state.showHypes}
          onChange={this.handleHypesToggle}
          title="Market-hype episodes — shade the hyped date periods (stats.mov_ave_market_hypes)"
          sx={{
            px: 1,
            py: 0.25,
            fontSize: "0.7rem",
            ...(this.state.showHypes
              ? {
                  color: HYPE_ACCENT_COLOR,
                  borderColor: HYPE_ACCENT_COLOR,
                  bgcolor: "rgba(126, 87, 194, 0.12)",
                }
              : {}),
          }}
        >
          {this.state.hypesLoading ? "Hyped…" : "Hyped"}
        </ToggleButton>
        {headerAction}
      </Box>
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

  /** Toggle the market-hype shading; the first enable fetches the code's
   *  episodes once and caches them (per code — reset on code change). */
  private handleHypesToggle = () => {
    const showHypes = !this.state.showHypes;
    this.setState({ showHypes });
    if (showHypes && this.state.hypeEpisodes == null && !this.state.hypesLoading) {
      const seq = ++this.loadSeq;
      this.setState({ hypesLoading: true });
      fetchMarketHypes(this.props.code, this.props.secType)
        .then((d) => {
          if (seq !== this.loadSeq) return;
          this.setState({ hypeEpisodes: d.episodes, hypesLoading: false });
        })
        .catch(() => {
          if (seq !== this.loadSeq) return;
          // No hype data (endpoint error / never built) — toggle stays on
          // with empty shading; clearing hypeEpisodes refetches on retry.
          this.setState({ hypeEpisodes: {}, hypesLoading: false });
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

    // AI Ask — plot info for the "?" beside the title. The inner
    // StockOhlcChart builds its option internally, so the intro + live
    // screenshot carry the value (derivePlotInfo tolerates the omitted
    // option). A fresh plotInfo per render is fine — the button keeps its
    // own open state.
    const { code, name, secType } = this.props;
    const displayName = name || data?.name || "—";
    const nBars = allRows.length;
    const aiAskButton = (
      <AiAskButton
        plotInfo={derivePlotInfo({
          title: `${code} · ${displayName}`,
          subtitle: nBars > 0
            ? `${nBars} bars · ${allRows[0].date} → ${allRows[allRows.length - 1].date}`
            : undefined,
          spec: {
            intro:
              `Daily price trend for ${code} (${secType}): OHLC candles — or a close line when ` +
              "OHLC is sparse — with MA5/MA20/MA60/MA120 overlays, trading-amount bars on a " +
              "right axis, and, where the data has them, margin-balance fills (RZ cash borrow " +
              "up / RQ sec borrow down) and a PE line on an offset axis. Gold diamonds mark " +
              "ex-dividend dates; the Hyped toggle shades market-hype episode bands. In " +
              "percentage mode OHLC + MAs are rebased to % change from the first valid close " +
              "(tooltips keep actual prices), and date gaps are broken so long holidays don't " +
              "draw artificial cliffs.",
            instruments: [
              {
                code,
                ...(displayName !== "—" ? { name: displayName } : {}),
                assetClass: secType,
              },
            ],
            window: windowedRows.length > 0
              ? {
                  start: windowedRows[0].date,
                  end: windowedRows[windowedRows.length - 1].date,
                  granularity: "daily",
                }
              : undefined,
            state: {
              mode: ohlcMode,
              hype_shading: this.state.showHypes,
              window: sliderOn ? "date-range slider" : "in-chart dataZoom",
            },
            searchKeywords: [
              ...(this.props.aiSearchKeywords ?? []),
              // the header toggles' active states — items of interest a
              // web search can use (trend terms + the shaded episodes)
              ...(this.state.showHypes ? ["market hype"] : []),
            ],
            notes: [
              "MAs are computed client-side from close; Amount bars are in 亿 (raw yuan / 1e8).",
            ],
          },
        })}
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
