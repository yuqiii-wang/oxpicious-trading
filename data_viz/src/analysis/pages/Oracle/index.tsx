/**
 * Oracle analysis page (default export).
 *
 * Thin wrapper over the shared codes-page shell
 * (@/analysis/pages/shared/SecCodesPageShell — the PE / Volatility /
 * Market Crowdedness page shape): the shell owns the SecNavShell
 * chrome (sec_type toggle · CodeSearchBar · Refresh +
 * SecClassificationNav), the codes-list fetch, the classification
 * filtering, and the pagination.
 *
 *   • sec_type toggle exposes INDEX only (the pipeline's run scope —
 *     analysis.oracle_signals is structured for etf/index/stock but
 *     runs index only for now; widening the run scope + this one
 *     array lights the other tabs up with no API change).
 *   • One panel per code (OracleCodeGroup): the hindsight-oracle
 *     decision chart (the close curve + the shared buy/sell marker
 *     layer at the forward-window extremum dates), the action-mix
 *     chips, and the latest decisions table — ONE fetchOracleSeries
 *     call per code.
 *
 * The oracle is the HINDSIGHT tier (analysis.oracle_signals, DECOUPLED
 * from the analysis_signals rule tier): v7 grammar — local extremes of
 * the 5-bar window [date-2, date+2] scanned with a 1-day stride, ONE
 * action per side per 5-consecutive-row window (the first candidate
 * claims the side's unit; a more extreme same-side candidate within it
 * upgrades the placement): buy at a local min, sell at a local max;
 * then the GENUINE-EXTREME gate on both sides — a BUY passing the
 * genuine-deep-min test (the drawdown into the pivot from the prior
 * 20 rows' high is 5%+ — a genuine deep min always buys, even when
 * a deeper local min follows within 20 days; a shallower min is a
 * small fluctuation that omits only when a materially deeper level
 * is imminent within those 20 rows), a SELL only at a genuine high
 * (no 5%+ fall from the prior 20 rows' ceiling: a weak bounce is not
 * a high; and no close 5%+ above within the next 20 rows: a higher
 * exit is imminent); then the
 * FAVORABLE-MOVE GRADING — each surviving pivot graded 0-100 by its
 * favorable move on EITHER side (the weighted sum 0.3 x the
 * backward-20-row move into it + 0.7 x the forward-20-row move after
 * it (all windows 20, both directions — user-directed 2026-11-20 "let
 * future look only be 20d not 60d" + 2026-11-21 "also only look back
 * 20d not 60d");
 * each leg ramped 2% no-action floor / 10% full; a zero weighted grade
 * — both legs below the floor — emits nothing), each kept row carrying
 * the next opposite placed pivot as its hindsight swing target. The
 * trail ends 2 bars before the data frontier.
 */
import { invalidateCacheForPrefix } from "@/lib/api-client";
import type { OracleSecType } from "@/lib/api-client";
import SecCodesPageShell from "@/analysis/pages/shared/SecCodesPageShell";
import {
  ORACLE_THEMES_SOURCES,
  fetchOraclePageCodes,
} from "./navSources";
import OracleCodeGroup from "./OracleCodeGroup";

export default function OraclePage() {
  return (
    <SecCodesPageShell
      title="Oracle"
      dataLabel="Oracle"
      backPath="/analysis/signals"
      backLabel="back to signals"
      subtitle={(headerLabel) =>
        `${headerLabel} — the hindsight oracle (analysis.oracle_signals, decoupled from the rule tier): every validated local extreme gets the perfect-foresight action — local extremes of the 5-bar window scanned daily (stride 1d), buy at a local minimum / sell at a local maximum, with one action PER SIDE per 5-day window (a buy and a sell may sit within the same 5 rows; same-side actions sit >= 5 rows apart — the first candidate claims the side's unit, a more extreme same-side candidate within it upgrades the placement); each side then speaks only at a GENUINE EXTREME of its own horizon — a BUY passes the genuine-deep-min test (the drawdown into the pivot from the prior 20 rows' high is 5%+ — a genuine deep min always buys, even when a deeper local min follows within the 20d horizon; a shallower min is a small fluctuation that omits only when a materially deeper level is imminent within those 20 rows), a SELL must be a genuine high (no more than a small fall from the prior 20 rows' ceiling: a weak bounce is not a high; and no close 5%+ above within the next 20 rows: a materially higher exit is imminent — do not sell the waypoint); each surviving pivot is then GRADED 0-100 by its favorable move on EITHER side (buy: biggest rise after / biggest drop before, sell: biggest drop after / biggest rise before — the forward window 20 traded rows (user-directed "let future look only be 20d not 60d"), the backward window unified at 20 too (2026-11-21 "also only look back 20d not 60d"); each leg ramped 2% no-action floor / 10% full and combined as the weighted sum 0.3 x the pre-action leg + 0.7 x the forward leg — the run-up into a top validates it even when the drop after falls short; a zero weighted grade — both legs below the floor — emits nothing; an unrealized frontier window whose backward leg also grades 0 rides "pending"), each kept row carrying the next opposite placed pivot as its hindsight swing target. The trail ends 2 bars before the data frontier.`}
      themesSources={ORACLE_THEMES_SOURCES}
      secTypes={["index"]}
      defaultSecType="index"
      onInvalidateCache={() => {
        invalidateCacheForPrefix("/api/analysis/oracle/");
      }}
      fetchCodes={fetchOraclePageCodes}
      emptyNoun="oracle decisions"
      refreshTooltip="Refresh Oracle themes + codes + chart data (bypass cache)"
      errorPrefix="Oracle data"
      renderCode={(c) => (
        <OracleCodeGroup
          code={c.code}
          name={c.name}
          secType={c.secType as OracleSecType}
        />
      )}
    />
  );
}
