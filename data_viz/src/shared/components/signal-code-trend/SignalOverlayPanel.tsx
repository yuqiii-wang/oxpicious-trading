/**
 * SignalOverlayPanel — the Recent Movements code-trend's CURVE overlay
 * panel: the code's continuous depth-response curves curve(s) =
 * mu_center + interp(fit_curve, s) per regime (analysis_signals.
 * signal_curves, latest snapshot), with the 1/2/3/5/10/15/20/25% tail
 * guides and each cell's own operating point — the 2026-10
 * depth-response formulation over the EPISODE population (the
 * 2026-10-06 tails change: every tail-episode day, episode-weighted;
 * 2026-10-07: the typical/re-entry toggle is BACK as the episode-PASS
 * population pick — the is_typical flags attribute every in-band day
 * to its pass (first-touch / re-entry, continuations included), so
 * each pass's curve now covers the deep tail) — over the per-horizon
 * path bars (next/3d/5d/10d/20d means beside the blend) that show what
 * the curve's one number summarizes. The OHLCV-boost toggle picks the
 * population VARIANT: none = the plain episode-weighted fit; boost =
 * the twin whose statistics are weight-blended by episode x each
 * day's ohlcv score (is_ohlcv_boosted — the population-variant PK).
 * (The retired THRESHOLD bars view was removed 2026-10-05; the curves
 * subsume it.)
 *
 * CONTROLLED from outside: the SIGNAL pick (the indicator family) left
 * this panel 2026-10-11 — it lives in the hosting panel's shared
 * controls strip now and drives the history events below too, so the
 * panel receives it (`signalType`) alongside the pre-fetched curves
 * (`curves` / `curvesLoading` / `curvesError` — the hosting panel's
 * fetch, whose response also feeds the Signal picker's options). The
 * variant + pass arrive the same way (`boost` / `epPass` — the hosting
 * panel's shared population pick). The curve-local Window / side picks
 * remain in here.
 */
import { useEffect, useMemo, useState } from "react";
import {
  MenuItem,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  TextField,
} from "@mui/material";
import {
  BaseChart,
  useChartThemeMode,
} from "@/shared/charts/base-chart";
import type { BaseChartProps } from "@/shared/charts/base-chart";
import type { SignalCurve } from "@/lib/api-client";
import {
  buildSignalCurveOption,
  DEFAULT_WINDOW_BY_TYPE,
  SIGNAL_TYPE_LABELS,
  WINDOW_LABEL_BY_TYPE,
} from "./signalOverlayOption";

export interface SignalOverlayPanelProps extends BaseChartProps {
  /** The population VARIANT: FALSE = the plain episode-weighted fit,
   *  TRUE = the OHLCV-boost twin — CONTROLLED by the hosting panel's
   *  shared population pick. */
  boost: boolean;
  /** The episode-PASS population drawn: "typical" = the first-touch
   *  passes' days (is_typical TRUE), "re-entry" = the re-entry passes'
   *  days (is_typical FALSE) — CONTROLLED by the hosting panel's shared
   *  population pick. */
  epPass: "typical" | "re-entry";
  /** The SIGNAL pick (the indicator family behind the curves) —
   *  CONTROLLED by the hosting panel's shared pick, which drives the
   *  history events below too. Null = nothing chosen yet (no curves and
   *  no history families) → the chart renders its empty state. */
  signalType: string | null;
  /** The code's pre-fetched curves (the hosting panel's fetch — the
   *  same response feeds the Signal picker's options at panel level). */
  curves: SignalCurve[];
  /** The curves fetch's loading / error state (BaseChart placeholders). */
  curvesLoading: boolean;
  curvesError: string | null;
}

export default function SignalOverlayPanel({
  boost,
  epPass,
  signalType,
  curves,
  curvesLoading,
  curvesError,
  ...base
}: SignalOverlayPanelProps) {
  const themeMode = useChartThemeMode();
  const [rsiWindow, setRsiWindow] = useState<number | null>(null);
  const [side, setSide] = useState<"bottom" | "top">("bottom");

  // The variant-scoped curves (the hosting panel's picks select which
  // boost variant AND episode-pass population feed the window picker
  // and the chart).
  const population = useMemo(
    () => curves.filter(
      (cv) => (cv.is_ohlcv_boosted ?? false) === boost
        && (cv.is_typical ?? true) === (epPass === "typical"),
    ),
    [curves, boost, epPass],
  );
  const tagged = useMemo(
    () => population.filter((cv) => cv.signal_type === signalType),
    [population, signalType],
  );
  const windows = useMemo(
    () => [...new Set(tagged.map((cv) => cv.rsi_window))].sort((a, b) => a - b),
    [tagged],
  );

  // The family's default window within the pick (rsi 14 / boll 20) when
  // present.
  useEffect(() => {
    if (windows.length === 0) {
      setRsiWindow(null);
      return;
    }
    if (rsiWindow == null || !windows.includes(rsiWindow)) {
      const def = DEFAULT_WINDOW_BY_TYPE[signalType ?? ""] ?? 14;
      setRsiWindow(windows.includes(def) ? def : windows[0]!);
    }
  }, [windows, rsiWindow, signalType]);

  const option = useMemo(() => {
    if (signalType == null || rsiWindow == null) return null;
    const cell = tagged.filter(
      (cv) => cv.rsi_window === rsiWindow && cv.side === side,
    );
    if (cell.length === 0) return null;
    return buildSignalCurveOption(
      population, signalType, rsiWindow, side, themeMode,
    );
  }, [population, tagged, signalType, rsiWindow, side, themeMode]);

  const snapshot = tagged[0]?.end_date;

  return (
    <Stack spacing={0.5}>
      <Stack
        direction="row"
        spacing={1}
        alignItems="center"
        justifyContent="space-between"
        sx={{ flexWrap: "wrap", rowGap: 0.5 }}
      >
        <Stack direction="row" spacing={1} alignItems="center">
          <TextField
            select
            size="small"
            label="Window"
            value={rsiWindow ?? ""}
            onChange={(e) => setRsiWindow(Number(e.target.value))}
            sx={{ minWidth: 110 }}
          >
            {windows.map((w) => (
              <MenuItem key={w} value={w} dense>
                {WINDOW_LABEL_BY_TYPE[signalType ?? ""] ?? "W"} {w}
              </MenuItem>
            ))}
          </TextField>
          <ToggleButtonGroup
            size="small"
            exclusive
            value={side}
            onChange={(_, v) => {
              if (v !== null) setSide(v);
            }}
          >
            <ToggleButton value="bottom">buy side</ToggleButton>
            <ToggleButton value="top">sell side</ToggleButton>
          </ToggleButtonGroup>
          {/* the SIGNAL + OHLCV-variant + episode-pass picks live in the
              hosting panel's shared controls strip — one pick each drives
              this curve fit AND the history events below it */}
        </Stack>
        {snapshot && (
          <span style={{ fontSize: 11, opacity: 0.7 }}>
            snapshot {snapshot}
          </span>
        )}
      </Stack>
      <BaseChart
        variant="bare"
        loading={curvesLoading}
        error={curvesError ? `Failed to load signal curves: ${curvesError}` : null}
        option={option}
        emptyText={
          signalType != null
            ? `No ${SIGNAL_TYPE_LABELS[signalType] ?? signalType} depth-response curves for this code`
            : "No depth-response curves for this code yet"
        }
        height={430}
        {...base}
      />
    </Stack>
  );
}
