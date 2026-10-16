/**
 * SignalPopulationToggles — the shared signal-history control strip:
 * the SIGNAL pick (the indicator family) + the event count + the
 * Analysis Advice button. (The OHLCV none/boost population toggle was
 * REMOVED 2026-10-16 with the boost twins' retirement: the boost's
 * tuned confidence lives on signal_rules.ohlcv_boost_conf, rendered by
 * the rules panel — history carries ONE population per event, so there
 * is nothing left to pick. The episode-PASS toggle was removed earlier
 * the same day.)
 *
 * Applied to the HISTORY population (chart markers + history table
 * together); both history surfaces (the live expansion + the analysis
 * Recent Movements panel) render this one strip so they cannot drift.
 */
import { Stack, ToggleButton, ToggleButtonGroup } from "@mui/material";
import AnalysisAdviceButton from "../analysis-advice";
import { SIGNAL_TYPE_LABELS } from "./families";

export interface SignalPopulationTogglesProps {
  /** The filtered event count (the strip's live counter). */
  count: number;
  /** The panel-level SIGNAL pick (the indicator family — mov_rsi /
   *  bollinger) with its available options: when the hosting panel
   *  manages one, the strip leads with its picker and the same pick
   *  drives the RULES panel above the history. Optional — the live
   *  surfaces render the full record and pass nothing (no picker). */
  signalType?: string | null;
  signalTypeOptions?: string[];
  onSignalTypeChange?: (signalType: string) => void;
  /** The Analysis Advice context — when provided, the strip renders the
   *  shared "A" advice button right after the signal pick (the analysis
   *  Short-Term Tech panel); the live surfaces pass nothing and render
   *  no advice icon. Null signalType (no family picked yet) hides it. */
  advice?: { secType: string; code: string; signalType: string | null };
}

export function SignalPopulationToggles({
  count,
  signalType,
  signalTypeOptions,
  onSignalTypeChange,
  advice,
}: SignalPopulationTogglesProps) {
  const showSignalPick = Boolean(
    signalTypeOptions && signalTypeOptions.length > 0 && onSignalTypeChange,
  );
  return (
    <Stack
      direction="row"
      spacing={1}
      alignItems="center"
      sx={{ flexWrap: "wrap", rowGap: 0.5 }}
    >
      {showSignalPick && (
        <ToggleButtonGroup
          size="small"
          exclusive
          value={signalType ?? ""}
          onChange={(_, v) => {
            if (v !== null) onSignalTypeChange!(v);
          }}
        >
          {signalTypeOptions!.map((t) => (
            <ToggleButton key={t} value={t}>
              {SIGNAL_TYPE_LABELS[t] ?? t}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
      )}
      {advice && advice.signalType != null && (
        <AnalysisAdviceButton
          secType={advice.secType}
          code={advice.code}
          signalType={advice.signalType}
        />
      )}
      <span style={{ fontSize: 11, opacity: 0.7 }}>
        {count} event{count === 1 ? "" : "s"}
      </span>
    </Stack>
  );
}
