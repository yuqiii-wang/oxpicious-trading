"""Comb configuration — SCHEMA ONLY.

The pydantic payload carried inside ``algo_configs.params["comb"]`` (and
threaded through the collector's ``params`` dict). This module owns the
shape, the defaults, the tolerant coercion of stale/legacy rows, and the
config-sourcing precedence — nothing else. No pandas, no compute.

Champion defaults (the 2026-11-17 study's wide-universe winner, skill
``singleton-strategy-tuning`` Parts 10-12): regime-matched 120-row gate +
t-stat weights at the 252-row window + quantile evidence sizing +
vol-state exposure + equity-curve guard; breadth finishing OFF (it was a
keeper only on the mc_pair selection). Every constant is a repo
convention (the 252-row standard year, the 0.25% buy gate, 1-sigma
bands, the K=5 floor) — zero tuned parameters.

CAUTION (skill Part 12, finding 4): the zero-filter diagnostic showed
macd's zeroing measures CONTRARIAN at every window. The per-family
zero-filter exclusion is NOT engine-validated, so it is deliberately not
a config field; if a future study validates it, add it here.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

#: Version tag stamped on params payloads that carry the ``comb`` block.
#: Readers treat a missing tag as version 1 (the pre-comb flat shape).
#: v3 = trainer-produced rows (the Set C comb grid + tuned Set A/B keys
#: written by ``_optm_engine`` under the ft-less config name).
#: v4 = adds the optional ``amplifier`` group (the signal-amplification
#: evidence sizing, default ON — old rows coerce to the default).
PARAMS_VERSION: int = 4

WeightMode = Literal["equal", "mean", "mean_bar", "tstat", "tstat_bar1", "ewma"]


class RegimeGateCfg(BaseModel):
    """Thesis-matched regime gate (BUY-only; SELLs always pass).

    TREND families (ma_cross, macd, rf_break) only BUY above the
    window-SMA; REV families (mov_rsi, bollinger, momentum, rebound,
    crowdedness) only BUY below it. The thesis sets are code constants
    (transforms), not config.
    """

    enabled: bool = True
    window: int = Field(default=120, ge=2)


class WeightsCfg(BaseModel):
    """Self-adaptive family weights from trailing completed-event quality.

    - ``tstat`` (default): w ∝ max(t, 0), t = μ·√n/σ — one statistic
      implements both the weight and the zero filter (t ≤ 0 → 0).
    - ``mean`` / ``mean_bar``: w ∝ max(μ, 0) / max(μ − bar, 0); the bar
      is the 0.25% buy gate used as a turnover throttle.
    - ``tstat_bar1``: w ∝ max(t − 1, 0) — only more significant than the
      repo's pooled floor earns weight (bar fixed at 1 by convention).
    - ``ewma``: streaming gap-decayed Welford moments (halflife rows),
      w ∝ max(μ/σ, 0) — the defensive low-drawdown variant.
    - ``equal``: the pre-comb baseline blend.
    """

    mode: WeightMode = "tstat"
    window_rows: int = Field(default=252, ge=5)
    floor_events: int = Field(default=5, ge=1)
    bar: float = Field(default=0.0, ge=0.0)
    halflife_rows: int = Field(default=63, ge=1)


class QuantileCfg(BaseModel):
    """Evidence sizing: an event's size = 0.5 + 0.5 × its recorded bp
    confidence's percentile within its OWN family+code trailing-year
    events (calendar window, the study's measured form)."""

    enabled: bool = True
    window_days: int = Field(default=365, ge=1)
    floor_events: int = Field(default=5, ge=1)


class ToggleCfg(BaseModel):
    """A single on/off switch (constants live in the transform)."""

    enabled: bool


class EquityGuardCfg(BaseModel):
    """2-pass equity-curve guard: BUYs × factor while the strategy's own
    trailing ``lookback_rows``-row pooled PnL slope is negative
    (shifted 1 day). Scales by STRATEGY PERFORMANCE, not position state
    (the position-state form measured worse — skill Part 2 D)."""

    enabled: bool = True
    lookback_rows: int = Field(default=60, ge=1)
    factor: float = Field(default=0.5, gt=0.0, le=1.0)


class AdhocActionsCfg(BaseModel):
    """Ad-hoc action module: the strategy's SELF-PROPOSED actions on bars
    where no family signal fired (first action = the position-aware,
    profit-aware TRIM; the module never buys — SELL-only into strength,
    so it cannot add exposure in falling markets).

    Position unit: lots = total_qty / 100 (one full-confidence BUY).
    Profit state: pnl_ratio = total_pnl / capital_deployed (per-code
    mark-to-market standing, realized + unrealized). The cap is
    profit-aware — a book at a loss or with only little gain keeps a
    LOW position; only a healthy-profit book may run high:

      pnl_ratio < healthy_profit_bar  →  cap = low_cap_lots
      pnl_ratio ≥ healthy_profit_bar  →  cap = healthy_cap_lots

    A trim fires when lots > cap AND the price shows a small rise that
    has stabilized (0 < r5 ≤ rise_max over the K=5 window, close ≥
    SMA5, vol5 ≤ its own trailing vol_ref_window_rows-row median) AND
    min_holding_period / cooldown are respected. The sell fraction
    brings the position back under the cap, capped at max_trim_frac
    ("sell some", never more than half per action); the resulting
    confidence must clear the blend conf_threshold (a natural
    dead-band — near-cap positions don't churn).

    Standing caveat (skill Part 2 D): a position overlay must beat the
    scheme's own no-overlay OOS Calmar before it joins the trainer's
    search space — Set C pins this group OFF until then.
    """

    enabled: bool = True
    rise_window_rows: int = Field(default=5, ge=1)
    rise_max: float = Field(default=0.03, gt=0.0)
    vol_window_rows: int = Field(default=5, ge=2)
    vol_ref_window_rows: int = Field(default=60, ge=2)
    low_cap_lots: float = Field(default=1.0, gt=0.0)
    healthy_cap_lots: float = Field(default=2.0, gt=0.0)
    healthy_profit_bar: float = Field(default=0.05, ge=0.0)
    max_trim_frac: float = Field(default=0.5, gt=0.0, le=1.0)
    cooldown_rows: int = Field(default=5, ge=1)


class RejectCfg(BaseModel):
    """REJECT-tier policy — family-local same-side self-suppression.

    The analysis tier's negative-confidence rows ("do NOT take this
    action at this price", ``engines/reject.py``) carried by the fetch
    as ``rej_*`` evidence columns. When enabled, a family's reject on
    (code, date, side) zeroes that family's OWN same-side
    ``sig_<family>`` on that bar — its failed-cell evidence suppressing
    its own signal. A reject NEVER touches another family's signal and
    NEVER invents a signal of the opposite side.

    CROSS-FAMILY reject offsets are STRUCTURALLY ABSENT (not a mode):
    the 2026-11-18 transfer study (skill singleton-strategy-tuning
    Part 16) measured them harmful — buys of family F exposed to ≥2
    OTHER families in negative trailing state measure +2.90% @71.5%
    vs +1.89% @68.1% unexposed (era-stable; the late era +5.26% @
    81.5%); a cross-family veto would cancel exactly the best buys.

    Default OFF: the reject tier is in its 20-trading-row birth
    probation soft-launch (0 rows live; ~6.7k index-scope cells age in
    from ~2026-12-31), so the module ships as a dormant seam (the
    adhoc-actions precedent). The trainer explores it ONLY when the
    fetched frames actually carry reject rows (availability-gated Set
    C axis, ``space.set_c_comb_grid``).
    """

    enabled: bool = False


class NoActionCfg(BaseModel):
    """NO-ACTION overlay policy — family-local same-side suppression
    of MARKED FIRED days (the reject tier's day-level mirror).

    The analysis tier's day-level marks ("do NOT take this action at
    this price", ``engines/no_action`` →
    ``analysis_signals.no_action_signals``) carried by the fetch as
    ``na_*`` evidence columns. When enabled, a mark on (code, date,
    side) zeroes that family's OWN same-side ``sig_<family>`` on that
    bar — unlike a reject (a CELL-level verdict on a cell that emits
    no trading rows), a mark BY CONSTRUCTION sits on a FIRED positive
    day, so the suppression cancels the marked fired event itself
    (the tier's calibrated claim: boll_squeeze_breakdown marked fired
    buys measured −0.27% vs kept +2.24% aligned blend). A mark NEVER
    touches another family's signal and NEVER invents a signal of the
    opposite side — cross-family offsets are STRUCTURALLY ABSENT (the
    Part-16 transfer study's finding, inherited verbatim).

    Default OFF (the dormant-seam fallback — the schema default stays
    OFF so stale/coerce paths and the trainer's own cell constructors
    are unchanged; the STANDING champion ``DEFAULT_COMB`` enables the
    overlay since 2026-11-18, and a trained row decides through the
    same precedence): the shipped mark
    population is one calibrated rule on one family; the trainer
    explores no-action-ON cells ONLY when the fetched frames carry
    marks (availability-gated Set C axis, ``space.set_c_comb_grid``)
    and an ON winner must beat its OFF twin through the same
    OOS/Calmar/risk funnel.
    """

    enabled: bool = False


class AmplifierCfg(BaseModel):
    """SIGNAL AMPLIFICATION sizing — the OHLCV-anatomy amplifier tier
    (``analysis_signals.signal_amplification_history``) as a per-event
    evidence scale (the U2 quantile-sizing sibling: evidence the
    analysis tier already stamps on the day, not family weighting).

    The fetch carries each bar's per-family amplifier sums as
    ``amp_bp_buy`` / ``amp_bp_sell`` (Σ amplify_bp over that day's
    bound amplifiers for the family+side). The transform scales the
    family's own signed ``sig_<family>`` by
    ``1 + clamp(Σbp / AMP_FACTOR_DIVISOR, ±0.5)`` — +600bp → ×1.3,
    −600bp → ×0.7 (the U3 band convention); non-amplifier days scale
    nothing. The tier's own evidence block is the grounding (700
    evidence-valid configs at index scope; amplifier days measure
    better than the family base — e.g. 000001 momentum breakout days
    Δ +5.05% @ 100% hit) — user-directed default ON 2026-11-18.
    """

    enabled: bool = True


class CombParams(BaseModel):
    """The full comb configuration payload."""

    enabled: bool = True
    regime_gate: RegimeGateCfg = RegimeGateCfg()
    weights: WeightsCfg = WeightsCfg()
    quantile_sizing: QuantileCfg = QuantileCfg()
    vol_state: ToggleCfg = ToggleCfg(enabled=True)
    equity_guard: EquityGuardCfg = EquityGuardCfg()
    breadth: ToggleCfg = ToggleCfg(enabled=False)
    adhoc_actions: AdhocActionsCfg = AdhocActionsCfg()
    reject: RejectCfg = RejectCfg()
    no_action: NoActionCfg = NoActionCfg()
    amplifier: AmplifierCfg = AmplifierCfg()


#: The champion configuration (study Part 10 wide-universe winner),
#: with the NO-ACTION overlay ON (2026-11-18 user-directed: the model
#: config adapts the no-action tier): the standing mixed strategy
#: suppresses the calibrated marked fired events out of the box; a
#: trained algo_configs row still overrides group-wise via
#: resolve_comb precedence (code < DB row < CLI), and the trainer grid
#: keeps constructing its own explicit ON/OFF twin cells — the
#: NoActionCfg schema default stays OFF (the dormant-seam fallback for
#: stale/coerce paths is unchanged).
DEFAULT_COMB: CombParams = CombParams(no_action=NoActionCfg(enabled=True))


def _lenient_group(raw_group: Any, default: BaseModel, group_name: str) -> Any:
    """Validate one config group, falling back to its default on any
    error (a stale row must never crash a load)."""
    if not isinstance(raw_group, dict):
        return default
    try:
        return type(default).model_validate(raw_group)
    except Exception as exc:  # noqa: BLE001 — tolerate ANY stale shape
        logger.warning("[comb] ignoring invalid '%s' group (%s)", group_name, exc)
        return default


def coerce_comb(raw: Any) -> CombParams:
    """Tolerantly build :class:`CombParams` from a params-dict value.

    Accepts None / missing (→ champion defaults), a JSON string, or a
    dict. Unknown keys are dropped, invalid groups fall back group-wise
    to defaults — never raises on a stale row.
    """
    if raw is None:
        return CombParams()
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            logger.warning("[comb] params['comb'] is a non-JSON string; using defaults")
            return CombParams()
    if not isinstance(raw, dict):
        logger.warning("[comb] params['comb'] has unexpected type %s; using defaults",
                       type(raw).__name__)
        return CombParams()
    base = CombParams()
    groups = {
        "regime_gate": base.regime_gate,
        "weights": base.weights,
        "quantile_sizing": base.quantile_sizing,
        "vol_state": base.vol_state,
        "equity_guard": base.equity_guard,
        "breadth": base.breadth,
        "adhoc_actions": base.adhoc_actions,
        "reject": base.reject,
        "no_action": base.no_action,
        "amplifier": base.amplifier,
    }
    fields: dict[str, Any] = {}
    if isinstance(raw.get("enabled"), bool):
        fields["enabled"] = raw["enabled"]
    for name, default in groups.items():
        fields[name] = _lenient_group(raw.get(name), default, name)
    return CombParams(**fields)


def resolve_comb(
    db_params: Optional[dict], cli_overrides: Optional[dict],
) -> CombParams:
    """The ONE place the comb sourcing precedence lives:

    champion defaults (code) < DB row ``params["comb"]`` < CLI overrides.

    ``db_params`` is the algo_configs row for the portfolio strategy_name
    (may be None / lack ``comb`` — old rows). Overrides replace config
    GROUPS wholesale (each group is always written whole — the loader's
    ``build_params`` merge is shallow by design).
    """
    merged: dict[str, Any] = DEFAULT_COMB.model_dump()
    for src in (db_params, cli_overrides):
        if not src:
            continue
        if isinstance(src, str):
            try:
                src = json.loads(src)
            except (TypeError, ValueError):
                logger.warning("[comb] ignoring non-JSON override source")
                continue
        if not isinstance(src, dict):
            continue
        comb_src = src.get("comb", src)
        if isinstance(comb_src, dict):
            for key in merged:
                if key in comb_src:
                    merged[key] = comb_src[key]
    return CombParams.model_validate(merged)


__all__ = [
    "PARAMS_VERSION",
    "AdhocActionsCfg",
    "AmplifierCfg",
    "CombParams",
    "DEFAULT_COMB",
    "EquityGuardCfg",
    "QuantileCfg",
    "RegimeGateCfg",
    "RejectCfg",
    "ToggleCfg",
    "WeightsCfg",
    "coerce_comb",
    "resolve_comb",
]
