/**
 * PxVolSection — the Px-Vol States nested rows (single-select per row):
 * layer 1 = the day's price SPEED (sharp/slow/flat rise/drop — t =
 * ret_1d / the code's own trailing 255-row σ_ret), layer 2 = the day's
 * trading-amount LEVEL STATE (up/flat/down vs the code's own
 * trailing-year amount distribution — z-scored log amount). The two rows
 * are independent picks of ONE state family, not a progressive
 * drill-down like High/Low Streaks. Picking one of each shades the
 * matching dates — green rise / red drop / gray flat, shade depth by
 * combo strength (sharp × heavy = darkest, e.g. rising price +
 * increasing amount = strong growth).
 *
 * PxVolCaption renders the section's status line; the PANEL places it
 * after the Trend section's caption to keep the original DOM order.
 */
import { Box, Chip, Typography } from "@mui/material";
import {
  PX_VOL_SPEED_OPTIONS,
  PX_VOL_VOL_OPTIONS,
  pxVolReading,
  type PxVolRun,
  type PxVolSpeed,
  type PxVolVolState,
} from "../chartOption";
import { SectionLabel } from "../SectionLabel";
import { PERIOD_GRID_SX, PERIOD_CHIP_SX } from "../constants";

interface Props {
  pxVolSpeed: PxVolSpeed | null;
  pxVolVol: PxVolVolState | null;
  onSpeedClick: (s: PxVolSpeed) => void;
  onVolClick: (v: PxVolVolState) => void;
  disabled: boolean;
}

export function PxVolSection({
  pxVolSpeed,
  pxVolVol,
  onSpeedClick,
  onVolClick,
  disabled,
}: Props) {
  return (
    <Box sx={{ ...PERIOD_GRID_SX, mt: 1 }}>
      {/* Row label */}
      <SectionLabel
        id="pxVolStates"
        active={pxVolSpeed != null || pxVolVol != null}
      />
      {/* Layer 1 — price speed (columns 1-5). */}
      {PX_VOL_SPEED_OPTIONS.map((o, col) => (
        <Chip
          key={o.key}
          label={o.label}
          size="small"
          clickable
          disabled={disabled}
          color={pxVolSpeed === o.key ? "primary" : "default"}
          variant={pxVolSpeed === o.key ? "filled" : "outlined"}
          onClick={() => onSpeedClick(o.key)}
          sx={{ gridColumn: col + 1, ...PERIOD_CHIP_SX }}
        />
      ))}
      {/* Layer 2 — trading-amount state (columns 1-3), always visible. */}
      {PX_VOL_VOL_OPTIONS.map((o, col) => (
        <Chip
          key={o.key}
          label={o.label}
          size="small"
          clickable
          disabled={disabled}
          color={pxVolVol === o.key ? "primary" : "default"}
          variant={pxVolVol === o.key ? "filled" : "outlined"}
          onClick={() => onVolClick(o.key)}
          sx={{ gridColumn: col + 1, ...PERIOD_CHIP_SX }}
        />
      ))}
    </Box>
  );
}

interface CaptionProps {
  pxVolSpeed: PxVolSpeed | null;
  pxVolVol: PxVolVolState | null;
  pxVolRuns: PxVolRun[];
  /** Whether the "pxvol" extras rows have landed (the caption's source
   *  line switches from the client-side replication notice). */
  priceVsAmtLoaded: boolean;
}

export function PxVolCaption({
  pxVolSpeed,
  pxVolVol,
  pxVolRuns,
  priceVsAmtLoaded,
}: CaptionProps) {
  return (
    <Typography
      variant="caption"
      color="text.secondary"
      sx={{ display: "block", mt: 0.5, fontSize: "0.65rem" }}
    >
      {pxVolSpeed == null || pxVolVol == null
        ? "pick both a price speed and an amount state to shade matching dates"
        : `${PX_VOL_SPEED_OPTIONS.find((o) => o.key === pxVolSpeed)?.label} × ${
            PX_VOL_VOL_OPTIONS.find((o) => o.key === pxVolVol)?.label
          } — ${pxVolReading(pxVolSpeed, pxVolVol)} · ${
            pxVolRuns.length === 0
              ? "no matching dates"
              : `${pxVolRuns.reduce((a, r) => a + r.days, 0)} matching ${
                  pxVolRuns.reduce((a, r) => a + r.days, 0) === 1 ? "day" : "days"
                } in ${pxVolRuns.length} run${pxVolRuns.length === 1 ? "" : "s"}` +
                  ` · longest ${Math.max(...pxVolRuns.map((r) => r.days))}d` +
                  ` · last ${pxVolRuns[pxVolRuns.length - 1].endDate}`
              } · thresholds t ±2.0σ (sharp) / ±1.26σ (slow) · z_amt(log-level) +2.0 / −0.92 · source: ${
                priceVsAmtLoaded
                  ? "analysis.mov_ave_price_vs_amt"
                  : "client-side replication (registry rows not loaded)"
              }`}
    </Typography>
  );
}
