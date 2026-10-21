/**
 * HighLowStreaksSection — the nested High/Low Streaks row: layer 1 holds
 * the band lookback periods (60/120/255/500/750/1275d, period-column
 * aligned with the OHLC row); clicking one expands layer 2 — band
 * tightness pcts (1/5/10%). Selecting a pct fills the LATEST date's
 * trailing period-row window with its top/bottom pct% price zones (light
 * purple above high_val, light yellow below low_val) and draws the
 * whole-window long break streak per side (computed in the panel /
 * shadeBuilders). Clicking a chart date anchors the window to the
 * trailing rows before that date ("show before that date"; click again
 * to return to the latest). The captions report the window span and
 * each side's streak span / streak count / days / extreme.
 */
import { Box, Chip, Typography } from "@mui/material";
import { fmtNum } from "@/lib/series";
import type {
  LongBandStreak,
  StreakBandWindow,
} from "@/shared/charts/streakBands";
import { SectionLabel } from "../SectionLabel";
import {
  HIGH_LOW_STREAK_PERIODS,
  HIGH_LOW_STREAK_PCTS,
  PERIOD_GRID_SX,
  PERIOD_CHIP_SX,
} from "../constants";

interface Props {
  streakPeriod: number | null;
  streakPct: number | null;
  onPeriodClick: (w: number) => void;
  onPctClick: (p: number) => void;
  disabled: boolean;
  streakWin: StreakBandWindow | null;
  longStreaks: { high: LongBandStreak[]; low: LongBandStreak[] } | null;
  anchorIdx: number | null;
  /** The "streaks" extras group has landed (gates the no-data hint). */
  streakRowsLoaded: boolean;
  hasStreakData: boolean;
}

export function HighLowStreaksSection({
  streakPeriod,
  streakPct,
  onPeriodClick,
  onPctClick,
  disabled,
  streakWin,
  longStreaks,
  anchorIdx,
  streakRowsLoaded,
  hasStreakData,
}: Props) {
  return (
    <>
      <Box sx={{ ...PERIOD_GRID_SX, mt: 1 }}>
        {/* Row label */}
        <SectionLabel id="highLowStreaks" active={streakPeriod != null} />
        {/* Layer 1 — periods, columns 3-8 (aligned with the OHLC row's
            60d/120d/255d/500d/750d/1275d buttons). */}
        {HIGH_LOW_STREAK_PERIODS.map((w, col) => (
          <Chip
            key={w}
            label={`${w}d`}
            size="small"
            clickable
            disabled={disabled}
            color={streakPeriod === w ? "primary" : "default"}
            variant={streakPeriod === w ? "filled" : "outlined"}
            onClick={() => onPeriodClick(w)}
            sx={{ gridColumn: col + 3, ...PERIOD_CHIP_SX }}
          />
        ))}
        {/* Layer 2 — pcts, expanded beneath the periods when one is
            active (columns 3-5, under the first three periods). */}
        {streakPeriod != null &&
          HIGH_LOW_STREAK_PCTS.map((p, col) => (
            <Chip
              key={p}
              label={`${p}%`}
              size="small"
              clickable
              disabled={disabled}
              color={streakPct === p ? "primary" : "default"}
              variant={streakPct === p ? "filled" : "outlined"}
              onClick={() => onPctClick(p)}
              sx={{ gridColumn: col + 3, ...PERIOD_CHIP_SX }}
            />
          ))}
      </Box>
      {streakPeriod != null && streakPct == null && (
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ display: "block", mt: 0.5, fontSize: "0.65rem" }}
        >
          pick a band tightness (pct) to shade break streaks
        </Typography>
      )}
      {streakPeriod != null && streakPct != null && (
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ display: "block", mt: 0.5, fontSize: "0.65rem" }}
        >
          light purple/yellow = trailing {streakPeriod}d top/bottom{" "}
          {streakPct}% zones
          {streakWin ? ` (${streakWin.startDate} → ${streakWin.endDate})` : ""},
          darker = break streaks (each shaded over its own span ·
          ≤5-day in-band gaps bridged) ·{" "}
          {longStreaks
            ? (longStreaks.high.length > 0 || longStreaks.low.length > 0
                ? [
                    longStreaks.high.length > 0
                      ? `high ${longStreaks.high.length} streak${longStreaks.high.length === 1 ? "" : "s"} · ${longStreaks.high.reduce((a, s) => a + s.days, 0)}d · peak ${fmtNum(longStreaks.high.reduce((a, s) => Math.max(a, s.extreme), -Infinity))} · ${longStreaks.high.slice(0, 2).map((s) => `${s.startDate.slice(2)}→${s.endDate.slice(2)}`).join(", ")}${longStreaks.high.length > 2 ? `, +${longStreaks.high.length - 2} more` : ""}`
                      : "high none",
                    longStreaks.low.length > 0
                      ? `low ${longStreaks.low.length} streak${longStreaks.low.length === 1 ? "" : "s"} · ${longStreaks.low.reduce((a, s) => a + s.days, 0)}d · trough ${fmtNum(longStreaks.low.reduce((a, s) => Math.min(a, s.extreme), Infinity))} · ${longStreaks.low.slice(0, 2).map((s) => `${s.startDate.slice(2)}→${s.endDate.slice(2)}`).join(", ")}${longStreaks.low.length > 2 ? `, +${longStreaks.low.length - 2} more` : ""}`
                      : "low none",
                  ].join(" · ")
                : "no streaks in this window")
            : "no window"}
          {streakWin
            ? anchorIdx != null
              ? ` · anchored to ${streakWin.endDate} (click again to clear)`
              : " · click a chart date to anchor the window"
            : ""}
        </Typography>
      )}
      {streakRowsLoaded && !hasStreakData && (
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ display: "block", mt: 0.5, fontSize: "0.65rem" }}
        >
          no streak data for this code — run{" "}
          <code>python -m analysis.mov_ave_spread</code> to build
          analysis.mov_ave_high_low_pct_streaks
        </Typography>
      )}
    </>
  );
}
