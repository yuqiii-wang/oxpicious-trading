/**
 * Shared buy/sell action chip — THE style every buy/sell text uses:
 * live trading signals, analysis Recent Movements forecast rows, and
 * the Singleton strategy decision table. Styling lives in
 * signalActionStyle.ts (pure helpers); this file is component-only.
 */
import { Chip, useTheme } from "@mui/material";
import { actionChipSx } from "./signalActionStyle";

export interface SignalActionChipProps {
  /** "buy" / "sell" — case-insensitive; anything non-buy renders sell. */
  action: string;
  /** 1..100 confidence — ramps border width + border/text alpha; null /
   *  undefined renders at full intensity (alpha 1, 2px border). */
  confidence?: number | null;
  /** Override the success/error hue (e.g. the Singleton last-day-sell
   *  purple) while keeping the confidence treatment. */
  color?: string;
  /** TRUE = the WAIT record (is_rejection — the owning strategy's gate
   *  declined or the cell is not trading yet): the chip goes NEUTRAL
   *  GREY and the label says WAIT instead of BUY/SELL — a not-yet
   *  condition is documentation, never a call. */
  rejected?: boolean;
  size?: "small" | "medium";
}

/** The shared outlined buy/sell chip. Label = uppercased action
 *  ("BUY"/"SELL") so every consumer reads identically; a wait record
 *  reads "WAIT" in the neutral grey. */
export default function SignalActionChip({
  action,
  confidence = null,
  color,
  rejected = false,
  size = "small",
}: SignalActionChipProps) {
  const theme = useTheme();
  const main = rejected
    ? theme.palette.text.secondary
    : (color ?? undefined);
  return (
    <Chip
      size={size}
      label={rejected ? "WAIT" : action.toUpperCase()}
      variant="outlined"
      sx={actionChipSx(
        rejected ? "neutral" : action,
        rejected ? 100 : (confidence ?? 100),
        theme,
        main,
      )}
    />
  );
}
