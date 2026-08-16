"""calibrate_greeks.py — Pin down QuantLib BlackCalculator greek formulas.

Computes QuantLib greeks at a known sigma on an exact-price grid and
compares candidate closed-form variants (with/without discount factor,
theta variants) to identify the exact formulas to replicate.
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import numpy as np
import QuantLib as ql

from _common.df_utils import bs_price_greeks

r = 0.02

rows = []
rng = np.random.default_rng(7)
for _ in range(300):
    S = rng.uniform(2.0, 5.0)
    K = rng.uniform(2.0, 5.0)
    T = rng.uniform(0.05, 1.0)
    sig = rng.uniform(0.1, 0.6)
    is_call = bool(rng.integers(0, 2))
    rows.append((S, K, T, sig, is_call))

for S, K, T, sig, is_call in rows[:0]:
    pass

print(f"{'S':>5s} {'K':>5s} {'T':>5s} {'sig':>5s} {'cp':>3s} | "
      f"{'d_QL':>8s} {'d_A':>8s} {'d_B':>8s} | "
      f"{'g_QL':>10s} {'g_A':>10s} {'g_B':>10s} | "
      f"{'th_QL':>10s} {'t1':>10s} {'t2':>10s} {'t3':>10s}")

err = {k: 0.0 for k in ["d_A", "d_B", "g_A", "g_B", "t1", "t2", "t3", "t4"]}
for S, K, T, sig, is_call in rows:
    payoff = ql.PlainVanillaPayoff(ql.Option.Call if is_call else ql.Option.Put, K)
    F = S * np.exp(r * T)
    D = np.exp(-r * T)
    calc = ql.BlackCalculator(payoff, F, sig * np.sqrt(T), D)

    p, *_ = bs_price_greeks(np.array([S]), np.array([K]), np.array([T]), r,
                            np.array([sig]), np.array([is_call]))
    assert abs(p[0] - calc.value()) < 1e-12, "price mismatch!"

    d_QL = calc.delta(S)
    g_QL = calc.gamma(S)
    th_QL = calc.thetaPerDay(S, T)

    price, d_vec, g_vec, v_vec, th_vec, r_vec = bs_price_greeks(
        np.array([S]), np.array([K]), np.array([T]), r,
        np.array([sig]), np.array([is_call]))

    sqrtT = np.sqrt(T)
    d1 = (np.log(F / K) + 0.5 * sig**2 * T) / (sig * sqrtT)
    d2 = d1 - sig * sqrtT
    from scipy.special import ndtr
    Nd1, Nd2 = ndtr(d1), ndtr(d2)
    if not is_call:
        Nd1, Nd2 = ndtr(d1) - 1.0, ndtr(d2) - 1.0
    phi = np.exp(-0.5 * d1**2) / np.sqrt(2 * np.pi)

    # delta candidates
    d_A = (Nd1 if is_call else ndtr(d1) - 1.0)              # no discount
    d_B = D * (Nd1 if is_call else ndtr(d1) - 1.0)          # with discount

    # gamma candidates
    g_A = phi / (S * sig * sqrtT)                           # no discount
    g_B = D * phi / (S * sig * sqrtT)                       # with discount

    # theta candidates (per day)
    Nc1, Nc2 = ndtr(d1), ndtr(d2)
    term1 = F * phi * sig / (2 * sqrtT)
    V = D * (F * Nc1 - K * Nc2) if is_call else D * (K * ndtr(-d2) - F * ndtr(-d1))
    if is_call:
        t1 = -D * (term1 + r * K * Nc2 - r * F * Nc1) / 365
        t2 = (-D * term1 + r * V) / 365
        t3 = (-D * term1 - r * V) / 365
    else:
        t1 = -D * (term1 - r * K * ndtr(-d2) + r * F * ndtr(-d1)) / 365
        t2 = (-D * term1 + r * V) / 365
        t3 = (-D * term1 - r * V) / 365
    t4 = th_vec[0]  # current module formula == t1

    for k, v in [("d_A", d_A), ("d_B", d_B), ("g_A", g_A), ("g_B", g_B),
                 ("t1", t1), ("t2", t2), ("t3", t3), ("t4", t4)]:
        ref = {"d_A": d_QL, "d_B": d_QL, "g_A": g_QL, "g_B": g_QL,
               "t1": th_QL, "t2": th_QL, "t3": th_QL, "t4": th_QL}[k]
        err[k] = max(err[k], abs(v - ref))

    if rows.index((S, K, T, sig, is_call)) < 3:
        print(f"{S:5.2f} {K:5.2f} {T:5.2f} {sig:5.2f} {'C' if is_call else 'P':>3s} | "
              f"{d_QL:8.5f} {d_A:8.5f} {d_B:8.5f} | "
              f"{g_QL:10.6f} {g_A:10.6f} {g_B:10.6f} | "
              f"{th_QL:10.6f} {t1:10.6f} {t2:10.6f} {t3:10.6f}")

print("\nMax abs error vs QuantLib over the grid:")
for k, v in err.items():
    print(f"  {k}: {v:.3e}")
