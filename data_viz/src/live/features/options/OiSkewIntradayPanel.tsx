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
import { useDocs } from "@/shared/docs";
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

  // Data-gap detection: the API resolves `date` from the underlying's spot
  // table. An empty date means the underlying has NO intraday spot rows at
  // all; a set date with zero bars means that day's spot rows are missing
  // (streamer gap). Either way the skew series cannot exist — the on-demand
  // compute requires spot bars and is never triggered — so say why the
  // chart is empty instead of showing a bare "[No data]".
  const spotGap = hasData
    ? null
    : date === ""
      ? "No intraday spot rows for this underlying — spot table empty (streamer gap; run the streamer backfill)"
      : `No intraday spot bars on ${date} — the skew series needs spot and was not computed (streamer gap)`;

  const subtitle = hasData
    ? `${date} · 5-min bars · OI base = ${resp?.snapshot_date ?? "—"} daily snapshot + day volume`
    : spotGap ?? "No data for this underlying / date";

  const docs = useDocs();
  const aiAskSpec = useMemo<AiAskSpec>(
    () => ({
      intro: docs.text("ui.live.oi-skew", { underlying, date }),
      instruments: underlying ? [{ code: underlying }] : [],
      window: { start: date, end: date, granularity: "intraday" },
      series: docs.series("ui.live.oi-skew", { unit }),
      state: { date, snapshotDate: resp?.snapshot_date ?? null },
      notes: docs.notes("ui.live.oi-skew"),
    }),
    [docs, underlying, date, unit, resp?.snapshot_date],
  );

  return (
    <BaseChart
      title="OI-weighted Moneyness Skew · Intraday (5-min)"
      subtitle={freeze && freezeSubtitle ? freezeSubtitle : subtitle}
      aiAsk={hasData && !freeze ? aiAskSpec : null}
      height={380}
      loading={loading || freeze}
      error={error ? `Failed to load intraday OI skew: ${error}` : null}
      emptyText={spotGap ?? "No data for this underlying / date"}
      option={freeze ? null : option}
    />
  );
}
