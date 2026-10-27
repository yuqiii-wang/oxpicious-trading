/**
 * ruleParams — the PURE readers of a rule cell's rule_params payload
 * (the shared vocabulary of the rules-era panels): the cell's display
 * key and the breach-bar word decoded from the family's bar_def.
 * Pure .ts (no component) so the panels stay fast-refresh clean.
 */
import type { SignalRule } from "@/lib/api-client";

export const ruleCellKey = (r: SignalRule): string =>
  `${r.signal_sub_type}|${r.trend}|${r.regime_state}|${r.action}`
  + `|${r.is_pe_boosted ? "pe" : "plain"}`
  + `|${r.is_ohlcv_boosted ? "boost" : "plain"}`;

const fmtPct = (v: number): string => {
  const s = (v * 100).toFixed(v * 100 % 1 === 0 ? 0 : 2).replace("-", "−");
  return s;
};

/** Plain number word (no ×100 — for values already in display units:
 *  the hedge_pairs bars carry pct/σ/ρ units natively). */
const fmtNum = (v: number): string =>
  Number.isInteger(v) ? String(v) : String(v).replace("-", "−");

/** The is_pe_boosted axis's gate suffix (the shared variants module's
 *  bar_def.pe_gate — the rebound study's capitulation barbell /
 *  outside-cheap vocabulary; any family's bar word carries it). */
function peGateSuffix(pe: unknown): string {
  if (!pe || typeof pe !== "object") return "";
  const rec = pe as Record<string, unknown>;
  const rule = String(rec.rule ?? "");
  return rule === "extremes"
    ? ` · PE ≤${fmtNum(Number(rec.cheap_below))} or ≥${
      fmtNum(Number(rec.rich_above))}`
    : rule === "not_cheap"
      ? ` · PE ≥${fmtNum(Number(rec.min))}`
      : " · PE gate";
}

/** The is_ohlcv_boosted axis's gate suffix (bar_def.ohlcv_gate — the
 *  turnover-climax confirmation: the day's amount ≥ the min grade ×
 *  its 255d MA). */
function ohlcvGateSuffix(g: unknown): string {
  if (!g || typeof g !== "object") return "";
  const rec = g as Record<string, unknown>;
  const min = typeof rec.min_code === "number" ? rec.min_code : null;
  return min == null ? " · AMT boost"
    : min >= 2 ? " · AMT ≥2.5× climax"
      : " · AMT ≥1.5× heavy";
}

/** The breach-bar display word from the cell's rule_params.bar_def —
 *  each family's own bar vocabulary, read defensively (unknown kinds
 *  fall back to the raw def), plus the shared variant-axes' gate
 *  suffixes. Pure; exported for reuse/tests. */
export function ruleBarLabel(barDef: Record<string, unknown> | undefined):
  string {
  if (!barDef || typeof barDef !== "object") return "—";
  return baseBarWord(barDef)
    + peGateSuffix(barDef.pe_gate)
    + ohlcvGateSuffix(barDef.ohlcv_gate);
}

/** The family bar word without the shared variant suffixes (the
 *  ruleBarLabel wrapper appends those from bar_def.pe_gate /
 *  .ohlcv_gate on every kind). */
function baseBarWord(barDef: Record<string, unknown>): string {
  const kind = String(barDef.kind ?? "");
  const num = (k: string): number | null => {
    const v = barDef[k];
    return typeof v === "number" ? v : null;
  };
  switch (kind) {
    case "rolling_quantile": {
      const tail = num("tail_pct");
      return tail == null ? "own tail quantile"
        : `own ${tail}% tail · ${String(barDef.window_days ?? "")}d`;
    }
    case "fixed_sigma": {
      const k = num("k");
      return k == null ? "±kσ" : `±${k}σ`;
    }
    case "cross_excess": {
      const bar = num("min_below_gap");
      return bar == null ? "cross excess bar"
        : `excess ≤ ${fmtPct(bar)}%`;
    }
    case "gap_pct": {
      const below = num("below");
      return below == null ? "gap bar" : `gap ≤ ${fmtPct(below)}%`;
    }
    case "episode_gap": {
      const gap = num("max_gap_days");
      return gap == null ? "retest window" : `retest gap ≤ ${gap}d`;
    }
    case "wall_dist_pct": {
      // the wall distances arrive in PERCENT units (put_dist/call_dist
      // are (price/wall - 1) x 100) — plain numbers, never x100
      const below = num("below");
      const above = num("above");
      if (below != null) return `dist ≤ ${fmtNum(below)}%`;
      if (above != null) return `dist ≥ +${fmtNum(above)}%`;
      return "wall breach";
    }
    case "corr_slope_interaction":
    case "corr_slope_z_interaction": {
      const corr = num("corr_below");
      const slope = num("slope5_gt");
      const z = num("z252_gt");
      const parts = [
        corr != null ? `corr < ${corr}` : null,
        slope != null ? `slope > +${fmtPct(slope)}%` : null,
        z != null ? `z > ${z}` : null,
      ].filter((p): p is string => p !== null);
      return parts.length > 0 ? parts.join(" & ") : "axis interaction";
    }
    case "corr_cross_up": {
      const through = num("through");
      const witness = num("witness_sessions");
      return `corr ≥ ${through ?? "−0.5"}`
        + (witness != null ? ` · witness ${witness}d` : "");
    }
    // the hedge_pairs trio (2026-10-24; the tail grid widened and the
    // BOTH-WINDOWS union cell landed 2026-10-25): all THREE curves
    // (both legs + the broad market) in their own adaptive RSI tails
    // the same day — bar_def.windows carries EACH surviving window's
    // self-picked tail (the cell fires on either window's resonance;
    // the single window/tail fields decode the pre-union rows); the
    // mirror kind is the blow-off asymmetry check
    case "trio_rsi_tail":
    case "trio_rsi_tail_mirror": {
      const tail = num("tail_pct");
      const win = num("bar_window_days");
      const rsi = num("window");
      const cmp = kind === "trio_rsi_tail_mirror" ? "≥" : "≤";
      const wins = Array.isArray(barDef.windows)
        ? (barDef.windows as Array<Record<string, unknown>>)
          .filter((w) => w != null && typeof w === "object")
          .map((w) => ({
            window: typeof w.window === "number" ? w.window : null,
            tail: typeof w.tail_pct === "number" ? w.tail_pct : null,
          }))
          .filter((w) => w.tail != null)
        : [];
      if (wins.length > 0) {
        return `trio ${wins.map((w) => `RSI(${w.window ?? "?"})${cmp}${fmtNum(w.tail as number)}%`).join(" · ")} tail`
          + (win != null ? ` · ${win}d` : "");
      }
      return tail == null
        ? "trio RSI own tail quantile"
        : `trio RSI(${rsi ?? 14}) ${cmp} own ${fmtNum(tail)}% tail`
          + (win != null ? ` · ${win}d` : "");
    }
    // the hedge_pairs SPREAD sub (2026-10-25): the pair's long-short
    // spread curve (leg A long / leg B short, no market curve) vs its
    // own adaptive RSI tail — tail_pct is the WINNING candidate's tail
    // (the bar the measured gate self-picked for the pair); the mirror
    // kind is the asymmetry check
    case "spread_rsi_tail":
    case "spread_rsi_tail_mirror": {
      const tail = num("tail_pct");
      const win = num("bar_window_days");
      const rsi = num("window");
      return tail == null
        ? "spread RSI own tail quantile"
        : `spread RSI(${rsi ?? 14}) ${
          kind === "spread_rsi_tail_mirror" ? "≥" : "≤"
        } own ${fmtNum(tail)}% tail`
          + (win != null ? ` · ${win}d` : "");
    }
    // hedge_pairs (2026-10-18 market-decoupled rework): the legs'
    // gauge is the Short-Term-Tech signal itself — Wilder RSI vs the
    // leg's OWN adaptive percentile tail (the mov_rsi convention);
    // tail_pct arrives in percent. (The per-code component rules are
    // REAL mov_rsi rows — their bar_def kind "rolling_quantile" is
    // the shared case above.)
    case "hp_entry_legs_rsi": {
      const tail = num("tail_pct");
      const witness = num("witness_window");
      return `legs' RSI ≤ own ${fmtNum(tail ?? 0)}% tail`
        + (witness != null ? ` · paired ≤ ${witness}d` : "");
    }
    // the 2026-10-23 more-triggers graduates II (docs/
    // portfolio_more_triggers_study_2026-10-23.md): the market-free
    // legs pair (the trio without the market witness) + the two
    // ONE-leg cross-washes (a floor breakdown on one leg + an RSI
    // capitulation witness on the other leg / the market)
    case "hp_entry_rsi_legs": {
      const tail = num("tail_pct");
      const witness = num("witness_window");
      return `both legs' RSI ≤ own ${fmtNum(tail ?? 0)}% tail`
        + (witness != null ? ` · paired ≤ ${witness}d` : "");
    }
    case "hp_entry_wash_rsi": {
      const w = num("window");
      const wit = num("witness_window");
      const rsi = num("rsi_tail_pct");
      return `one leg < ${w ?? 120}d floor + other leg RSI ${
        fmtNum(rsi ?? 3)}% lo`
        + (wit != null ? ` · paired ≤ ${wit}d` : "");
    }
    case "hp_entry_wash_mkt1": {
      const w = num("window");
      const wit = num("witness_window");
      const mkt = num("mkt_tail_pct");
      return `one leg < ${w ?? 120}d floor + market RSI ${
        fmtNum(mkt ?? 3)}% lo`
        + (wit != null ? ` · paired ≤ ${wit}d` : "");
    }
    case "hp_exit_mkt_high": {
      const tail = num("tail_pct");
      const witness = num("witness_window");
      return `mkt RSI ≥ own ${fmtNum(tail ?? 0)}% hi tail`
        + (witness != null ? ` · ≤ ${witness}d` : "");
    }
    case "hp_catchup_rsi_hi": {
      const tail = num("tail_pct");
      return tail != null
        ? `lag RSI ≥ own ${fmtNum(tail)}% hi tail`
        : "laggard exhaustion";
    }
    case "hp_harvest": {
      const z = num("ratio_z");
      return z != null ? `|ratio z| ≥ ${fmtNum(z)}` : "ratio extreme";
    }
    // the 2026-10-20 momentum tilt (the harvest's opposite pair,
    // docs/hedge_pairs_spread_rotation_study_2026-10-20.md): at the
    // ratio divergence the drift measured CONTINUATION — the buy side
    // takes the strong leg, the sell side trims the laggard (both
    // legs selected by sign(ratio_z) same day)
    case "hp_mom_tilt": {
      const z = num("ratio_z");
      return z != null
        ? `mom tilt |ratio z| ≥ ${fmtNum(z)}`
        : "momentum tilt";
    }
    case "hp_stop_regime": {
      const broke = num("rho_off_break");
      const hedge = num("hedgeable_rho");
      const witness = num("witness_window");
      return `offset ρ ≥ ${fmtNum(broke ?? 0)}`
        + (hedge != null ? ` · hedgeable ρ ≤ ${fmtNum(hedge)}` : "")
        + (witness != null ? ` within ${witness}d` : "");
    }
    // the 2026-10-19 more-triggers graduates (docs/
    // hedge_pairs_more_triggers_study_2026-10-19.md): the rf washout
    // (both legs' Donchian-floor witnesses; the _mkt twin adds the
    // market's RSI lo-tail witness), the deep hedgeable turn-on, and
    // the weak leg's MA reclaim
    case "hp_entry_wash": {
      const w = num("window");
      const wit = num("witness_window");
      const mkt = num("mkt_tail_pct");
      return `both legs < ${w ?? 120}d floor`
        + (wit != null ? ` · paired ≤ ${wit}d` : "")
        + (mkt != null ? ` · mkt RSI ${fmtNum(mkt)}% lo` : "");
    }
    case "hp_entry_rho50": {
      const rho = num("rho_deep");
      return `offset ρ ≤ ${fmtNum(rho ?? -0.5)}`;
    }
    case "hp_entry_catchup_reclaim": {
      const ma = num("ma_window");
      return `weak leg re-crosses MA${ma ?? 60}`;
    }
    case "vol_ath_escalation": {
      const win = num("ath_window");
      const margin = num("ath_margin_pct");
      const slope = num("slope5_gt");
      const dn = num("downside_window");
      const parts = [
        win != null && margin != null
          ? `vol > ${win}d max +${fmtPct(margin / 100)}%` : null,
        slope != null ? `slope > +${fmtPct(slope)}%` : null,
        dn != null ? `spot < ${dn}d-ago` : null,
      ].filter((p): p is string => p !== null);
      return parts.length > 0 ? parts.join(" & ") : "vol escalation";
    }
    // the panic-peak mirror (ovix_euphoria_peak, 2026-10-19): the same
    // escalation bar with the spot leg ABOVE its anchor — the
    // blow-off-top exit
    case "vol_ath_euphoria": {
      const win = num("ath_window");
      const margin = num("ath_margin_pct");
      const slope = num("slope5_gt");
      const up = num("upside_window");
      const parts = [
        win != null && margin != null
          ? `vol > ${win}d max +${fmtPct(margin / 100)}%` : null,
        slope != null ? `slope > +${fmtPct(slope)}%` : null,
        up != null ? `spot > ${up}d-ago` : null,
      ].filter((p): p is string => p !== null);
      return parts.length > 0 ? parts.join(" & ") : "vol euphoria";
    }
    case "atr_multiple": {
      const k = num("multiple");
      const slope = num("slope5_gt");
      return (k != null ? `ret ≤ −${fmtNum(k)}×ATR` : "ATR breakout")
        + (slope != null ? ` & slope > +${fmtPct(slope)}%` : "");
    }
    // the rebound family (2026-10-26): the drop→rebound→resolution
    // cells — the drop bar (min_drop_pct below the trailing-Wd high)
    // resolved by capit / fail / reclaim; the PE-conditioned twin's
    // gate word rides the shared pe_gate suffix (the is_pe_boosted
    // axis)
    case "rebound_resolution": {
      const d = num("min_drop_pct");
      const win = num("window");
      const word = ({
        capit: "drop",
        fail: "stall",
        reclaim: "reclaim",
      } as Record<string, string>)[String(barDef.resolution ?? "")];
      return `${word ?? "rebound"} ≥ ${d != null ? fmtNum(d) : "5"}%`
        + (win != null ? ` · ${win}d high` : "");
    }
    // the macd family (2026-10-26): the classic signal-line crossover
    // cells — the bar is the EMA9 signal line itself (value = the
    // histogram as pct of close); the word renders the classic spans
    case "macd_cross": {
      const s = num("ema_short");
      const l = num("ema_long");
      const g = num("ema_signal");
      return "signal-line cross"
        + (s != null && l != null && g != null
          ? ` · EMA${fmtNum(s)}/${fmtNum(l)}/${fmtNum(g)}` : "");
    }
    default:
      return kind ? kind : "—";
  }
}
