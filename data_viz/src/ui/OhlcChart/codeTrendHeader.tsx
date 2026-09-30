/**
 * codeTrendHeader — the card-header renderers of CodeTrendChart (title
 * with board chips, subtitle with bar count + date span, the mode /
 * regimes toggle actions). Split out of CodeTrendChart.tsx; the class's
 * protected renderTitle / renderSubtitle / renderHeaderActions hooks
 * delegate here, so overriding those hooks in a subclass still works.
 */
import { Box, ToggleButton } from "@mui/material";
import OhlcModeToggle from "@/shared/components/OhlcModeToggle";
import type { OhlcMode } from "@/lib/ohlc";
import { REGIME_ACCENT_COLORS } from "@/shared/charts/regimeBands";
import type { SecBoardTag } from "@shared/types";
import type { CodeTrendChartProps, CodeTrendData } from "./codeTrendTypes";

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

/** Header title (card variant): "code · name" + the board tag(s). */
export function CodeTrendTitle({
  code,
  name,
  secType,
  title,
  data,
  boardTags,
}: Pick<CodeTrendChartProps, "code" | "name" | "secType" | "title"> & {
  data: CodeTrendData | null;
  boardTags: SecBoardTag[];
}) {
  if (title !== undefined) return title;
  const displayName = name || data?.name || "—";
  // Board tag beside the code title: a stock shows its single listing
  // board; an ETF/index shows the board mix of its composition
  // (pct DESC, shares < 0.5% dropped so a rounded 0% never renders).
  const tags = boardTags.filter((t) => t.pct >= 0.5);
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
      {tags.length > 0 && (
        <span
          title={boardTip}
          style={{ display: "inline-flex", alignItems: "center", gap: 3 }}
        >
          {tags.map((t) => <BoardChip key={t.board} tag={t} showPct={showPct} />)}
        </span>
      )}
    </span>
  );
}

/** Header subtitle (card variant) — bars + date span + extras. */
export function CodeTrendSubtitle({
  subtitleExtra,
  data,
}: {
  subtitleExtra?: string;
  data: CodeTrendData | null;
}) {
  const n = data?.rows.length ?? 0;
  const span =
    data && n > 0 ? ` · ${data.rows[0].date} → ${data.rows[n - 1].date}` : "";
  return (
    <span style={{ fontSize: "0.7rem", color: "var(--chart-subtitle)" }}>
      {data ? `${n} bars${span}${subtitleExtra ? ` · ${subtitleExtra}` : ""}` : "Loading…"}
    </span>
  );
}

/** Header actions (card variant) — mode toggle + Regimes toggle +
 *  prop extras. The Regimes toggle sits beside the Absolute / % Change
 *  toggle so every page's code trend can shade the market-regime spans. */
export function CodeTrendHeaderActions({
  showModeToggle = true,
  headerAction,
  ohlcMode,
  onModeChange,
  showRegimes,
  regimesLoading,
  onRegimesToggle,
}: {
  showModeToggle?: boolean;
  headerAction?: React.ReactNode;
  ohlcMode: OhlcMode;
  onModeChange: (mode: OhlcMode) => void;
  showRegimes: boolean;
  regimesLoading: boolean;
  onRegimesToggle: () => void;
}) {
  return (
    <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
      {showModeToggle && (
        <OhlcModeToggle
          value={ohlcMode}
          onChange={onModeChange}
        />
      )}
      <ToggleButton
        value="regimes"
        size="small"
        selected={showRegimes}
        onChange={onRegimesToggle}
        title="Market regimes — shade hot (purple) / panic (red) / quiet (blue) spans (stats.market_regimes)"
        sx={{
          px: 1,
          py: 0.25,
          fontSize: "0.7rem",
          ...(showRegimes
            ? {
                color: REGIME_ACCENT_COLORS.hot,
                borderColor: REGIME_ACCENT_COLORS.hot,
                bgcolor: "rgba(126, 87, 194, 0.12)",
              }
            : {}),
        }}
      >
        {regimesLoading ? "Regimes…" : "Regimes"}
      </ToggleButton>
      {headerAction}
    </Box>
  );
}
