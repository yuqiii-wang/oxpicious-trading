/**
 * SignalPopulationControls — the shared signal-history POPULATION
 * controls: the OHLCV variant pick (none = the plain record; boost =
 * the OHLCV-boost twin, the same events with w-scaled confidence) and
 * the episode-PASS filter (all = every event; typical = the typical
 * passes' days — buy: the isolated first touch, sell: the re-test;
 * re-entry = the re-entry passes' days — flicker re-crossings /
 * continuations / isolated breakouts, the noisy population). The same
 * two toggles the curve overlay panel carries, applied to the HISTORY
 * population (chart markers + history table filter together); both
 * history surfaces (the live expansion + the analysis Recent
 * Movements panel) render this one control so they cannot drift.
 *
 * The filter itself lives in `useSignalPopulation` — unstamped rows
 * (pre-migration history, live-only days: is_typical null) ride "all"
 * only, so an unrebuilt sec_type keeps its full record until stamped.
 */
import { useMemo, useState } from "react";
import { Stack, ToggleButton, ToggleButtonGroup } from "@mui/material";
import type { TradingSignal } from "@/lib/api-client";
import { SIGNAL_TYPE_LABELS } from "./signalOverlayOption";

export type SignalEpPass = "all" | "typical" | "re-entry";

export interface SignalPopulation {
  /** The OHLCV variant pick: FALSE = plain, TRUE = the boost twin. */
  boost: boolean;
  /** The episode-PASS filter. */
  epPass: SignalEpPass;
  /** The filter applied to a history signals array. */
  apply: (signals: TradingSignal[]) => TradingSignal[];
  setBoost: (boost: boolean) => void;
  setEpPass: (epPass: SignalEpPass) => void;
}

export function useSignalPopulation(): SignalPopulation {
  const [boost, setBoost] = useState(false);
  const [epPass, setEpPass] = useState<SignalEpPass>("all");
  return useMemo<SignalPopulation>(
    () => ({
      boost,
      epPass,
      setBoost,
      setEpPass,
      apply: (signals) =>
        signals.filter((s) => {
          if ((s.is_ohlcv_boosted ?? false) !== boost) return false;
          if (epPass === "all") return true;
          if (s.is_typical === null || s.is_typical === undefined) {
            return false;
          }
          return s.is_typical === (epPass === "typical");
        }),
    }),
    [boost, epPass],
  );
}

export interface SignalPopulationTogglesProps {
  /** The bound population state (the hook's return). */
  population: SignalPopulation;
  /** The filtered event count (the strip's live counter). */
  count: number;
  /** The panel-level SIGNAL pick (the indicator family — mov_rsi /
   *  bollinger) with its available options: when the hosting panel
   *  manages one, the strip leads with its picker and the same pick
   *  drives the curve overlay above the history. Optional — the live
   *  surfaces render the full record and pass nothing (no picker). */
  signalType?: string | null;
  signalTypeOptions?: string[];
  onSignalTypeChange?: (signalType: string) => void;
}

export function SignalPopulationToggles({
  population,
  count,
  signalType,
  signalTypeOptions,
  onSignalTypeChange,
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
      <ToggleButtonGroup
        size="small"
        exclusive
        value={population.boost ? "boost" : "none"}
        onChange={(_, v) => {
          if (v !== null) population.setBoost(v === "boost");
        }}
      >
        <ToggleButton value="none">OHLCV none</ToggleButton>
        <ToggleButton value="boost">OHLCV boost</ToggleButton>
      </ToggleButtonGroup>
      <ToggleButtonGroup
        size="small"
        exclusive
        value={population.epPass}
        onChange={(_, v) => {
          if (v !== null) population.setEpPass(v);
        }}
      >
        <ToggleButton value="all">pass: all</ToggleButton>
        <ToggleButton value="typical">pass: typical</ToggleButton>
        <ToggleButton value="re-entry">pass: re-entry</ToggleButton>
      </ToggleButtonGroup>
      <span style={{ fontSize: 11, opacity: 0.7 }}>
        {count} event{count === 1 ? "" : "s"}
      </span>
    </Stack>
  );
}
