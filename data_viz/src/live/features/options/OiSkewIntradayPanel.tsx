/**
 * OiSkewIntradayPanel — the Live "Options" tab's plot: underlying spot
 * (5-min closes, solid) vs the OI-weighted moneyness skewness price
 * (dashed mean + per-expiry curves, width ∝ OI), mirroring the dataviz
 * Open Interests tab's skew-over-time chart with the x-axis switched
 * from daily dates to 5-min times.
 *
 * Data arrives precomputed: bars + series from /api/live-options (the
 * route spawns python -m live.options_intraday_skewness on demand when
 * the series is missing or stale), so this component only renders —
 * no in-browser skew math.
 */
import { useMemo } from "react";
import { BaseChart, useChartThemeMode } from "@/shared/charts/base-chart";
import type { AiAskSpec } from "@/shared/ai-ask";
import type { LiveOptionsOiSkewResponse } from "@shared/types";
import { buildOiSkewIntradayOption } from "./oiSkewIntradayOption";

interface Props {
  resp: LiveOptionsOiSkewResponse | null;
  /** Spot unit label — "yuan" (ETF targets) or "points" (index targets). */
  unit: string;
  loading: boolean;
  error: string | null;
  /** Freeze the plot behind the full-card spinner: set while the response on
   *  screen belongs to a DIFFERENT underlying/date than the one selected, or
   *  while a compute / yday-ref process runs — the curves would otherwise
   *  keep showing the previous selection with only a corner spinner. */
  freeze?: boolean;
  /** Subtitle shown while frozen (the default subtitle describes the stale
   *  response and would name the wrong underlying). */
  freezeSubtitle?: string;
}

export default function OiSkewIntradayPanel({
  resp,
  unit,
  loading,
  error,
  freeze = false,
  freezeSubtitle,
}: Props) {
  const themeMode = useChartThemeMode();

  const option = useMemo(
    () => buildOiSkewIntradayOption(resp, themeMode, unit),
    [resp, themeMode, unit],
  );

  const underlying = resp?.underlying_code ?? "";
  const date = resp?.date ?? "";
  const hasData = (resp?.bars.length ?? 0) > 0;

  const subtitle = hasData
    ? `${date} · 5-min bars · OI base = ${resp?.snapshot_date ?? "—"} daily snapshot + day volume`
    : "No data for this underlying / date";

  const aiAskSpec = useMemo<AiAskSpec>(
    () => ({
      intro:
        `Intraday OI-weighted moneyness skewness of ${underlying} on ${date} — ` +
        "the Live sibling of the dataviz Open Interests tab's skew chart, with the " +
        "x-axis switched from daily dates to 5-minute bars. Solid = underlying spot " +
        "(5-min closes); dashed = OI-weighted moneyness skew price S × E[M] where " +
        "E[M] = Σ(OI·K/S)/Σ(OI) per expiry group (line width ∝ the group's OI; the " +
        "thick dashed line blends all groups); the shade band is spot↔skew. Open " +
        "interest per bar is ESTIMATED as the previous trading day's daily OI plus " +
        "the day-cumulative traded volume (an upper bound — closing trades are not " +
        "netted out). Indication: skew riding above spot = OI parked overhead " +
        "(supply); below spot = support beneath.",
      instruments: underlying ? [{ code: underlying }] : [],
      window: { start: date, end: date, granularity: "intraday" },
      series: [
        { name: "Underlying Spot", unit, description: "5-minute close of the option's underlying" },
        {
          name: "Skew (OI-wtd mean)",
          unit,
          description: "spot rebased by the all-expiry OI-weighted mean moneyness (sentinel expiry 9998-12-31 rows)",
        },
        {
          name: "Skew <expiry>",
          unit,
          description: "per-expiry-group skew price; line width encodes the group's OI",
        },
      ],
      state: { date, snapshotDate: resp?.snapshot_date ?? null },
      notes: [
        "The series is computed server-side (python -m live.options_intraday_skewness) into live.options_intraday_skewness; the route spawns it on demand when missing/stale.",
        "SSE contracts carry OI base 0 (no daily per-contract OI source) so their intraday OI is traded volume only; SZSE/CFFEX contracts hold the flat prev-day daily OI.",
      ],
    }),
    [underlying, date, unit, resp?.snapshot_date],
  );

  return (
    <BaseChart
      title="OI-weighted Moneyness Skew · Intraday (5-min)"
      subtitle={freeze && freezeSubtitle ? freezeSubtitle : subtitle}
      aiAsk={hasData && !freeze ? aiAskSpec : null}
      height={380}
      loading={loading || freeze}
      error={error ? `Failed to load intraday OI skew: ${error}` : null}
      emptyText="No data for this underlying / date"
      option={freeze ? null : option}
    />
  );
}
