/**
 * VolIndexPanel — "Vol Index · 30d Model-Free (VIX-style)".
 *
 * The CBOE-VIX analogue for the SELECTED underlying: the daily 30-day
 * constant-maturity model-free implied-volatility index (100·√variance
 * of the OTM-strip replication off settlement prices — the smile LEVEL,
 * per docs/options_vol_smile_study.md §9). One curve, driven by the
 * page's Underlying selector (store), like every other panel.
 */
import { useEffect, useMemo, useState } from "react";
import { BaseChart, useChartThemeMode } from "@/shared/charts/base-chart";
import type { AiAskSpec } from "@/shared/ai-ask";
import { useStore } from "@/store/filters";
import { fetchOptionsVolIndex } from "@/lib/api-client/options";
import type { VolIndexRow } from "@shared/types";
import { buildVolIndexOption, volIndexUnderlyingLabel } from "./volIndexOption";

export default function VolIndexPanel() {
  const themeMode = useChartThemeMode();
  const underlyingCode = useStore((s) => s.underlyingCode);
  const [rows, setRows] = useState<VolIndexRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!underlyingCode) return;
    let cancelled = false;
    setRows(null);
    setError(null);
    fetchOptionsVolIndex(underlyingCode)
      .then((resp) => {
        if (!cancelled) setRows(resp.rows);
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, [underlyingCode]);

  const option = useMemo(
    () => (rows && rows.length > 0 ? buildVolIndexOption(rows, themeMode) : null),
    [rows, themeMode],
  );

  // AI Ask — the formula + reading guide (former card subtitle) lives in the
  // intro; the card shows only the concise identity line.
  const aiAskSpec = useMemo<AiAskSpec>(
    () => ({
      intro:
        "The CBOE-VIX analogue for the SELECTED underlying: the daily 30-day constant-maturity " +
        "model-free implied-volatility index — 100·√(30-day constant-maturity model-free variance) " +
        "from the OTM-strip replication on settlement prices (CBOE VIX methodology, r=2%) — the " +
        "smile LEVEL of the selected underlying (see docs/options_vol_smile_study.md §9). One " +
        "curve, driven by the page's Underlying selector.",
      instruments: underlyingCode ? [{ code: underlyingCode }] : [],
      series: [
        {
          name: volIndexUnderlyingLabel(underlyingCode),
          unit: "vol pts",
          description: "daily 30-day model-free implied-volatility index (the smile LEVEL)",
        },
      ],
      suggestedQuestions: [
        "Is the 30-day model-free vol index elevated or compressed versus its recent range?",
        "How has the smile level evolved — rising risk pricing or vol crush?",
        "Does the vol index level confirm or diverge from the underlying's recent realized move?",
      ],
      notes: [
        "SZSE ETF legs inherit the known ETF settle-data condition; CFFEX index legs carry the deep history.",
      ],
    }),
    [underlyingCode],
  );

  return (
    <BaseChart
      title="Vol Index · 30d Model-Free (VIX-style)"
      subtitle="30-day model-free vol index (VIX-style) · the smile LEVEL · vol pts"
      aiAsk={aiAskSpec}
      height={340}
      option={option}
      // rows still null and no error → the fetch is in flight (or no
      // underlying selected) — keep the prior centered-spinner behavior.
      loading={rows == null && error == null}
      error={error ? `Failed to load vol index: ${error}` : null}
      emptyText="No vol-index rows for this underlying — the 30-day model-free replication needs settle-priced OTM strips on expiries bracketing 30 days."
    />
  );
}
