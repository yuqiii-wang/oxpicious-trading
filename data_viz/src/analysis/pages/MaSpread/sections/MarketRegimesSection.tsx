/**
 * MarketRegimesSection — the stats.market_regimes daily label's
 * contiguous spans (the market_regime_spans table): calm = the market's
 * quiet background stretches, hot = elevated volatility WITH volume
 * expansion, panic = elevated volatility without it, quiet = volume
 * expansion without price movement. Multi-select among ALL regimes —
 * calm included (its grey shade marks the calm stretches). Each picked
 * regime shades its spans in its palette color (calm grey / hot purple /
 * panic red / quiet blue — the same accents the forecast tables color
 * their regime cells with); the caption reports each pick's span count /
 * days / last end.
 */
import { Box, Chip, Typography } from "@mui/material";
import {
  REGIME_ACCENT_COLORS,
  REGIME_LABELS,
  ALL_REGIMES,
} from "@/shared/charts/regimeBands";
import type { MarketRegime, MarketRegimeSpans } from "@shared/types";
import { SectionLabel } from "../SectionLabel";
import { PERIOD_GRID_SX, PERIOD_CHIP_SX } from "../constants";

interface Props {
  regimePicks: Set<MarketRegime>;
  onToggle: (r: MarketRegime) => void;
  disabled: boolean;
  loading: boolean;
  regimeSpans: Partial<MarketRegimeSpans> | null;
}

export function MarketRegimesSection({
  regimePicks,
  onToggle,
  disabled,
  loading,
  regimeSpans,
}: Props) {
  return (
    <>
      <Box sx={{ ...PERIOD_GRID_SX, mt: 1 }}>
        <SectionLabel
          id="marketRegimes"
          active={regimePicks.size > 0}
          suffix={loading ? " — loading…" : ""}
        />
        {ALL_REGIMES.map((r, col) => (
          <Chip
            key={r}
            label={REGIME_LABELS[r]}
            size="small"
            clickable
            disabled={disabled}
            color={regimePicks.has(r) ? "primary" : "default"}
            variant={regimePicks.has(r) ? "filled" : "outlined"}
            onClick={() => onToggle(r)}
            sx={{
              gridColumn: col + 1,
              ...PERIOD_CHIP_SX,
              ...(regimePicks.has(r)
                ? {
                    color: REGIME_ACCENT_COLORS[r],
                    borderColor: REGIME_ACCENT_COLORS[r],
                  }
                : {}),
            }}
          />
        ))}
      </Box>
      {regimePicks.size > 0 && (
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ display: "block", mt: 0.5, fontSize: "0.65rem" }}
        >
          {regimeSpans == null
            ? "fetching the code's regime spans…"
            : ALL_REGIMES.filter((r) => regimePicks.has(r))
                .map((r) => {
                  const spans = regimeSpans[r] ?? [];
                  const days = spans.reduce((a, sp) => a + sp.spanDays, 0);
                  return `${REGIME_LABELS[r]}: ${
                    spans.length === 0
                      ? "no spans"
                      : `${spans.length} span${spans.length === 1 ? "" : "s"} · ${days}d · last ${spans[spans.length - 1].endDate}`
                  }`;
                })
                .join(" · ") + " · source: stats.market_regimes (vol_z / amt_z vs own trailing 255d, bars ±1.0σ)"}
        </Typography>
      )}
    </>
  );
}
