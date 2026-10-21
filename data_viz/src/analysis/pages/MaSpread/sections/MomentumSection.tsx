/**
 * MomentumSection — the lookback-adaptive momentum row beneath RSI Trends
 * (analysis.mov_ave_momentum): the two fixed anchors (5d / 20d) plus the
 * three volatility-adaptive short/mid/long terms whose lookback n is
 * derived per (code, market regime) via equal-expected-move targeting
 * over the regime slice's median EWMA vol. The adaptive chips spell their
 * CURRENT effective lookback (latest day, e.g. "short (14d)") once the
 * auto-loaded group lands. Any pick replaces the amt bars with the picked
 * terms' lines on the offset right axis (below RSI Trends / Slopes /
 * Curvatures in the slot's precedence); the caption spells the latest
 * day's effective lookbacks + the regime they were derived for.
 */
import { Box, Chip, Typography } from "@mui/material";
import { REGIME_LABELS } from "@/shared/charts/regimeBands";
import type { MovAveSpreadMomentumDay } from "@shared/types";
import { SectionLabel } from "../SectionLabel";
import {
  MOMENTUM_TERMS,
  PERIOD_GRID_SX,
  PERIOD_CHIP_SX,
  momentumChipLabel,
} from "../constants";
import type { MomentumTerm } from "../chartOption";

interface Props {
  momentumTerms: MomentumTerm[];
  onToggleTerm: (term: MomentumTerm) => void;
  disabled: boolean;
  /** The "momentum" group's auto-load is still in flight (label suffix). */
  loadingMomentum: boolean;
  momentumRows: MovAveSpreadMomentumDay[] | undefined;
}

export function MomentumSection({
  momentumTerms,
  onToggleTerm,
  disabled,
  loadingMomentum,
  momentumRows,
}: Props) {
  return (
    <>
      <Box sx={{ ...PERIOD_GRID_SX, mt: 1 }}>
        {/* Row label */}
        <SectionLabel
          id="momentum"
          active={momentumTerms.length > 0}
          suffix={loadingMomentum ? " — loading…" : ""}
        />
        {MOMENTUM_TERMS.map((term, col) => (
          <Chip
            key={term}
            label={momentumChipLabel(term, momentumRows)}
            size="small"
            clickable
            disabled={disabled}
            color={momentumTerms.includes(term) ? "primary" : "default"}
            variant={momentumTerms.includes(term) ? "filled" : "outlined"}
            onClick={() => onToggleTerm(term)}
            sx={{ gridColumn: col + 3, ...PERIOD_CHIP_SX }}
          />
        ))}
      </Box>
      {momentumTerms.length > 0 &&
        momentumRows != null &&
        momentumRows.length > 0 &&
        (() => {
          const last = momentumRows.at(-1)!;
          const vol =
            last.vol_regime != null
              ? `σ ${(last.vol_regime * 100).toFixed(2)}%/d · `
              : "";
          return (
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block", mt: 0.5, fontSize: "0.65rem" }}
            >
              momentum = price[t]/price[t−n] − 1 · latest {last.date}:{" "}
              {REGIME_LABELS[last.regime]} ({vol}
              short {last.lb_short}d · mid {last.lb_mid}d · long{" "}
              {last.lb_long}d — lookbacks adapt to the regime's median
              volatility; hover the chart for per-date lookbacks)
            </Typography>
          );
        })()}
    </>
  );
}
