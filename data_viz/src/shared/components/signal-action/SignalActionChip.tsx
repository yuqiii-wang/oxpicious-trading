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
  size?: "small" | "medium";
}

/** The shared outlined buy/sell chip. Label = uppercased action
 *  ("BUY"/"SELL") so every consumer reads identically. */
export default function SignalActionChip({
  action,
  confidence = null,
  color,
  size = "small",
}: SignalActionChipProps) {
  const theme = useTheme();
  return (
    <Chip
      size={size}
      label={action.toUpperCase()}
      variant="outlined"
      sx={actionChipSx(action, confidence ?? 100, theme, color)}
    />
  );
}
