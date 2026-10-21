/**
 * TrendSection — the signals tier's trend phase chips (top / bottom /
 * rise / fall, multi-pick; analysis.trend_days via the trend-spans API —
 * the RSI re-entry classification shared with the rule cells, replacing
 * the retired client-side close-vs-MA60 classifier 2026-10-15): rise/
 * fall = the RSI(10) momentum episodes + the 2026-10-21 travel claim
 * (a traveling MA255-gap extreme claims rise/fall directly),
 * top/bottom = the STATIONED MA255-gap extremes. Each picked phase
 * shades its own date spans (light red / green / blue / amber) on
 * every selected pair (date-based rects). A pick takes the panel's
 * single shade slot — the other shade rows' picks clear (that handler
 * lives in the panel).
 */
import { Box, Chip, Typography } from "@mui/material";
import {
  TREND_TYPES,
  TREND_TYPE_LABELS,
  TREND_ACCENTS,
  type TrendType,
} from "../chartOption";
import { SectionLabel } from "../SectionLabel";
import { PERIOD_GRID_SX, PERIOD_CHIP_SX } from "../constants";

interface Props {
  trendPicks: Set<TrendType>;
  onToggle: (t: TrendType) => void;
}

export function TrendSection({ trendPicks, onToggle }: Props) {
  return (
    <>
      <Box sx={{ ...PERIOD_GRID_SX, mt: 1 }}>
        <SectionLabel id="trendBands" active={trendPicks.size > 0} />
        {TREND_TYPES.map((t, col) => (
          <Chip
            key={t}
            label={TREND_TYPE_LABELS[t]}
            size="small"
            clickable
            color={trendPicks.has(t) ? "primary" : "default"}
            variant={trendPicks.has(t) ? "filled" : "outlined"}
            onClick={() => onToggle(t)}
            sx={{
              gridColumn: col + 1,
              ...PERIOD_CHIP_SX,
              ...(trendPicks.has(t)
                ? {
                    color: TREND_ACCENTS[t],
                    borderColor: TREND_ACCENTS[t],
                  }
                : {}),
            }}
          />
        ))}
      </Box>
      {trendPicks.size > 0 && (
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ display: "block", mt: 0.5, fontSize: "0.65rem" }}
        >
          signals-tier trend spans shaded (analysis.trend_days) — rise /
          fall: RSI(10) re-entry momentum episodes + the travel claim
          (a MA255-gap extreme whose leg traveled ≥4 average daily
          moves from the prior 60-row high/low with the guard agreeing
          claims rise/fall directly) · top / bottom: the STATIONED
          MA255-gap extremes (RSI re-cross of 50 after a deep ≤30/≥70
          anchor flips the trend within 40d) · unknown = background ·
          dates from the rules tier, drawn on every pair
        </Typography>
      )}
    </>
  );
}
