"""test_bs_greeks.py — Verify vectorized BS Greeks (_common.df_utils) vs QuantLib."""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import time
import numpy as np
import pandas as pd

from _common.df_utils import compute_iv_and_greeks

try:
    import QuantLib as ql
    HAS_QL = True
except ImportError:
    HAS_QL = False
    print("QuantLib not available, skipping comparison")

from scipy.optimize import brentq

r = 0.02


def ql_reference(S, K, T, price, is_call):
    """Old row-by-row QuantLib+brentq computation (ground truth)."""
    try:
        payoff_type = ql.Option.Call if is_call else ql.Option.Put
        payoff = ql.PlainVanillaPayoff(payoff_type, K)
        F = S * np.exp(r * T)
        D = np.exp(-r * T)

        def objective(sigma):
            return ql.BlackCalculator(payoff, F, sigma * np.sqrt(T), D).value() - price

        vol = brentq(objective, 1e-6, 5.0, xtol=1e-8, rtol=1e-8, maxiter=200)
        calc = ql.BlackCalculator(payoff, F, vol * np.sqrt(T), D)
        return vol, calc.delta(S), calc.thetaPerDay(S, T), calc.gamma(S), calc.vega(T) * 0.01, calc.rho(T) * 0.01
    except Exception:
        return (np.nan,) * 6


# ===========================================================================
print("=" * 64)
print("TEST 1: Correctness vs QuantLib (SZSE-style scaled inputs)")
print("=" * 64)
np.random.seed(42)
n = 2000
S = np.random.uniform(2.0, 5.0, n)
K = np.random.uniform(2.0, 5.0, n)
T = np.random.uniform(0.02, 1.2, n)
is_call = np.random.choice([True, False], n)
sigma_true = np.random.uniform(0.08, 0.9, n)

# Prices from the vectorized pricer itself (self-consistent data)
from _common.df_utils import bs_price_greeks
price, *_ = bs_price_greeks(S, K, T, r, sigma_true, is_call)
# add tiny noise (like real market data)
price = price * (1 + np.random.normal(0, 0.001, n))

test_df = pd.DataFrame({
    "underlying_close": S * 1000,
    "strike_price": K * 1000,
    "settle": price * 10000,
    "days_to_expiry": T * 365,
    "option_type": np.where(is_call, "CALL", "PUT"),
})

t0 = time.time()
iv_new, delta_new, theta_new, gamma_new, vega_new, rho_new = compute_iv_and_greeks(
    test_df, use_gpu=False, verbose=False)
t_new = time.time() - t0
print(f"vectorized CPU: {t_new:.3f}s for {n:,} rows")

t0 = time.time()
ref = np.array([ql_reference(S[i], K[i], T[i], price[i], bool(is_call[i])) for i in range(n)])
t_ql = time.time() - t0
print(f"QuantLib row-by-row: {t_ql:.3f}s for {n:,} rows   (speedup {t_ql/t_new:.0f}x)")

names = ["IV", "Delta", "Theta", "Gamma", "Vega", "Rho"]
new = [iv_new, delta_new, theta_new, gamma_new, vega_new, rho_new]
for name, a in zip(names, new):
    b = ref[:, names.index(name)]
    both = np.isfinite(a) & np.isfinite(b)
    only_new = np.isfinite(a) & ~np.isfinite(b)
    only_ref = ~np.isfinite(a) & np.isfinite(b)
    diff = np.abs(a[both] - b[both])
    scale = np.maximum(np.abs(b[both]), 1e-12)
    print(f"  {name:6s}: n_both={both.sum():4d}  max_abs={np.max(diff):.3e}  "
          f"mean_abs={np.mean(diff):.3e}  max_rel={np.max(diff/scale):.3e}  "
          f"nan_only_new={only_new.sum()}  nan_only_ref={only_ref.sum()}")

# ===========================================================================
print()
print("=" * 64)
print("TEST 2: Performance at scale (1.6M rows, like the real SZSE build)")
print("=" * 64)
n_large = 1_600_000
S2 = np.random.uniform(2.0, 5.0, n_large)
K2 = np.random.uniform(2.0, 5.0, n_large)
T2 = np.random.uniform(0.02, 1.2, n_large)
is_call2 = np.random.choice([True, False], n_large)
sig2 = np.random.uniform(0.08, 0.9, n_large)
price2, *_ = bs_price_greeks(S2, K2, T2, r, sig2, is_call2)

big_df = pd.DataFrame({
    "underlying_close": S2 * 1000,
    "strike_price": K2 * 1000,
    "settle": price2 * 10000,
    "days_to_expiry": T2 * 365,
    "option_type": np.where(is_call2, "CALL", "PUT"),
})

t0 = time.time()
iv2, d2, th2, g2, v2, r2 = compute_iv_and_greeks(big_df, use_gpu=False, verbose=False)
t_cpu = time.time() - t0
err = np.abs(iv2 - sig2)

# Real convergence metric: does the model price at the solved IV match
# the market price? (Always yes — see _probe_conv analysis: every row
# converges; apparent IV "errors" on synthetic deep-OTM/ITM rows are
# float64 price-flat degeneracy where distinct sigmas give identical
# prices, e.g. option price 2.5e-311, unresolvable by ANY solver.)
p_solved, *_ = bs_price_greeks(S2, K2, T2, r, iv2, is_call2)
price_resid = np.abs(p_solved - price2)
solvable = np.isfinite(iv2)
# sigma resolvability: rows need time value >> float64 granularity.
# Deep-ITM/OTM rows whose time value underflows (e.g. put with K/S=1.9,
# T=0.02: N(-d) rounds to 1.0, price == D*(K-F) for ANY sigma in a
# ~0.5-wide band) have fundamentally indeterminate IV — no solver can
# pin sigma there. Require time value > 1e-9 * spot.
D2 = np.exp(-r * T2)
intrinsic = np.where(is_call2, np.maximum(S2 - D2 * K2, 0),
                     np.maximum(D2 * K2 - S2, 0))
resolvable = solvable & (np.abs(price2 - intrinsic) > 1e-9 * S2)
print(f"CPU (numpy):  {t_cpu:.2f}s for {n_large:,} rows")
print(f"  max |price(iv) - price| (solvable rows) = "
      f"{price_resid[solvable].max():.3e}  <- solver converged")
print(f"  max |IV-true| on sigma-resolvable rows (n={resolvable.sum():,}): "
      f"{err[resolvable].max():.3e}")

try:
    import cupy  # noqa
    t0 = time.time()
    iv2g, d2g, th2g, g2g, v2g, r2g = compute_iv_and_greeks(big_df, use_gpu=True, verbose=False)
    t_gpu = time.time() - t0
    p_solved_g, *_ = bs_price_greeks(S2, K2, T2, r, iv2g, is_call2)
    solvable_g = np.isfinite(iv2g)
    print(f"GPU (cupy):   {t_gpu:.2f}s for {n_large:,} rows")
    print(f"  max |price(iv) - price| = "
          f"{np.abs(p_solved_g[solvable_g] - price2[solvable_g]).max():.3e}"
          f"  <- solver converged")
    print(f"  max |IV-true| on sigma-resolvable rows: "
          f"{np.abs(iv2g[resolvable & solvable_g] - sig2[resolvable & solvable_g]).max():.3e}")
    print(f"GPU speedup vs CPU: {t_cpu/t_gpu:.1f}x")
except Exception as e:
    print(f"GPU test skipped: {type(e).__name__}: {e}")

# ===========================================================================
print()
print("=" * 64)
print("TEST 3: CFFEX-style (no scaling) + csv_delta fallback")
print("=" * 64)
n3 = 5000
S3 = np.random.uniform(3000, 4000, n3)
K3 = np.random.uniform(3000, 4000, n3)
T3 = np.random.uniform(0.05, 0.6, n3)
ic3 = np.random.choice([True, False], n3)
p3, *_ = bs_price_greeks(S3, K3, T3, r, np.random.uniform(0.1, 0.4, n3), ic3)
cf_df = pd.DataFrame({
    "underlying_close": S3,
    "strike_price": K3,
    "settle": p3,
    "days_to_expiry": T3 * 365,
    "option_type": np.where(ic3, "CALL", "PUT"),
    "csv_delta": np.full(n3, 0.5),
})
iv3, d3, th3, g3, v3, r3 = compute_iv_and_greeks(
    cf_df, use_gpu=False, price_scale=1.0, opt_scale=1.0,
    csv_delta_col="csv_delta", verbose=False)
print(f"NaN IV rows: {np.isnan(iv3).sum()} (a few deep-ITM rows are expected:"
      f" time value below float64 resolution -> IV undefined, same as brentq)")
print(f"delta filled from csv (unsolvable rows): {np.isclose(d3, 0.5).sum()}")
# force some unsolvable rows: price below intrinsic
deep = np.full(50, 1e-9)
cf_df.loc[cf_df.index[:50], "settle"] = deep
iv3b, d3b, *_ = compute_iv_and_greeks(
    cf_df, use_gpu=False, price_scale=1.0, opt_scale=1.0,
    csv_delta_col="csv_delta", verbose=False)
print(f"with below-intrinsic rows: NaN IV = {np.isnan(iv3b).sum()}, "
      f"csv_delta fallback = {np.isclose(d3b, 0.5).sum()}")

print()
print("ALL TESTS COMPLETE")
