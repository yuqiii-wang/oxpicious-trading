"""The ohlcv boost — the trust-score subsystem of the signal rules
tier (analysis.analysis_signals.engines.boost), EXTRACTED from
rule_base as a dedicated collaborator class (the
architecture-consolidation refactor, 2026-10-15 — the ABC composes it
by delegation; a family opts in through the OHLCV_BOOST class flag).

The boost is a MERGED confidence pass over each trigger's DAY-0
anatomy (the OHLC pattern code, the (vol_state, px_speed) amount /
price-speed cell, the tail depth z past the sub signal's own bar), its
coefficients SELF-ADAPTIVE — derived per run, in code, from the
family's own measured anchor population (no artifact). DISABLED BY
DEFAULT (RuleEntry.OHLCV_BOOST = False). Since 2026-10-16 its ONLY
product is the per-cell trust-weighted confidence base stamped on the
rule rows (``ohlcv_boost_conf`` — the boost-twin history rows are
retired: only confidence is tuned, never the population). The
derivation constants carry the retired ohlcv_boost subsystem's
methodology (ported, then merged 2026-10-15, extracted 2026-10-15).

The math runs on a REAL host pandas frame (the facts are host dicts
past the row-materialization boundary already) — building through the
slow ctor keeps every groupby/map/quantile below off the cudf proxy
(zero fallback lines). One host-numpy boundary per fit (the sanctioned
scipy regression boundary).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import lsq_linear

from _common.df_utils import host_array, safe_columns
from _common.labels import ACTION_CODE, SIDE_CODE

BOOST_MIN_POOL = 60          # per-side anchor floor to derive at all
BOOST_MIN_TRAIN_N = 60       # a G2 fit split's anchor floor
BOOST_MIN_PATTERN_N = 10     # ladder evidence floors (thinner entries
BOOST_MIN_STATE_N = 10       #   are absent; scoring falls back)
BOOST_MIN_CELL_N = 30        #   cell -> marginal -> NEUTRAL 0)
BOOST_YEAR_MIN_N = 10        # a year's anchor floor (retention / G2)
BOOST_YEAR_AGREE = 0.70      # the per-year sign-agreement share
BOOST_BETA_BOUNDS = (0.0, 1.5)
BOOST_GAMMA_BOUNDS = (-1.0, 1.0)
BOOST_TOP_P = 0.80           # the G3 top-quintile cut
BOOST_WEIGHT_FLOOR = 0.5
BOOST_DEPTH_Z_CAP = 2.0
# the day-0 anatomy axes: the OHLC pattern, the amount/price-speed
# cell, the TURNOVER-CLIMAX grade (2026-10-16: the day's trading_amount
# / its 255d MA bucketed 0/1/2 — the buying-climax / capitulation-
# volume literature as a trust axis), and the tail depth z
BOOST_AXIS_KEYS = ("ohlc", "vol", "amt", "rsi")
BOOST_AXIS_COLUMNS = ("eo", "ev", "ea", "er")


class OhlcvBoost:
    """One entry-run's boost collaborator: collects the fired history
    rows' day-0 anchor facts (``note_anchor``), then ``finalize``
    derives the coefficients SELF-ADAPTIVELY from this run's own anchor
    population and stamps the per-cell trust-weighted confidence base
    onto the rule rows — ``ohlcv_boost_conf`` = sum(w*y)/sum(w) over the
    cell's measurable triggers, w = max(1 + score/K, floor), the SAME
    trust-weighted measurement the ship gates certify. Only confidence
    is tuned, never the population — history carries NO boost rows (the
    2026-10-16 twins retirement; the plain rows are the population of
    record, the boost is NEVER a gate). Sides whose derivation fails
    the ship gates (G1 improv / G2 expanding-year OOS / G3
    top-quintile) stamp nothing — a NULL, reported, not forced. A no-op
    when the family did not opt in or the frame carries no day-0
    axes."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        # per-emitted-history-row anchor facts, in lockstep (see
        # run_cells) — finalize's derivation + scoring population
        self.facts: list[dict] = []
        # the day-0 axes ride the family frame? (mov_rsi and bollinger
        # fetch them; the options frames do not — the boost then
        # silently stays off for the run)
        self.axes_ready = False

    def check_axes(self, df: pd.DataFrame) -> bool:
        """Whether the frame carries the day-0 anatomy axes — called
        once per run before fact collection."""
        self.axes_ready = {"pattern", "vol_state", "px_speed"} \
            <= set(safe_columns(df))
        return self.axes_ready

    def note_anchor(self, fact: dict) -> None:
        """Record one fired history row's day-0 anchor facts (the
        run_cells replay appends these in lockstep with the rows)."""
        self.facts.append(fact)

    def finalize(self, rules_rows: list[dict], history_rows: list[dict]) \
            -> tuple[list[dict], list[dict], dict]:
        report: dict = {"enabled": self.enabled, "sides": {}, "cells": 0}
        if not self.enabled or not self.facts:
            report["skipped"] = ("" if self.enabled
                                 else "disabled (family default)")
            return rules_rows, history_rows, report
        # the boost math runs on a REAL host pandas frame (the facts
        # are host dicts past the row-materialization boundary
        # already) — building through the slow ctor keeps every
        # groupby/map/quantile below off the cudf proxy (zero
        # fallback lines)
        real_pd = getattr(pd, "_fsproxy_slow", pd)
        facts = real_pd.DataFrame(self.facts)
        # families whose fetch carries no turnover axis (the options
        # frames): a neutral 0 amt grade — the ladder maps to 0 and
        # retention kills the axis (an honest absence, never a gate)
        if "amt" not in facts.columns:
            facts["amt"] = 0
        pool = facts[np.isfinite(facts["y_bp"])].reset_index(drop=True)
        # the depth z within the (code, sub_type) identity — the same
        # normalization the runtime scoring applies
        pool = self._attach_depth_z(pool)
        coefs: dict[int, dict] = {}
        for side_code in (SIDE_CODE["bottom"], SIDE_CODE["top"]):
            side_pool = pool[pool["side"] == side_code]
            name = "bottom" if side_code == SIDE_CODE["bottom"] else "top"
            if len(side_pool) < BOOST_MIN_POOL:
                report["sides"][name] = {
                    "passed": False, "reason": f"pool {len(side_pool)} "
                    f"< {BOOST_MIN_POOL}",
                }
                continue
            fit = self._fit_boost_side(side_pool)
            g2 = self._boost_g2(side_pool)
            passed = (fit["gates"]["g1"] and fit["gates"]["g3"]
                      and g2["passed"] and any(fit["use"].values()))
            report["sides"][name] = {
                "passed": bool(passed), "n": int(len(side_pool)),
                "base_bp": fit["ladders"]["base_bp"],
                "coefs": fit["coefs"], "use": fit["use"], "k": fit["k"],
                "g1_improv_bp": fit["gates"]["improv_bp"],
                "g3_top_quintile_bp": fit["gates"]["top_quintile_bp"],
                "g2": g2,
            }
            if passed:
                coefs[int(side_code)] = {
                    "pattern": fit["ladders"]["pattern"],
                    "marginal": fit["ladders"]["marginal"],
                    "cells": fit["ladders"]["cells"],
                    "depth_bp_per_z": fit["ladders"]["depth_bp_per_z"]
                    if fit["use"]["rsi"] else 0.0,
                    "coefs": fit["coefs"], "use": fit["use"],
                    "k": fit["k"],
                }
        if not coefs:
            return rules_rows, history_rows, report
        report["cells"] = self._stamp_boost_conf(rules_rows, coefs)
        return rules_rows, history_rows, report

    def _stamp_boost_conf(self, rules_rows: list[dict],
                          coefs: dict[int, dict]) -> int:
        """The per-cell trust-weighted confidence base onto the matching
        rule rows: w per anchor fact (the derivation's scoring
        semantics), then sum(w*y)/sum(w) over each cell's MEASURABLE
        facts — the cell's dir_ave under the boost's own weighting, in
        percent (the rule_params.confidence.base_pct scale). Only rows
        whose (code, sub, trend, regime, action) match a passing side's
        anchors get a value; the rest keep the engine's NULL."""
        w = self._boost_weights(
            getattr(pd, "_fsproxy_slow", pd).DataFrame(self.facts), coefs,
        )
        action_of_side = {
            SIDE_CODE["bottom"]: ACTION_CODE["buy"],
            SIDE_CODE["top"]: ACTION_CODE["sell"],
        }
        agg: dict[tuple, tuple[float, float]] = {}
        for fact, wi in zip(self.facts, w):
            if wi is None or not fact.get("ok", False):
                continue
            key = (str(fact["code"]), int(fact["sub_code"]),
                   int(fact["trend"]), int(fact["regime"]),
                   action_of_side[int(fact["side"])])
            wy, wn = agg.get(key, (0.0, 0.0))
            agg[key] = (wy + float(wi) * float(fact["y_bp"]),
                        wn + float(wi))
        values = {
            key: round(wy / wn, 4)
            for key, (wy, wn) in agg.items() if wn > 0
        }
        stamped = 0
        for row in rules_rows:
            key = (str(row["code"]), int(row["signal_sub_type"]),
                   int(row["trend"]), int(row["regime_state"]),
                   int(row["action"]))
            if key in values:
                row["ohlcv_boost_conf"] = values[key]
                stamped += 1
        return stamped

    def _attach_depth_z(self, frame: pd.DataFrame) -> pd.DataFrame:
        """The side-aligned depth z-scored within (code, sub_type) and
        capped — the depth axis's normalization on any anchor frame."""
        g = frame["d"].groupby(
            [frame["code"], frame["sub_type"]], sort=False,
        )
        mu, sd = g.transform("mean"), g.transform("std")
        return frame.assign(depth_z=((frame["d"] - mu)
                                     / sd.where(sd > 1e-9))
                            .fillna(0.0)
                            .clip(-BOOST_DEPTH_Z_CAP, BOOST_DEPTH_Z_CAP))

    def _measure_boost_ladders(self, pop: pd.DataFrame) -> dict:
        """The three axis edge tables vs the side's own base (bp),
        measured by groupby at the evidence floors; thinner entries are
        absent (the scoring fallback chain absorbs them)."""
        base = float(pop["y_bp"].mean())

        def _floored(by: list[str], floor: int) -> dict:
            agg = pop.groupby(by, sort=False)["y_bp"].agg(
                ["mean", "size"],
            )
            keep = agg[agg["size"] >= floor]
            return {k: round(float(v) - base, 1)
                    for k, v in keep["mean"].items()}

        pattern = _floored(["pattern"], BOOST_MIN_PATTERN_N)
        pattern = {int(c): e for c, e in pattern.items() if c >= 0}
        marginal = _floored(["vol_state"], BOOST_MIN_STATE_N)
        marginal = {int(c): e for c, e in marginal.items() if c >= 0}
        amt = _floored(["amt"], BOOST_MIN_PATTERN_N)
        amt = {int(c): e for c, e in amt.items() if c >= 0}
        cells = _floored(["vol_state", "px_speed"], BOOST_MIN_CELL_N)
        cells = {(int(vs), int(ps)): e for (vs, ps), e in cells.items()
                 if vs >= 0 and ps >= 0}
        var = float(pop["depth_z"].var())
        depth_slope = round(
            float(pop["depth_z"].cov(pop["y_bp"])) / var, 1,
        ) if np.isfinite(var) and var > 1e-12 else 0.0
        return {"base_bp": round(base, 1), "pattern": pattern,
                "marginal": marginal, "amt": amt, "cells": cells,
                "depth_bp_per_z": depth_slope}

    @staticmethod
    def _boost_edges(pop: pd.DataFrame, ladders: dict) -> pd.DataFrame:
        """The four per-anchor axis edges as frame columns — the
        scoring semantics: pattern missing/unknown -> 0, cell first /
        marginal fallback / NEUTRAL 0, the turnover-climax grade's
        ladder (NEUTRAL 0), depth z x the measured slope."""
        out = pop.copy()
        out["eo"] = out["pattern"].map(ladders["pattern"]) \
            .fillna(0.0).astype("float64")
        # int64 FIRST — the raw int8 codes overflow at *100 (the cell
        # key encoding requires the widened arithmetic)
        cell_key = out["vol_state"].astype("int64") * 100 \
            + out["px_speed"].astype("int64")
        out["ev"] = cell_key.map(
            {vs * 100 + ps: e for (vs, ps), e in ladders["cells"].items()},
        )
        out["ev"] = out["ev"].fillna(
            out["vol_state"].map(ladders["marginal"]),
        ).fillna(0.0).astype("float64")
        out["ea"] = out["amt"].map(
            ladders.get("amt", {})).fillna(0.0).astype("float64")
        out["er"] = out["depth_z"] * ladders["depth_bp_per_z"]
        return out

    def _boost_design(self, pop: pd.DataFrame, use: dict) \
            -> tuple[np.ndarray, np.ndarray]:
        """[1, eo, ev, ea, er, eo.ev/100] + y as HOST arrays (dropped
        axes zeroed; the fixed coefficient order) — the ONE host-numpy
        boundary of the boost (the sanctioned scipy regression
        boundary)."""
        zero = np.zeros(len(pop))
        cols = [
            host_array(pop[c].to_numpy(dtype="float64")) if use[key]
            else zero
            for c, key in zip(BOOST_AXIS_COLUMNS, BOOST_AXIS_KEYS)
        ]
        cross = host_array((pop["eo"] * pop["ev"] / 100.0)
                           .to_numpy(dtype="float64")) \
            if (use["ohlc"] and use["vol"]) else zero
        X = np.column_stack([np.ones(len(pop)), *cols, cross])
        y = host_array(pop["y_bp"].to_numpy(dtype="float64"))
        return X, y

    @staticmethod
    def _boost_ls(X: np.ndarray, y: np.ndarray) -> dict:
        """The bounded LS trust fit: beta in [0, 1.5] (the monotone
        trust ceiling), gamma in [-1, 1]; the NAMED coefficient dict."""
        n_beta = len(BOOST_AXIS_KEYS)
        lo = np.array(
            [-np.inf, *BOOST_BETA_BOUNDS[:1] * n_beta,
             BOOST_GAMMA_BOUNDS[0]],
            dtype="float64",
        )
        hi = np.array(
            [np.inf, *BOOST_BETA_BOUNDS[1:] * n_beta,
             BOOST_GAMMA_BOUNDS[1]],
            dtype="float64",
        )
        ok = np.isfinite(y) & np.isfinite(X).all(axis=1)
        res = lsq_linear(X[ok], y[ok], bounds=(lo, hi), max_iter=200)
        keys = ("b0", *(f"beta_{k}" for k in BOOST_AXIS_KEYS), "gamma")
        return dict(zip(keys, (round(float(v), 4) for v in res.x)))

    def _boost_retention(self, pop: pd.DataFrame, coefs: dict) -> dict:
        """Per-axis retention by per-year sign agreement: the per-year
        univariate slope of the response on each axis edge must agree
        with the full-sample beta in >= BOOST_YEAR_AGREE of the
        measurable years (>= BOOST_YEAR_MIN_N anchors)."""
        use: dict = {}
        for col, key in zip(BOOST_AXIS_COLUMNS, BOOST_AXIS_KEYS):
            beta = coefs[f"beta_{key}"]
            x = pop[col]
            per = pop.assign(
                _x=x, _y=pop["y_bp"], _xy=x * pop["y_bp"], _x2=x * x,
            ).groupby("year", sort=False).agg(
                n=("_y", "size"), sx=("_x", "sum"), sy=("_y", "sum"),
                sxy=("_xy", "sum"), sx2=("_x2", "sum"),
            )
            n = per["n"]
            mx, my = per["sx"] / n, per["sy"] / n
            var = per["sx2"] / n - mx * mx
            cov = per["sxy"] / n - mx * my
            slope = cov / var.where(var > 1e-12)
            meas = slope[slope.notna() & (n >= BOOST_YEAR_MIN_N)
                         & (slope.abs() > 1e-9)]
            agree = int((np.sign(meas) == np.sign(beta)).sum())
            n_meas = int(len(meas))
            use[key] = bool(
                abs(beta) > 1e-9 and n_meas > 0
                and agree / n_meas >= BOOST_YEAR_AGREE,
            )
        return use

    def _boost_score(self, pop: pd.DataFrame, coefs: dict, use: dict) \
            -> pd.Series:
        # starts from a column-derived zero Series (never the proxy
        # pd.Series ctor — the boost math stays host-pure)
        score = pop["eo"] * 0.0
        if use["ohlc"]:
            score = score + coefs["beta_ohlc"] * pop["eo"]
        if use["vol"]:
            score = score + coefs["beta_vol"] * pop["ev"]
        if use["amt"]:
            score = score + coefs["beta_amt"] * pop["ea"]
        if use["rsi"]:
            score = score + coefs["beta_rsi"] * pop["er"]
        if use["ohlc"] and use["vol"]:
            score = score + coefs["gamma"] * pop["eo"] * pop["ev"] / 100.0
        return score

    def _fit_boost_side(self, pop: pd.DataFrame) -> dict:
        """Ladders -> edges -> bounded fit -> retention -> re-fit -> K
        (the score p99, the '@ 1.5x weight' rule) -> G1/G3."""
        ladders = self._measure_boost_ladders(pop)
        edged = self._boost_edges(pop, ladders)
        coefs0 = self._boost_ls(
            *self._boost_design(edged, {k: True for k in BOOST_AXIS_KEYS}),
        )
        use = self._boost_retention(edged, coefs0)
        coefs = self._boost_ls(*self._boost_design(edged, use))
        for key in BOOST_AXIS_KEYS:
            if not use[key]:
                coefs[f"beta_{key}"] = 0.0
        if not (use["ohlc"] and use["vol"]):
            coefs["gamma"] = 0.0
        scored = edged.assign(
            score=self._boost_score(edged, coefs, use),
        )
        k = round(max(float(scored["score"].quantile(0.99)), 1.0), 1)
        w = (1.0 + scored["score"] / k).clip(lower=BOOST_WEIGHT_FLOOR)
        y = scored["y_bp"]
        plain = float(y.mean())
        improv = float((w * y).sum() / w.sum()) - plain
        bar = float(scored["score"].quantile(BOOST_TOP_P))
        hi = y[scored["score"] >= bar]
        top_edge = float(hi.mean()) - plain if len(hi) else float("nan")
        return {
            "ladders": ladders, "coefs": coefs, "use": use, "k": k,
            "gates": {
                "improv_bp": round(improv, 1),
                "top_quintile_bp": round(top_edge, 1),
                "g1": improv > 0.0, "g3": top_edge > 0.0,
            },
        }

    def _boost_g2(self, pop: pd.DataFrame) -> dict:
        """G2: expanding-year walk-forward — per evaluation year, the
        whole fit re-derived on years < Y only, the year scored
        out-of-sample; PASS = a majority of measurable years with
        improv > 0."""
        years = sorted(int(v) for v in pop["year"].drop_duplicates()
                       .tolist())
        legs: list[dict] = []
        for yr in years[1:]:
            train = pop[pop["year"] < yr]
            ev = pop[pop["year"] == yr]
            if len(train) < BOOST_MIN_TRAIN_N or len(ev) < BOOST_YEAR_MIN_N:
                continue
            fit = self._fit_boost_side(train)
            edged_ev = self._boost_edges(ev, fit["ladders"])
            scored_ev = edged_ev.assign(
                score=self._boost_score(edged_ev, fit["coefs"],
                                        fit["use"]),
            )
            w = (1.0 + scored_ev["score"] / fit["k"]) \
                .clip(lower=BOOST_WEIGHT_FLOOR)
            y = scored_ev["y_bp"]
            improv = float((w * y).sum() / w.sum()) - float(y.mean())
            legs.append({"year": yr, "n": int(len(ev)),
                         "improv_bp": round(improv, 1)})
        pos = sum(1 for r in legs if r["improv_bp"] > 0)
        return {"legs": legs, "passed": len(legs) > 0
                and pos > len(legs) / 2.0,
                "years_positive": f"{pos}/{len(legs)}"}

    def _boost_weights(self, facts: pd.DataFrame,
                       coefs: dict[int, dict]) -> list[float | None]:
        """w per fact row (None = the side carries no config — the row's
        cell stamps nothing): the depth z within (code, sub_type) over
        the fact population, the ladders through the same cell ->
        marginal -> neutral fallbacks as the derivation."""
        scored = self._attach_depth_z(facts)
        w: list[float | None] = [None] * len(facts)
        for side_code, spec in coefs.items():
            idx = scored.index[scored["side"] == side_code]
            if len(idx) == 0:
                continue
            sl = scored.loc[idx]
            e = self._boost_edges(sl, spec)
            score = self._boost_score(e, spec["coefs"], spec["use"])
            weight = np.maximum(
                1.0 + score / spec["k"], BOOST_WEIGHT_FLOOR,
            )
            for pos, wi in zip(idx.tolist(),
                               host_array(weight).tolist()):
                w[pos] = float(wi)
        return w


__all__ = ["OhlcvBoost"]
