/**
 * SignalOverlayPanel — the Recent Movements code-trend's CURVE overlay
 * panel: the code's continuous depth-response curves mu(s) = mu_center
 * + slope * (s - s_center) per regime (analysis_signals.signal_curves,
 * latest snapshot), with the 1/2/3/5/10% tail guides and each cell's
 * own operating point — the 2026-10 depth-response formulation.
 * (The retired THRESHOLD bars view was removed 2026-10-05; the curves
 * subsume it.)
 *
 * Data: GET /api/live-data/trading-signals/curves.
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
import {
  fetchSignalCurves,
  type SignalCurve,
} from "@/lib/api-client";
import { stripCodeSuffix } from "@/ui/OhlcChart/codeTrendSources";
import {
  buildSignalCurveOption,
  SIGNAL_TYPE_LABELS,
} from "./signalOverlayOption";

export interface SignalOverlayPanelProps extends BaseChartProps {
  secType: "index" | "etf" | "stock";
  code: string;
}

export default function SignalOverlayPanel({
  secType,
  code,
  ...base
}: SignalOverlayPanelProps) {
  const themeMode = useChartThemeMode();
  const [curves, setCurves] = useState<SignalCurve[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tag, setTag] = useState<string | null>(null);
  const [rsiWindow, setRsiWindow] = useState<number | null>(null);
  const [side, setSide] = useState<"bottom" | "top">("bottom");

  const bare = stripCodeSuffix(code);

  // Fetch the code's curves (cancelled-guard effect).
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchSignalCurves(secType, bare)
      .then((r) => {
        if (!cancelled) {
          setCurves(r.curves);
          setLoading(false);
        }
      })
      .catch((e: unknown) => {
        if (!cancelled) {
          setError(String(e));
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [secType, bare]);

  // The signal TAGS the curves carry (the indicator families — only
  // mov_rsi exists today) and, scoped to the selected tag, the windows.
  const tags = useMemo(
    () => [...new Set(curves.map((cv) => cv.signal_type))].sort(),
    [curves],
  );
  const tagged = useMemo(
    () => (tag == null ? [] : curves.filter((cv) => cv.signal_type === tag)),
    [curves, tag],
  );
  const windows = useMemo(
    () => [...new Set(tagged.map((cv) => cv.rsi_window))].sort((a, b) => a - b),
    [tagged],
  );

  // Defaults: first tag, then rsi14 within it when present.
  useEffect(() => {
    if (tags.length === 0) {
      setTag(null);
      return;
    }
    if (tag == null || !tags.includes(tag)) setTag(tags[0]!);
  }, [tags, tag]);
  useEffect(() => {
    if (windows.length === 0) {
      setRsiWindow(null);
      return;
    }
    if (rsiWindow == null || !windows.includes(rsiWindow)) {
      setRsiWindow(windows.includes(14) ? 14 : windows[0]!);
    }
  }, [windows, rsiWindow]);

  const option = useMemo(() => {
    if (tag == null || rsiWindow == null) return null;
    const cell = tagged.filter(
      (cv) => cv.rsi_window === rsiWindow && cv.side === side,
    );
    if (cell.length === 0) return null;
    return buildSignalCurveOption(curves, tag, rsiWindow, side, themeMode);
  }, [curves, tagged, tag, rsiWindow, side, themeMode]);

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
            label="Signal"
            value={tag ?? ""}
            onChange={(e) => setTag(e.target.value)}
            sx={{ minWidth: 96 }}
          >
            {tags.map((t) => (
              <MenuItem key={t} value={t} dense>
                {SIGNAL_TYPE_LABELS[t] ?? t}
              </MenuItem>
            ))}
          </TextField>
          <TextField
            select
            size="small"
            label="RSI window"
            value={rsiWindow ?? ""}
            onChange={(e) => setRsiWindow(Number(e.target.value))}
            sx={{ minWidth: 110 }}
          >
            {windows.map((w) => (
              <MenuItem key={w} value={w} dense>
                RSI {w}
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
        </Stack>
        {snapshot && (
          <span style={{ fontSize: 11, opacity: 0.7 }}>
            snapshot {snapshot}
          </span>
        )}
      </Stack>
      <BaseChart
        variant="bare"
        loading={loading}
        error={error ? `Failed to load signal curves: ${error}` : null}
        option={option}
        emptyText="No depth-response curves for this code yet"
        height={260}
        {...base}
      />
    </Stack>
  );
}
