/**
 * RsiTrendsSection — the right-axis RSI overlay's own row (moved out of
 * the header Amt/Slopes/Curvatures toggle). The section rests fully OFF:
 * the DAYS row multi-picks the Wilder-RSI window lines (3/6/10/14/20/60d —
 * any pick replaces the amt bars while the header toggle sits at Amt;
 * Slopes/Curvatures keep precedence) AND scopes the pct row's ranks; the
 * first day pick AUTO-arms the 1% shade (the panel's effect), the last
 * day un-picked takes it off. The PCT row single-picks the Top-%
 * history-rank shade (analysis.mov_ave_rsi_ranks) — green where the RSI
 * ranked within the top pct/2 of the picked windows' own history (rises),
 * red where within the bottom pct/2 (drops); one qualifying window draws
 * the base fill, several overlapping on a date stack it darker. Both rows
 * sit on the section grid's period columns (3-8) like the other rows.
 */
import { Box, Chip, Stack, Typography } from "@mui/material";
import { SectionLabel } from "../SectionLabel";
import { RSI_WINDOWS, RSI_TOP_PCTS, PERIOD_GRID_SX, PERIOD_CHIP_SX } from "../constants";

interface Props {
  rsiDays: number[];
  /** The population toggle (upper-right of the section, once a day is
   *  picked): null = all days; "typical" / "re-entry" = the rsi analysis
   *  layer's is_typical populations only. */
  rsiPopulation: "typical" | "re-entry" | null;
  onPopulationToggle: (p: "typical" | "re-entry") => void;
  rsiTopPct: number | null;
  onToggleDay: (w: number) => void;
  /** pct = 0 clears the pick; the active pct toggles off by clicking it
   *  again (the panel decides which to pass). */
  onSelectPct: (pct: number) => void;
}

export function RsiTrendsSection({
  rsiDays,
  rsiPopulation,
  onPopulationToggle,
  rsiTopPct,
  onToggleDay,
  onSelectPct,
}: Props) {
  return (
    <>
      <Box sx={{ ...PERIOD_GRID_SX, mt: 1, position: "relative" }}>
        {/* Population toggle — the section's upper-right corner, once
            an RSI trend is selected: typical = the first-touch
            episodes' days (each episode's first tail day AND its
            gap-1 continuation days — the deep path); re-entry = the
            same-episode re-registrations' days (each re-qualification
            AND its continuations); neither = all days. */}
        {rsiDays.length > 0 && (
          <Stack
            direction="row"
            spacing={0.5}
            sx={{ position: "absolute", top: -30, right: 0 }}
          >
            <Chip
              label="typical"
              size="small"
              clickable
              color={rsiPopulation === "typical" ? "secondary" : "default"}
              variant={rsiPopulation === "typical" ? "filled" : "outlined"}
              onClick={() => onPopulationToggle("typical")}
              sx={PERIOD_CHIP_SX}
            />
            <Chip
              label="re-entry"
              size="small"
              clickable
              color={rsiPopulation === "re-entry" ? "secondary" : "default"}
              variant={rsiPopulation === "re-entry" ? "filled" : "outlined"}
              onClick={() => onPopulationToggle("re-entry")}
              sx={PERIOD_CHIP_SX}
            />
          </Stack>
        )}
        {/* Row label */}
        <SectionLabel
          id="rsiTrends"
          active={rsiDays.length > 0 || rsiTopPct != null}
        />
        {/* Days row — multi-select, columns 3-8. */}
        {RSI_WINDOWS.map((w, col) => (
          <Chip
            key={w}
            label={`${w}d`}
            size="small"
            clickable
            color={rsiDays.includes(w) ? "primary" : "default"}
            variant={rsiDays.includes(w) ? "filled" : "outlined"}
            onClick={() => onToggleDay(w)}
            sx={{ gridColumn: col + 3, ...PERIOD_CHIP_SX }}
          />
        ))}
        {/* Pct row — single-select toggles, columns 3-7 (no Off entry:
            the active pct toggles off by clicking it again). */}
        {RSI_TOP_PCTS.map((p, col) => (
          <Chip
            key={p}
            label={`${Math.round(p * 100)}%`}
            size="small"
            clickable
            color={rsiTopPct === p ? "primary" : "default"}
            variant={rsiTopPct === p ? "filled" : "outlined"}
            onClick={() => onSelectPct(rsiTopPct === p ? 0 : p)}
            sx={{ gridColumn: col + 3, ...PERIOD_CHIP_SX }}
          />
        ))}
      </Box>
      {rsiTopPct != null && (
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ display: "block", mt: 0.5, fontSize: "0.65rem" }}
        >
          Top {Math.round(rsiTopPct * 100)}% split — green bands where the
          RSI ranked within the top {Math.round(rsiTopPct * 500) / 10}% of
          its own{" "}
          {rsiDays.length > 0
            ? `${rsiDays.slice().sort((a, b) => a - b).join("/")}d`
            : "history (any window)"}{" "}
          (rises), red where within the bottom{" "}
          {Math.round(rsiTopPct * 500) / 10}% (drops); one qualifying
          window draws the base fill, each extra window overlapping the
          date stacks it darker
          {rsiDays.length > 0
            ? ` · drawing RSI ${rsiDays.slice().sort((a, b) => a - b).join("/")}d lines`
            : " · pick days above to scope the ranks and draw the lines"}
        </Typography>
      )}
    </>
  );
}
