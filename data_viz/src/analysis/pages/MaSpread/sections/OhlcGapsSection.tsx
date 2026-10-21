/**
 * OhlcGapsSection — the Top-10% OHLC Days independent toggles (the gaps
 * store's wick top-decile flags, analysis.mov_ave_spreads_detail_
 * ohlc_gaps): spike fade = the day's upper wick >= its trailing-252d
 * 90th percentile (mark at the day's HIGH), flush recover = the lower
 * wick likewise (mark at the day's LOW). Independent — a day can be
 * both. The group's data lands on the first toggle pick (the /extras
 * "gaps" metric) and is then filtered client-side.
 */
import { Box, Chip, Typography } from "@mui/material";
import { SectionLabel } from "../SectionLabel";
import { PERIOD_GRID_SX, PERIOD_CHIP_SX } from "../constants";
import type { GapMarksState } from "../types";

interface Props {
  gapMarks: GapMarksState;
  onToggle: (kind: "spike" | "flush") => void;
  disabled: boolean;
  /** Flagged-day counts per kind (the caption's ×N readout). */
  counts: { spike: number; flush: number };
}

export function OhlcGapsSection({ gapMarks, onToggle, disabled, counts }: Props) {
  return (
    <>
      <Box sx={{ ...PERIOD_GRID_SX, mt: 1 }}>
        <SectionLabel id="ohlcGaps" active={gapMarks.spike || gapMarks.flush} />
        <Chip
          label="spike fade"
          size="small"
          clickable
          disabled={disabled}
          color={gapMarks.spike ? "primary" : "default"}
          variant={gapMarks.spike ? "filled" : "outlined"}
          onClick={() => onToggle("spike")}
          sx={{ gridColumn: 3, ...PERIOD_CHIP_SX }}
        />
        <Chip
          label="flush recover"
          size="small"
          clickable
          disabled={disabled}
          color={gapMarks.flush ? "primary" : "default"}
          variant={gapMarks.flush ? "filled" : "outlined"}
          onClick={() => onToggle("flush")}
          sx={{ gridColumn: 4, ...PERIOD_CHIP_SX }}
        />
      </Box>
      {(gapMarks.spike || gapMarks.flush) && (
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ display: "block", mt: 0.5, fontSize: "0.65rem" }}
        >
          {[
            gapMarks.spike
              ? `spike fade shaded ×${counts.spike} (light red)`
              : null,
            gapMarks.flush
              ? `flush recover shaded ×${counts.flush} (light green)`
              : null,
          ]
            .filter(Boolean)
            .join(" · ")}{" "}
          — wick in the trailing-252d top decile (≥120 obs); tooltip
          shows the day's gap legs
        </Typography>
      )}
    </>
  );
}
