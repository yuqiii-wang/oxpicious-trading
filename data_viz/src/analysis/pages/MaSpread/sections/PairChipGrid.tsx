/**
 * PairChipGrid — the panel's three pair-chip sections: Simple MA (2 rows)
 * + Exponential MA (2 rows) + the Trading Amt/MA row. Every chip fills
 * its period grid cell; the layout constants live in ../constants.
 *
 * Price and EMA chips are always loaded with the default chart. Amt/MA
 * chips belong to the "amt" extras group: before it loads the row shows
 * PLACEHOLDER chips whose click loads the group (freezing every control
 * until it resolves) and selects the pair as soon as the data lands.
 * Clicking an already-active Amt/MA chip toggles it off ("unclick") and
 * recovers the normal OHLC price style by falling back to Price/MA5 —
 * that click logic lives in the panel (onChipClick / onAmtPlaceholderClick).
 */
import { Box, Chip } from "@mui/material";
import type { MovAveSpreadPairSeries } from "@shared/types";
import { SectionLabel } from "../SectionLabel";
import {
  LONG_MA_ORDER,
  LONG_EMA_ORDER,
  PERIOD_GRID_SX,
  PERIOD_CHIP_SX,
} from "../constants";

interface Props {
  pairs: MovAveSpreadPairSeries[];
  /** `${kind}-${ma_short}-${ma_long}` → index in pairs. */
  pairIndexMap: Map<string, number>;
  selectedIdx: number;
  /** Whether the row's selected pair is an Amt/MA pair (bold row label). */
  amtPairSelected: boolean;
  disabled: boolean;
  /** Whether the "amt" extras group has landed (real chips vs
   *  placeholders). */
  amtLoaded: boolean;
  /** A pair chip click — receives the pair + its index + whether it is
   *  the active one (the panel owns the amt fetch-through / toggle-off
   *  logic). */
  onChipClick: (pair: MovAveSpreadPairSeries, idx: number, active: boolean) => void;
  /** An Amt/MA placeholder chip click (the group is not loaded yet) —
   *  col is the row column index (the amt series append after the 18
   *  defaults in this row's column order). */
  onAmtPlaceholderClick: (col: number) => void;
}

export function PairChipGrid({
  pairs,
  pairIndexMap,
  selectedIdx,
  amtPairSelected,
  disabled,
  amtLoaded,
  onChipClick,
  onAmtPlaceholderClick,
}: Props) {
  // Render a single pair chip (used in the 2-row pair grids). The chip
  // fills its grid column: display:flex overrides MUI's default
  // inline-flex so width:100% takes effect, and the label is centered.
  const renderPairChip = (pair: MovAveSpreadPairSeries, idx: number) => {
    const active = idx === selectedIdx;
    return (
      <Chip
        label={pair.pair_label}
        clickable
        size="small"
        disabled={disabled}
        color={active ? "primary" : "default"}
        variant={active ? "filled" : "outlined"}
        onClick={() => onChipClick(pair, idx, active)}
        sx={PERIOD_CHIP_SX}
      />
    );
  };

  return (
    <>
      {/* ---- Simple MA section ---- */}
      <SectionLabel id="pairsSimple" standalone suffix=" — click to switch" />
      <Box sx={PERIOD_GRID_SX}>
        {/* Price row (ma_short = 0): one chip per long-MA column.
            Always clickable — selecting one recovers the normal OHLC
            price style (exits the amt-envelope view). */}
        {LONG_MA_ORDER.map((maLong, col) => {
          const idx = pairIndexMap.get(`price-0-${maLong}`);
          return (
            <Box key={`price-${col}`} sx={{ gridColumn: col + 1 }}>
              {idx != null && renderPairChip(pairs[idx], idx)}
            </Box>
          );
        })}
        {/* MA5 row (ma_short = 5): no MA5/MA5 pair — col 0 left empty.
            Always clickable. */}
        {LONG_MA_ORDER.map((maLong, col) => {
          if (maLong === 5) {
            return <Box key={`ma5-empty-${col}`} sx={{ gridColumn: col + 1 }} />;
          }
          const idx = pairIndexMap.get(`price-5-${maLong}`);
          return (
            <Box key={`ma5-${col}`} sx={{ gridColumn: col + 1 }}>
              {idx != null && renderPairChip(pairs[idx], idx)}
            </Box>
          );
        })}
      </Box>

      {/* ---- Exponential MA section ---- */}
      <SectionLabel id="pairsEma" standalone mt={1} suffix=" — click to switch" />
      <Box sx={PERIOD_GRID_SX}>
        {/* Price/EMA row (ma_short = 0): one chip per long-EMA column. */}
        {LONG_EMA_ORDER.map((emaLong, col) => {
          const idx = pairIndexMap.get(`ema-0-${emaLong}`);
          return (
            <Box key={`ema-price-${col}`} sx={{ gridColumn: col + 1 }}>
              {idx != null && renderPairChip(pairs[idx], idx)}
            </Box>
          );
        })}
        {/* EMA6/EMA row (ma_short = 6): no EMA6/EMA6 pair — col 0 empty. */}
        {LONG_EMA_ORDER.map((emaLong, col) => {
          if (emaLong === 6) {
            return <Box key={`ema6-empty-${col}`} sx={{ gridColumn: col + 1 }} />;
          }
          const idx = pairIndexMap.get(`ema-6-${emaLong}`);
          return (
            <Box key={`ema6-${col}`} sx={{ gridColumn: col + 1 }}>
              {idx != null && renderPairChip(pairs[idx], idx)}
            </Box>
          );
        })}
      </Box>

      {/* ---- Trading Amt/MA section ---- */}
      <Box sx={{ ...PERIOD_GRID_SX, mt: 1 }}>
        {/* Row label */}
        <SectionLabel
          id="tradingAmt"
          active={amtPairSelected}
          suffix={
            disabled && !amtLoaded
              ? " — loading…"
              : !amtLoaded
                ? " — click an Amt/MA chip to load"
                : ""
          }
        />
        {LONG_MA_ORDER.map((maLong, col) => {
          const idx = pairIndexMap.get(`amt--1-${maLong}`);
          if (idx == null && !amtLoaded) {
            // Placeholder chip — the "amt" extras group isn't loaded
            // yet. Clicking it fetches the group (freezing every
            // control until it resolves) and selects the pair as soon
            // as it lands (amt series append after the 18 defaults, in
            // this row's column order).
            return (
              <Box key={`amt-${col}`} sx={{ gridColumn: col + 1 }}>
                <Chip
                  label={`Amt/MA${maLong}`}
                  size="small"
                  clickable
                  disabled={disabled}
                  color="default"
                  variant="outlined"
                  onClick={() => onAmtPlaceholderClick(col)}
                  sx={PERIOD_CHIP_SX}
                />
              </Box>
            );
          }
          return (
            <Box key={`amt-${col}`} sx={{ gridColumn: col + 1 }}>
              {idx != null && renderPairChip(pairs[idx], idx)}
            </Box>
          );
        })}
      </Box>
    </>
  );
}
